package com.karya.conferencer

/**
 * Moved out of the now-superseded VoipCallManager.kt so CallProvider.kt,
 * VoipSdkClient.kt, AbstractVoipCallService.kt, and ConferencerController.kt
 * don't depend on a file that's about to be marked dead.
 */
enum class CallRole { CALLER, CALLEE }
