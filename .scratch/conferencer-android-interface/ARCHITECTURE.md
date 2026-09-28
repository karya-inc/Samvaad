# Conferencer Android Interface — Architecture (single source of truth)

**Status**: design prototype, not yet building/compiled, not yet integrated into `karya-android-client`.
**Purpose of this document**: everything a fresh session needs to continue this work without re-reading
the full discussion history. `README.md` in this same directory has the round-by-round "how we got here"
narrative if that's ever needed; this file is the "where we are now" reference and should be kept current
as the design evolves — update it in place rather than appending a new document each session.

## 1. What this is

An open-source Android library, extracted from Karya's real VoIP/conferencer feature
(`karya-android-client/feature/conferencer/`), for building a reliable, provider-agnostic VoIP calling
feature on Android: join/leave a call, survive process/Activity lifecycle via a foreground service, track
call duration correctly, and expose clean state for a UI to observe.

**Guiding principle for what belongs in the library vs. stays out**: *mechanism, not policy*. The SDK
abstraction, the foreground-service lifecycle plumbing, the duration timer, and the reconciliation logic
(guards against redelivery, transient blips, etc.) are mechanism — genuinely reusable regardless of what
product sits on top. Anything that would require inheriting from the library, knowing about a specific
backend, or presuming a specific push-payload/metadata shape is policy, and stays with the consumer
(Karya's own app included).

## 2. Source material this reacts to (real code, not invented from scratch)

All paths relative to `/home/deepanshu-pratik/Karya/karya-android-client/`:

- `feature/conferencer/domain/src/main/java/com/daiatech/domain/VoipCallManager.kt` — old thin proxy interface
- `feature/conferencer/domain/src/main/java/com/daiatech/domain/VoipCallState.kt` — local SDK/UI lifecycle (kept, barely changed)
- `feature/conferencer/domain/src/main/java/com/daiatech/domain/VoipUiStates.kt` — Karya-specific rich UI state (stays out)
- `feature/conferencer/domain/src/main/java/com/daiatech/domain/models/CallProvider.kt` — old closed enum (DAILYCO, TWILIO)
- `feature/conferencer/domain/src/main/java/com/daiatech/domain/CallServiceFactory.kt` — old fixed-dispatch factory
- `feature/conferencer/domain/src/main/java/com/daiatech/domain/mappers/StateMapper.kt` — maps VoipCallState → VoipUiState
- `feature/conferencer/src/main/java/com/daiatech/conferencer/voip/VoipCallService.kt` — the real Android foreground `Service`, hardcoded to Daily.co's `DailyManager`
- `feature/conferencer/src/main/java/com/daiatech/conferencer/voip/dailyco/DailyCoVoipManagerImpl.kt` — real `VoipCallManager` impl, binds to the Service
- `feature/dashboard/src/main/java/com/daiatech/app/dashboard/ui/screens/voipCall/VoipController.kt` — `VoIPCalleeController`, a global singleton `object`
- `feature/dashboard/.../voipCall/models/CallScreenState.kt` — yet another UI-state sealed type
- `feature/task/src/main/java/com/daiatech/task/domain/runtime/controller/impl/UserConferencerVoipCallerController.kt` — caller-side controller (1200+ lines), **must implement** `UserComponentController<UserConferencerVoipCallerComponent>` (Karya's task-component-runtime interface) — this fact is load-bearing, see §4.
- Backend: `karya-server/server/common/src/notification/Index.ts` (`sendPushNotification`) and
  `karya-server/server/box/src/conferencer/conferencerProviders/DailyCo/DailyCoImpl.ts` (where
  `VOIP_INCOMING_CALL` is actually sent) — see §7, real bug found there.

## 3. The three layers

```
CallProvider.kt ─┐
CallRole.kt      ├─→ VoipSdkClient.kt ─→ AbstractVoipCallService.kt ─→ ConferencerBinding.kt
VoipCallState.kt ┘        (SDK)                (Service)                   (Binding)
```

Dependencies point one way only. `AbstractVoipCallService` knows nothing about `ConferencerBinding` or
about any consumer's metadata type. `VoipSdkClient` knows nothing about Android Service/notification
plumbing.

### 3.1 SDK layer — `VoipSdkClient.kt`

The *only* provider-specific piece (Daily.co/Twilio/Agora/...). One implementation per SDK.

```kotlin
interface VoipSdkClient {
    val callState: StateFlow<VoipCallState>
    fun join(config: String, role: CallRole)   // `config` is opaque — Daily.co reads it as a room URL
    fun leave()
    fun toggleMic(enable: Boolean)
    fun isMicrophoneEnabled(): Boolean
    fun release()                               // called once, when the owning service is torn down
}
interface VoipSdkClientFactory {
    fun create(provider: CallProvider): VoipSdkClient
}
```

Does **not** track duration (see 3.2 for why) and knows nothing about Android's Service/Binder/notification
plumbing.

### 3.2 Service layer — `AbstractVoipCallService.kt`

SDK-agnostic Android foreground `Service`. Owns:

- Foreground-notification lifecycle (`startForeground` in `onStartCommand`; concrete subclass supplies
  the actual `Notification` via `buildNotification()` — per-app branding, can't be generic)
- The Binder (`LocalBinder`, a **named** inner class, not anonymous — see §8 for why that matters)
- The single `VoipSdkClient` instance for this call's lifetime (created via the abstract
  `sdkClientFactory`, resolved once from `EXTRA_PROVIDER_ID` on the start `Intent`; a second `join()` call
  on an already-joined instance no-ops)
- **Duration**: stamped from `callStartedAtEpochMillis = System.currentTimeMillis()` the first time the
  relayed state reports `Ongoing`; the exposed `durationSeconds` flow is *recomputed fresh every tick*
  from that timestamp, not incremented — correct even if the ticker coroutine gets delayed/suspended by
  the OS. **Deliberately centralized here, not in the SDK layer**: every SDK adapter gets duration
  tracking correct for free instead of each one risking a subtly different bug independently.
- **Self-stop**: watches its own relayed state; on `Ended`/`Error`, calls `stopSelf()`. Centralizes
  "when does the service die" in one place instead of requiring every caller to remember a separate
  explicit stop step (the real app's `cleanup()` used to require `leaveCall()` *and* `stopServiceIfRunning()`
  as two things a caller had to get right together).
- `companion object { var isRunning }` — polled by `ConferencerBinding.attachIfRunning()` for
  orphan-call detection after a process restart.

Constants: `EXTRA_PROVIDER_ID`, `EXTRA_CONFIG`, `EXTRA_ROLE` — the three things a start `Intent` carries.

**Kotlin gotcha**: `isRunning` lives on the companion object of the *abstract* class. Must be referenced
as `AbstractVoipCallService.isRunning`, never through a concrete subclass's name — Kotlin doesn't give
each subclass its own companion instance.

### 3.3 Binding layer — `ConferencerBinding.kt`

**A plain, final class held as a field and delegated to — NOT a base class to extend.** This was the
single most important correction across this design's iterations (see §4 for why).

```kotlin
class ConferencerBinding<M>(
    context: Context,
    private val role: CallRole,
    private val serviceClass: Class<out AbstractVoipCallService>,
    private val callId: (metadata: M) -> String?,
    private val joinConfig: (metadata: M) -> String,
    private val provider: (metadata: M) -> CallProvider,
)
```

Binds directly to the Service (no Manager proxy tier — that tier's only job, relaying state, is
subsumed here since this class has to relay anyway to merge in caller-supplied metadata `M`).

Public surface: `uiState: StateFlow<ConferencerUiState<M>>`, `onIncomingCall(metadata: M)` (callee-only),
`acceptOrInitiate(metadata: M)`, `declineOrEnd()`, `toggleMic(enable: Boolean)`, `attachIfRunning(): Boolean`,
`destroy()`.

`M` is the consumer's own call-metadata type (Karya: `CallInfo` — caller/callee name+number, meeting
link, conference id). The three constructor lambdas are how this class stays ignorant of what `M` looks
like.

`ConferencerUiState<M>` (same file) — `Idle`, `Incoming(meta)`, `Connecting(meta?)`,
`Ongoing(meta?, durationSeconds: StateFlow<Long>)`, `Disconnecting(meta?)`, `Error(meta?, message?)`.
Replaces three independently-evolved Karya types that covered nearly the same shape: `VoipUiState`,
`CallScreenState`/`CallScreen`, `VoipCallUiState`.

## 4. The core decision: composition, not inheritance

`ConferencerBinding` was originally designed (and briefly built) as `abstract class ConferencerController<M>`,
meant to be subclassed directly by Karya's caller/callee controllers. **This is wrong and was corrected.**

Reason, concrete not stylistic: `UserConferencerVoipCallerController` (Karya's real caller-side
controller) already **must** implement `UserComponentController<UserConferencerVoipCallerComponent>` —
Karya's task-component-runtime requires this of every task input component. Kotlin has single class
inheritance. A controller required to extend `UserComponentController` cannot *also* extend an abstract
`ConferencerController` base class. Any other adopter with their own mandatory base class (a DI-managed
ViewModel base, their own MVI framework) hits the identical wall.

**Resolution**: `ConferencerBinding` is a plain class. A consumer's own controller holds one as a private
field and delegates to it, while independently satisfying whatever *other* interface its own app
architecture requires, and independently calling its own backend API repository — neither of which this
library needs to know exists. E.g. `UserConferencerVoipCallerController` would hold
`private val binding: ConferencerBinding<CallInfo>`, forward `uiState`/`onIncomingCall`/etc., and
separately implement `UserComponentController` and call `KaryaApiRepository`.

This is also the concrete answer to "how far should the framework go": stop at the boundary where going
further would mean the library dictating what a consumer's class extends, what backend it talks to, or
what its push-payload shape is.

## 5. State model: what replaced what

| Old (4 types, overlapping) | New |
|---|---|
| `CallState` (mirrors backend `call_status`: `RINGING_CALLER`, `BUSY_CALLEE`, etc.) | **Dropped from this library entirely.** Remote/server state is a different axis from local SDK lifecycle; reconciling the two ("local vs. server state parity") is real, unsolved work, but it's the consumer's own reconciliation logic, not baked in here. |
| `VoipCallState` (local SDK lifecycle) | **Kept, barely changed** — `Idle`, `Dialing`, `Incoming`, `Connecting`, `Ongoing`, `Disconnecting`, `Ended`, `Error(cause?, message?)`. Already provider-agnostic in the real code. |
| `VoipUiState` | **Replaced by `ConferencerUiState<M>`** (§3.3) |
| `CallScreenState`/`CallScreen`/`VoipCallUiState` | **Replaced by `ConferencerUiState<M>`** (§3.3) |

## 6. Production guards preserved (each is a real, shipped bug fix — do not drop silently)

Found by reading `VoIPCalleeController` directly, not guessed. All live in `ConferencerBinding`, commented
inline at each call site:

1. **`onServiceDisconnected`** (bound service died unexpectedly — OOM/system kill) must flip state to
   `Idle` regardless of the last relayed state; nothing left to reconcile against.
2. **Transient-`Idle` rollback**: a transient `Idle` report from the Service, while a call is pending or
   active, must not drop the UI back to the no-call screen unless the state is genuinely `Ended`/`Error`.
3. **Redelivery dedup**: redelivered incoming-call metadata for a conference already accepted, or already
   past the incoming screen, must be ignored, not reprocessed.
4. **Relay job survives across calls**: the relay collector must not be torn down between calls (only on
   `destroy()`) — otherwise the next incoming call has nothing collecting its state transitions.

## 7. Real bugs/gaps found while designing this (status: found, not all fixed)

1. **UNFIXED — bound service won't actually stop while bound.** Traced end-to-end: `declineOrEnd()`
   calls `service?.leaveCall()` → SDK reports `Ended` → Service's relay collector calls `stopSelf()`.
   But `ConferencerBinding` is still *bound* at that point (`declineOrEnd()` never unbinds — only
   `destroy()` does). Android's documented behavior: a service that is both started and bound will not
   actually stop until every client unbinds, regardless of how many times `stopSelf()`/`stopService()` is
   called. Result: the service lingers alive, bound, in a stopped-but-not-destroyed limbo after a normal
   call end, until something eventually calls `destroy()` — nothing currently triggers that
   automatically. **Proposed fix** (not yet applied): `ConferencerBinding` should unbind itself the moment
   it observes a terminal state through the relay (inside `onServiceStateChanged`), not wait for an
   external `destroy()` call, while leaving `relayJob`/`scope` alive for the next call.
2. **UNFIXED — no `CoroutineExceptionHandler` in `AbstractVoipCallService`'s scope.** `sdkClient.join()`/
   `leave()`/`toggleMic()` and the `client.callState.collect{}` coroutine all run in the Service's own
   scope (`Dispatchers.Main + SupervisorJob()`). `SupervisorJob` only isolates *sibling* coroutines from
   each other — it does not swallow an exception. An uncaught exception thrown from inside any
   `VoipSdkClient` implementation (a provider adapter bug) is not contained; it crashes the whole process,
   taking the foreground service down with it. Worth considering a `CoroutineExceptionHandler` installed
   on this scope to at least log/contain SDK-adapter bugs instead of a full process crash.
3. **UNFIXED, backend, separate repo (`karya-server`)** — `sendPushNotification` (`common/src/notification/Index.ts`)
   never sets FCM's `android.priority`. `VOIP_INCOMING_CALL` (sent from
   `box/src/conferencer/conferencerProviders/DailyCo/DailyCoImpl.ts`) goes through the data-only message
   branch, which defaults to **normal** priority. Combined with Android 12+'s restriction on starting a
   foreground service from the background (§8) — the *only* applicable exemption there is a high-priority
   FCM message — an incoming call arriving while the app is backgrounded/killed on Android 12+ may be
   **unable to start the foreground call service at all**. Fix is on the backend: add
   `android: { priority: 'high' }` to that message. Not yet applied; needs explicit go-ahead since it's a
   different repo/track than this Android library.
