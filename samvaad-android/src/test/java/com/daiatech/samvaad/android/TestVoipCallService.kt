package com.daiatech.samvaad.android

import android.app.Notification
import androidx.core.app.NotificationCompat
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.VoipSdkClient
import com.daiatech.samvaad.core.VoipSdkClientFactory

/** Minimal concrete [AbstractVoipCallService] for tests -- `fakeClient` must be set on the
 * instance (via [android.app.Service]'s controller, before `onStartCommand`) before use. */
class TestVoipCallService : AbstractVoipCallService() {

    lateinit var fakeClient: FakeVoipSdkClient
    var factory: FakeVoipSdkClientFactory? = null

    override val sdkClientFactory: VoipSdkClientFactory
        get() = (factory ?: FakeVoipSdkClientFactory(fakeClient)).also { factory = it }

    var buildNotificationCallCount = 0
        private set

    /** Captured from [callStartedAtEpochMillis] on each [buildNotification] call -- this is the
     * real access pattern a consumer subclass (e.g. to call setWhen() for a chronometer) uses;
     * exposed here so tests can observe it without needing subclass access themselves. */
    var lastSeenCallStartedAtEpochMillis: Long? = null
        private set

    override fun buildNotification(): Notification {
        buildNotificationCallCount++
        lastSeenCallStartedAtEpochMillis = callStartedAtEpochMillis
        return NotificationCompat.Builder(this, "test-channel").build()
    }
}
