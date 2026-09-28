package com.karya.conferencer

/**
 * Identifies which VoIP provider backs a call.
 *
 * PROTOTYPE NOTE (reacting to the real code at
 * karya-android-client/feature/conferencer/domain/.../models/CallProvider.kt):
 * the real one is `enum class CallProvider { DAILYCO, TWILIO }`. A closed enum
 * is the one thing here that can't survive open-sourcing as-is -- a third
 * party adopting this library to wrap Agora, Zoom, or their own SDK would
 * have to fork the library just to add a provider name. Replaced with a
 * value class wrapping a plain string id, so registering a new provider is
 * just picking a new id -- no enum, no fork.
 *
 * Karya's own two providers become simple constants on top, so existing call
 * sites (`CallProvider.DAILYCO`) don't need to change shape, just the type
 * they resolve to.
 */
@JvmInline
value class CallProvider(val id: String) {
    companion object {
        val DAILYCO = CallProvider("dailyco")
        val TWILIO = CallProvider("twilio")
    }
}
