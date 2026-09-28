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

    override fun buildNotification(): Notification =
        NotificationCompat.Builder(this, "test-channel").build()
}