4. **UNFIXED — `callStartedAtEpochMillis` in `AbstractVoipCallService` is never reset back to `null`.**
   Found during a memory/resource-leak review of Samvaad's actual implementation (which otherwise matches
   this prototype's shape faithfully, including this gap — it was already present here, not introduced
   during adaptation). `startDurationTicker()`'s loop reads `callStartedAtEpochMillis ?: break` as if it
   were a live safeguard that stops the ticker once the field goes back to null, but nothing in this file
   ever nulls it — the ticker's only real exit paths are the explicit `tickerJob?.cancel()` calls (on
   `Ended`/`Error`, and in `onDestroy()`). Not a live leak today (both real paths do cancel it correctly),
   but the `?: break` is dead code, and there's no independent safeguard if some `VoipSdkClient`
   implementation never emits a terminal state and the Service is never destroyed — an unbounded 1-second
   ticker coroutine. **Proposed fix**: reset `callStartedAtEpochMillis = null` wherever the ticker is
   cancelled, making the loop's own break condition live rather than decorative.

## 8. Android platform constraints (verified against official docs, not memory — see README round for
   the raw fetches and confidence caveats)

- **Android 12 (API 31)+**: apps cannot start a foreground service from the background except for a
  specific exemption list. The one relevant here: the triggering FCM message must be high priority (see
  §7.3 — currently not satisfied).
