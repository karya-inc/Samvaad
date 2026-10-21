"""Step executor: maps each vocabulary step onto Device calls, and evaluates expectations.

Deliberately knows nothing about files, reports or oracles -- it takes a Step and a Device and
either returns a short detail string or raises StepFailure. That keeps it reusable by anything
that can build Steps (today the YAML loader and `--flow`; a future seeded random generator would
call the same functions).

Device duck-type used here (adb_utils.Device implements it; qa/tests/fake_device.py fakes it):
  launch_app join_meeting tap_resource_id tap_text find_by_resource_id text_of_resource_id
  call_state_label set_rotation press_power screen_off screen_on unlock press_home press_back
  open_recents tap_notification swipe_notification kill_process pid input_text set_network
  has_network trim_memory reboot screenshot pss_kb notification_posted services
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .model import Step


class StepFailure(Exception):
    """A step could not be carried out (UI node missing, command rejected, ...)."""


@dataclass
class ExecEnv:
    """Everything a step needs besides the device: where to put files, the meeting URL, and
    injectable sleep (unit tests replace it so they run instantly)."""

    meeting_url: str | None
    artifacts_dir: Path
    iteration: int = 1
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic  # unit tests pass a fake that sleep() advances
    memory: dict = field(default_factory=dict)  # device name -> [{"label", "iteration", "pss_kb"}]


def _need_node(node: object, what: str) -> None:
    if node is None:
        raise StepFailure(f"{what} not found on screen")


def _launch_app(env: ExecEnv, name: str, dev, args: dict) -> str | None:
    dev.launch_app()
    if dev.find_by_resource_id("call_state_label", timeout=15) is None:
        raise StepFailure("app UI (call_state_label) did not appear within 15s of launch")
    return None


def _join_call(env, name, dev, args):
    if not env.meeting_url:
        raise StepFailure("no meeting URL: set QA_MEETING_URL (or pass --meeting-url)")
    try:
        dev.join_meeting(env.meeting_url)
    except AssertionError as exc:
        raise StepFailure(f"could not drive the Join UI: {exc}") from None
    return None


def _leave_call(env, name, dev, args):
    for rid in ("leave_button", "cancel_button"):
        node = dev.find_by_resource_id(rid, timeout=4)
        if node is not None:
            dev.tap_resource_id(rid, timeout=2)
            return f"tapped {rid}"
    if args.get("mode") == "if_present":
        return "no leave/cancel button on screen: nothing to do"
    raise StepFailure(f"neither leave_button nor cancel_button is on screen (call_state={dev.call_state_label()})")


def _simple(method: str, pause: float = 1.0):
    def run(env, name, dev, args):
        getattr(dev, method)()
        env.sleep(pause)
    return run


def _rotate(env, name, dev, args):
    dev.set_rotation(args["to"])
    env.sleep(2.0)  # let the Activity finish its configuration change


def _bring_to_foreground(env, name, dev, args):
    if args.get("via") == "notification":
        if not dev.tap_notification():
            raise StepFailure("the call notification is not in the notification shade")
    else:
        dev.launch_app()
    env.sleep(1.5)


def _kill_process(env, name, dev, args):
    before = dev.pid()
    dev.kill_process(args.get("how", "force_stop"))
    env.sleep(1.0)
    after = dev.pid()
    if args.get("how", "force_stop") == "force_stop" and after is not None:
        raise StepFailure(f"force-stop did not kill the process (pid {before} -> {after})")
    return f"pid {before} -> {after}"


def _swipe_notification(env, name, dev, args):
    method = dev.swipe_notification()
    if not method:
        raise StepFailure("the app has no notification posted, nothing to swipe")
    return f"swiped (row located via {method})"


def _tap(env, name, dev, args):
    try:
        if "id" in args:
            dev.tap_resource_id(args["id"], timeout=10)
        else:
            dev.tap_text(args["text"], timeout=10)
    except AssertionError as exc:
        raise StepFailure(str(exc)) from None
    env.sleep(0.5)


def _input_text(env, name, dev, args):
    try:
        if args.get("into"):
            dev.tap_resource_id(args["into"], timeout=10)
    except AssertionError as exc:
        raise StepFailure(str(exc)) from None
    dev.input_text(args["text"].replace(" ", "%s"))
    env.sleep(0.5)


def _network(env, name, dev, args):
    on, via = args["state"] == "on", args.get("via", "airplane")
    dev.set_network(on, via)
    if via in ("airplane", "all"):  # the only modes that genuinely cut the emulator's connectivity
        deadline = env.clock() + (30 if on else 10)
        while env.clock() < deadline:
            if dev.has_network() == on:
                return f"network {'restored' if on else 'cut'}"
            env.sleep(1.0)
        raise StepFailure(f"connectivity did not become {'available' if on else 'unavailable'} after network {args['state']}")
    env.sleep(2.0)
    return f"svc {via} {args['state']} (note: emulator falls back to its other link)"


def _low_memory(env, name, dev, args):
    out = dev.trim_memory(args.get("level", "RUNNING_CRITICAL"))
    if out:
        raise StepFailure(f"am send-trim-memory failed: {out}")
    env.sleep(1.0)


def _reboot(env, name, dev, args):
    dev.reboot()


def _screenshot(env, name, dev, args):
    shots = env.artifacts_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    label = args.get("label") or "shot"
    path = shots / f"i{env.iteration}-{name}-{label}.png"
    dev.screenshot(str(path))
    return str(path.relative_to(env.artifacts_dir))


def _memory_snapshot(env, name, dev, args):
    pss = dev.pss_kb()
    if pss is None:
        raise StepFailure("app process not running: no PSS to record")
    env.memory.setdefault(name, []).append({"label": args.get("label") or "snapshot", "iteration": env.iteration, "pss_kb": pss})
    return f"PSS {pss / 1024:.1f} MB"


ACTIONS: dict[str, Callable] = {
    "launch_app": _launch_app,
    "join_call": _join_call,
    "leave_call": _leave_call,
    "rotate": _rotate,
    "power_button": _simple("press_power"),
    "screen_off": _simple("screen_off"),
    "screen_on": _simple("screen_on"),
    "unlock": _simple("unlock", 0.5),
    "press_home": _simple("press_home"),
    "press_back": _simple("press_back"),
    "open_recents": _simple("open_recents", 1.5),
    "bring_to_foreground": _bring_to_foreground,
    "kill_process": _kill_process,
    "swipe_notification": _swipe_notification,
    "tap": _tap,
    "input_text": _input_text,
    "network": _network,
    "low_memory": _low_memory,
    "reboot": _reboot,
    "screenshot": _screenshot,
    "memory_snapshot": _memory_snapshot,
}


def execute_action(step: Step, dev, env: ExecEnv) -> str | None:
    """Run one device action. Returns a detail string for the timeline, or raises StepFailure."""
    try:
        return ACTIONS[step.action](env, step.device, dev, step.args)
    except StepFailure:
        raise
    except Exception as exc:  # adb timeouts etc. -- a step that blows up is a failed step, not a crashed run
        raise StepFailure(f"{type(exc).__name__}: {exc}") from None


# -- expectations ---------------------------------------------------------------------------------


def check_conditions(dev, conds: dict) -> list[str]:
    """Evaluate every condition against the live device; returns one message per condition that
    does not hold (empty list = all hold)."""
    failures = []
    if "call_state" in conds:
        want = conds["call_state"]
        want = want if isinstance(want, list) else [want]
        got = dev.call_state_label(timeout=3)
        if got is None:
            failures.append(f"call_state: expected {'|'.join(want)} but the app UI is not readable (app not in front, screen off, or process dead)")
        elif got not in want:
            failures.append(f"call_state: expected {'|'.join(want)}, got {got}")
    if "notification" in conds:
        posted = dev.notification_posted()
        if posted != (conds["notification"] == "posted"):
            failures.append(f"notification: expected {conds['notification']}, it is {'posted' if posted else 'absent'}")
    if "process" in conds:
        alive = dev.pid() is not None
        if alive != (conds["process"] == "alive"):
            failures.append(f"process: expected {conds['process']}, it is {'alive' if alive else 'dead'}")
    if "foreground_service" in conds:
        running = any(dev.services().values())
        if running != (conds["foreground_service"] == "running"):
            failures.append(f"foreground_service: expected {conds['foreground_service']}, it is {'running' if running else 'stopped'}")
    for ui in conds.get("ui", []):
        text = dev.text_of_resource_id(ui["id"], timeout=3)
        if text is None:
            failures.append(f"ui {ui['id']}: node not found on screen")
        elif "equals" in ui and text != ui["equals"]:
            failures.append(f"ui {ui['id']}: expected text == '{ui['equals']}', got '{text}'")
        elif "contains" in ui and ui["contains"] not in text:
            failures.append(f"ui {ui['id']}: expected text containing '{ui['contains']}', got '{text}'")
    return failures


def wait_for_conditions(dev, conds: dict, timeout: float, env: ExecEnv) -> list[str]:
    """Poll check_conditions until it passes or `timeout` elapses; returns the last failures."""
    deadline = env.clock() + timeout
    while True:
        failures = check_conditions(dev, conds)
        if not failures or env.clock() >= deadline:
            return failures
        env.sleep(1.0)
