package com.daiatech.samvaad.android

import android.app.Application
import android.content.ComponentName
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.NetworkQuality
import com.daiatech.samvaad.core.VoipCallState
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows
import org.robolectric.shadows.ShadowLooper

/**
 * Covers wayfinder ticket "Surface through ConferencerBinding" (network-health map):
 * ConferencerUiState.Ongoing.networkQuality mirrors durationSeconds's existing pattern exactly --
 * a direct reference to the Service's own StateFlow, no separate relay machinery.
 */
@RunWith(RobolectricTestRunner::class)
class ConferencerBindingNetworkQualityTest {

    private data class TestMeta(val id: String)

    private val context = ApplicationProvider.getApplicationContext<Application>()
    private val shadowApp = Shadows.shadowOf(context)

    private fun idle() = ShadowLooper.idleMainLooper()

    @Test
    fun `Ongoing carries the Service's live networkQuality StateFlow`() {
        val fakeClient = FakeVoipSdkClient().apply { networkQualityToReturn = NetworkQuality.POOR }

        val startIntent = Intent(context, TestVoipCallService::class.java).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, "dailyco")
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, "call-1")
            putExtra(AbstractVoipCallService.EXTRA_ROLE, CallRole.CALLER.name)
        }
        val serviceController = Robolectric.buildService(TestVoipCallService::class.java, startIntent).also {
            it.get().fakeClient = fakeClient
            it.create()
            it.startCommand(0, 0)

            val componentName = ComponentName(context, TestVoipCallService::class.java)
            val binder = it.get().onBind(Intent(context, TestVoipCallService::class.java))
            shadowApp.setComponentNameAndServiceForBindService(componentName, binder)
            shadowApp.setBindServiceCallsOnServiceConnectedDirectly(true)
        }

        val binding = ConferencerBinding<TestMeta>(
            context = context,
            role = CallRole.CALLER,
            serviceClass = TestVoipCallService::class.java,
            callId = { it.id },
            joinConfig = { it.id },
            provider = { CallProvider.DAILYCO },
        )
        binding.acceptOrInitiate(TestMeta("call-1"))
        idle()

        fakeClient.emit(VoipCallState.Ongoing)
        idle()

        val state = binding.uiState.value
        check(state is ConferencerUiState.Ongoing<TestMeta>)
        assertEquals(NetworkQuality.POOR, state.networkQuality.value)

        serviceController.destroy()
    }
}
