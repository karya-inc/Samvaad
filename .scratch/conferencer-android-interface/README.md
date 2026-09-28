# Conferencer Android Interface — open-source prototype

Round 3: a complete revamp, not incremental polish on the existing interfaces. Grounded in the real
code (`karya-android-client/feature/conferencer/` and the caller/callee controllers), but not
constrained by its current shape.

## The problem this revamp is solving

The real app has **five tiers** doing what should be three jobs, and **four separate state
representations** of nearly the same Idle/Ringing/Connecting/Active concept:

- `VoipCallManager`/`DailyVoipManagerImpl` — a thin proxy that binds to...
- `VoIPCallService` — a real Android foreground `Service`, hardcoded to a concrete `DailyManager`
  (Daily.co-specific), which is then relayed by...
- `VoIPCalleeController` — a global singleton `object` (callee side), **and independently**...
- `UserConferencerVoipCallerController` — 1200+ lines, buried inside the generic task-component-runtime
  (caller side)

The last two duplicate real, shipped bug fixes (found by reading the actual code, not guessed):
FCM-redelivery-clobbers-in-progress-call guards, transient-`Idle`-blip rollback guards, a null
`callerNumber` crash guard, "don't cancel the relay job between calls." And the state model is spread
across `CallState` (mirrors the backend's old `call_status`), `VoipCallState` (local SDK lifecycle),
`VoipUiState`, and `CallScreenState`/`CallScreen`/`VoipCallUiState` — four sealed types for one concept.

## The revamped architecture: three layers

1. **SDK layer** (`VoipSdkClient.kt`) — the *only* provider-specific piece (Daily.co/Twilio/Agora/...).
   Reports state transitions, executes join/leave/toggleMic. Does not track duration.
2. **Service layer** (`AbstractVoipCallService.kt`) — SDK-agnostic. Owns the foreground-notification
   lifecycle, the Binder, the single `VoipSdkClient` for this call's lifetime, and duration (stamped from
   a wall-clock timestamp the first time relayed state reports `Ongoing`, recomputed fresh each tick —
   centralized here, not in the SDK layer, so every provider gets it correct for free). Also now
   self-stops (`stopSelf()`) once it observes its own relayed state go terminal (`Ended`/`Error`) —
   previously this was two separate steps (`leaveCall()` + an explicit `stopService()`) a caller had to
   remember to do together.
3. **Binding layer** (`ConferencerBinding.kt`, renamed from `ConferencerController` in round 4 — see
   below) — binds straight to the Service (no Manager proxy in between — its only job, relaying state,
   is subsumed here since this class has to relay anyway to merge in call metadata). Generic over
   `CallRole` (one code path for caller and callee, branching only where behavior genuinely differs)
   and over a metadata type `M` (Karya's `CallInfo` — caller/callee name/number, meeting link,
   conference id — is Karya-specific and stays out; a consumer supplies its own).

`ConferencerUiState<M>` (same file) replaces `VoipUiState` + `CallScreenState`/`CallScreen`/
`VoipCallUiState` — one sealed type instead of three independently-evolved ones.

**Every guard listed in "the problem" above is preserved** in `ConferencerBinding` — see the file's
own doc comment and the inline "Preserved guard" comments at each call site. This was a deliberate
choice: a clean rewrite that silently drops hard-won bug fixes just reintroduces those bugs.

## Round 4: composition, not inheritance — and why

Round 3 shipped `ConferencerController` as an `abstract class` meant to be subclassed by
`VoIPCalleeController` and `UserConferencerVoipCallerController`. Checking the real caller controller's
declaration killed that: `UserConferencerVoipCallerController` already **must** implement
`UserComponentController<UserConferencerVoipCallerComponent>` — Karya's task-component-runtime requires
it of every task input component. Kotlin has single class inheritance, so a controller required to
extend `UserComponentController` cannot *also* extend an abstract `ConferencerController` base class.
Any other adopter with their own mandatory base class (a DI-managed ViewModel base, their own MVI
framework) hits the identical wall.

So `ConferencerController` became **`ConferencerBinding`** — a plain, final class held as a field and
delegated to, not extended. The three extraction functions (`callId`/`joinConfig`/`provider`) that were
`protected abstract fun` overrides became constructor lambdas for the same reason. Concretely, Karya's
own `UserConferencerVoipCallerController` would hold a private `ConferencerBinding<CallInfo>`, delegate
`uiState`/`onIncomingCall`/`acceptOrInitiate`/etc. to it, while independently satisfying
`UserComponentController` and independently calling its own `KaryaApiRepository` for backend requests —
none of which this library needs to know exists.

