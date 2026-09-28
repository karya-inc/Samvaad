package com.daiatech.samvaad.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CallProviderTest {

    @Test
    fun `two instances with the same id are equal`() {
        val a = CallProvider("dailyco")
        val b = CallProvider("dailyco")

        assertEquals(a, b)
        assertEquals(a.hashCode(), b.hashCode())
    }

    @Test
    fun `instances with different ids are not equal`() {
        assertNotEquals(CallProvider("dailyco"), CallProvider("twilio"))
    }

    @Test
    fun `well-known constants expose the expected ids`() {
        assertEquals("dailyco", CallProvider.DAILYCO.id)
        assertEquals("twilio", CallProvider.TWILIO.id)
        assertEquals("agora", CallProvider.AGORA.id)
    }

    @Test
    fun `a well-known constant equals a freshly constructed provider with the same id`() {
        // This is the point of an open value class over a closed enum: a third party can
        // construct their own CallProvider and have it compare equal / interoperate with the
        // built-in constants purely by id, with no shared enum entry and no forking required.
        assertEquals(CallProvider.DAILYCO, CallProvider("dailyco"))
    }

    @Test
    fun `third parties can construct their own provider ids without modifying this class`() {
        val custom = CallProvider("acme-voip")

        assertEquals("acme-voip", custom.id)
        assertNotEquals(CallProvider.DAILYCO, custom)
        assertTrue(custom == CallProvider("acme-voip"))
        assertFalse(custom == CallProvider("dailyco"))
    }
}
