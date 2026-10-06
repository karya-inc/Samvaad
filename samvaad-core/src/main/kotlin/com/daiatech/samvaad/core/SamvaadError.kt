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
 *
 * Being open rather than sealed has one real cost: a `when` branching on [SamvaadError] can never
 * be exhaustive (the compiler can't enumerate every possible subclass), so always include an
 * `else` branch when handling it -- this is not an oversight to "fix" later.
 *
 * **Extending from your own module.** Subclass directly for one-off cases, same as every type
 * nested here does:
 * ```
 * class CallerBusy : SamvaadError("You're already on another call") {
 *     override val errorCode = "CALLER_BUSY"
 * }
 * ```
 * Or define your own closed family for a related set of outcomes -- a `sealed class` extending
 * [SamvaadError] is exhaustive *within your own module* (every one of its subclasses must live
 * alongside it), while still being a plain [SamvaadError] from this library's point of view:
 * ```
 * sealed class BackendCallError(message: String) : SamvaadError(message) {
 *     object CallerBusy : BackendCallError("You're already on another call") {
 *         override val errorCode = "CALLER_BUSY"
 *     }
 *     object CalleeBusy : BackendCallError("The person you're calling is busy") {
 *         override val errorCode = "CALLEE_BUSY"
 *     }
 *     object AlreadyConnected : BackendCallError("This call is already connected") {
 *         override val errorCode = "ALREADY_CONNECTED"
 *     }
 * }
 * ```
 * Either way, it shows up as a `SamvaadError` (or `is BackendCallError`) to any code further up
 * the chain -- e.g. carried as [VoipCallState.Error.cause] -- without this library ever needing
 * to know your own backend's vocabulary.
 */
abstract class SamvaadError(message: String?, cause: Throwable? = null) : Exception(message, cause) {

    /**
     * A short, stable, SCREAMING_SNAKE_CASE identifier for this specific failure -- e.g.
     * `"CLIENT_CREATION_FAILED"`. Unlike the Kotlin class name, this survives a consumer's R8/
     * ProGuard-minified release build unmangled (it's a plain string literal, not a class name
     * subject to renaming), so it's the thing to log, filter on, or group crash reports by --
     * not `this::class.simpleName`, which may not be.
     */
    abstract val errorCode: String

    final override fun toString(): String = "${this::class.simpleName}[$errorCode]: $message"

    /** [VoipSdkClientFactory.create] threw instead of returning a [VoipSdkClient]. */
    class ClientCreationFailed(val provider: CallProvider, cause: Throwable) : SamvaadError(
        "Failed to create a VoipSdkClient for provider '${provider.id}'" +
            (cause.message?.let { ": $it" } ?: ""),
        cause,
    ) {
        override val errorCode = "CLIENT_CREATION_FAILED"
    }

    /**
     * A [VoipSdkClient] implementation's own operation failed (join rejected, provider SDK
     * internal error, leave failed, ...). Implementations report through this instead of only
     * ever having a plain message string to work with -- see `DailyCoVoipSdkClient` in the sample
     * app for a concrete usage.
     */
    class ProviderError(val provider: CallProvider, message: String?, cause: Throwable? = null) : SamvaadError(
        message ?: "Provider '${provider.id}' reported a call failure",
        cause,
    ) {
        override val errorCode = "PROVIDER_ERROR"
    }
}
