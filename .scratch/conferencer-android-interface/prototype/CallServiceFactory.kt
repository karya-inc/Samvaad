package com.karya.conferencer

/**
 * SUPERSEDED by the round-3 revamp, same as VoipCallManager.kt -- kept only
 * for the discussion trail. This factory located a VoipCallManager; that
 * tier is gone. The provider-registry IDEA this file introduced survives,
 * just one layer down: see VoipSdkClientFactory in VoipSdkClient.kt, which
 * AbstractVoipCallService uses to resolve a provider id to an SDK client.
 */
interface CallServiceFactory {
    fun getService(provider: CallProvider): VoipCallManager
}

class DefaultCallServiceFactory : CallServiceFactory {
    private val registry = mutableMapOf<CallProvider, () -> VoipCallManager>()

    /** Register (or replace) how to obtain the manager for [provider]. */
    fun register(provider: CallProvider, factory: () -> VoipCallManager) {
        registry[provider] = factory
    }

    override fun getService(provider: CallProvider): VoipCallManager {
        val factory = registry[provider]
            ?: throw IllegalStateException(
                "No VoipCallManager registered for provider '${provider.id}'. " +
                    "Call register(provider) { ... } before requesting it."
            )
        return factory()
    }
}
