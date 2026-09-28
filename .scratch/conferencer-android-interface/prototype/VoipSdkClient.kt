package com.karya.conferencer

import kotlinx.coroutines.flow.StateFlow

/**
 * The ONLY provider-specific layer. One implementation per SDK (Daily.co,
 * Twilio, Agora, ...). Deliberately knows nothing about Android's
 * Service/notification/Binder plumbing (that's AbstractVoipCallService's
 * job) and does NOT track call duration (see that file for why -- duration
 * is centralized one layer up so every provider gets it correct for free).
 *
 * `config` in [join] is intentionally opaque here -- Daily.co's
 * implementation interprets it as a room URL; a token-based SDK might expect
 * something else entirely. This interface doesn't get to assume the shape.
 */
interface VoipSdkClient {
    val callState: StateFlow<VoipCallState>

    fun join(config: String, role: CallRole)
    fun leave()
    fun toggleMic(enable: Boolean)
    fun isMicrophoneEnabled(): Boolean

    /** Release SDK resources. Called exactly once, when the owning service is torn down. */
    fun release()
}

/** Resolves a [CallProvider] to the SDK client that implements it. */
interface VoipSdkClientFactory {
    fun create(provider: CallProvider): VoipSdkClient
}
