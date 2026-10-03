package com.daiatech.samvaad.android

import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.daiatech.samvaad.core.NetworkQuality
import com.daiatech.samvaad.core.VoipCallState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLooper

/**
 * Covers wayfinder ticket "Polling location and cadence" (network-health map): networkQuality is
 * polled on the same ticker as durationSeconds, not a separate one, starting only once the call is
 * genuinely Ongoing and freezing (not resetting) once the call ends.
 */
@RunWith(RobolectricTestRunner::class)
class AbstractVoipCallServiceNetworkQualityTest {

    private fun startIntent() =
        Intent(ApplicationProvider.getApplicationContext(), TestVoipCallService::class.java).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, "dailyco")
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, "config")
            putExtra(AbstractVoipCallService.EXTRA_ROLE, "CALLER")
        }

    private fun idle() = ShadowLooper.idleMainLooper()

    @Test
    fun `networkQuality is null before the call is Ongoing`() {
        val fakeClient = FakeVoipSdkClient().apply { networkQualityToReturn = NetworkQuality.GOOD }
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        controller.get().fakeClient = fakeClient

        controller.create().startCommand(0, 0)
        idle()

        assertNull(controller.get().networkQuality.value)

        controller.destroy()
    }

    @Test
    fun `networkQuality reflects the SDK client once the call is Ongoing`() {
        val fakeClient = FakeVoipSdkClient().apply { networkQualityToReturn = NetworkQuality.POOR }
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        controller.get().fakeClient = fakeClient

        controller.create().startCommand(0, 0)
        fakeClient.emit(VoipCallState.Ongoing)
        idle()

        assertEquals(NetworkQuality.POOR, controller.get().networkQuality.value)

        controller.destroy()
    }

    @Test
    fun `networkQuality freezes at its last reading after the call ends, it does not reset to null`() {
        val fakeClient = FakeVoipSdkClient().apply { networkQualityToReturn = NetworkQuality.BAD }
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        controller.get().fakeClient = fakeClient

        controller.create().startCommand(0, 0)
        fakeClient.emit(VoipCallState.Ongoing)
        idle()
        assertEquals(NetworkQuality.BAD, controller.get().networkQuality.value)

        fakeClient.emit(VoipCallState.Ended)
        idle()

        assertEquals(
            "expected the last reading to freeze in place, matching durationSeconds's existing behavior",
            NetworkQuality.BAD,
            controller.get().networkQuality.value,
        )

        controller.destroy()
    }
}
