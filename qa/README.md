# qa/ — emulator-driven E2E lifecycle & memory-leak suite

Automates the manual emulator investigation from the memory-leak session: two low-end/low-
resource emulators, a real provider call, screen rotation, notification swipe-dismiss, process
backgrounding and process death, all watched via LeakCanary's logcat output.

This is **not** Android instrumented tests. Nothing here could be: booting two emulators,
rotating the screen via `settings put system user_rotation`, swiping a system notification, and
killing the app's process over adb are all host/OS-level operations a single on-device
instrumentation process has no way to orchestrate. This is a host-side `pytest` suite that drives
real emulators over `adb`, same as a human tester would.

## Prerequisites

- `ANDROID_HOME` (or `ANDROID_SDK_ROOT`) set, with `emulator` and `platform-tools` present.
  **If everything silently skips with no obvious reason** (`4 skipped` and nothing else useful),
  this is almost always it — `android_sdk` skips the whole session before even checking
  `QA_MEETING_URL` if it isn't set. `pytest.ini` sets `-rs` by default specifically so the skip
  reason always prints; if you don't see it, you're on an older local pytest config that
  overrides it.
  In `bash`/`zsh`: `export ANDROID_HOME=/path/to/sdk`. In `fish`: `set -x ANDROID_HOME /path/to/sdk`
  (fish doesn't support `export`, and inline `VAR=val cmd` prefixes don't persist across lines the
  way a `source`d script does either).
