# qa/ — emulator-driven E2E scenario, lifecycle & memory-leak suite

Automates the manual emulator investigation from the memory-leak session: two low-end/low-
resource emulators, a real provider call, screen rotation, notification swipe-dismiss, process
backgrounding and process death, all watched via LeakCanary's logcat output.

This is **not** Android instrumented tests. Nothing here could be: booting two emulators,
rotating the screen via `settings put system user_rotation`, swiping a system notification, and
killing the app's process over adb are all host/OS-level operations a single on-device
instrumentation process has no way to orchestrate. This is a host-side `pytest` suite that drives
real emulators over `adb`, same as a human tester would.

> **Writing or reproducing a flow? Read [`SCENARIOS.md`](SCENARIOS.md).** Flows are declarative YAML
> files in `qa/scenarios/` (step vocabulary, expectations, always-on oracles, one-line `--flow` form,
> bug-report-to-scenario cheat sheet). This README covers setup and the shared plumbing.

## Prerequisites

- `ANDROID_HOME` (or `ANDROID_SDK_ROOT`) set, with `emulator` and `platform-tools` present.
  **If everything silently skips with no obvious reason**, this is almost always it. `pytest.ini`
  sets `-rs` so the skip reason always prints.
  In `bash`/`zsh`: `export ANDROID_HOME=/path/to/sdk`. In `fish`: `set -x ANDROID_HOME /path/to/sdk`.
- Two AVDs named `leak_test_low_1` / `leak_test_low_2` (scenario devices `a` / `b`; override with
  `QA_AVD_A` / `QA_AVD_B`). If you don't have them yet:
  ```
  ANDROID_HOME=/path/to/sdk ./qa/scripts/create_low_end_avds.sh
  ```
  This requires an x86_64 system image already installed.
- Python 3.9+, and `pip install -r qa/requirements.txt` (`pytest` and `pyyaml`; everything else is
  stdlib `subprocess`/`xml`).

## Running

```
export QA_MEETING_URL="https://your-test-room.daily.co/xxxxx"     # a real, live test room

python3 qa/run.py --list                                  # scenarios
python3 qa/run.py --dry-run --run all                     # validate everything, no device
python3 qa/run.py --run rotate-then-power-button-during-call
pytest -m emulator qa/                                    # every scenario file is a test
pytest -m emulator qa/ -k notification -v
pytest qa/                                                # offline: unit tests + scenario validation
```

Reports land in `qa/.artifacts/<scenario>/report.md` (+ `report.json`, logcat, screenshots).
Never hardcode a real external room URL into a checked-in file: scenarios say `meeting: env`.
Scenarios that join a call are skipped (pytest) or refused (CLI) with a clear reason when
`QA_MEETING_URL` is unset.

Useful environment variables:

| Variable | Effect |
|---|---|
| `QA_MEETING_URL` | Required for any scenario that joins a real call. |
| `QA_SKIP_BUILD=1` | Reuse the APK already on disk instead of rebuilding (`:app:assembleDebug`). |
| `QA_KEEP_EMULATORS=1` | Leave booted emulators running afterwards. |
| `QA_HEADLESS=1` | Boot emulators with `-no-window -no-audio` (no display needed). |
| `QA_ARTIFACTS_DIR` | Where reports/captures land (default: `qa/.artifacts/`). |
| `QA_AVD_A` / `QA_AVD_B` | AVD names for devices `a` / `b`. |
| `QA_ROOM_DRAIN_S` | Seconds to wait after a scenario that joined a call before the next one reuses the room (default 30; see SCENARIOS.md). |
| `QA_LOGCAT_FILTERS` | Extra logcat filter specs for the capture (diagnosis). |
| `QA_REPEAT=N` | pytest: override every scenario's `repeat`. |
| `QA_MEMORY_GROWTH_MB` | Default threshold of the `memory_growth` oracle (40). |

## What the committed scenarios cover

The four flows of the earlier hand-written `test_call_lifecycle.py` are now scenario files with the
same meaning (that file was removed: everything it did is expressible as steps, expectations and
oracles). The old per-test `leak_watch` asserts (no `FATAL EXCEPTION`, no confirmed LeakCanary leak)
are now the always-on `crash` and `leakcanary` oracles, applied to every scenario.

