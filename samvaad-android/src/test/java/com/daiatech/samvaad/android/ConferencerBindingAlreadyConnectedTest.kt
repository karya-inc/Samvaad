package com.daiatech.samvaad.android

import android.app.Application
import android.content.ComponentName
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.VoipCallState
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows
import org.robolectric.shadows.ShadowLooper

/**
 * Covers the matrix's ALREADY_CONNECTED race (callee joins before the caller's cancel commits),
 * generalized to this local architecture with no backend involved: [ConferencerBinding.declineOrEnd]
 * optimistically moves to Idle in anticipation of `leave()` winning, but the provider can still
 * report a genuinely active state afterward if the call connected first.
 */
@RunWith(RobolectricTestRunner::class)
class ConferencerBindingAlreadyConnectedTest {

    private data class TestMeta(val id: String)

    private val context = ApplicationProvider.getApplicationContext<Application>()
    private val shadowApp = Shadows.shadowOf(context)

    private fun idle() = ShadowLooper.idleMainLooper()

    private fun bindService(fakeClient: FakeVoipSdkClient): ConferencerBinding<TestMeta> {
        val startIntent = Intent(context, TestVoipCallService::class.java).apply {
            putExtra(AbstractVoipCallService.EXTRA_PROVIDER_ID, "dailyco")
            putExtra(AbstractVoipCallService.EXTRA_CONFIG, "call-1")
            putExtra(AbstractVoipCallService.EXTRA_ROLE, CallRole.CALLER.name)
        }
        Robolectric.buildService(TestVoipCallService::class.java, startIntent).also {
            it.get().fakeClient = fakeClient
            it.create()
            it.startCommand(0, 0)

            val componentName = ComponentName(context, TestVoipCallService::class.java)
            val binder = it.get().onBind(Intent(context, TestVoipCallService::class.java))
            shadowApp.setComponentNameAndServiceForBindService(componentName, binder)
            shadowApp.setBindServiceCallsOnServiceConnectedDirectly(true)
        }

        return ConferencerBinding(
            context = context,
            role = CallRole.CALLER,
            serviceClass = TestVoipCallService::class.java,
            callId = { it.id },
            joinConfig = { it.id },
            provider = { CallProvider.DAILYCO },
        )
    }

    @Test
    fun `reconciles into the real call when it connects right after declineOrEnd`() {
        val fakeClient = FakeVoipSdkClient()
        val binding = bindService(fakeClient)

        binding.acceptOrInitiate(TestMeta("call-1"))
        idle()
        fakeClient.emit(VoipCallState.Connecting)
        idle()

        // User taps Cancel while still Connecting -- UI optimistically goes to Idle.
        binding.declineOrEnd()
        idle()
        check(binding.uiState.value is ConferencerUiState.Idle<TestMeta>)
        assertEquals(1, fakeClient.leaveCallCount)

        // But the remote participant had already joined -- leave() lost the race.
        fakeClient.emit(VoipCallState.Ongoing)
        idle()

        val state = binding.uiState.value
        check(state is ConferencerUiState.Ongoing<TestMeta>)
        assertEquals(TestMeta("call-1"), state.meta)
    }

    @Test
    fun `a genuine Ended after declineOrEnd still settles at Idle, not stuck reconciling`() {
        val fakeClient = FakeVoipSdkClient()
        val binding = bindService(fakeClient)

        binding.acceptOrInitiate(TestMeta("call-1"))
        idle()
        fakeClient.emit(VoipCallState.Ongoing)
        idle()

        binding.declineOrEnd()
        idle()
        fakeClient.emit(VoipCallState.Ended)
        idle()

        check(binding.uiState.value is ConferencerUiState.Idle<TestMeta>)
    }
}
