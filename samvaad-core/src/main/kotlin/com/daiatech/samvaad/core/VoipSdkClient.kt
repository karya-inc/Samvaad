package com.daiatech.samvaad.core

import kotlinx.coroutines.flow.StateFlow

/**
 * The only provider-specific layer. One implementation per SDK (Daily.co, Twilio, Agora, ...).
 *
 * Deliberately knows nothing about Android's Service/Binder/notification plumbing -- that's a
 * different module's job -- and does not track call duration, which is centralized elsewhere so
 * every provider gets it correct for free instead of risking a subtly different bug per adapter.
 *
 * `config` in [join] is intentionally opaque here -- a Daily.co implementation might interpret it
 * as a room URL, a token-based SDK might expect something else entirely. This interface doesn't
 * get to assume the shape.
 */
interface VoipSdkClient {
    val callState: StateFlow<VoipCallState>

    fun join(config: String, role: CallRole)
    fun leave()
    fun toggleMic(enable: Boolean)
    fun isMicrophoneEnabled(): Boolean

    /**
     * Start or stop cloud recording, where the underlying SDK supports it. Not every provider
     * necessarily does -- a no-op implementation is a valid choice for one that doesn't, same as
     * how a provider without a real mute API could still satisfy [toggleMic] as a no-op.
     */
    fun setRecordingEnabled(enabled: Boolean)

    /** Release SDK resources. Called exactly once, when the owning component is torn down. */
    fun release()
}

/** Resolves a [CallProvider] to the SDK client that implements it. */
interface VoipSdkClientFactory {
    fun create(provider: CallProvider): VoipSdkClient
}
