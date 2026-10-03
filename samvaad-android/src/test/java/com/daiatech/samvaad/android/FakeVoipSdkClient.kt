package com.daiatech.samvaad.android

import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.NetworkQuality
import com.daiatech.samvaad.core.VoipCallState
import com.daiatech.samvaad.core.VoipSdkClient
import com.daiatech.samvaad.core.VoipSdkClientFactory
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Test double standing in for a real SDK adapter (Daily.co/Twilio/...) -- lets tests drive
 * [VoipCallState] transitions directly and inject failures, without any real Android/SDK
 * dependency. */
class FakeVoipSdkClient : VoipSdkClient {
    private val _callState = MutableStateFlow<VoipCallState>(VoipCallState.Idle)
    override val callState: StateFlow<VoipCallState> = _callState.asStateFlow()

    var joinCallCount = 0
        private set
    var leaveCallCount = 0
        private set
    var releaseCallCount = 0
        private set
    var micEnabled = false
        private set
    var recordingEnabled = false
        private set

    /** When true, [join] throws instead of succeeding -- simulates a buggy SDK adapter. */
    var joinShouldThrow = false

    /** Settable by a test to drive what [networkQuality] returns on the next poll. */
    var networkQualityToReturn: NetworkQuality? = null

    fun emit(state: VoipCallState) {
        _callState.value = state
    }

    override fun join(config: String, role: CallRole) {
        joinCallCount++
        if (joinShouldThrow) throw IllegalStateException("FakeVoipSdkClient: simulated join() failure")
    }

    override fun leave() {
        leaveCallCount++
    }

    override fun toggleMic(enable: Boolean) {
        micEnabled = enable
    }

    override fun isMicrophoneEnabled(): Boolean = micEnabled

    override fun setRecordingEnabled(enabled: Boolean) {
        recordingEnabled = enabled
    }

    override fun isRecordingEnabled(): Boolean = recordingEnabled

    override fun networkQuality(): NetworkQuality? = networkQualityToReturn

    override fun release() {
        releaseCallCount++
    }
}

class FakeVoipSdkClientFactory(private val client: FakeVoipSdkClient) : VoipSdkClientFactory {
    var createCallCount = 0
        private set

    override fun create(provider: CallProvider): VoipSdkClient {
        createCallCount++
        return client
    }
}