**This is also the answer to "how far should the framework go"**: mechanism, not policy. The SDK
abstraction, the Service plumbing, the timer, and this reconciliation logic are genuinely reusable
regardless of what product sits on top — that's mechanism. The moment "framework" would mean *inheriting
from it*, knowing about a specific backend, or presuming a specific push-payload shape beyond a generic
type parameter — that's policy, and it stays with each consumer, Karya included. Concretely this rules
out an architecture shaped like "SDK → Service ↔ Controller → CallerController/CalleeController" (the
framework's Controller sitting between the Service and Karya's own controllers as a base class) in favor
of "SDK → Service (exposes state/functions) ← CallerController/CalleeController → backend", except the
reusable relay/guard logic doesn't have to be thrown away to get there — it's still available, just as
something held, not extended.

## Files

- `CallProvider.kt` — open value class (not a closed enum), so third parties can register a provider
  without forking.
- `CallRole.kt` — `CALLER`/`CALLEE`, split out on its own since `VoipCallManager.kt` (which used to
  define it) is now superseded.
- `VoipCallState.kt` — local SDK/UI lifecycle, unchanged from round 2.
- `VoipSdkClient.kt` — the SDK layer interface + factory.
- `AbstractVoipCallService.kt` — the Service layer.
- `ConferencerBinding.kt` — the composable binding layer + `ConferencerUiState<M>` (see Round 4 below).
- `VoipCallManager.kt`, `CallServiceFactory.kt` — **superseded**, kept only so the discussion trail
  (why the Manager tier was eliminated) isn't lost. Do not build against these.

## Out of scope — stays inside `karya-android-client`

- `VoipUiState`/`StateMapper`'s Karya-specific fields (contact names, call summaries, the
  `AlreadyCompleted` case tied to `ConferencerError.ConferencerRecordAlreadyExists` and MTA completion)
  — these become a consumer's own `M` metadata type and its own mapping logic on top of
  `ConferencerUiState<M>`, not part of the interface.
- `CallState.kt` (mirrors the backend's `call_status`) — remote/server state, a different axis from
  `VoipCallState`'s local lifecycle. Reconciling the two ("local vs. server state parity") is real,
  unsolved work flagged during the backend FSM effort, but it's a consumer's own reconciliation logic to
  write using `ConferencerController`, not something baked into the interface.
- Anything backend-API-shaped (initiate/reject/end requests, FCM payload parsing, MTA ids, task-runtime
  submission tracking like `VoipSubmissionTracker`) — none of that appears in these files.

## Not yet decided

- Package/artifact name, Maven coordinates, min SDK, license.
- Whether a reference `VoipSdkClient` implementation (Daily.co) gets open-sourced too, or only the
  interfaces.
- Whether this stays a Gradle module inside `karya-android-client` first (as discussed) or moves to its
  own repo now.
- `AbstractVoipCallService` currently supports exactly one call per Service instance lifetime (a second
  `join()` on an already-joined instance no-ops) — not yet stress-tested against the real
  orphan-reattachment scenarios the original `attachIfRunning`/`isCallServiceRunning` exist for.
- Whether `ConferencerBinding` needs its own explicit "detach without stopping the call" method (the
  original `VoipCallManager.detach()`) distinct from `destroy()` — right now `destroy()` only unbinds
  (Android's unbind ≠ stop, so this is implicitly already detach-like), but that distinction isn't named
  or documented anywhere in the new code the way it was explicitly called out before.
- A real bug the round-4 rename surfaced and fixed: `current.metadata()?.let(::callId)` was valid when
  `callId` was a member function; once it became a constructor-held lambda, `::callId` no longer means
  the same thing and had to become `current.metadata()?.let { callId(it) }`. Worth double-checking there
  isn't a second instance of this same pattern elsewhere if this file is extended further.

## Not verified

No `kotlinc` available in this environment. Reviewed by hand for one real issue already caught and fixed
(a generic-type-inference call site made explicit rather than relying on unverified inference, and a
variable-shadowing readability issue) — but nothing here has been compiled. Treat as a design sketch to
react to, not tested code.
