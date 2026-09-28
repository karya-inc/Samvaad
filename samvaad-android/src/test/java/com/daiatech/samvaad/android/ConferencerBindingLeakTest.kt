package com.daiatech.samvaad.android

import android.app.Application
import android.content.ComponentName
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.VoipCallState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows
import org.robolectric.android.controller.ServiceController
import org.robolectric.shadows.ShadowLooper

/**
 * Real bind/unbind-lifecycle tests via Robolectric -- these are the scenarios that couldn't be
 * exercised in a plain Mockito-based test (real Intent construction throws under the plain
 * unit-test android.jar stub). Covers exactly the two things this whole design exists to get
 * right: never double-register a bind for one eventual unbind, and always actually unbind once a
 * call reaches a terminal state (ARCHITECTURE.md §7.1) rather than leaving the Service alive,
 * bound, in a stopped-but-not-destroyed limbo.
 */
@RunWith(RobolectricTestRunner::class)
class ConferencerBindingLeakTest {

    private data class TestMeta(val id: String)

    private val context = ApplicationProvider.getApplicationContext<Application>()
    private val shadowApp = Shadows.shadowOf(context)

    private fun newBinding(role: CallRole = CallRole.CALLER) = ConferencerBinding<TestMeta>(
        context = context,
        role = role,
        serviceClass = TestVoipCallService::class.java,
        callId = { it.id },
        joinConfig = { it.id },
        provider = { CallProvider.DAILYCO },
    )

    private fun idle() = ShadowLooper.idleMainLooper()

    /**
     * Builds a real [TestVoipCallService] instance and drives it through onCreate +
     * onStartCommand (with a fake client so [AbstractVoipCallService]'s own internal relay -- the
     * thing that actually populates its public `callState` -- starts collecting), then wires
     * Robolectric's bind-service shadow to connect to this exact instance.
     */
    private fun buildAndStartService(fakeClient: FakeVoipSdkClient): ServiceController<TestVoipCallService> {
        val startIntent = Intent(context, TestVoipCallService::class.java).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, "dailyco")
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, "call-1")
            putExtra(AbstractVoipCallService.EXTRA_ROLE, CallRole.CALLER.name)
        }
        return Robolectric.buildService(TestVoipCallService::class.java, startIntent).also { controller ->
            val service = controller.get()
            service.fakeClient = fakeClient
            controller.create()
            controller.startCommand(0, 0)

            val componentName = ComponentName(context, TestVoipCallService::class.java)
            val binder = service.onBind(Intent(context, TestVoipCallService::class.java))
            shadowApp.setComponentNameAndServiceForBindService(componentName, binder)
            shadowApp.setBindServiceCallsOnServiceConnectedDirectly(true)
        }
    }

    @Test
    fun `onIncomingCall then acceptOrInitiate before the connection lands results in exactly one bind`() {
        val binding = newBinding(role = CallRole.CALLEE)

        // Neither call's bindIfNeeded() sees the OTHER's bind as connected yet -- this is exactly
        // the race the eager `bound = true` fix (in bindIfNeeded()) exists to prevent.
        binding.onIncomingCall(TestMeta("call-1"))
        binding.acceptOrInitiate(TestMeta("call-1"))

        assertEquals(1, shadowApp.boundServiceConnections.size)
    }

    @Test
    fun `destroy without ever binding does not attempt to unbind`() {
        val binding = newBinding()

        binding.destroy()

        assertEquals(0, shadowApp.unboundServiceConnections.size)
    }

    @Test
    fun `reaching a terminal state unbinds automatically, without an explicit destroy call`() {
        val fakeClient = FakeVoipSdkClient()
        val serviceController = buildAndStartService(fakeClient)

        val binding = newBinding()
        binding.acceptOrInitiate(TestMeta("call-1"))
        idle()

        assertEquals(
            "expected the bind to connect synchronously (setBindServiceCallsOnServiceConnectedDirectly)",
            1,
            shadowApp.boundServiceConnections.size,
        )

        fakeClient.emit(VoipCallState.Ongoing)
        idle()
        assertTrue(binding.uiState.value is ConferencerUiState.Ongoing<*>)
        assertEquals(0, shadowApp.unboundServiceConnections.size) // still bound, call is live

        fakeClient.emit(VoipCallState.Ended)
        idle()

        // The core leak-prevention fix (ARCHITECTURE.md §7.1): reaching a terminal state must
        // unbind on its own -- a service that's both started and bound never actually stops
        // otherwise, regardless of stopSelf()/stopService() calls.
        assertEquals(1, shadowApp.unboundServiceConnections.size)
        assertTrue(binding.uiState.value is ConferencerUiState.Idle<*>)

        serviceController.destroy()
    }

    @Test
    fun `onServiceDisconnected also releases the binding registration`() {
        val fakeClient = FakeVoipSdkClient()
        val serviceController = buildAndStartService(fakeClient)
        val componentName = ComponentName(context, TestVoipCallService::class.java)

        val binding = newBinding()
        binding.acceptOrInitiate(TestMeta("call-1"))
        idle()
        assertEquals(1, shadowApp.boundServiceConnections.size)

        // Simulate the bound service's process dying unexpectedly -- Robolectric has no API to
        // simulate a real process crash, so fire the callback directly on the actual
        // ServiceConnection ConferencerBinding registered (fetched from the shadow's own
        // bookkeeping, not via reflection into a private field).
        val connection = shadowApp.boundServiceConnections.single()
        connection.onServiceDisconnected(componentName)
        idle()

        // This is the bug this fix (added during code review) exists for: onServiceDisconnected
        // used to flip UI state to Idle without ever calling unbindService() -- one bindService()
        // call left permanently unmatched, and a later bindIfNeeded() would wrongly think it was
        // still bound and skip rebinding entirely.
        assertEquals(1, shadowApp.unboundServiceConnections.size)
        assertTrue(binding.uiState.value is ConferencerUiState.Idle<*>)

        serviceController.destroy()
    }
}
