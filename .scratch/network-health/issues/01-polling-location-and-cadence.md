Type: grilling
Status: resolved

## Question

`VoipSdkClient.networkQuality(): NetworkQuality?` is a pull getter (see [ADR-0001](../../../docs/adr/0001-network-quality-pull-getter.md)). Something one layer up needs to poll it and relay the result reactively for the UI, the same way raw duration math became the reactive `durationSeconds` StateFlow in `AbstractVoipCallService`.

Decide:

- Does this reuse the existing `startDurationTicker()` loop in `AbstractVoipCallService.kt` (same 1-second cadence, same lifecycle — only ticks while a call is `Ongoing`), or get its own separate ticker?
- If separate: what cadence, and does it run for the whole service lifetime (from `onStartCommand`) or only while `Ongoing`, matching duration?
- Where does the polled value live — a new `StateFlow<NetworkQuality?>` on `AbstractVoipCallService` (parallel to `durationSeconds`), or something else?

Out of scope for this ticket: how it surfaces through `ConferencerUiState`/`ConferencerBinding` — that's [ticket 02](./02-surface-through-conferencer-binding.md), blocked on this one's answer.

## Answer

Reuse the existing `startDurationTicker()` loop — no separate ticker. Inside that same `while (isActive) { ...; delay(1_000) }` body, also poll `sdkClient?.networkQuality()` and push it into a new property, `val networkQuality: StateFlow<NetworkQuality?>` on `AbstractVoipCallService`, parallel to `durationSeconds`. Same start/stop lifecycle as duration: begins the moment `VoipCallState.Ongoing` is first seen, stops on `Ended`/`Error`.

Reasoning: `networkQuality()` is a cheap cached-field read (ADR-0001), so there's no cadence cost to piggybacking on duration's 1s tick, and Daily's underlying signal only reports anything meaningful once media is flowing anyway — the same window duration already covers. Running a second near-identical coroutine for no real gain was rejected.

It freezes at its last reading after the call ends rather than resetting to `null`, matching `durationSeconds`'s existing (if previously undocumented) behavior — deliberately now, not by accident. See [CONTEXT.md](../../../CONTEXT.md)'s "Network quality" entry for the added note on how this polled relay relates to the SDK layer's own pull getter.

No ADR: reusing the ticker and the freeze behavior are both cheap to reverse later and don't carry a deep structural trade-off, unlike ADR-0001's pull-vs-reactive call.