- **`rotation-no-leak`**: two devices join the same call, rotate landscape/portrait, go Home and
  come back; the call must stay `Ongoing` and LeakCanary must confirm no leak (the original manual
  investigation's main scenario; see "On LeakCanary noise").
- **`notification-not-swipe-dismissable`**: regression test for the `CallStyle` fix
  (`AbstractVoipCallService.ACTION_HANG_UP`, `SampleVoipCallService`'s `CallStyle.forOngoingCall`):
  after a swipe-to-dismiss the notification must still be posted and the call `Ongoing`.
- **`background-then-foreground-keeps-call`**: Home, wait 5 s, foreground; the call must still be
  `Ongoing` and notified.
- **`process-death-returns-to-idle`**: a genuine `force-stop` (not `am kill`, a no-op against a
  foreground service, see `Device.kill()`) mid-call, then relaunch: a clean `Idle` screen, no
  foreground service, no notification, never a stale "in call" UI.
- **`rotate-then-power-button-during-call`**: the example from a user report (rotate, power button
  off/on, unlock) with no explicit symptom: the oracles judge it.
- **`join-leave-stress`**: join and leave the same call 4 times (`repeat: 4`); each cycle must end in a clean Idle with no service/notification left, and the `memory_growth` oracle compares PSS across cycles.
- **`remote-leaves-ends-call`**: when the only other participant hangs up, the remaining device must end its call too (`last remote participant left -- disconnecting`) and clean up.
- **`negative-control-kill-mid-call`**: expects a live call after killing the app, which can never
  hold; it passes only if the framework reports the issue (`expect_issue: true`).

**Why most flows use two devices:** a real discovery made while building this suite: a single device
joining a Daily.co room alone completes its own local WebRTC join (`"Completed request join: Success"`),
but `DailyCoVoipSdkClient` only reports `VoipCallState.Ongoing` once a *second* participant is
detected in the room. The validator rejects an `Ongoing` expectation with nobody else in the room.

## On LeakCanary noise

`adb_utils.scan_logcat_for_leaks_and_crashes` (used by the `leakcanary` oracle) sums every `"N APPLICATION LEAKS"` LeakCanary
reports across a capture, and the oracle reports a finding if that total is nonzero. It deliberately does
**not** fail on:
- `"Found N object(s) retained, not dumping heap yet"` — below LeakCanary's dump threshold.
- `"All retained objects have been garbage collected"` — a retained object that resolved itself.

Both of those were observed repeatedly in the original manual investigation and self-resolved —
treating them as failures would make this suite flaky for no real signal. Only a completed heap
analysis reporting actual leaked objects counts.

## How the UI is driven (and why it isn't pixel coordinates)

`MainActivity.kt`'s `JoinMeetingScreen` carries `Modifier.testTag(...)` on every interactive
element, plus `Modifier.semantics { testTagsAsResourceId = true }` at the root — without that
second part, `testTag` never reaches the Android accessibility tree outside a `ComposeTestRule`,
and a host-side `uiautomator dump` (what `adb_utils.Device` uses) would see no `resource-id` at
all. With it, `Device.tap_resource_id("join_button")` etc. work the same way across any screen
resolution or layout tweak, instead of breaking the moment someone adjusts a padding value.

There's also a `call_state_label` test tag showing the raw `ConferencerUiState` subclass name
(`"Ongoing"`, `"Idle"`, ...) — unambiguous for a test to assert on, unlike the human-facing
strings ("In call — 00:21") that are free to change wording at any time.

## What's been live-verified vs. what to confirm on your machine

Built and debugged against real emulators, not written blind:

- `testTagsAsResourceId` genuinely surfaces `Modifier.testTag(...)` as `resource-id` in a
  `uiautomator dump` on this Compose version -- confirmed by dumping the UI and grepping for
  `call_state_label`/`meeting_link_field`/`join_button`.
- `Device.join_meeting()` (tap the field, type the URL, **read it back and retry**, dismiss the keyboard, tap Join) correctly
  drives a real app into `Connecting` against a real Daily.co room. The read-back exists because
  `adb shell input text` silently dropped characters on a busy emulator (a 56-character URL arrived
  as `tht`), which surfaced as a 404 from the provider.
- **A real discovery, not an assumption:** a single device joining a room alone never reaches
  `Ongoing` -- see "Why most flows use two devices" above. This was found by watching a live single-device
  run time out, not anticipated up front.
- Booting two emulators sequentially (`boot_emulator()` called twice) wastes wall-clock time and
  made the second boot more prone to timing out; `boot_emulators()` launches both up front and
  waits concurrently instead -- also found live, not anticipated.

**Not fully re-verified end-to-end in this session:** a complete two-emulator run of all four
flows back-to-back in the original session (the scenario rewrite was live-verified; see the commit/PR notes). Booting two low-end (`swiftshader_indirect` software-rendered) emulators
concurrently got noticeably slower on the host used to build this suite as its uptime grew (two
days and counting, heavy sustained use) -- a second emulator that normally registers with adb
within ~40s took several minutes under that load. `boot_timeout_s` defaults to 300s to give this
realistic room, but **the first time you run this suite, budget extra patience (or watch
`adb devices` yourself) rather than assuming a hang means something is broken.** If two
emulators consistently can't both boot within a few minutes on your machine, that's host
resource pressure (CPU/RAM/disk contention under swiftshader), not a bug in the suite -- try
closing other heavy processes, `--headless`, or running `--run rotation-no-leak` alone first
(it's the one this suite matters most for).

## Known limitations / TODOs

- Emulators are booted once per session and *reset* between scenarios (app data cleared, rotation,
  network, screen) rather than re-booted: much faster, but anything outside that reset (system
  settings a scenario changed, a prior `reboot`) could leak between scenarios.
- No CI wiring yet — this needs a runner with KVM (hardware acceleration) for the emulators to
  boot in reasonable time, which most hosted CI doesn't provide without extra configuration.
  TODO if/when this needs to run automatically rather than on demand.
- `rotation-no-leak` rotates once; raise `repeat:` in the file (or `QA_REPEAT=5`) for higher
  confidence against a flakier race.
