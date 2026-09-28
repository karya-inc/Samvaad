package com.karya.conferencer

import android.app.Notification
import android.app.Service
import android.content.Intent
import android.os.Binder
import android.os.IBinder
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

/**
 * PROTOTYPE NOTE: this is the new piece your question surfaced -- the real
 * VoIPCallService owns a concrete `DailyManager` directly, which is exactly
 * what makes it Daily.co-specific today. This base class is that same
 * foreground-service/Binder/lifecycle plumbing, made SDK-agnostic by
 * depending only on [VoipSdkClient] (see that file), so it can be
 * open-sourced once and reused by every provider adapter.
 *
 * Responsibilities that live HERE, not in any SDK adapter:
 *  - Foreground-service start/stop and the notification (subclass supplies
 *    the actual Notification -- can't be generic, it's per-app branding)
 *  - Binder exposure to whatever VoipCallManager impl binds to this service
 *  - Owning the single VoipSdkClient instance for this call's lifetime
 *  - Duration: stamped the FIRST time the relayed state flow reports
 *    Ongoing -- deliberately NOT delegated to the SDK layer (see
 *    VoipSdkClient.kt's KDoc and the conversation this reacts to)
 *  - The isRunning flag other code polls for orphan-call detection
 *
 * A concrete subclass (e.g. `KaryaVoipCallService`) only needs to supply
 * [sdkClientFactory] and [buildNotification] -- everything else is inherited.
 */
abstract class AbstractVoipCallService : Service() {

    /** Which SDK implementation to use, resolved once per call from the start Intent. */
    protected abstract val sdkClientFactory: VoipSdkClientFactory

    /** App-specific foreground notification (icon/text/channel are not this library's concern). */
    protected abstract fun buildNotification(): Notification

    private var sdkClient: VoipSdkClient? = null
    private var relayJob: Job? = null
    private var scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private val _callState = MutableStateFlow<VoipCallState>(VoipCallState.Idle)
    val callState: StateFlow<VoipCallState> = _callState.asStateFlow()

    private var callStartedAtEpochMillis: Long? = null
    private val _durationSeconds = MutableStateFlow(0L)
    val durationSeconds: StateFlow<Long> = _durationSeconds.asStateFlow()
    private var tickerJob: Job? = null

    /** Named (not anonymous) so ConferencerController can safely cast to it in onServiceConnected. */
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
        val providerId = intent?.getStringExtra(EXTRA_PROVIDER_ID)
        val config = intent?.getStringExtra(EXTRA_CONFIG)
        val role = intent?.getStringExtra(EXTRA_ROLE)?.let { CallRole.valueOf(it) }
        if (providerId != null && config != null && role != null) {
            join(CallProvider(providerId), config, role)
        }
        return START_NOT_STICKY
    }

    private fun join(provider: CallProvider, config: String, role: CallRole) {
        if (sdkClient != null) return // already joined for this service instance's lifetime
        val client = sdkClientFactory.create(provider)
        sdkClient = client

        relayJob?.cancel()
        relayJob = scope.launch {
            client.callState.collect { state ->
                _callState.value = state
                if (state is VoipCallState.Ongoing && callStartedAtEpochMillis == null) {
                    callStartedAtEpochMillis = System.currentTimeMillis()
                    startDurationTicker()
                }
                // Centralizing "when does the service die" here, rather than
                // requiring every caller (Controller) to remember a separate
                // stopService() step -- the real app's cleanup() used to call
                // both leaveCall() AND stopServiceIfRunning() as two things a
                // caller had to get right together.
                if (state == VoipCallState.Ended || state is VoipCallState.Error) {
                    tickerJob?.cancel()
                    stopSelf()
                }
            }
        }
        client.join(config, role)
    }

    fun leaveCall() {
        sdkClient?.leave()
    }

    fun toggleMic(enable: Boolean) {
        sdkClient?.toggleMic(enable)
    }

    fun isMicrophoneEnabled(): Boolean = sdkClient?.isMicrophoneEnabled() ?: false

    private fun startDurationTicker() {
        tickerJob?.cancel()
        tickerJob = scope.launch {
            while (isActive) {
                val startedAt = callStartedAtEpochMillis ?: break
                // Recomputed from the timestamp every tick, not incremented --
                // correct even if this coroutine was delayed/suspended by the
                // OS, unlike a naive counter that just adds 1 per "tick".
                _durationSeconds.value = (System.currentTimeMillis() - startedAt) / 1000
                delay(1000)
            }
        }
    }

    override fun onDestroy() {
        tickerJob?.cancel()
        relayJob?.cancel()
        sdkClient?.release()
        scope.cancel()
        isRunning = false
        super.onDestroy()
    }

    companion object {
        var isRunning: Boolean = false
            private set

        const val NOTIFICATION_ID = 4210 // subclass may override via its own buildNotification/channel

        const val EXTRA_PROVIDER_ID = "com.karya.conferencer.EXTRA_PROVIDER_ID"
        const val EXTRA_CONFIG = "com.karya.conferencer.EXTRA_CONFIG"
        const val EXTRA_ROLE = "com.karya.conferencer.EXTRA_ROLE"
    }
}
