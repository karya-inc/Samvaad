package com.karya.conferencer

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * PROTOTYPE NOTE -- round 4. Was `abstract class ConferencerController<M>`
 * meant to be subclassed; now a plain, final class meant to be HELD AS A
 * FIELD and delegated to. That change is deliberate, not cosmetic:
 * `UserConferencerVoipCallerController` (the real caller-side controller)
 * already MUST implement `UserComponentController<UserConferencerVoipCallerComponent>`
 * -- Karya's task-component-runtime requires it. Kotlin has single class
 * inheritance, so a controller that has to extend `UserComponentController`
 * cannot ALSO extend an abstract `ConferencerController` base class. Any
 * other adopter with their own mandatory base class (their own DI-managed
 * ViewModel, their own MVI framework) would hit the identical wall.
 * Composition sidesteps this entirely: nothing here dictates what a
 * consumer's own controller class extends.
 *
 * This REPLACES what three things in the real app *did*, without replacing
 * their actual classes:
 *  - `VoipCallManager` / `DailyVoipManagerImpl` (the old thin proxy tier --
 *    its only job, relaying Service state, is done here directly, since this
 *    class has to relay anyway to merge in call metadata)
 *  - the relay-and-reconcile logic duplicated across `VoIPCalleeController`
 *    (a global singleton `object`) and `UserConferencerVoipCallerController`
 *    (1200+ lines, buried inside the task-component-runtime) -- both would
 *    hold one of these instead of hand-rolling their own relay
 *
 * Generic over [CallRole] (one code path, branch on role only where behavior
 * genuinely differs -- e.g. only the callee ever receives an incoming-call
 * push) and over metadata type [M] (Karya's `CallInfo` -- caller/callee
 * name+number, meeting link, conference id -- is Karya-specific and does NOT
 * belong in an open-source library; a consumer supplies their own). The
 * three extraction functions that used to be `protected abstract fun`
 * overrides are now plain constructor lambdas, for the same composition
 * reason as the class itself.
 *
 * Guards preserved from the real VoIPCalleeController (each one was a real,
 * shipped bug fix -- dropping them silently on a "clean rewrite" would
 * reintroduce bugs that were already found and fixed once):
 *  - onServiceDisconnected (abrupt process death of the bound service) must
 *    still flip local state to Idle -- see [onServiceDisconnected] below.
 *  - A transient Idle report from the Service, while a call is pending or
 *    active, must NOT be allowed to drop the UI back to the no-call screen
 *    unless the state is genuinely Ended or Error ("rollback" guard).
 *  - Redelivered incoming-call metadata for a conference already accepted,
 *    or already past the incoming screen, must be ignored, not reprocessed.
 *  - The relay collector must NOT be torn down between calls -- only when
 *    this instance is destroyed -- otherwise the next incoming call has
 *    nothing collecting its state transitions.
 */
