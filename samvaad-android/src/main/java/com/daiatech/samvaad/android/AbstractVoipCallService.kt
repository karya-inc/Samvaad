package com.daiatech.samvaad.android

import android.app.Notification
import android.app.Service
import android.content.Intent
import android.os.Binder
import android.os.IBinder
import androidx.core.app.NotificationManagerCompat
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.NetworkQuality
import com.daiatech.samvaad.core.SamvaadError
import com.daiatech.samvaad.core.VoipCallState
import com.daiatech.samvaad.core.VoipSdkClient
import com.daiatech.samvaad.core.VoipSdkClientFactory
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import timber.log.Timber

/**
 * SDK-agnostic foreground [Service] that owns a single call's [VoipSdkClient] for the service
 * instance's lifetime.
 *
 * A concrete subclass only needs to supply [sdkClientFactory] and [buildNotification] --
 * everything else (foreground-notification lifecycle, the [Binder], duration tracking, and
 * self-stop on a terminal call state) is inherited.
 *
 * Deliberately declares no `foregroundServiceType` in any manifest and requests no runtime
 * permissions of its own -- each consumer decides `phoneCall` vs `microphone` (and the
 * permissions that go with it) for their own use case in their own manifest. This class is
 * mechanism, not policy.
 */
abstract class AbstractVoipCallService : Service() {

    /** Resolves which SDK implementation to use for a given [CallProvider]. */
    protected abstract val sdkClientFactory: VoipSdkClientFactory

    /** App-specific foreground notification -- icon/text/channel are not this library's concern. */
    protected abstract fun buildNotification(): Notification

