package com.daiatech.samvaad.android

import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class RobolectricSmokeTest {
    @Test
    fun `robolectric provides a real application context`() {
        val context = ApplicationProvider.getApplicationContext<android.app.Application>()
        assertNotNull(context)
    }
}
