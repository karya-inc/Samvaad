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
import com.daiatech.samvaad.core.VoipCallState
import com.daiatech.samvaad.core.VoipSdkClient
import com.daiatech.samvaad.core.VoipSdkClientFactory
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

/**
 * The only per-app pieces [AbstractVoipCallService] requires: which SDK to use, and what the
 * foreground notification looks like. Everything else -- the foreground-service lifecycle, the
 * Binder, duration tracking, exception containment -- is inherited from the library.
 *
 * Declares no `foregroundServiceType`/permissions of its own in code -- those live in
 * AndroidManifest.xml, this sample's own choice (`microphone`, per the library's settled decision
 * that the library itself stays silent on this).
 */
@AndroidEntryPoint
class SampleVoipCallService : AbstractVoipCallService() {

    // Hilt-injected rather than constructed inline: the seam that lets DailyCoVoipSdkClient's
    // CallClient dependency be swapped for a fake in a plain JVM unit test (see DailyCallClient.kt).
    @Inject
    lateinit var dailyCallClientFactory: DailyCallClientFactory

    override val sdkClientFactory: VoipSdkClientFactory = object : VoipSdkClientFactory {
        override fun create(provider: CallProvider): VoipSdkClient =
            DailyCoVoipSdkClient(applicationContext, dailyCallClientFactory)
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
        val builder = NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("Samvaad sample call")
            .setSmallIcon(android.R.drawable.ic_menu_call)
            .setContentIntent(contentIntent)
            .setOngoing(true)

        // Captured once, not re-read via callState.value below -- the StateFlow could
        // theoretically change between two separate reads; branching on a single snapshot keeps
        // this notification's text/chronometer self-consistent. No `else`: if VoipCallState ever
        // gains a new case, this fails to compile until it's handled here -- exactly the kind of
        // missing-branch bug (VoipCallState.Ended silently falling through to "Ringing...") this
        // guards against now.
        when (callState.value) {
            VoipCallState.Ongoing ->
                // setUsesChronometer + setWhen -- the system renders and ticks the elapsed-time
                // text itself from here on. Deliberately not a hand-rolled per-second repost:
                // this is the one call site reposting the notification (on this state
                // transition), and nothing needs to run again every second just to keep a timer
                // moving.
                builder
                    .setContentText("In call")
                    .setUsesChronometer(true)
                    .setWhen(callStartedAtEpochMillis ?: System.currentTimeMillis())

            VoipCallState.Idle, VoipCallState.Dialing, VoipCallState.Incoming, VoipCallState.Connecting ->
                builder.setContentText("Ringing...")

            VoipCallState.Disconnecting -> builder.setContentText("Ending call...")
            VoipCallState.Ended -> builder.setContentText("Call ended")
            is VoipCallState.Error -> builder.setContentText("Call error")
        }

        return builder.build()
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
