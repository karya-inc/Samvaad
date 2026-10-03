package com.daiatech.samvaad.app

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.isActive
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever

/**
 * [DailyCoVoipSdkClient.release] is the one coroutine-scope-cancellation path in this app that
 * depends on a *different* class's lifecycle method actually calling it --
 * AbstractVoipCallService.onDestroy() -> sdkClient?.release() -- and, unlike ConferencerBinding
 * and AbstractVoipCallService, it previously had zero test coverage (ARCHITECTURE.md gap: the
 * `app` module had no test source set at all). Made reachable here via [FakeDailyCallClient],
 * which never touches the real `co.daily.CallClient` -- that class's own static initializer sets
 * up a real EGL/WebRTC display and throws under any JVM unit test, mocked or not (confirmed
 * empirically before this seam existed).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class DailyCoVoipSdkClientTest {

    @Before
    fun setUp() {
        Dispatchers.setMain(StandardTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newContext(): Context {
        val context = mock<Context>()
        whenever(context.applicationContext).thenReturn(context)
        return context
    }

    /** No public seam exposes scope liveness -- this is the one thing worth reflecting into. */
    private fun scopeOf(client: DailyCoVoipSdkClient): CoroutineScope {
        val field = DailyCoVoipSdkClient::class.java.getDeclaredField("scope")
        field.isAccessible = true
        return field.get(client) as CoroutineScope
    }

    @Test
    fun `release removes the exact same listener instance that init registered`() {
        val fakeDaily = FakeDailyCallClient()
        val client = DailyCoVoipSdkClient(newContext(), FakeDailyCallClientFactory(fakeDaily))

        client.release()

        assertSame(
            "a copy-pasted second listener object here would un-register the wrong instance, " +
                "leaking the real one forever",
            fakeDaily.addedListener,
            fakeDaily.removedListener,
        )
        assertTrue(fakeDaily.addedListener != null)
        assertTrue(fakeDaily.releaseCallCount == 1)
    }

    @Test
    fun `release cancels the scope`() {
        val client = DailyCoVoipSdkClient(newContext(), FakeDailyCallClientFactory(FakeDailyCallClient()))

        assertTrue(scopeOf(client).isActive)
        client.release()

        assertFalse(scopeOf(client).isActive)
    }

    @Test
    fun `release still releases the CallClient and cancels the scope when removeListener throws`() {
        val fakeDaily = FakeDailyCallClient().apply { removeListenerShouldThrow = true }
        val client = DailyCoVoipSdkClient(newContext(), FakeDailyCallClientFactory(fakeDaily))

        // Must not propagate -- release() wraps removeListener/release in their own try/catch
        // specifically so one failing doesn't skip the other or leave the scope uncancelled.
        client.release()

        assertTrue(fakeDaily.releaseCallCount == 1)
        assertFalse(scopeOf(client).isActive)
    }

    @Test
    fun `release still cancels the scope when callClient-release itself throws`() {
        val fakeDaily = FakeDailyCallClient().apply { releaseShouldThrow = true }
        val client = DailyCoVoipSdkClient(newContext(), FakeDailyCallClientFactory(fakeDaily))

        client.release()

        assertFalse(scopeOf(client).isActive)
    }
}
