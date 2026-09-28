package com.karya.conferencer

/**
 * PROTOTYPE NOTE: this is essentially unchanged from the real
 * karya-android-client VoipCallState -- it was already provider-agnostic and
 * carries no Karya-specific concepts (no MTA ids, no task/project references).
 * This is exactly the shape an open-source interface wants: it describes what
 * ANY VoIP SDK integration looks like from the local device's point of view,
 * regardless of which backend or provider is driving it.
 *
 * Deliberately NOT modeling the server's authoritative call state here (the
 * RINGING/RECORDING/COMPLETED/... machine from the backend FSM) -- that's a
 * networked concern with its own guard rules and races (see karya-server's
 * VoIP Call FSM map), and conflating the two was exactly the "Android/server
 * state parity" problem flagged during that work. This sealed interface is
 * ONLY the local SDK/UI lifecycle; reconciling it against a remote status is
 * left to the consuming app (see VoipCallManager's KDoc below).
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