    private val exceptionHandler = CoroutineExceptionHandler { _, throwable ->
        // An uncaught exception from a VoipSdkClient implementation (a third-party provider
        // adapter bug) must never crash the host process -- log and swallow it instead.
        // SupervisorJob alone does NOT do this: it only isolates sibling coroutines from each
        // other, it doesn't contain an exception on its own.
        Timber.e(throwable, "Uncaught exception in AbstractVoipCallService's coroutine scope")
    }
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob() + exceptionHandler)

    private var sdkClient: VoipSdkClient? = null
    private var relayJob: Job? = null

    private val _callState = MutableStateFlow<VoipCallState>(VoipCallState.Idle)
    val callState: StateFlow<VoipCallState> = _callState.asStateFlow()

    /** Epoch millis when the call first became [VoipCallState.Ongoing], or null before/after.
     * Read-only to subclasses (e.g. to call `NotificationCompat.Builder.setWhen()` for a live
     * chronometer in [buildNotification]) -- only this class may set it. */
    protected var callStartedAtEpochMillis: Long? = null
        private set
    private val _durationSeconds = MutableStateFlow(0L)
    val durationSeconds: StateFlow<Long> = _durationSeconds.asStateFlow()

    // Polled on the same ticker as durationSeconds (ticket: network-health map, "Polling location
    // and cadence") -- not a separate ticker, since networkQuality() is a cheap cached-field read
    // and Daily's underlying signal only reports anything meaningful once media is flowing anyway,
    // the same window duration already covers. Freezes at its last reading on Ended/Error, same as
    // durationSeconds, rather than resetting to null.
    private val _networkQuality = MutableStateFlow<NetworkQuality?>(null)
    val networkQuality: StateFlow<NetworkQuality?> = _networkQuality.asStateFlow()

    private var tickerJob: Job? = null

    /** Named (not anonymous) so a bound consumer can safely cast to it in onServiceConnected. */
    inner class LocalBinder : Binder() {
        fun service(): AbstractVoipCallService = this@AbstractVoipCallService
    }
    private val binder = LocalBinder()
    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        isRunning = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification())

        // A self-targeting PendingIntent a subclass's notification can use for a "hang up"
        // action (e.g. NotificationCompat.CallStyle's required hangUpIntent) -- mechanism, not
        // policy: the library provides the action and handling, the consumer builds the actual
        // PendingIntent/notification style around it.
        if (intent?.action == ACTION_HANG_UP) {
            leaveCall()
            return START_NOT_STICKY
        }

        val providerId = intent?.getStringExtra(EXTRA_PROVIDER_ID)
        val config = intent?.getStringExtra(EXTRA_CONFIG)
        val role = intent?.getStringExtra(EXTRA_ROLE)?.let { roleName ->
            try {
                CallRole.valueOf(roleName)
            } catch (e: IllegalArgumentException) {
                Timber.e(e, "Unrecognized CallRole extra: %s", roleName)
                null
            }
        }
        if (providerId != null && config != null && role != null) {
            join(CallProvider(providerId), config, role)
        }
        return START_NOT_STICKY
    }

    private fun join(provider: CallProvider, config: String, role: CallRole) {
        if (sdkClient != null) {
            // Not surfaced through _callState -- doing so would incorrectly disrupt the real,
            // already-running call for every observer of that shared state, over a duplicate
            // request that was never going to replace it anyway (e.g. a redelivered start
            // command, or a UI double-tap that raced ConferencerBinding's own guard).
            Timber.w(
                SamvaadAndroidError.AlreadyInCall(),
                "AbstractVoipCallService: join() called again on an already-joined service instance -- ignoring",
            )
            return
        }
        // Called directly from onStartCommand(), not inside scope.launch -- NOT covered by
        // exceptionHandler below. An exception from a buggy sdkClientFactory (e.g. the
        // underlying SDK's own construction failing) would otherwise crash onStartCommand and
        // take the whole foreground service, and the host process, down with it.
        val client = try {
            sdkClientFactory.create(provider)
        } catch (e: Exception) {
            val error = SamvaadError.ClientCreationFailed(provider, e)
            Timber.e(error, "AbstractVoipCallService: sdkClientFactory.create() threw -- cannot join this call")
            _callState.value = VoipCallState.Error(cause = error, message = error.message)
            stopSelf()
            return
        }
        sdkClient = client

        relayJob?.cancel()
        relayJob = scope.launch {
            client.callState.collect { state ->
                _callState.value = state
                if (state is VoipCallState.Ongoing && callStartedAtEpochMillis == null) {
                    callStartedAtEpochMillis = System.currentTimeMillis()
                    startDurationTicker()
                }
                // Centralized "when does the service die" here so every caller gets it for
                // free, rather than requiring a separate explicit stop step alongside leaving
                // the call.
                if (state == VoipCallState.Ended || state is VoipCallState.Error) {
                    tickerJob?.cancel()
                    // Reset so startDurationTicker()'s own `?: break` is a live safeguard, not
                    // dead code, if this same Service instance's state ever churns further.
                    callStartedAtEpochMillis = null
                    stopSelf()
                }
                // Reposted once per genuine state transition (e.g. ringing -> Ongoing), not on
                // every ticker tick -- a subclass wanting a live-ticking timer uses
                // setUsesChronometer()/setWhen(callStartedAtEpochMillis) in buildNotification(),
                // which the system itself ticks natively. No new coroutine/ticker needed here,
                // and nothing new that could leak.
                updateNotification()
            }
        }
        // Launched, not called synchronously, so an exception from a provider adapter's join()
        // is caught by exceptionHandler above instead of propagating out of onStartCommand.
        scope.launch { client.join(config, role) }
    }

    fun leaveCall() {
        scope.launch { sdkClient?.leave() }
    }

    fun toggleMic(enable: Boolean) {
        scope.launch { sdkClient?.toggleMic(enable) }
    }

    fun setRecordingEnabled(enabled: Boolean) {
        scope.launch { sdkClient?.setRecordingEnabled(enabled) }
    }

    fun isRecordingEnabled(): Boolean = try {
        sdkClient?.isRecordingEnabled() ?: false
    } catch (e: Exception) {
        Timber.e(e, "Exception querying VoipSdkClient recording state")
        false
    }

    fun isMicrophoneEnabled(): Boolean = try {
        sdkClient?.isMicrophoneEnabled() ?: false
    } catch (e: Exception) {
        Timber.e(e, "Exception querying VoipSdkClient microphone state")
        false
    }

    private fun updateNotification() {
        try {
            NotificationManagerCompat.from(this).notify(NOTIFICATION_ID, buildNotification())
        } catch (e: Exception) {
            // e.g. POST_NOTIFICATIONS revoked mid-call on API 33+ -- must not crash the relay
            // collector, which would silently kill call-state handling as collateral damage.
            Timber.e(e, "Exception updating foreground notification")
        }
    }

    private fun startDurationTicker() {
        tickerJob?.cancel()
        tickerJob = scope.launch {
            while (isActive) {
                val startedAt = callStartedAtEpochMillis ?: break
                // Recomputed from the timestamp every tick, not incremented -- correct even if
                // this coroutine is delayed/suspended by the OS.
                _durationSeconds.value = (System.currentTimeMillis() - startedAt) / 1000
                val newQuality = try {
                    sdkClient?.networkQuality()
                } catch (e: Exception) {
                    Timber.e(e, "Exception querying VoipSdkClient network quality")
                    null
                }
                // Logged on transition into BAD only, not every tick while it stays BAD -- this
                // ticks every 1s, and re-logging on every tick for the whole span of a bad
                // connection would flood logcat for no added signal.
                if (newQuality == NetworkQuality.BAD && _networkQuality.value != NetworkQuality.BAD) {
                    Timber.e("AbstractVoipCallService: network quality dropped to BAD")
                }
                _networkQuality.value = newQuality
                delay(1_000)
            }
        }
    }

    override fun onDestroy() {
        tickerJob?.cancel()
        relayJob?.cancel()
        // Not scope.launch{} -- release() must run synchronously here, before scope.cancel()
        // below; a launched coroutine on Dispatchers.Main isn't guaranteed to run before the
        // very next line executes and would race with (likely lose to) that cancel().
        try {
            sdkClient?.release()
        } catch (e: Exception) {
            Timber.e(e, "Exception releasing VoipSdkClient in onDestroy")
        }
        scope.cancel()
        isRunning = false
        super.onDestroy()
    }

    companion object {
        // Must always be referenced as AbstractVoipCallService.isRunning, never through a
        // concrete subclass's name -- Kotlin doesn't give each subclass its own companion
        // instance.
        var isRunning: Boolean = false
            private set

        private const val NOTIFICATION_ID = 4210

        const val EXTRA_PROVIDER_ID = "com.daiatech.samvaad.android.EXTRA_PROVIDER_ID"
        const val EXTRA_CONFIG = "com.daiatech.samvaad.android.EXTRA_CONFIG"
        const val EXTRA_ROLE = "com.daiatech.samvaad.android.EXTRA_ROLE"
        const val ACTION_HANG_UP = "com.daiatech.samvaad.android.ACTION_HANG_UP"
    }
}