- Two AVDs named `leak_test_low_1` / `leak_test_low_2`. If you don't have them yet:
  ```
  ANDROID_HOME=/path/to/sdk ./qa/scripts/create_low_end_avds.sh
  ```
  This requires an x86_64 system image already installed (`sdkmanager "system-images;android-<API>;google_apis;x86_64"`
  if you don't have one) — it reuses whichever is already present rather than downloading a new one.
- Python 3.9+, and `pip install -r qa/requirements.txt` (just `pytest` — no extra dependencies;
  everything else is stdlib `subprocess`/`xml`).

## Running

```
cd qa
python3 -m venv .venv
source .venv/bin/activate        # bash/zsh
# source .venv/bin/activate.fish   # fish
pip install -r requirements.txt
QA_MEETING_URL="https://your-test-room.daily.co/xxxxx" pytest -m emulator .
```

Every test that needs a real call is skipped with a clear reason if `QA_MEETING_URL` isn't set —
see the `requires_meeting_url` marker in `test_call_lifecycle.py`. Never hardcode a real external
room URL into the checked-in test file. Without one set, you can still sanity-check the suite's
shape with `pytest --collect-only -m emulator .` -- it lists every test without booting anything.

Common commands while iterating:

```
# Just one flow, verbose, with print() output visible live:
QA_MEETING_URL=... pytest -m emulator -v -s test_call_lifecycle.py::test_call_notification_is_not_swipe_dismissable_while_ongoing

# Skip rebuilding the APK every run -- reuse what's already built:
QA_SKIP_BUILD=1 QA_MEETING_URL=... pytest -m emulator .

# Leave emulators running after a failure, to poke at the exact failure state by hand:
QA_KEEP_EMULATORS=1 QA_MEETING_URL=... pytest -m emulator .
```

After any run, check `qa/.artifacts/<test_name>/logcat_*.log` for the captured logcat per test --
that's what `leak_watch` scanned for confirmed LeakCanary leaks and crashes, kept on disk
specifically so a failure doesn't require reproducing it live to diagnose.

Useful environment variables:

| Variable | Effect |
|---|---|
| `QA_MEETING_URL` | Required for any test that joins a real call. |
| `QA_SKIP_BUILD=1` | Reuse the APK already on disk instead of rebuilding (`:app:assembleDebug`) at the start of the session — faster when iterating on test flows themselves. |
| `QA_KEEP_EMULATORS=1` | Leave booted emulators running after the test session instead of killing them — handy for poking at the failure state by hand. |
| `QA_ARTIFACTS_DIR` | Where per-test logcat captures/screenshots land (default: `qa/.artifacts/`). |

## What each flow covers

- **`test_two_devices_join_same_call_survive_rotation_without_leak`** — the original investigation's
  main scenario: two low-end devices, both join the same room, both rotate, LeakCanary's logcat
  is scanned for a confirmed `"N APPLICATION LEAKS"` block (not just a transient "retained, not
  dumping yet" line — see "On LeakCanary noise" below).
- **`test_call_notification_is_not_swipe_dismissable_while_ongoing`** — regression test for the
  `CallStyle` fix (`AbstractVoipCallService.ACTION_HANG_UP`, `SampleVoipCallService`'s
  `CallStyle.forOngoingCall`). Before that fix, `setOngoing(true)` alone stopped protecting the
  notification from a swipe on Android 13+.
- **`test_backgrounding_then_foreground_preserves_ongoing_call`** — press Home, wait N seconds,
  foreground again; the call must still be live, still notified, with no crash or leak in between.
- **`test_process_death_mid_call_relaunches_to_clean_idle`** — a genuine `force-stop` (not
  `am kill`, which is a no-op against a process running a foreground service — see
  `Device.kill()`'s docstring) mid-call, then relaunch. Must land on a clean `Idle` screen, never
  a stale "in call" UI — there's no local media session left to reattach to after real process
  death.

See the `TODO` comments at the bottom of `test_call_lifecycle.py` for flows worth adding next
(mic/recording-toggle races, multi-cycle join/leave stress, a real OOM-kill simulation).

**Why every test uses two devices, even ones that only assert against one:** a real discovery
made while building this suite, not an upfront assumption — a single device joining a Daily.co
room alone completes its own local WebRTC join successfully (confirmed in logcat: `"Completed
request join: Success"`), but `DailyCoVoipSdkClient` only reports `VoipCallState.Ongoing` once a
*second* participant is detected in the room. So the notification/backgrounding/process-death
tests all use `join_as_pair()`, which joins both devices and waits for the first to reach
`Ongoing` before the test body runs — the second device just sits there as a silent participant.

## On LeakCanary noise

`adb_utils.scan_logcat_for_leaks_and_crashes` sums every `"N APPLICATION LEAKS"` LeakCanary
reports across a capture, and fails the test if that total is nonzero. It deliberately does
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
- `Device.join_meeting()` (tap the field, type the URL, dismiss the keyboard, tap Join) correctly
  drives a real app into `Connecting` against a real Daily.co room.
- **A real discovery, not an assumption:** a single device joining a room alone never reaches
  `Ongoing` -- see `join_as_pair()`'s docstring. This was found by watching a live single-device
  run time out, not anticipated up front, and is why every test here uses two devices.
- Booting two emulators sequentially (`boot_emulator()` called twice) wastes wall-clock time and
  made the second boot more prone to timing out; `boot_emulators()` launches both up front and
  waits concurrently instead -- also found live, not anticipated.

**Not fully re-verified end-to-end in this session:** a complete two-emulator run of all four
flows back-to-back. Booting two low-end (`swiftshader_indirect` software-rendered) emulators
concurrently got noticeably slower on the host used to build this suite as its uptime grew (two
days and counting, heavy sustained use) -- a second emulator that normally registers with adb
within ~40s took several minutes under that load. `boot_timeout_s` defaults to 300s to give this
realistic room, but **the first time you run this suite, budget extra patience (or watch
`adb devices` yourself) rather than assuming a hang means something is broken.** If two
emulators consistently can't both boot within a few minutes on your machine, that's host
resource pressure (CPU/RAM/disk contention under swiftshader), not a bug in the suite -- try
closing other heavy processes, or running `test_two_devices_join_same_call_survive_rotation_without_leak`
alone first (it's the one this suite matters most for).

## Known limitations / TODOs

- Each test boots its own emulator(s) rather than sharing a session-scoped device — slower, but
  a failure in one test can't leave stale state for the next. Worth revisiting if this suite grows
  enough that boot time dominates the run.
- No CI wiring yet — this needs a runner with KVM (hardware acceleration) for the emulators to
  boot in reasonable time, which most hosted CI doesn't provide without extra configuration.
  TODO if/when this needs to run automatically rather than on demand.
- `test_two_devices_join_same_call_survive_rotation_without_leak` rotates once; see the `TODO` in
  that test for adding more cycles if you want higher confidence against a flakier race.
