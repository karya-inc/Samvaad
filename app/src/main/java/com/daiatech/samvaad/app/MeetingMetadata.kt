package com.daiatech.samvaad.app

/**
 * The sample app's own call-metadata type -- what [com.daiatech.samvaad.android.ConferencerBinding]
 * is generic over. A real consumer would use something richer (caller/callee name, conference id,
 * ...); this sample only needs the room URL the user typed in.
 */
data class MeetingMetadata(val meetingLink: String)