class ConferencerBinding<M>(
    context: Context,
    private val role: CallRole,
    private val serviceClass: Class<out AbstractVoipCallService>,
    private val callId: (metadata: M) -> String?,
    private val joinConfig: (metadata: M) -> String,
    private val provider: (metadata: M) -> CallProvider,
) {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var service: AbstractVoipCallService? = null
    private var bound = false
    private var relayJob: Job? = null

    private val _uiState = MutableStateFlow<ConferencerUiState<M>>(ConferencerUiState.Idle<M>())
    val uiState: StateFlow<ConferencerUiState<M>> = _uiState.asStateFlow()

    private var acceptedCallId: String? = null

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val localBinder = binder as? AbstractVoipCallService.LocalBinder ?: return
            service = localBinder.service()
            bound = true
            startRelayIfNeeded()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            // Preserved guard: the bound foreground service died unexpectedly
            // (OOM/system kill). Flip to Idle regardless of whatever the
            // last relayed state was -- there is nothing left to reconcile
            // against.
            bound = false
            service = null
            relayJob?.cancel()
            relayJob = null
            _uiState.value = ConferencerUiState.Idle<M>()
        }
    }

    private fun startRelayIfNeeded() {
        if (relayJob?.isActive == true) return
        val svc = service ?: return
        relayJob = scope.launch {
            svc.callState.collect { state -> onServiceStateChanged(state) }
        }
    }

    private fun onServiceStateChanged(state: VoipCallState) {
        val current = _uiState.value
        val metadata = current.metadata()

        // Preserved guard: a transient Idle report must not drop an in-progress
        // or pending call back to the no-call screen.
        val callPending = metadata != null || acceptedCallId != null
        val transientIdle = state is VoipCallState.Idle && callPending
        if (transientIdle) return

        _uiState.value = when (state) {
            VoipCallState.Idle -> if (metadata != null) current else ConferencerUiState.Idle<M>()
            VoipCallState.Dialing, VoipCallState.Incoming, VoipCallState.Connecting ->
                ConferencerUiState.Connecting(metadata)
            VoipCallState.Ongoing ->
                ConferencerUiState.Ongoing(metadata, service?.durationSeconds ?: MutableStateFlow(0L))
            VoipCallState.Disconnecting -> ConferencerUiState.Disconnecting(metadata)
            VoipCallState.Ended -> ConferencerUiState.Idle<M>()
            is VoipCallState.Error -> ConferencerUiState.Error(metadata, state.message)
        }

        if (state == VoipCallState.Ended || state is VoipCallState.Error) {
            acceptedCallId = null
        }
    }

    /** Callee only: an incoming-call push arrived. No-ops for redelivery of an already-handled call. */
    fun onIncomingCall(metadata: M) {
        require(role == CallRole.CALLEE) { "onIncomingCall is callee-only" }
        val id = callId(metadata)

        // Preserved guard: ignore redelivered metadata for a conference already accepted.
        if (id != null && id == acceptedCallId) return

        // Preserved guard: ignore redelivery once we're already past the incoming screen for this call.
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
        bindIfNeeded()
        val intent = Intent(appContext, serviceClass).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, provider(metadata).id)
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, joinConfig(metadata))
            putExtra(AbstractVoipCallService.EXTRA_ROLE, role.name)
        }
        appContext.startForegroundService(intent)
    }

    fun declineOrEnd() {
        service?.leaveCall()
        // Preserved: do NOT cancel relayJob here -- it must survive to
        // collect the next incoming call's transitions. Only destroy() does.
        acceptedCallId = null
        _uiState.value = ConferencerUiState.Idle<M>()
    }

    fun toggleMic(enable: Boolean) {
        service?.toggleMic(enable)
    }

    /**
     * Reattach to an already-running call after this process was killed and
     * restarted with a fresh Controller instance (e.g. cold start while a
     * call is still alive in the background service). Returns false with no
     * side effect if there's nothing to attach to -- callers use that to
     * distinguish "genuinely idle" from "a call is running but I'm not
     * watching it yet" before deciding what to show.
     */
    fun attachIfRunning(): Boolean {
        if (!AbstractVoipCallService.isRunning) return false
        bindIfNeeded()
        return true
    }

    private fun bindIfNeeded() {
        if (bound) return
        appContext.bindService(Intent(appContext, serviceClass), connection, Context.BIND_AUTO_CREATE)
    }

    /** Call from the owning Service's onDestroy, never from a UI event (endCall/decline). */
    fun destroy() {
        relayJob?.cancel()
        if (bound) appContext.unbindService(connection)
        scope.cancel()
    }
}

/**
 * Replaces VoipUiState + CallScreenState/CallScreen/VoipCallUiState -- those
 * three were independently-evolved sealed classes covering nearly the same
 * Idle/Ringing/Connecting/Active shape. Generic over [M] so Karya's
 * caller-name/number/summary fields don't leak into the open-source type.
 */
sealed class ConferencerUiState<M> {
    abstract fun metadata(): M?

    class Idle<M> : ConferencerUiState<M>() { override fun metadata(): M? = null }
    data class Incoming<M>(val meta: M) : ConferencerUiState<M>() { override fun metadata() = meta }
    data class Connecting<M>(val meta: M?) : ConferencerUiState<M>() { override fun metadata() = meta }
    data class Ongoing<M>(val meta: M?, val durationSeconds: StateFlow<Long>) : ConferencerUiState<M>() {
        override fun metadata() = meta
    }
    data class Disconnecting<M>(val meta: M?) : ConferencerUiState<M>() { override fun metadata() = meta }
    data class Error<M>(val meta: M?, val message: String?) : ConferencerUiState<M>() { override fun metadata() = meta }
}
