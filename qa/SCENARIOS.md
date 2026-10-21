# Scenario guide: from a bug report to a reproducible emulator run

A user says: *"I opened the app, placed a call, rotated the screen, pressed the power button, and then X broke."*
This suite turns that sentence into a short YAML file, runs it on real emulators, and tells you
**whether the flow has an issue and at which step it first showed up**.

```
bug report  ->  qa/scenarios/<name>.yaml  ->  python3 qa/run.py --run <name>  ->  qa/.artifacts/<name>/report.md
```

Contents: [Quick start](#quick-start) · [File format](#file-format) · [Steps](#step-vocabulary) ·
[Expectations](#expectations) · [Oracles](#oracles-always-on-checks) · [One-line form](#one-line-form-for-quick-reproduction) ·
[Template](#template) · [Bug report to scenario](#turning-a-bug-report-into-a-scenario) · [Reports](#reports) ·
[Running](#running) · [Troubleshooting](#troubleshooting) · [Limitations](#limitations-and-honesty-notes) · [Roadmap](#roadmap)

## Quick start

```bash
export ANDROID_HOME=$HOME/Android/Sdk              # fish: set -x ANDROID_HOME ...
export QA_MEETING_URL=https://<your>.daily.co/<test-room>   # never commit a real room URL
pip install -r qa/requirements.txt                 # pytest + pyyaml

python3 qa/run.py --list                           # what scenarios exist
python3 qa/run.py --dry-run --run rotate-then-power-button-during-call   # validate + print the plan, no device
python3 qa/run.py --run rotate-then-power-button-during-call             # run it on two emulators
pytest -m emulator qa/ -k rotate                   # same thing through pytest
```

`--headless` (or `QA_HEADLESS=1`) boots the emulators without a window, for machines without a display.

## File format

One scenario per file in `qa/scenarios/`, and the file name must equal `name:` (so `--run <name>` finds it).

```yaml
name: rotate-then-power-button-during-call     # lowercase letters, digits, - _ ; must match the file name
description: "User report: ..."                 # free text: put the bug report here
tags: [rotation, power]                         # free-form labels
devices: [a, b]                                 # actors; default [a, b]. a = primary, b = helper participant
meeting: env                                    # env = $QA_MEETING_URL (see Roadmap for other sources)
repeat: 1                                       # run the whole flow N times in one session
expect_issue: false                             # true = negative control (see below); default false
steps:
  - a: launch_app
  - a: join_call
  - b: launch_app
  - b: join_call
  - wait_for: {device: a, call_state: Ongoing, timeout: 45}
  - a: {rotate: landscape}
  - a: power_button
  - wait: 5s
  - a: power_button
  - a: unlock
  - expect: {device: a, call_state: Ongoing, notification: posted}
checks: default                                 # oracles: default | [list to run only these] | {disable: [...], <oracle>: {options}}
```

**Devices.** `a` boots the AVD `leak_test_low_1` (512 MB, 1 core), `b` boots `leak_test_low_2`
(override with `QA_AVD_A` / `QA_AVD_B`). At most two emulators run at once. **Why `b` exists:** the
sample app only reaches `Ongoing` once a *second* participant is in the room (a lone participant
sits in `Connecting`), so any flow that needs a live call has `b` launch and join too.
A scenario that declares only `[a]` never boots `b`.

**Step forms.** Every step is one `key: value` pair:

| Written as | Meaning |
|---|---|
| `- a: launch_app` | a step with no arguments |
| `- a: rotate landscape` | step name, then a single argument |
| `- a: {rotate: landscape}` | same, mapping form |
| `- a: {tap: {text: Leave call}}` | several/named arguments |
| `- all: launch_app` | the step on every declared device, in order (a then b) |
| `- wait: 5s` | sleep (`500ms`, `5s`, `2m`, or a bare number of seconds) |
| `- wait_for: {device: a, call_state: Ongoing, timeout: 45}` | poll until the conditions hold, else fail (default timeout 30s) |
| `- expect: {device: a, notification: posted}` | check once, right now |

YAML gotcha: an unquoted `off`/`on` is read as a boolean by YAML 1.1. `network: off` works anyway (the
validator maps it back), but quote anything else that looks like a boolean.

**Failure semantics.**
- A failed `expect` is a *finding*; the flow continues (later steps may show more).
- A timed-out `wait_for`, or a step that cannot be carried out (a button is not on screen), is a finding
  and **aborts the rest of the flow**: later steps are recorded as `skipped`, because they are meaningless
  once a precondition failed. Oracles still run on whatever happened.
- `repeat: N` runs the steps N times back to back in one session (emulators booted once). Make the flow
  end in the state it began in (e.g. `leave_call`), or later iterations start from a different state.
- `expect_issue: true` marks a **negative control**: the run counts as *intended* only if it finds an
  issue. Used to prove the framework can fail a flow (`negative-control-kill-mid-call`).

**Validation.** `--dry-run` (and `pytest qa/`, offline) validates every file and reports *all* problems as
`file:line: error: message`: unknown step (with a "did you mean"), unknown device, bad argument,
unknown condition/check/option, duplicate keys, YAML syntax errors, and **unreachable expectations**:
`call_state: Ongoing` on a device that never ran `join_call` (or already left), `Ongoing` with nobody
else in the room, `join_call` before `launch_app`. Things that can never pass but are legitimate for a
negative control (expecting a live call after `kill_process`) are *warnings*, shown in `--dry-run` and the report.

## Step vocabulary

Device steps are written `<device>: <step> [args]`. `python3 qa/run.py --list-steps` prints this table from the code.

| Step | Arguments | What it does (how) |
|---|---|---|
| `launch_app` | - | Starts the app like the launcher icon (`am start` MAIN/LAUNCHER): cold start, or resumes the existing task. Waits up to 15 s for the UI. |
| `join_call` | - | From Idle: types `$QA_MEETING_URL` into the field, **reads it back and retries** (adb `input text` drops characters on busy emulators), dismisses the keyboard, taps Join. Does not wait for Connecting/Ongoing: use `wait_for`. |
| `leave_call` (alias `hang_up`) | `mode`: `required` (default) / `if_present` | Via the UI: taps `leave_button` (Ongoing) or `cancel_button` (Connecting). `if_present` does nothing instead of failing when neither is on screen (e.g. the sample app already ended the call because the last remote participant left). |
| `rotate` | `to`: `portrait`, `landscape`, `reverse_portrait`, `reverse_landscape`, `auto` | Pins `user_rotation` (turns auto-rotate off); `auto` hands control back to the accelerometer. |
| `power_button` | - | `KEYCODE_POWER`: toggles screen off/on, exactly like the physical key. |
| `screen_off` / `screen_on` | - | Idempotent `KEYCODE_SLEEP` / `KEYCODE_WAKEUP` (does not unlock). |
| `unlock` | - | Wakes the screen if asleep and dismisses a swipe/no-security keyguard (`wm dismiss-keyguard`). No PIN/pattern support. |
| `press_home`, `press_back`, `open_recents` | - | Key events HOME, BACK, APP_SWITCH. |
| `bring_to_foreground` | `via`: `launcher` (default), `notification` | `launcher`: resume the task like tapping the icon. `notification`: open the shade and tap the call notification. |
| `kill_process` | `how`: `force_stop` (default), `am_kill` | Genuine process death via `am force-stop` (verified: the step fails if the pid survives). `am_kill` is Android's polite kill and is **ignored while a foreground service runs**. |
| `swipe_notification` | - | Opens the shade and swipes the call notification sideways. Fails the step if the app has no call notification posted. The row is located through uiautomator, or, because uiautomator cannot dump a shade containing the call's ticking chronometer ("could not get idle state"), at 34% of the screen height (the first row on the 480x800 AVDs); the step detail says which was used. |
| `tap` | `id=<resource id>` or `text=<exact text>` (a bare value means id) | uiautomator lookup, tap at the node's centre. |
| `input_text` | `text`, optional `into=<resource id>` | `input text` (spaces become `%s`); `into` taps the field first. |
| `network` | `state`: `off`/`on`; `via`: `airplane` (default), `wifi`, `data`, `all` | `airplane`: `cmd connectivity airplane-mode` (API 31+), then waits until `dumpsys connectivity` agrees. `wifi`/`data`/`all`: `svc`. See limitations: on emulators wifi-off alone just falls back to the emulated cellular link. |
| `low_memory` | `level` (default `RUNNING_CRITICAL`; also `HIDDEN RUNNING_MODERATE BACKGROUND RUNNING_LOW MODERATE COMPLETE`) | `am send-trim-memory`: the app gets `onTrimMemory(level)`. It does not kill anything. |
| `reboot` | - | `adb reboot`, waits for boot. Heavy (about a minute); the process is gone afterwards; logcat capture restarts into `logcat_<dev>.after-rebootN.log`. |
| `screenshot` | optional `label` | PNG into `<artifacts>/screenshots/`. |
| `memory_snapshot` | optional `label` | Records the app's PSS (`dumpsys meminfo`) into the report's memory table. |
| `wait` | duration | Sleep (not device-addressed). |
| `wait_for` | `device`, conditions, `timeout` | Poll the expectation conditions (below) until true or timeout. |

## Expectations

Used by `expect:` (once) and `wait_for:` (polled). All listed conditions must hold.

| Condition | Values | Reads |
|---|---|---|
| `call_state` | `Idle Incoming Connecting Ongoing Disconnecting Error`, or a list = any of | the app's `call_state_label` test hook (uiautomator). **Needs the app on screen and the screen on**; otherwise the expectation fails with "UI not readable". |
| `notification` | `posted` / `absent` | `dumpsys notification`: the app's **call** notification, i.e. a record of the package flagged `FOREGROUND_SERVICE` or `category=call`. The app's other notifications (LeakCanary posts its own in debug builds) are ignored. |
| `process` | `alive` / `dead` | `ps` for the exact process name |
| `foreground_service` | `running` / `stopped` | a foreground `ServiceRecord` of the app in `dumpsys activity services` |
| `ui` | `{id: <resource id>, equals: <text>}` or `contains:`; a list is allowed | the text of that node |

```yaml
- expect: {device: a, call_state: [Idle, Error], foreground_service: stopped, ui: {id: call_state_label, equals: Idle}}
```

## Oracles: always-on checks

Oracles run **after every step** (and at the end) whether or not you wrote an expectation: they are what
make "this flow has an issue" a verdict rather than a pile of logs. Each finding records the oracle,
severity, the **step and timestamp where it first appeared**, an evidence excerpt (logcat lines or the
observed state), and a screenshot plus UI dump taken at that moment. The same problem seen again is one
finding with an occurrence count.

Snapshot oracles (`service_notification`, `stale_call_ui`, `wake_locks`) are **debounced**: real
transitions are asynchronous (the service stops a moment after the UI says Idle), so a hit is re-checked
after `settle` seconds (default 2) and only reported if it still holds.

| Oracle | Severity | Flags | Does not catch | Reliability |
|---|---|---|---|---|
| `crash` | critical | `FATAL EXCEPTION` whose `Process:` is the app; native `Fatal signal` in an app-named process | other apps' crashes (ignored on purpose); handled exceptions logged at error level | High |
| `anr` | critical | `ANR in <package>` from ActivityManager | jank that does not trip the 5 s/10 s ANR timeouts | High |
| `leakcanary` | high | LeakCanary `N APPLICATION LEAKS`, N>0 (reuses `scan_logcat_for_leaks_and_crashes`) | *transient* "retained, not dumping heap yet" (deliberately: GC-timing noise); leaks LeakCanary has not analysed yet when the run ends | High when it fires; can miss late dumps |
| `leaked_resources` | high | `Activity <app> has leaked window / ServiceConnection / IntentReceiver` | leaks the platform does not report | High |
| `service_notification` | high | UI Connecting/Ongoing but no notification, or service not foreground; UI Idle but the foreground service or notification still exists | states where the UI is unreadable (screen off, other app in front): no verdict is made then | Medium (debounced; depends on the UI hook) |
| `stale_call_ui` | high | UI shows Connecting/Ongoing while no call service is running and the process is alive | a UI stuck on Idle/Error; stale UI while the screen is off | Medium (debounced; depends on the UI hook) |
| `wake_locks` | medium | An app-owned wake lock (`dumpsys power`, including locks attributed to the app's uid through WorkSource, e.g. audioserver's `AudioIn`) still held after the call ended | locks held by other processes; short-lived locks released within `settle` | Medium |
| `process_death` | critical | The app's pid changed or vanished on a step that is not `kill_process`/`reboot`. Also polls the *other* device each step, so a helper dying during someone else's step is attributed correctly | a crash that restarts within one step and keeps the same pid (impossible), or a death between two polls attributed to the next step | High |
| `memory_growth` | medium | PSS (`dumpsys meminfo`) after the last of `repeat` iterations vs after the first (the first is the warm-up baseline) exceeds `max_growth_mb` (default 40, `QA_MEMORY_GROWTH_MB` or `checks:` option) | slow leaks below the threshold; anything with `repeat` < `min_iterations` (default 3) (reported as a note, not a finding) | **Low**: PSS is noisy (GC timing, JIT, caches). Use `repeat: 5+` and treat it as a smoke alarm; confirm with `leakcanary` |

Configuring checks:

```yaml
checks: default                     # everything
checks: [crash, anr]                # ONLY these
checks:
  disable: [wake_locks]
  memory_growth: {max_growth_mb: 25, min_iterations: 4}
  service_notification: {settle: 4}
```

Not shipped, on purpose: there is no check for "audio is actually flowing" or "the remote participant can hear
us" (no reliable adb signal on an emulator), and no oracle for jank/frame drops.

## One-line form for quick reproduction

```bash
python3 qa/run.py --flow "a:launch_app, a:join_call, b:launch_app, b:join_call, wait_for a call_state=Ongoing, a:rotate landscape, a:power_button, wait 5s, a:power_button, a:unlock, expect a call_state=Ongoing" \
    --name my-bug --save qa/scenarios/my-bug.yaml
```

It is validated exactly like a file (same errors, same default oracles), prints the **equivalent YAML**
(so you can commit it as a regression scenario; `--save` writes it for you), then runs it. Add `--dry-run` to only validate.

Grammar: comma-separated items.

| Item | Example |
|---|---|
| `<device>:<step> [arg]` | `a:rotate landscape`, `a:tap text=Leave call`, `all:launch_app` |
| `wait <duration>` | `wait 5s` |
| `wait_for <device> k=v ...` | `wait_for a call_state=Ongoing timeout=45` |
| `expect <device> k=v ...` | `expect a call_state=Ongoing notification=posted process=alive` |

`call_state=Idle|Error` means any-of; `ui.call_state_label=Idle` means equals, `ui.call_state_label~=Idl` contains.
Limits: no commas inside a value; use a YAML file for those.

## Template

Copy to `qa/scenarios/<name>.yaml` and edit. Delete what you do not need.

```yaml
name: my-bug-name                     # == file name
description: "Paste the user's report here, verbatim."
tags: [rotation]
devices: [a, b]                       # [a] for flows that never need a live call
meeting: env
repeat: 1
steps:
  # 1. get into the call (b makes the sample app reach Ongoing)
  - a: launch_app
  - a: join_call
  - b: launch_app
  - b: join_call
  - wait_for: {device: a, call_state: Ongoing, timeout: 45}
  # 2. what the user did, in order
  - a: {rotate: landscape}
  # 3. what should still be true
  - expect: {device: a, call_state: Ongoing, notification: posted}
checks: default
```

## Turning a bug report into a scenario

1. **Quote the report** in `description:`; it travels with the regression test.
2. **Write the setup** that gets to the point where the user acted. For a call, that is the five lines of the template (launch/join both, `wait_for ... Ongoing`).
3. **Translate each user action in order** with the cheat sheet below. One action, one step; add `wait: 2s`
   where the user paused (backgrounding bugs often need the app to sit for a while).
4. **State what "broke" means** with an `expect:`. If the report is vague ("it just broke", "weird screen"),
   skip this: the oracles catch crashes, ANRs, leaks, stale in-call UI and stuck services on their own. Add an `expect` only for a specific symptom you can name ("the call dropped" = `call_state: Ongoing`).
5. **Dry-run** (`--dry-run`) to catch typos and unreachable steps.
6. **Run it**; read `qa/.artifacts/<name>/report.md`: verdict, first-appearance step, evidence, screenshot.
7. **Not reproducing?** Raise `repeat:` (races need several tries), add `wait:` pauses, vary the order or devices, or add `low_memory`/`network` steps if the user was on a bad phone/link.
8. **Reproduces?** Fix the bug, keep the file: it is now the regression test (`pytest -m emulator qa/ -k <name>`).

### Cheat sheet: bug-report phrase to step

| The user said | Step |
|---|---|
| "opened the app" | `a: launch_app` |
| "placed / joined a call" | `a: join_call` (plus the helper joining: `b: launch_app`, `b: join_call`) |
| "waited for the call to connect" | `wait_for: {device: a, call_state: Ongoing}` |
| "hung up / left the call" | `a: leave_call` |
| "cancelled while connecting" | `a: leave_call` (taps Cancel in Connecting) |
| "turned the phone sideways" | `a: rotate landscape` |
| "turned it back / upright" | `a: rotate portrait` |
| "turned it upside down" | `a: rotate reverse_portrait` |
| "pressed the power button" (to lock) | `a: power_button` |
| "pressed power again / woke it up" | `a: power_button` then `a: unlock` |
| "locked the phone and left it" | `a: screen_off`, `wait: 30s` |
| "unlocked the phone" | `a: unlock` |
| "went to the home screen" | `a: press_home` |
| "came back to the app" / "tapped the app icon" | `a: bring_to_foreground` |
| "tapped the notification to return" | `a: {bring_to_foreground: notification}` |
| "went home and came back" | `a: press_home` + `a: bring_to_foreground` |
| "pressed back" | `a: press_back` |
| "opened the app switcher / recents" | `a: open_recents` |
| "swiped the notification away" | `a: swipe_notification` |
| "swiped the app away from recents" / "force-closed it" | `a: kill_process` |
| "the app was killed / restarted by the system" | `a: kill_process` then `a: launch_app` (closest available; see limitations) |
| "my phone is low on memory" / "lots of apps open" | `a: {low_memory: RUNNING_CRITICAL}` |
| "lost signal / turned on airplane mode" | `a: {network: off}` |
| "got signal back" | `a: {network: on}` |
| "wifi dropped" | `a: {network: {state: off, via: wifi}}` (see limitations) |
| "restarted the phone" | `a: reboot` |
| "tapped Leave / tapped the mic switch" | `a: {tap: leave_button}`, `a: {tap: mic_switch}` (resource ids), or `a: {tap: {text: Leave call}}` |
| "typed something" | `a: {input_text: {text: hello, into: some_field}}` |
| "it did this every time / after a few calls" | `repeat: 5` (flow must end back at Idle) |
| "then X broke" | oracles + an `expect` for X |
| "the other person ..." | the same steps on `b:` |

## Reports

Each run writes `qa/.artifacts/<scenario>/` (override with `QA_ARTIFACTS_DIR` / `--artifacts-dir`; the directory is wiped at the start of each run):

- `report.md`: one-line verdict (**PASS** / **ISSUE FOUND**), findings (oracle, severity, step, timestamp, evidence, links), the step-by-step timeline (timestamp, status, and per-step device state: call state, notification, pid, foreground service), memory table, notes/warnings, links to artifacts, and the equivalent steps.
- `report.json`: the same, machine-readable (`verdict`, `findings[]`, `timeline[]` with `snapshots`, `plan`, `memory`).
- `logcat_<dev>.log`: full capture (`-v time`, error-level for all tags plus LeakCanary), `findings/NN-<oracle>-<dev>.{png,xml}`: screenshot + uiautomator dump at the moment each finding first appeared, `screenshots/`: from `screenshot` steps.

**Exit code** of `run.py`: `0` every scenario behaved as intended (PASS, or ISSUE FOUND for an `expect_issue` control), `1` an issue was found (or a negative control found none), `2` bad input/setup. Under pytest, a scenario test fails in exactly the same cases.

The timeline's "ui?" for the call state means the app's UI was not readable at that moment (screen off, app in the background, process dead); that is expected after `power_button` or `press_home`.

## Running

| Command | |
|---|---|
| `python3 qa/run.py --list` | scenarios and their tags; invalid files are listed with errors |
| `--run <name-or-path>` (repeatable, or `all`) | run scenarios (one emulator session for all of them) |
| `--flow "<compact>" [--name n] [--save path]` | ad-hoc flow |
| `--dry-run` | validate and print the resolved plan; touches no device (does not even need `ANDROID_HOME`) |
| `--repeat N` | override `repeat` (pytest: `QA_REPEAT=N`) |
| `--keep-emulators` | leave emulators running (`QA_KEEP_EMULATORS=1`) |
| `--skip-build` | reuse the APK on disk (`QA_SKIP_BUILD=1`) |
| `--headless` | no emulator window (`QA_HEADLESS=1`) |
| `--meeting-url URL` | instead of `QA_MEETING_URL` |
| `--list-steps` | step vocabulary and oracles |
| `QA_LOGCAT_FILTERS="OkHttp:V ..."` | extra logcat filter specs appended to the capture, for a one-off diagnosis. The capture already includes the library/sample Timber tags (`ConferencerBinding`, `AbstractVoipCallService`, `SampleVoipCallService`, `DailyCoVoipSdkClient`) at full verbosity |
| `pytest -m emulator qa/ [-k name]` | every scenario file is one test, id = scenario name |
| `pytest qa/` | offline: unit tests + validation of every scenario file; live tests deselected/skipped without `-m emulator` |

Between scenarios the harness waits `QA_ROOM_DRAIN_S` seconds (default 30) after any scenario that joined a call, because the provider keeps force-stopped participants in the room for a while; without that, the next scenario sees those ghosts as remote participants and the sample app ends its call when they time out (seen live: a device went Idle by itself 8 s after joining). It also resets each emulator (force-stop, `pm clear` + permissions re-granted, portrait, network on, screen on, home) instead of rebooting, so a scenario never inherits the previous one's state but a session of six scenarios boots only once.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| everything skipped | `ANDROID_HOME` unset (pytest prints the reason via `-rs`) or `QA_MEETING_URL` unset |
| `could not type the text into 'meeting_link_field' reliably` | the emulator is too busy for `adb input text`; close other heavy processes, retry. The step already types in short chunks, reads the field back and retries 4 times |
| call reaches `Error` with `404 Not Found` in logcat | the room does not exist/expired (`Failed to fetch room information`); use a live room |
| `wait_for ... Ongoing` times out, state stays `Connecting` | only one participant in the room (add `b: join_call`), or network/room problem; check `logcat_a.log` for `DailyCoVoipSdkClient` |
| `call_state: ... UI is not readable` | screen is off or another app is in front; add `screen_on`/`unlock`/`bring_to_foreground` before the expectation |
| emulator will not boot | cold boots of the 512 MB AVD take 40 s to several minutes under load; try `--headless`, free RAM; `boot_timeout_s` is 300 s |
| a device went `Idle` by itself shortly after `join_call` | ghost participants of the previous scenario's room; raise `QA_ROOM_DRAIN_S`, or use a fresh room |
| a validation error you do not understand | the message has `file:line`; the vocabulary is in `--list-steps` |
| stray emulators after a killed run | `pgrep -a qemu-system`; the harness registers an `atexit` shutdown but a `kill -9` bypasses it |
| report says `ui?` everywhere | the app crashed or never launched; read `logcat_<dev>.log` and the `crash`/`process_death` findings |

## Limitations and honesty notes

- **Real OS memory kills are not simulated.** `kill_process` is `am force-stop`, which also clears the app's notification/alarms and marks it user-stopped; an OOM kill of a foreground-service app is not reproducible reliably over adb. `low_memory` only delivers `onTrimMemory`.
- **`network` on emulators.** `airplane` and `all` genuinely cut connectivity (verified through `dumpsys connectivity`). `wifi`/`data` alone toggles one of two emulated links, so the device often keeps working over the other. Packet loss/latency (`adb emu network ...`) is not exposed as a step yet.
- **`rotate` pins `user_rotation`**; a physical accelerometer flip is not reproducible. The app's own orientation policy is exercised the same way.
- **`unlock`** handles a no-security keyguard only. The emulators created by `qa/scripts/create_low_end_avds.sh` have none, so the lock screen mostly never appears; the power-button flow still exercises screen off/on, `onStop`/`onStart` and the keyguard-less resume path.
- **Call state comes from the sample app's `call_state_label` hook**, so it is unreadable while the app is not on screen; snapshot oracles that depend on it make no judgement then (stated in the oracle table). A consumer app without that hook can use `process`, `notification` and `foreground_service` conditions plus the logcat oracles.
- **LeakCanary** only reports after it has analysed a heap dump, which can lag the run; the final drain waits one second. `memory_growth` is a coarse alarm, not a leak proof.
- Two-participant requirement, `Ongoing` only with a helper: a property of `DailyCoVoipSdkClient`, not of the harness.
- Timeline timestamps are host time; logcat evidence lines carry the device clock.

## Roadmap

Not implemented, reserved on purpose:

- **`meeting: auto`**: create a throwaway room from a provider API key instead of `QA_MEETING_URL`. The schema accepts the word and rejects it with a clear "not implemented yet" message, so scenarios written today will not need renaming.
- **Random-sequence mode**: seeded random steps from the vocabulary, run against the same oracles, with the failing sequence shrunk to a minimal scenario file. The hooks exist: `Scenario`/`Step` are plain dataclasses that do not care where they came from; `executor.execute_action` / `check_conditions` take a `Step` and a device; oracles are pure functions over text or a `Snapshot`; `run_scenario` accepts any `Scenario` and a `RunConfig.on_step_done` callback so a generator can steer from each step's result; `compact.to_yaml` writes any scenario back out as a file.
- More providers for `meeting:`; packet-loss/latency steps via the emulator console.

## Layout

```
qa/scenarios/*.yaml     scenario files (one per file)
qa/run.py               CLI
qa/samvaad_qa/          vocab.py (step table) schema.py (load+validate) compact.py (one-liner, YAML writer)
                        executor.py (steps, expectations) oracles.py runner.py report.py session.py (emulators)
qa/adb_utils.py         Device: everything adb (shared with the old suite)
qa/test_scenarios.py    pytest: one test per scenario file
qa/tests/               offline unit tests (no emulator): parser, oracles, runner with a fake Device, reports, CLI
```
