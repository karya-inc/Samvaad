package com.daiatech.samvaad.core

/**
 * Identifies which VoIP provider backs a call.
 *
 * Deliberately an open value class wrapping a plain string id, not a closed enum -- a third
 * party adopting this library to wrap a provider this module doesn't know about (Agora, Zoom,
 * their own in-house SDK, ...) can just construct a new [CallProvider] with a new id. No enum
 * entry to add here, no fork required.
 *
 * A couple of well-known providers are offered as constants for convenience, but the type
 * itself stays open: nothing about this class prevents anyone from creating more.
 */
@JvmInline
value class CallProvider(val id: String) {
    companion object {
        val DAILYCO = CallProvider("dailyco")
        val TWILIO = CallProvider("twilio")
        val AGORA = CallProvider("agora")
    }
}
