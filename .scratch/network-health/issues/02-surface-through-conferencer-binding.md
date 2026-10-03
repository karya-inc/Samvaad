Type: grilling
Blocked by: 01
Status: resolved

## Question

Once `AbstractVoipCallService` exposes a relayed, reactive network-quality value (shape decided in [ticket 01](./01-polling-location-and-cadence.md)), how does it reach `MainActivity`'s Compose UI through `ConferencerBinding`/`ConferencerUiState<M>`?

Candidates to weigh:

- A new field on `ConferencerUiState.Ongoing` (alongside `meta`, `durationSeconds`) — mirrors how duration already surfaces, but changes a public data class's shape.
- A wholly separate `StateFlow<NetworkQuality?>` property on `ConferencerBinding`, parallel to (not inside) `uiState` — keeps `ConferencerUiState` purely about call lifecycle, matches the "network quality is a different axis than call state" distinction settled during domain modeling (see [CONTEXT.md](../../../CONTEXT.md)).
- Something else.

Also decide: does `ConferencerBinding`'s own relay (`onServiceStateChanged`/`startRelay()`) need to change to carry this, or can it be collected independently via its own job?

## Answer

Found a cleaner precedent than either listed candidate: `ConferencerUiState.Ongoing` already does exactly this for `durationSeconds` today -- `onServiceStateChanged` constructs it with `service?.durationSeconds ?: MutableStateFlow(0L)`, a direct reference to the Service's own `StateFlow`, not re-relayed through any of `ConferencerBinding`'s own reactive machinery. `networkQuality` mirrors this exactly: a new field on `ConferencerUiState.Ongoing`, constructed with `service?.networkQuality ?: MutableStateFlow(null)`.

This resolves the "which candidate" question (it's effectively a third option neither listed form took) and the relay question in one move: no change needed to `startRelay()`/`onServiceStateChanged`'s collection logic at all -- just one more constructor argument on the existing `Ongoing` branch. Zero new relay machinery.

Implemented in `samvaad-android/.../ConferencerBinding.kt`; covered by `ConferencerBindingNetworkQualityTest.kt`.
