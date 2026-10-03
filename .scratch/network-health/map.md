# Network health — map

## Destination

A design spec for adding provider-agnostic network-quality reporting to the Samvaad VoIP library and sample app: a `networkQuality(): NetworkQuality?` pull getter on `VoipSdkClient` (`GOOD`/`POOR`/`BAD`, `null` = no reading available — see [CONTEXT.md](../../CONTEXT.md)), relayed reactively through the Service/Binding layers the same way duration already is, and displayed in the sample app's UI with a derived "bad network" flag (`quality == BAD`). The library stays UI-free. Daily's separate transport-interruption signal is out of scope.

## Notes

- Domain vocabulary: [CONTEXT.md](../../CONTEXT.md) (`Network quality`, `No reading available`, `VoipCallState`) and [docs/adr/0001-network-quality-pull-getter.md](../../docs/adr/0001-network-quality-pull-getter.md) (why pull, not reactive, at the `VoipSdkClient` layer).
- Every ticket here depends on the existing relay/ticker mechanics — re-read `VoipSdkClient.kt`, `AbstractVoipCallService.kt`, and `ConferencerBinding.kt` before resolving, not just this map.
- Standing boundary this effort exists to preserve: mechanism in the library, policy (including all UI) in the sample app. Don't let a ticket's resolution quietly reintroduce Daily-specific concepts above the adapter layer, or UI concepts below it.

## Decisions so far

- [Polling location and cadence](./issues/01-polling-location-and-cadence.md): reuse `startDurationTicker()`'s existing 1s loop (no separate ticker); new `networkQuality: StateFlow<NetworkQuality?>` parallel to `durationSeconds`, same Ongoing→Ended/Error lifecycle, freezes at its last reading rather than resetting to null.
- [Surface through ConferencerBinding](./issues/02-surface-through-conferencer-binding.md): `networkQuality` becomes a new field on `ConferencerUiState.Ongoing`, a direct reference to the Service's StateFlow -- found to exactly match `durationSeconds`'s existing pattern, no new relay machinery.
- [Prototype the sample app display](./issues/03-prototype-sample-app-display.md): a dot + label indicator in `MainActivity`, plus a red warning line specifically for `BAD`. Built, not yet eyeballed on a real device/emulator (none available in-session).

## Not yet specified

- Whether the signal needs debouncing/hysteresis to avoid UI flicker, if real-device testing shows Daily's `GOOD`/`POOR`/`BAD` classification flapping rapidly within a single call. Not specifiable until the polling/relay mechanism (tickets 01/02) actually exists to observe real behavior against.

## Out of scope

- Daily's transport-level connection-interruption signal (`onNetworkConnectionStatusUpdated`: signalling/send/recv connected/interrupted) — a materially different, per-transport signal, not a variant of network quality. See CONTEXT.md's "Connection status / transport interruption" entry. (Ruled out during destination-grilling, never ticketed.)
