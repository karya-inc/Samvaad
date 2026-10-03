package com.daiatech.samvaad.app

import co.daily.model.Threshold
import com.daiatech.samvaad.core.NetworkQuality
import org.junit.Assert.assertEquals
import org.junit.Test

class DailyNetworkQualityMappingTest {

    @Test
    fun `Good maps to GOOD`() {
        assertEquals(NetworkQuality.GOOD, mapDailyThreshold(Threshold.Good))
    }

    @Test
    fun `Low maps to POOR`() {
        assertEquals(NetworkQuality.POOR, mapDailyThreshold(Threshold.Low))
    }

    @Test
    fun `VeryLow maps to BAD`() {
        assertEquals(NetworkQuality.BAD, mapDailyThreshold(Threshold.VeryLow))
    }
}
