package com.daiatech.samvaad.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SamvaadAndroidErrorTest {

    @Test
    fun `ServiceBindFailed exposes the service class, not just a generic message`() {
        val error = SamvaadAndroidError.ServiceBindFailed(AbstractVoipCallService::class.java)

        assertEquals(AbstractVoipCallService::class.java, error.serviceClass)
        assertEquals("SERVICE_BIND_FAILED", error.errorCode)
        assertTrue(error.message!!.contains("AbstractVoipCallService"))
    }

    @Test
    fun `AlreadyInCall has a stable errorCode`() {
        assertEquals("ALREADY_IN_CALL", SamvaadAndroidError.AlreadyInCall().errorCode)
    }
}
