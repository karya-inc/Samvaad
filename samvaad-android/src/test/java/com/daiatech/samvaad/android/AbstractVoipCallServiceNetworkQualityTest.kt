package com.daiatech.samvaad.android

import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.daiatech.samvaad.core.NetworkQuality
import com.daiatech.samvaad.core.VoipCallState
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLooper
import timber.log.Timber
import java.util.concurrent.TimeUnit

/**
 * Covers wayfinder ticket "Polling location and cadence" (network-health map): networkQuality is
 * polled on the same ticker as durationSeconds, not a separate one, starting only once the call is
 * genuinely Ongoing and freezing (not resetting) once the call ends. Also covers the Timber.e
 * log fired on a transition into the worst (BAD) reading.
 */
@RunWith(RobolectricTestRunner::class)
class AbstractVoipCallServiceNetworkQualityTest {

    private class RecordingTree : Timber.Tree() {
        val errors = mutableListOf<String>()
        override fun log(priority: Int, tag: String?, message: String, t: Throwable?) {
            if (priority >= android.util.Log.ERROR) errors += message
        }
    }

    private val recordingTree = RecordingTree()

    init {
        Timber.plant(recordingTree)
    }

    @After
    fun tearDown() {
        Timber.uproot(recordingTree)
    }

    private fun startIntent() =
        Intent(ApplicationProvider.getApplicationContext(), TestVoipCallService::class.java).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, "dailyco")
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, "config")
            putExtra(AbstractVoipCallService.EXTRA_ROLE, "CALLER")
        }

    private fun idle() = ShadowLooper.idleMainLooper()

    /** Advances past one more 1s tick of the reused duration/network-quality ticker. */
    private fun idleOneTick() = ShadowLooper.idleMainLooper(1_000, TimeUnit.MILLISECONDS)

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

    @Test
    fun `logs via Timber-e when network quality transitions to BAD`() {
        val fakeClient = FakeVoipSdkClient().apply { networkQualityToReturn = NetworkQuality.GOOD }
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        controller.get().fakeClient = fakeClient

        controller.create().startCommand(0, 0)
        fakeClient.emit(VoipCallState.Ongoing)
        idle()
        assertEquals(NetworkQuality.GOOD, controller.get().networkQuality.value)
        assertTrue(
            "no BAD-transition log expected yet -- quality is GOOD",
            recordingTree.errors.none { it.contains("network quality dropped to BAD") },
        )

        fakeClient.networkQualityToReturn = NetworkQuality.BAD
        idleOneTick()

        assertEquals(NetworkQuality.BAD, controller.get().networkQuality.value)
        assertTrue(
            "expected a Timber.e log on transitioning into BAD",
            recordingTree.errors.any { it.contains("network quality dropped to BAD") },
        )

        controller.destroy()
    }

    @Test
    fun `does not re-log every tick while network quality stays BAD`() {
        val fakeClient = FakeVoipSdkClient().apply { networkQualityToReturn = NetworkQuality.BAD }
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        controller.get().fakeClient = fakeClient

        controller.create().startCommand(0, 0)
        fakeClient.emit(VoipCallState.Ongoing)
        idle() // first reading: null -> BAD, logs once

        val logCountAfterFirstReading = recordingTree.errors.count { it.contains("network quality dropped to BAD") }
        assertEquals(1, logCountAfterFirstReading)

        idleOneTick()
        idleOneTick()

        assertEquals(
            "expected no additional log while quality stays BAD across further ticks",
            logCountAfterFirstReading,
            recordingTree.errors.count { it.contains("network quality dropped to BAD") },
        )

        controller.destroy()
    }
}
