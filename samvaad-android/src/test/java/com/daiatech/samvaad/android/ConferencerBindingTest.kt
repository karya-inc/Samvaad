package com.daiatech.samvaad.android

import android.content.Context
import com.daiatech.samvaad.core.CallRole
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test
import org.mockito.kotlin.any
import org.mockito.kotlin.mock
import org.mockito.kotlin.never
import org.mockito.kotlin.verify
import org.mockito.kotlin.whenever

/**
 * Covers only what's genuinely reachable in a plain JVM unit test, which turns out to be
 * narrower than it first looks. This project has no Robolectric/instrumentation setup, and two
 * separate things need it, not just one:
 *
 * - The state-relay-driven guards (transient-Idle rollback, the unbind-on-terminal-state fix,
 *   onServiceDisconnected -> Idle) all live behind the private [android.content.ServiceConnection],
 *   which only fires from a real bound [android.app.Service] -- no public seam reaches it.
 * - Less obviously: [ConferencerBinding.acceptOrInitiate] and [ConferencerBinding.attachIfRunning]
 *   (when it actually binds) construct a real [android.content.Intent] -- and unlike a mocked
 *   [Context], a real `Intent`'s methods throw `RuntimeException("... not mocked")` under the
 *   plain unit-test android.jar stub, same as any other unmocked Android framework class. Mocking
 *   `Context` alone doesn't route around that. This rules out testing the accepted-call
 *   redelivery-dedup guard too, since exercising it requires calling `acceptOrInitiate` first.
 *
 * What's left, and what's actually covered here: the CALLEE-only guard on [onIncomingCall], the
 * plain Idle/Incoming state transitions that don't touch the Service/Intent at all, and
 * [ConferencerBinding.attachIfRunning]'s early-return path (verified to never reach `bindService`
 * when no service is running, so it never reaches the `Intent` construction that would throw).
 */
class ConferencerBindingTest {

    private data class TestMetadata(val id: String, val tag: String)

    private fun newContext(): Context {
        val context = mock<Context>()
        whenever(context.applicationContext).thenReturn(context)
        return context
    }

    private fun newBinding(role: CallRole): ConferencerBinding<TestMetadata> = ConferencerBinding(
        context = newContext(),
        role = role,
        serviceClass = AbstractVoipCallService::class.java,
        callId = { it.id },
        joinConfig = { "config" },
        provider = { com.daiatech.samvaad.core.CallProvider.DAILYCO },
    )

    @Test
    fun `onIncomingCall is callee-only`() {
        val binding = newBinding(role = CallRole.CALLER)

        assertThrows(IllegalArgumentException::class.java) {
            binding.onIncomingCall(TestMetadata(id = "call-1", tag = "irrelevant"))
        }
    }

    @Test
    fun `onIncomingCall sets Incoming state for a genuinely new call`() {
        val binding = newBinding(role = CallRole.CALLEE)

        binding.onIncomingCall(TestMetadata(id = "call-1", tag = "first"))

        val state = binding.uiState.value
        check(state is ConferencerUiState.Incoming<TestMetadata>)
        assertEquals("first", state.meta.tag)
    }

    @Test
    fun `attachIfRunning returns false and does not bind when no service is running`() {
        val context = newContext()
        val binding = ConferencerBinding<TestMetadata>(
            context = context,
            role = CallRole.CALLEE,
            serviceClass = AbstractVoipCallService::class.java,
            callId = { it.id },
            joinConfig = { "config" },
            provider = { com.daiatech.samvaad.core.CallProvider.DAILYCO },
        )

        assertFalse(binding.attachIfRunning())
        verify(context, never()).bindService(any<android.content.Intent>(), any(), any<Int>())
    }

    @Test
    fun `declineOrEnd resets state to Idle`() {
        val binding = newBinding(role = CallRole.CALLEE)
        binding.onIncomingCall(TestMetadata(id = "call-1", tag = "first"))

        binding.declineOrEnd()

        assertEquals(ConferencerUiState.Idle::class.java, binding.uiState.value.javaClass)
    }
}
