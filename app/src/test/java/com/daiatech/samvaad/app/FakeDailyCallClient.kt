package com.daiatech.samvaad.app

import android.content.Context
import co.daily.CallClientListener

/**
 * Never references the real `co.daily.CallClient` -- that's the entire point (see
 * [DailyCallClient]'s doc). Mirrors the fake-not-mock style [FakeVoipSdkClient] already uses in
 * `samvaad-android`'s own leak tests.
 */
class FakeDailyCallClient : DailyCallClient {

    var addedListener: CallClientListener? = null
        private set
    var removedListener: CallClientListener? = null
        private set
    var releaseCallCount = 0
        private set

    var removeListenerShouldThrow = false
    var releaseShouldThrow = false

    override fun addListener(listener: CallClientListener) {
        addedListener = listener
    }

    override fun removeListener(listener: CallClientListener) {
        removedListener = listener
        if (removeListenerShouldThrow) throw RuntimeException("boom: removeListener")
    }

    override fun release() {
        releaseCallCount++
        if (releaseShouldThrow) throw RuntimeException("boom: release")
    }

    override fun join(url: String, onResult: (errorMessage: String?) -> Unit) = Unit
    override fun leave(onResult: (errorMessage: String?) -> Unit) = Unit
    override fun setMicrophoneEnabled(enabled: Boolean) = Unit
    override fun isMicrophoneEnabled(): Boolean = false
    override fun remoteParticipantCount(): Int = 0
    override fun startRecording(onResult: (errorMessage: String?) -> Unit) = Unit
    override fun stopRecording() = Unit
}

class FakeDailyCallClientFactory(private val client: DailyCallClient) : DailyCallClientFactory {
    override fun create(context: Context): DailyCallClient = client
}
