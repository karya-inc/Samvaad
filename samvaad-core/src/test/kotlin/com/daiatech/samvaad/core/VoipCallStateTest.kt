package com.daiatech.samvaad.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

class VoipCallStateTest {

    @Test
    fun `Error carries the cause and message it was constructed with`() {
        val cause = IllegalStateException("sdk exploded")

        val error = VoipCallState.Error(cause = cause, message = "join failed")

        assertSame(cause, error.cause)
        assertEquals("join failed", error.message)
    }

    @Test
    fun `Error defaults cause and message to null when not supplied`() {
        val error = VoipCallState.Error()

        assertNull(error.cause)
        assertNull(error.message)
    }

    @Test
    fun `Error instances with equal cause and message compare equal`() {
        val cause = IllegalStateException("boom")

        val a = VoipCallState.Error(cause = cause, message = "failed")
        val b = VoipCallState.Error(cause = cause, message = "failed")

        assertEquals(a, b)
    }

    @Test
    fun `Error instances with different messages are not equal`() {
        val a = VoipCallState.Error(message = "failed")
        val b = VoipCallState.Error(message = "different reason")

        assertNotEquals(a, b)
    }

    @Test
    fun `the singleton lifecycle states are stable references`() {
        // Idle, Dialing, etc. are declared as objects: every reference to VoipCallState.Ongoing
        // must be the exact same instance, which is what a StateFlow consumer relies on to do
        // cheap reference comparisons if it wants to.
        val state: VoipCallState = VoipCallState.Ongoing

        assertSame(VoipCallState.Ongoing, state)
        assertNotEquals(VoipCallState.Ongoing, VoipCallState.Ended)
    }
}
