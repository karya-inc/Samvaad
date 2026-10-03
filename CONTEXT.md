# Samvaad

A provider-agnostic Android VoIP calling library (`samvaad-core`/`samvaad-android`) plus a sample app (`app`) demonstrating it against Daily.co. Mechanism lives in the library; policy (UI, specific providers, backend reconciliation) stays with the consumer.

## Language

**Network quality**:
A graded, provider-agnostic assessment of the current call's network link, distinct from [VoipCallState](#voipcallstate) — orthogonal axes, not a lifecycle phase. A call can be `Ongoing` with good or bad network at different moments without its lifecycle state changing at all. Three levels: `GOOD`, `POOR`, `BAD`, mirroring the common shape every VoIP SDK's own packet-loss-derived classification tends to collapse to.
_Avoid_: Call quality (implies a judgment about perceived audio/video output, which this doesn't measure), network stats/network health (too vague, invites raw-number creep), connection status (a different, out-of-scope signal — see below).

The Service layer's own `networkQuality` (a `StateFlow`) is a polled snapshot of the SDK layer's `networkQuality()` (a pull getter) — same name on purpose, same concept, different layer. It can lag the adapter's own live value by up to one tick interval; it is not a second, independently-authoritative source.

**No reading available**:
`VoipSdkClient.networkQuality()` returns `null` for this — not a fourth enum value. Covers two situations a consumer can't reliably tell apart from the type alone, and isn't meant to: the call has just started and no reading has arrived yet (transient, resolves itself), or this provider adapter never implements the signal at all (permanent, "don't bother"). Deliberately the same sentinel for both — a provider's default implementation returns `null` for free, with zero boilerplate required to opt out.

**VoipCallState**:
The local SDK/UI lifecycle of a call (Idle/Dialing/Incoming/Connecting/Ongoing/Disconnecting/Ended/Error), as observed on-device. Provider-agnostic. Distinct from a backend's authoritative call status, and distinct from [network quality](#network-quality) — lifecycle phase, not link health.

## Out of scope (for now)

**Connection status / transport interruption**:
Some provider SDKs separately report discrete connect/interrupt events per network transport (e.g. signalling vs. media send/receive). A different, more granular signal than network quality — not modeled here. If ever needed, it's a separate concept, not a variant of network quality.
