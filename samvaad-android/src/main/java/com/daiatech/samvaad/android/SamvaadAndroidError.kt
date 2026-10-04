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
        SamvaadAndroidError(message)

    /**
     * `Context.bindService()` returned `false` synchronously -- no `ServiceConnection` callback
     * will ever arrive for this attempt. Without this, [ConferencerBinding] left the UI in
     * [ConferencerUiState.Connecting] forever with no explanation.
     */
    class ServiceBindFailed(message: String = "bindService() returned false -- the call could not be started") :
        SamvaadAndroidError(message)
}
