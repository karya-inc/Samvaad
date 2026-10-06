package com.daiatech.samvaad.android

import com.daiatech.samvaad.core.SamvaadError

/**
 * Failures specific to this module's own Service/Binder lifecycle layer, as opposed to
 * [SamvaadError]'s provider/generic cases. A plain subclass, same as any consumer could write --
 * nothing here is special-cased by the core module.
 */
abstract class SamvaadAndroidError(message: String?, cause: Throwable? = null) : SamvaadError(message, cause) {

    /**
     * [AbstractVoipCallService.join]/[ConferencerBinding.acceptOrInitiate] was called while this
     * instance already has a call in progress -- a duplicate/racing initiate. Logged at the point
     * of detection, not surfaced through [ConferencerUiState] -- doing so would incorrectly
     * disrupt the real call already running, for every observer of that shared state, over a
     * request that was never going to replace it anyway.
     */
    class AlreadyInCall(message: String = "A call is already in progress on this service instance") :
        SamvaadAndroidError(message) {
        override val errorCode = "ALREADY_IN_CALL"
    }

    /**
     * `Context.bindService()` returned `false` synchronously -- no `ServiceConnection` callback
     * will ever arrive for this attempt. Without this, [ConferencerBinding] left the UI in
     * [ConferencerUiState.Connecting] forever with no explanation. [serviceClass] is the service
     * [ConferencerBinding] was constructed with, so a consumer juggling more than one
     * [AbstractVoipCallService] subclass can tell which one failed to bind without parsing the
     * message string.
     */
    class ServiceBindFailed(
        val serviceClass: Class<*>,
        message: String = "bindService() returned false for ${serviceClass.name} -- the call could not be started",
    ) : SamvaadAndroidError(message) {
        override val errorCode = "SERVICE_BIND_FAILED"
    }

    /**
     * The call actually connected (the provider reported a genuinely active state) right as
     * [ConferencerBinding.declineOrEnd] told it to leave -- [declineOrEnd] optimistically moves to
     * Idle in anticipation of `leave()` winning, but the remote participant joined first and lost
     * that race. Logged only, not forced into [ConferencerUiState.Error]: [ConferencerBinding]
     * reconciles into the real active call (using the metadata captured right before declining)
     * instead of pretending the decline won when it didn't.
     */
    class AlreadyConnected(message: String = "The call connected right as it was being declined/ended") :
        SamvaadAndroidError(message) {
        override val errorCode = "ALREADY_CONNECTED"
    }
}
