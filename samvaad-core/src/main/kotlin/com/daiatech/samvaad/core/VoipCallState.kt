package com.daiatech.samvaad.core

/**
 * The local SDK/UI lifecycle of a call, as observed on this device. Provider-agnostic: any
 * [VoipSdkClient] implementation (Daily.co, Twilio, Agora, ...) reports its state through this
 * same shape.
 *
 * Deliberately does NOT model a backend's authoritative call status (e.g. a server-side FSM
 * with states like "ringing"/"busy"/"completed"). That's a different axis -- a networked
 * concern with its own guard rules and races -- and reconciling it against this local lifecycle
 * is left entirely to the consumer. This library only describes what any VoIP SDK integration
 * looks like from the local device's point of view.
 */
sealed interface VoipCallState {
    object Idle : VoipCallState
    object Dialing : VoipCallState
    object Incoming : VoipCallState
    object Connecting : VoipCallState
    object Ongoing : VoipCallState
    object Disconnecting : VoipCallState
    object Ended : VoipCallState
    data class Error(val cause: Throwable? = null, val message: String? = null) : VoipCallState
}
