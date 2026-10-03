package com.daiatech.samvaad.app

import android.content.Context
import co.daily.CallClient
import co.daily.CallClientListener
import timber.log.Timber

/**
 * The only seam between [DailyCoVoipSdkClient] and the real [CallClient]. Exists purely so a
 * plain JVM unit test can substitute a fake that never references [CallClient] at all --
 * [CallClient]'s own static initializer sets up a real EGL/WebRTC display
 * (`co.daily.util.EglUtils` -> `android.opengl.EGL14.eglGetDisplay`), which throws under any JVM
 * unit test (Mockito included -- even mocking it forces that static init to run, confirmed
 * empirically, not just reasoned about). Narrowed to exactly the [CallClient] surface this app
 * actually uses, not a general-purpose Daily wrapper.
 */
interface DailyCallClient {
    fun addListener(listener: CallClientListener)
    fun removeListener(listener: CallClientListener)
    fun release()
    fun join(url: String, onResult: (errorMessage: String?) -> Unit)
    fun leave(onResult: (errorMessage: String?) -> Unit)
    fun setMicrophoneEnabled(enabled: Boolean)
    fun isMicrophoneEnabled(): Boolean
    fun remoteParticipantCount(): Int
    fun startRecording(onResult: (errorMessage: String?) -> Unit)
    fun stopRecording()
}

fun interface DailyCallClientFactory {
    fun create(context: Context): DailyCallClient
}

class DailyCallClientFactoryImpl @javax.inject.Inject constructor() : DailyCallClientFactory {
    override fun create(context: Context): DailyCallClient = DailyCallClientImpl(context)
}

/** Thin adapter over the real [CallClient] -- does no state tracking of its own. */
private class DailyCallClientImpl(context: Context) : DailyCallClient {

    private val callClient = CallClient(context.applicationContext)

    override fun addListener(listener: CallClientListener) {
        callClient.addListener(listener)
    }

    override fun removeListener(listener: CallClientListener) {
        callClient.removeListener(listener)
    }

    override fun release() {
        callClient.release()
    }

    override fun join(url: String, onResult: (errorMessage: String?) -> Unit) {
        callClient.join(url = url) { result -> onResult(result.error?.msg) }
    }

    override fun leave(onResult: (errorMessage: String?) -> Unit) {
        callClient.leave { result -> onResult(result.error?.msg) }
    }

    override fun setMicrophoneEnabled(enabled: Boolean) {
        callClient.setInputsEnabled(microphone = enabled)
    }

    override fun isMicrophoneEnabled(): Boolean = callClient.inputs().microphone.isEnabled

    override fun remoteParticipantCount(): Int =
        callClient.participants().all.count { !it.value.info.isLocal }

    override fun startRecording(onResult: (errorMessage: String?) -> Unit) {
        callClient.startRecording { result ->
            result.success?.let { Timber.i("DailyCallClientImpl: recording started, stream id=$it") }
            onResult(result.error?.msg)
        }
    }

    override fun stopRecording() {
        callClient.stopRecording()
    }
}
