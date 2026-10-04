package com.daiatech.samvaad.core

/**
 * Base type for every failure Samvaad (or a provider/consumer extending it) can produce. Carried
 * as a plain [Throwable] -- e.g. [VoipCallState.Error.cause] -- rather than needing a dedicated
 * field anywhere that already accepts a cause.
 *
 * Open, not sealed: a sealed hierarchy can only be subclassed from inside this module. A
 * [VoipSdkClient] implementation reporting its own provider-specific failure, or a consumer's own
 * backend reporting its own named outcome (e.g. a busy/conflict response from an initiate-call
 * API), needs to add a new leaf under this same type from their own module -- not be forced into
 * an untyped message string. samvaad-android subclasses this too, for failures specific to its
 * own Service/Binder layer (see `SamvaadAndroidError`).
 */
abstract class SamvaadError(message: String?, cause: Throwable? = null) : Exception(message, cause) {

    /** [VoipSdkClientFactory.create] threw instead of returning a [VoipSdkClient]. */
    class ClientCreationFailed(provider: CallProvider, cause: Throwable) : SamvaadError(
        "Failed to create a VoipSdkClient for provider '${provider.id}'" +
            (cause.message?.let { ": $it" } ?: ""),
        cause,
    )

    /**
     * A [VoipSdkClient] implementation's own operation failed (join rejected, provider SDK
     * internal error, leave failed, ...). Implementations report through this instead of only
     * ever having a plain message string to work with -- see `DailyCoVoipSdkClient` in the sample
     * app for a concrete usage.
     */
    class ProviderError(provider: CallProvider, message: String?, cause: Throwable? = null) : SamvaadError(
        message ?: "Provider '${provider.id}' reported a call failure",
        cause,
    )
}
