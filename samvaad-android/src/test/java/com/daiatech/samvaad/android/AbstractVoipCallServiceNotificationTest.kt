package com.daiatech.samvaad.android

import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.daiatech.samvaad.core.VoipCallState
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLooper
import java.util.concurrent.TimeUnit

/**
 * The foreground notification is reposted once per genuine call-state transition (Ringing ->
 * Ongoing, etc.), reusing the existing relay collector -- not a new ticker, and not reposted on
 * every tick while Ongoing. The live timer itself is NotificationCompat's own chronometer
 * (setUsesChronometer + setWhen), which the system ticks natively with zero app-side repeated
 * work -- deliberately chosen over a hand-rolled per-second repost loop so this feature adds no
 * new coroutine/lifecycle surface to leak.
 */
@RunWith(RobolectricTestRunner::class)
class AbstractVoipCallServiceNotificationTest {

    private fun startIntent() =
        Intent(ApplicationProvider.getApplicationContext(), TestVoipCallService::class.java).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, "dailyco")
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, "config")
            putExtra(AbstractVoipCallService.EXTRA_ROLE, "CALLER")
        }

    private fun idle() = ShadowLooper.idleMainLooper()
    private fun idleOneTick() = ShadowLooper.idleMainLooper(1_000, TimeUnit.MILLISECONDS)

    @Test
    fun `notification is reposted once per state transition, not on every ticker tick`() {
        val fakeClient = FakeVoipSdkClient()
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        val service = controller.get()
        service.fakeClient = fakeClient

        controller.create().startCommand(0, 0)
        idle()
        // One post from startForeground()'s own buildNotification() call in onStartCommand(),
        // plus one more from the relay collector's first emission -- a StateFlow always replays
        // its current value (Idle) to a brand new collector, even with no real transition yet.
        assertEquals(2, service.buildNotificationCallCount)

        fakeClient.emit(VoipCallState.Dialing)
        idle()
        assertEquals(3, service.buildNotificationCallCount)

        fakeClient.emit(VoipCallState.Ongoing)
        idle()
        assertEquals(4, service.buildNotificationCallCount)

        // Several ticker ticks while Ongoing -- must NOT trigger further reposts; the chronometer
        // ticks the displayed text natively, not this code.
        idleOneTick()
        idleOneTick()
        idleOneTick()
        assertEquals(
            "expected no additional notification reposts from ticker ticks alone",
            4,
            service.buildNotificationCallCount,
        )

        fakeClient.emit(VoipCallState.Ended)
        idle()
        assertEquals(5, service.buildNotificationCallCount)

        controller.destroy()
    }

    @Test
    fun `callStartedAtEpochMillis is readable by a subclass's buildNotification, and only set once Ongoing`() {
        val fakeClient = FakeVoipSdkClient()
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        val service = controller.get()
        service.fakeClient = fakeClient

        controller.create().startCommand(0, 0)
        idle()
        assertEquals(null, service.lastSeenCallStartedAtEpochMillis)

        fakeClient.emit(VoipCallState.Ongoing)
        idle()
        assert(service.lastSeenCallStartedAtEpochMillis != null) {
            "expected callStartedAtEpochMillis to be readable and set once Ongoing, for a subclass's " +
                "buildNotification() to use with setWhen()"
        }

        controller.destroy()
    }
}
