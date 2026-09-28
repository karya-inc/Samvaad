package com.daiatech.samvaad.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.daiatech.samvaad.android.AbstractVoipCallService
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.VoipSdkClient
import com.daiatech.samvaad.core.VoipSdkClientFactory

/**
 * The only per-app pieces [AbstractVoipCallService] requires: which SDK to use, and what the
 * foreground notification looks like. Everything else -- the foreground-service lifecycle, the
 * Binder, duration tracking, exception containment -- is inherited from the library.
 *
 * Declares no `foregroundServiceType`/permissions of its own in code -- those live in
 * AndroidManifest.xml, this sample's own choice (`microphone`, per the library's settled decision
 * that the library itself stays silent on this).
 */
class SampleVoipCallService : AbstractVoipCallService() {

    override val sdkClientFactory: VoipSdkClientFactory = object : VoipSdkClientFactory {
        override fun create(provider: CallProvider): VoipSdkClient = DailyCoVoipSdkClient(applicationContext)
    }

    override fun buildNotification(): Notification {
        ensureNotificationChannel()

        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )

        // NotificationCompat, not the raw platform Notification.Builder(Context, String) --
        // that 2-arg constructor is API 26+ only, and this module's minSdk is 23. NotificationCompat
        // handles the pre/post-26 channel distinction internally.
        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("Samvaad sample call")
            .setContentText("Call in progress")
            .setSmallIcon(android.R.drawable.ic_menu_call)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .build()
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(NOTIFICATION_CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "Calls",
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
    }

    companion object {
        private const val NOTIFICATION_CHANNEL_ID = "samvaad_sample_calls"
    }
}
