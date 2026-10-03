package com.daiatech.samvaad.android

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import androidx.core.content.ContextCompat
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.NetworkQuality
import com.daiatech.samvaad.core.VoipCallState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import timber.log.Timber

/**
 * A plain, final class held as a field and delegated to — NOT a base class to extend.
 *
 * `UserConferencerVoipCallerController` (Karya's real caller-side controller) already must
 * implement `UserComponentController<UserConferencerVoipCallerComponent>` — Kotlin has single
 * class inheritance, so a controller required to extend that cannot also extend an abstract
 * binding base class. Any other adopter with their own mandatory base class (a DI-managed
 * ViewModel, their own MVI framework) hits the identical wall. Composition sidesteps this
 * entirely: nothing here dictates what a consumer's own controller class extends.
 *
 * Binds directly to [AbstractVoipCallService] — no separate Manager proxy tier; relaying state
 * is this class's job anyway, since it has to relay to merge in caller-supplied metadata [M].
 * Generic over [CallRole] (one code path, branching only where behavior genuinely differs — e.g.
 * only the callee ever receives [onIncomingCall]) and over [M] (a consumer's own call-metadata
 * type; this library knows nothing about its shape).
 */
class ConferencerBinding<M>(
    context: Context,
    private val role: CallRole,
    private val serviceClass: Class<out AbstractVoipCallService>,
    private val callId: (metadata: M) -> String?,
    private val joinConfig: (metadata: M) -> String,
    private val provider: (metadata: M) -> CallProvider,
) {
    // applicationContext, not the passed-in Context directly -- this field outlives whatever
    // Activity/Fragment/Context a caller happened to construct this with.
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var service: AbstractVoipCallService? = null
    private var bound = false
    private var relayJob: Job? = null

    private val _uiState = MutableStateFlow<ConferencerUiState<M>>(ConferencerUiState.Idle())
    val uiState: StateFlow<ConferencerUiState<M>> = _uiState.asStateFlow()

    private var acceptedCallId: String? = null

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val localBinder = binder as? AbstractVoipCallService.LocalBinder ?: return
            service = localBinder.service()
            startRelay()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            // Preserved guard: the bound foreground service died unexpectedly (OOM/system
            // kill). Flip to Idle regardless of whatever the last relayed state was -- there's
            // nothing left to reconcile against. Also release our own binding registration
            // explicitly (the framework doesn't do that for us just because the remote process
            // died) -- otherwise it's a real leak: one bindService() call now permanently
            // unmatched by an unbindService() call, and a later bindIfNeeded() would think we're
            // still bound and skip rebinding entirely.
            Timber.e("ConferencerBinding: bound service disconnected unexpectedly (process killed?) -- resetting to Idle")
            relayJob?.cancel()
            relayJob = null
            unbindIfNeeded()
            _uiState.value = ConferencerUiState.Idle()
        }
    }

    private fun startRelay() {
        val svc = service ?: return
        // Always cancel-then-relaunch here, rather than a `relayJob?.isActive == true` guard:
        // once the unbind-on-terminal-state fix below can retire a relayJob whose Service
        // instance has genuinely gone away, that Job stays "active" forever (its collect never
        // completes on its own, since a StateFlow never completes) -- a guard like that would
        // wrongly skip relaying the *next* call's freshly-bound Service instance. onServiceConnected
        // only fires once per actual new connection, so unconditionally restarting here is safe.
        relayJob?.cancel()
        relayJob = scope.launch {
            svc.callState.collect { state -> onServiceStateChanged(state) }
        }
    }

    private fun onServiceStateChanged(state: VoipCallState) {
        val current = _uiState.value
        val metadata = current.metadata()

        // Preserved guard: a transient Idle report must not drop an in-progress or pending call
        // back to the no-call screen.
        val callPending = metadata != null || acceptedCallId != null
        val transientIdle = state is VoipCallState.Idle && callPending
        if (transientIdle) return

        _uiState.value = when (state) {
            // metadata is always null here: transientIdle above already returned early for
            // every case where state is Idle AND metadata != null.
            VoipCallState.Idle -> ConferencerUiState.Idle()
            VoipCallState.Dialing, VoipCallState.Incoming, VoipCallState.Connecting ->
                ConferencerUiState.Connecting(metadata)
            VoipCallState.Ongoing ->
                ConferencerUiState.Ongoing(
                    metadata,
                    service?.durationSeconds ?: MutableStateFlow(0L),
                    service?.networkQuality ?: MutableStateFlow(null),
                )
            VoipCallState.Disconnecting -> ConferencerUiState.Disconnecting(metadata)
            VoipCallState.Ended -> ConferencerUiState.Idle()
            is VoipCallState.Error -> ConferencerUiState.Error(metadata, state.message)
        }

        if (state == VoipCallState.Ended || state is VoipCallState.Error) {
            acceptedCallId = null

            // Settled fix (ARCHITECTURE.md §7.1, previously unfixed in the real app): unbind now,
            // rather than waiting for an external destroy() call. Android's documented behavior:
            // a service that's both started and bound won't actually stop until every client
            // unbinds, regardless of how many times stopSelf()/stopService() is called -- without
            // this, the service lingers alive, bound, in a stopped-but-not-destroyed limbo after
            // every normal call end. `scope` itself is NOT cancelled here (only destroy() does
            // that) -- the relaying *capability* must survive so the next call gets a fresh relay
            // via startRelay() above, even though this specific relayJob's Service instance is
            // about to actually be destroyed now that nothing's bound to it anymore.
            unbindIfNeeded()
            relayJob?.cancel()
            relayJob = null
        }
    }

    /** Callee only: an incoming-call push arrived. No-ops for redelivery of an already-handled call. */
    fun onIncomingCall(metadata: M) {
        require(role == CallRole.CALLEE) { "onIncomingCall is callee-only" }
        val id = callId(metadata)

        // Preserved guard: ignore redelivered metadata for a conference already accepted.
        if (id != null && id == acceptedCallId) return

        // Preserved guard: ignore redelivery once we're already past the incoming screen for
        // this call.
        val current = _uiState.value
        val sameCall = id != null && id == current.metadata()?.let { callId(it) }
        val pastIncoming = current !is ConferencerUiState.Idle
        if (sameCall && pastIncoming) return

        bindIfNeeded()
        _uiState.value = ConferencerUiState.Incoming(metadata)
    }

    /** Callee: user tapped Accept. Caller: user tapped Call. */
    fun acceptOrInitiate(metadata: M) {
        acceptedCallId = callId(metadata)
        // Seed the metadata now, not just on the callee path (where onIncomingCall already did
        // this): for the caller, this is the *only* place metadata is ever attached, and without
        // it current.metadata() would stay null for the whole call -- every Connecting/Ongoing/
        // Disconnecting/Error state a caller sees would silently carry meta = null.
        _uiState.value = ConferencerUiState.Connecting(metadata)
        bindIfNeeded()
        val intent = Intent(appContext, serviceClass).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, provider(metadata).id)
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, joinConfig(metadata))
            putExtra(AbstractVoipCallService.EXTRA_ROLE, role.name)
        }
        // Not appContext.startForegroundService(intent) directly -- that method itself doesn't
        // exist below API 26. ContextCompat.startForegroundService falls back to startService()
        // pre-26, where the foreground-service background-start restrictions this is otherwise
        // working around don't apply yet anyway.
        ContextCompat.startForegroundService(appContext, intent)
    }

    fun declineOrEnd() {
        service?.leaveCall()
        // Preserved guard: do NOT cancel relayJob here -- it must survive to collect this call's
        // transition through to its terminal state (which is what actually unbinds, above).
        acceptedCallId = null
        _uiState.value = ConferencerUiState.Idle()
    }

    fun toggleMic(enable: Boolean) {
        service?.toggleMic(enable)
    }

    fun setRecordingEnabled(enabled: Boolean) {
        service?.setRecordingEnabled(enabled)
    }

    /** Current mic state, read from the live SDK client -- not tracked separately by this class. */
    fun isMicrophoneEnabled(): Boolean = service?.isMicrophoneEnabled() ?: false

    /** Current recording state, read from the live SDK client -- not tracked separately by this class. */
    fun isRecordingEnabled(): Boolean = service?.isRecordingEnabled() ?: false

    /**
     * Reattach to an already-running call after this process was killed and restarted with a
     * fresh [ConferencerBinding] instance (e.g. cold start while a call is still alive in the
     * background service). Returns `false` with no side effect if there's nothing to attach to
     * -- callers use that to distinguish "genuinely idle" from "a call is running but I'm not
     * watching it yet" before deciding what to show.
     */
    fun attachIfRunning(): Boolean {
        if (!AbstractVoipCallService.isRunning) return false
        bindIfNeeded()
        return true
    }

    private fun bindIfNeeded() {
        if (bound) return
        // Set eagerly, before the actual bindService() call, not just once onServiceConnected
        // confirms it -- that callback lands asynchronously, so onIncomingCall() then
        // acceptOrInitiate() shortly after (a real sequence: incoming call, user taps Accept)
        // would otherwise both see bound == false and call bindService() twice, registering the
        // same ServiceConnection with the framework twice for one eventual unbindService() call.
        bound = true
        val requested = appContext.bindService(
            Intent(appContext, serviceClass),
            connection,
            Context.BIND_AUTO_CREATE,
        )
        if (!requested) {
            // bindService() failed synchronously; nothing pending after all. Leaves `bound` reset
            // so a later retry (e.g. the next acceptOrInitiate) doesn't permanently skip binding.
            bound = false
            Timber.e("ConferencerBinding: bindService() returned false -- call will not start")
        }
    }

    private fun unbindIfNeeded() {
        if (!bound) return
        try {
            appContext.unbindService(connection)
        } catch (e: IllegalArgumentException) {
            // "Service not registered" -- the framework's own bookkeeping already dropped this
            // ServiceConnection (e.g. a prior unbind this class itself issued, or the process
            // restarted). Logged, not rethrown: an uncaught exception here would otherwise
            // propagate out of onServiceStateChanged/onServiceDisconnected/destroy() and crash
            // the host, while `bound`/`service` below still need clearing regardless.
            Timber.e(e, "ConferencerBinding: unbindService() failed -- already unregistered?")
        }
        bound = false
        service = null
    }

    /** Call from the owning component's teardown, never from a UI event (endCall/decline). */
    fun destroy() {
        relayJob?.cancel()
        relayJob = null
        unbindIfNeeded()
        scope.cancel()
    }
}

/**
 * Replaces `VoipUiState`/`CallScreenState`/`CallScreen`/`VoipCallUiState` -- those were
 * independently-evolved sealed classes covering nearly the same Idle/Ringing/Connecting/Active
 * shape. Generic over [M] so a consumer's own metadata fields don't leak into this type.
 */
sealed class ConferencerUiState<M> {
    abstract fun metadata(): M?

    class Idle<M> : ConferencerUiState<M>() {
        override fun metadata(): M? = null
    }

    data class Incoming<M>(val meta: M) : ConferencerUiState<M>() {
        override fun metadata() = meta
    }

    data class Connecting<M>(val meta: M?) : ConferencerUiState<M>() {
        override fun metadata() = meta
    }

    data class Ongoing<M>(
        val meta: M?,
        val durationSeconds: StateFlow<Long>,
        val networkQuality: StateFlow<NetworkQuality?>,
    ) : ConferencerUiState<M>() {
        override fun metadata() = meta
    }

    data class Disconnecting<M>(val meta: M?) : ConferencerUiState<M>() {
        override fun metadata() = meta
    }

    data class Error<M>(val meta: M?, val message: String?) : ConferencerUiState<M>() {
        override fun metadata() = meta
    }
}