- **Android 14 (API 34)+**: declaring a `foregroundServiceType` in the manifest is mandatory, matched to
  actual use. Two real candidates for VoIP, **not yet decided which one**:
  - `phoneCall` — needs `FOREGROUND_SERVICE_PHONE_CALL` permission *and* either `MANAGE_OWN_CALLS`
    (self-managed `ConnectionService` integration) or the default-dialer role. Full OS call-UI
    integration (Bluetooth headset button, Do Not Disturb bypass) but real integration work not yet done.
  - `microphone` — just `RECORD_AUDIO` (needed anyway), no Telecom integration. Simpler, matches what's
    actually built today.
- **`startForeground()` timeout**: a mandatory short window after `startForegroundService()`
  (`ForegroundServiceDidNotStartInTimeException` if missed). Two external sources disagreed on the exact
  number (~5s vs ~10s) — the real codebase's own comment says ~5s; trust that over either web summary,
  verify directly against the "service timeout" doc if an exact number is load-bearing for something.
- **`POST_NOTIFICATIONS` (Android 13+)**: lower-confidence finding — plausibly the foreground service
  keeps running even if this permission is denied, just without a visible notification. Not independently
  confirmed; verify directly if load-bearing.

## 9. Out of scope — stays inside `karya-android-client`, not part of the OSS library

