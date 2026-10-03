Type: grilling
Blocked by: 01

## Question

Once `AbstractVoipCallService` exposes a relayed, reactive network-quality value (shape decided in [ticket 01](./01-polling-location-and-cadence.md)), how does it reach `MainActivity`'s Compose UI through `ConferencerBinding`/`ConferencerUiState<M>`?

Candidates to weigh:

- A new field on `ConferencerUiState.Ongoing` (alongside `meta`, `durationSeconds`) — mirrors how duration already surfaces, but changes a public data class's shape.
- A wholly separate `StateFlow<NetworkQuality?>` property on `ConferencerBinding`, parallel to (not inside) `uiState` — keeps `ConferencerUiState` purely about call lifecycle, matches the "network quality is a different axis than call state" distinction settled during domain modeling (see [CONTEXT.md](../../../CONTEXT.md)).
- Something else.

Also decide: does `ConferencerBinding`'s own relay (`onServiceStateChanged`/`startRelay()`) need to change to carry this, or can it be collected independently via its own job?
