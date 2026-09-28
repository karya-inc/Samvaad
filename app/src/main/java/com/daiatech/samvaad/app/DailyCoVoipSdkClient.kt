package com.daiatech.samvaad.app

import android.content.Context
import co.daily.CallClient
import co.daily.CallClientListener
import co.daily.model.CallState
import co.daily.model.Participant
import co.daily.model.ParticipantLeftReason
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.VoipCallState
import com.daiatech.samvaad.core.VoipSdkClient
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import timber.log.Timber

/**
 * The only Daily.co-specific piece in this sample -- everything else (the Service, the Binding,
 * the UI) talks only to [VoipSdkClient]'s generic surface. Adapted from the real, shipped
 * integration in karya-android-client's `feature/conferencer/.../dailyco/DailyCoManager.kt`,
 * trimmed down for a single-role (caller-only) sample: no leave-timeout guard, no debug-signal
 * telemetry, no automatic record-on-connect (recording here is the explicit, user-toggled
 * feature this sample is demonstrating, via [setRecordingEnabled]).
 */
class DailyCoVoipSdkClient(context: Context) : VoipSdkClient {

    private val appContext = context.applicationContext
    private val callClient = CallClient(appContext)

    private val _callState = MutableStateFlow<VoipCallState>(VoipCallState.Idle)
    override val callState: StateFlow<VoipCallState> = _callState.asStateFlow()

    private val listener = object : CallClientListener {
        override fun onCallStateUpdated(state: CallState) {
            Timber.d("DailyCoVoipSdkClient: onCallStateUpdated: $state")
            when (state) {
                CallState.joined -> {
                    // Daily doesn't fire onParticipantJoined for a remote already in the room
                    // when we join -- without this check we'd sit on Connecting forever in that
                    // case (matches the same quirk karya-android-client's DailyCoManager guards
                    // against).
                    val remoteAlreadyPresent = try {
                        callClient.participants().all.any { !it.value.info.isLocal }
                    } catch (e: Exception) {
                        Timber.e(e, "DailyCoVoipSdkClient: failed to read participants on joined")
                        false
                    }
                    _callState.value = if (remoteAlreadyPresent) {
                        VoipCallState.Ongoing
                    } else {
                        VoipCallState.Connecting
                    }
                }

                CallState.leaving -> _callState.value = VoipCallState.Disconnecting
                CallState.left -> _callState.value = VoipCallState.Ended
                CallState.initialized, CallState.joining -> Unit // join() already reported Dialing
                else -> Timber.w("DailyCoVoipSdkClient: unhandled CallState: $state")
            }
        }

        override fun onParticipantJoined(participant: Participant) {
            if (participant.info.isLocal) return
            Timber.d("DailyCoVoipSdkClient: remote participant joined")
            _callState.value = VoipCallState.Ongoing
        }

        override fun onParticipantLeft(participant: Participant, reason: ParticipantLeftReason) {
            if (participant.info.isLocal) return
            val remoteRemaining = try {
                callClient.participants().all.count { !it.value.info.isLocal }
            } catch (e: Exception) {
                Timber.e(e, "DailyCoVoipSdkClient: failed to read participants on left")
                0
            }
            if (remoteRemaining == 0) {
                Timber.d("DailyCoVoipSdkClient: last remote participant left -- disconnecting")
                _callState.value = VoipCallState.Disconnecting
                leave()
            }
        }

        override fun onError(message: String) {
            Timber.e("DailyCoVoipSdkClient: Daily SDK error: $message")
            _callState.value = VoipCallState.Error(message = message)
        }
    }

    init {
        callClient.addListener(listener)
    }

    override fun join(config: String, role: CallRole) {
        Timber.d("DailyCoVoipSdkClient: joining $config")
        _callState.value = VoipCallState.Dialing
        try {
            callClient.join(url = config) { result ->
                result.error?.let { err ->
                    Timber.e("DailyCoVoipSdkClient: error joining call: ${err.msg}")
                    _callState.value = VoipCallState.Error(message = err.msg)
                    return@join
                }
                result.success?.let {
                    try {
                        callClient.setInputsEnabled(microphone = true)
                    } catch (e: Exception) {
                        Timber.e(e, "DailyCoVoipSdkClient: failed to enable mic after join")
                    }
                }
            }
        } catch (e: Exception) {
            Timber.e(e, "DailyCoVoipSdkClient: exception thrown calling join()")
            _callState.value = VoipCallState.Error(cause = e, message = e.message)
        }
    }

    override fun leave() {
        try {
            callClient.leave { result ->
                result.error?.let { err ->
                    Timber.e("DailyCoVoipSdkClient: error leaving call: ${err.msg}")
                    _callState.value = VoipCallState.Error(message = err.msg)
                }
            }
        } catch (e: Exception) {
            Timber.e(e, "DailyCoVoipSdkClient: exception thrown calling leave()")
            _callState.value = VoipCallState.Error(cause = e, message = e.message)
        }
    }

    override fun toggleMic(enable: Boolean) {
        try {
            callClient.setInputsEnabled(microphone = enable)
        } catch (e: Exception) {
            Timber.e(e, "DailyCoVoipSdkClient: failed to toggle mic enable=$enable")
        }
    }

    override fun isMicrophoneEnabled(): Boolean = try {
        callClient.inputs().microphone.isEnabled
    } catch (e: Exception) {
        Timber.e(e, "DailyCoVoipSdkClient: failed to read microphone state")
        false
    }

    override fun setRecordingEnabled(enabled: Boolean) {
        try {
            if (enabled) {
                callClient.startRecording { result ->
                    result.error?.let { err ->
                        Timber.e("DailyCoVoipSdkClient: failed to start recording: ${err.msg}")
                    }
                    result.success?.let {
                        Timber.i("DailyCoVoipSdkClient: recording started, stream id=$it")
                    }
                }
            } else {
                callClient.stopRecording()
            }
        } catch (e: Exception) {
            Timber.e(e, "DailyCoVoipSdkClient: failed to set recording enabled=$enabled")
        }
    }

    override fun release() {
        try {
            callClient.removeListener(listener)
        } catch (e: Exception) {
            Timber.e(e, "DailyCoVoipSdkClient: failed to remove listener")
        }
        try {
            callClient.release()
        } catch (e: Exception) {
            Timber.e(e, "DailyCoVoipSdkClient: failed to release CallClient")
        }
    }
}