- `VoipUiState`/`StateMapper`'s Karya-specific fields (contact names, call summaries, the
  `AlreadyCompleted` case tied to `ConferencerError.ConferencerRecordAlreadyExists` and MTA completion)
  — becomes a consumer's own `M` and its own mapping on top of `ConferencerUiState<M>`.
- `CallState.kt` (mirrors backend `call_status`) — a different axis; reconciling local vs. remote state
  is the consumer's job.
- Anything backend-API-shaped: initiate/reject/end requests, FCM payload parsing, MTA ids, task-runtime
  submission tracking (`VoipSubmissionTracker`/`VoipSubmissionTicket`).
- `UserComponentController`, `ExecutorContext`, `TaskLogger`, and everything else in Karya's
  task-component-runtime.

## 10. File manifest

Location: `karya-server/server/.scratch/conferencer-android-interface/prototype/`

| File | Status |
|---|---|
| `CallProvider.kt` | live — open value class (not closed enum), so third parties can register a provider without forking |
| `CallRole.kt` | live — `CALLER`/`CALLEE` |
| `VoipCallState.kt` | live — local SDK/UI lifecycle |
| `VoipSdkClient.kt` | live — SDK layer interface + factory |
| `AbstractVoipCallService.kt` | live — Service layer (§3.2, §7.1, §7.2 open issues) |
| `ConferencerBinding.kt` | live — Binding layer + `ConferencerUiState<M>` (§3.3, §7.1 open issue) |
| `VoipCallManager.kt` | **superseded** — kept only for discussion-trail context, do not build against |
| `CallServiceFactory.kt` | **superseded** — same |

