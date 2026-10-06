package com.daiatech.samvaad.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SamvaadErrorTest {

    @Test
    fun `ClientCreationFailed exposes the provider as a real field, not just in the message`() {
        val cause = RuntimeException("factory exploded")
        val error = SamvaadError.ClientCreationFailed(CallProvider.DAILYCO, cause)

        assertEquals(CallProvider.DAILYCO, error.provider)
        assertEquals(cause, error.cause)
        assertEquals("CLIENT_CREATION_FAILED", error.errorCode)
        assertTrue(error.message!!.contains("dailyco"))
        assertTrue(error.message!!.contains("factory exploded"))
    }

    @Test
    fun `ProviderError exposes the provider as a real field and falls back to a default message`() {
        val withMessage = SamvaadError.ProviderError(CallProvider.DAILYCO, "room is full")
        assertEquals(CallProvider.DAILYCO, withMessage.provider)
        assertEquals("room is full", withMessage.message)
        assertEquals("PROVIDER_ERROR", withMessage.errorCode)

        val withoutMessage = SamvaadError.ProviderError(CallProvider.TWILIO, message = null)
        assertTrue(withoutMessage.message!!.contains("twilio"))
    }

    @Test
    fun `toString includes the obfuscation-safe errorCode, not just the class name`() {
        val error = SamvaadError.ProviderError(CallProvider.DAILYCO, "boom")

        // errorCode, not this::class.simpleName, is the part a consumer should rely on
        // surviving a minified release build -- toString() surfaces both, but errorCode is
        // the contract.
        assertTrue(error.toString().contains("PROVIDER_ERROR"))
    }

    @Test
    fun `a consumer can define its own SamvaadError subclass from outside this module`() {
        // Exercises the whole point of SamvaadError being open, not sealed: a third party's own
        // failure type, received as a plain Throwable (e.g. VoipCallState.Error.cause), is still
        // catchable as a SamvaadError.
        val caught: Throwable = BackendCallError.CallerBusy

        assertTrue(caught is SamvaadError)
        val error = caught as SamvaadError
        assertEquals("CALLER_BUSY", error.errorCode)
        assertEquals("You're already on another call", error.message)
    }

    /** A consumer's own closed family of outcomes, exhaustive within their own module. */
    private sealed class BackendCallError(message: String) : SamvaadError(message) {
        object CallerBusy : BackendCallError("You're already on another call") {
            override val errorCode = "CALLER_BUSY"
        }
    }
}
