package com.karya.conferencer

import kotlinx.coroutines.flow.StateFlow

/**
 * SUPERSEDED by the round-3 revamp -- kept in the prototype folder only so
 * the discussion trail (why we moved away from a Manager tier) isn't lost.
 * Do not build against this file; see ConferencerController.kt instead.
 *
 * Everything this interface did (bind to the service, relay callState,
 * detach/attachIfRunning/isCallServiceRunning) is now done directly by
 * ConferencerController binding straight to AbstractVoipCallService -- an
 * intermediate Manager tier had no remaining job once the Controller has to
 * relay state anyway to merge in call metadata. CallRole moved to its own
 * file (CallRole.kt) so the other prototype files don't depend on this one.
 *
 * (Original two flagged changes from the real VoipCallManager -- isCaller:
 * Boolean -> role: CallRole, and Error gaining an optional message -- both
 * carried forward into VoipSdkClient.kt / VoipCallState.kt.)
 */
interface VoipCallManager {
    val callState: StateFlow<VoipCallState>

    fun initialize()
    fun startCall(roomUrl: String, contactName: String?, role: CallRole)
    fun toggleMic(enable: Boolean)
    fun leaveCall()
    fun getDurationSeconds(): StateFlow<Long>
    fun cleanup()
    fun attachIfRunning()

    /**
     * Release this manager's binding to the underlying call service without
     * stopping the service or ending the call. Use when the owning
     * controller is being torn down (e.g. the user backed out of the screen
     * mid-call) but the call itself should keep running so a fresh
     * controller can [attachIfRunning] and resume.
     *
     * Unlike [cleanup], this does NOT stop the service and does NOT reset
     * [callState] to Idle.
     */
    fun detach()

    /**
     * Whether there is currently a foreground call service running for this
     * provider. A reliable synchronous signal -- does not depend on this
     * manager being bound.
     *
     * Callers use this to detect orphaned calls: if persisted/remote state
     * says a call is in progress but [isCallServiceRunning] returns false,
     * the call ended in the background without any controller observing the
     * terminal state. The owning app can then reconcile immediately instead
     * of restoring an Ongoing UI for a call that no longer exists -- this is
     * the local half of the "local vs. server state parity" problem; the
     * remote half (what does the backend's FSM say the call's state is) is
     * intentionally NOT this interface's concern.
     */
    fun isCallServiceRunning(): Boolean
}