No `kotlinc` in this environment — nothing here has been compiled. Reviewed by hand; two real
implementation bugs were caught and fixed during review (a generic-type-inference call site made
explicit, and a `::callId` reference that stopped being valid once `callId` became a constructor lambda
instead of a member function) — but treat as an unverified design sketch, not tested code.

## 11. Open questions / not yet decided

- Package/artifact name, Maven coordinates, min SDK, license.
- Whether a reference `VoipSdkClient` implementation (Daily.co) gets open-sourced too, or only interfaces.
- Whether this stays a Gradle module inside `karya-android-client` first (current plan) or moves to its
  own repo immediately.
- `phoneCall` vs `microphone` foreground service type (§8).
- Whether/when to apply the three fixes in §7.
- Whether `ConferencerBinding` needs its own explicit "detach without stopping the call" method (the
  original `VoipCallManager.detach()`) distinct from `destroy()`.

## 12. How to continue in a fresh session

1. Read this file fully first.
2. If you need the blow-by-blow reasoning/rationale trail (why each decision was made, in what order),
   read `README.md` in this same directory — it's the round-by-round discussion log this file summarizes.
3. Read the actual `.kt` files in `prototype/` before changing anything — this document describes them
   accurately as of when it was written, but the files are the ground truth if they ever diverge.
4. The backend-side finding (§7.3) lives in a different repo (`karya-server`, not
   `karya-android-client`) — don't conflate fixing it with changes to this library.
