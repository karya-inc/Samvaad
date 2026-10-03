package com.daiatech.samvaad.core

/**
 * A graded, provider-agnostic assessment of the current call's network link. Distinct from
 * [VoipCallState] -- an orthogonal axis, not a lifecycle phase. A call can be [VoipCallState.Ongoing]
 * with [GOOD] or [BAD] network at different moments without its lifecycle state changing at all.
 *
 * [VoipSdkClient.networkQuality] returns `null`, not a fourth value here, when no reading is
 * available -- covering both "no reading has arrived yet" and "this provider doesn't implement
 * the signal at all" with the same sentinel, deliberately. See CONTEXT.md's "No reading available"
 * entry.
 */
enum class NetworkQuality {
    GOOD,
    POOR,
    BAD,
}
