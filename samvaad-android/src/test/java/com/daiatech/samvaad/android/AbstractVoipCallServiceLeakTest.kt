package com.daiatech.samvaad.android

import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import timber.log.Timber

/**
 * Real Service-lifecycle tests via Robolectric -- these are the scenarios that were previously
 * unreachable in a plain JVM unit test (constructing a real Service, real Intent, real
 * startForeground/onDestroy). Focused on the two things that actually matter for "does this leak
 * or crash": the isRunning flag's lifecycle, and whether an exception from a buggy SDK adapter is
 * actually contained rather than crashing the host process (ARCHITECTURE.md §7.2).
 */
@RunWith(RobolectricTestRunner::class)
class AbstractVoipCallServiceLeakTest {

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

    private fun startIntent(providerId: String = "dailyco", config: String = "config", role: String = "CALLER") =
        Intent(ApplicationProvider.getApplicationContext(), TestVoipCallService::class.java).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, providerId)
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, config)
            putExtra(AbstractVoipCallService.EXTRA_ROLE, role)
        }

    @Test
    fun `isRunning is true after onCreate and false after onDestroy`() {
        val fakeClient = FakeVoipSdkClient()
        val controller = Robolectric.buildService(TestVoipCallService::class.java)
        controller.get().fakeClient = fakeClient

        controller.create()
        assertTrue(AbstractVoipCallService.isRunning)

        controller.destroy()
        assertFalse(AbstractVoipCallService.isRunning)
    }

    @Test
    fun `a throwing SDK adapter join() is contained, not left to crash the process`() {
        val fakeClient = FakeVoipSdkClient().apply { joinShouldThrow = true }
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        controller.get().fakeClient = fakeClient

        // If the exception weren't contained by AbstractVoipCallService's
        // CoroutineExceptionHandler, this call itself would rethrow and fail the test.
        controller.create().startCommand(0, 0)
        org.robolectric.shadows.ShadowLooper.idleMainLooper()

        assertTrue(
            "expected an error to be logged via Timber for the contained exception",
            recordingTree.errors.any { it.contains("Uncaught exception", ignoreCase = true) },
        )
        // The service itself is still alive -- a contained SDK bug doesn't take the process down.
        assertTrue(AbstractVoipCallService.isRunning)

        controller.destroy()
    }

    @Test
    fun `join is only attempted once per service instance even if onStartCommand fires twice`() {
        val fakeClient = FakeVoipSdkClient()
        val controller = Robolectric.buildService(TestVoipCallService::class.java, startIntent())
        controller.get().fakeClient = fakeClient

        controller.create()
        controller.startCommand(0, 0)
        controller.startCommand(0, 1) // e.g. a redelivered/duplicate start Intent
        org.robolectric.shadows.ShadowLooper.idleMainLooper()

        assertEquals(1, fakeClient.joinCallCount)

        controller.destroy()
    }
}
