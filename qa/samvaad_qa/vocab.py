"""The step and expectation vocabulary: one declarative table that the validator, the executor's
dispatch, the `--list-steps` output and the docs all read, so they cannot drift apart.

A step's arguments are described by ArgSpecs; `normalize_args()` turns whatever the author wrote
(nothing, a bare value like `landscape`, `key=value` text, or a mapping) into one canonical dict
or raises ArgError with a message meant to be shown to a person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import adb_utils

CALL_STATES = ["Idle", "Incoming", "Connecting", "Ongoing", "Disconnecting", "Error"]
# States in which a call is live: the foreground service and its notification must exist.
ACTIVE_CALL_STATES = ("Connecting", "Ongoing")


class ArgError(ValueError):
    """A bad argument; the message is user-facing (it ends up after `file:line: error:`)."""


def parse_duration(raw: object) -> float:
    """'5s' / '500ms' / '2m' / '1.5s' / bare number (seconds) -> seconds."""
    if isinstance(raw, bool):
        raise ArgError(f"'{raw}' is not a duration (use e.g. 5s, 500ms, 2m)")
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m)?\s*", str(raw))
        if not match:
            raise ArgError(f"'{raw}' is not a duration (use e.g. 5s, 500ms, 2m)")
        value = float(match.group(1)) * {"ms": 0.001, "s": 1, "m": 60, None: 1}[match.group(2)]
    if value < 0:
        raise ArgError("a duration cannot be negative")
    return value


def _norm_choice(value: object) -> str:
    # YAML 1.1 reads an unquoted `off` / `on` as a boolean; the author meant the word.
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


@dataclass
class ArgSpec:
    name: str
    kind: str = "str"  # str | choice | duration | int
    choices: list[str] = field(default_factory=list)
    required: bool = False
    default: object = None
    help: str = ""

    def convert(self, value: object) -> object:
        if self.kind == "choice":
            norm = _norm_choice(value)
            allowed = {_norm_choice(c): c for c in self.choices}
            if norm not in allowed:
                raise ArgError(f"'{self.name}' must be one of {', '.join(self.choices)} (got '{value}')")
            return allowed[norm]
        if self.kind == "duration":
            return parse_duration(value)
        if self.kind == "int":
            if isinstance(value, bool) or not str(value).strip().lstrip("-").isdigit():
                raise ArgError(f"'{self.name}' must be a whole number (got '{value}')")
            return int(value)
        if value is None or isinstance(value, (dict, list)):
            raise ArgError(f"'{self.name}' must be text (got {value!r})")
        return str(value)


@dataclass
class StepSpec:
    name: str
    summary: str
    args: list[ArgSpec] = field(default_factory=list)
    positional: str | None = None  # which arg a bare value (`rotate: landscape`) fills
    aliases: list[str] = field(default_factory=list)
    one_of: list[str] = field(default_factory=list)  # exactly one of these args must be given
    heavy: bool = False

    def usage(self) -> str:
        if not self.args:
            return self.name
        parts = []
        for a in self.args:
            shown = "|".join(a.choices) if a.choices else a.kind
            parts.append(f"{a.name}=<{shown}>" + ("" if a.required else "?"))
        return f"{self.name} " + " ".join(parts)


_KV_RE = re.compile(r"^(\w+)=(.*)$", re.DOTALL)


def normalize_args(spec: StepSpec, raw: object) -> dict:
    """raw: None | str | number | dict as written in the file. Returns the canonical args dict."""
    if raw is None:
        given: dict = {}
    elif isinstance(raw, dict):
        given = {str(k): v for k, v in raw.items()}
    else:
        text = str(raw).strip()
        kv = _KV_RE.match(text) if isinstance(raw, str) else None
        if kv and any(a.name == kv.group(1) for a in spec.args):
            given = {kv.group(1): kv.group(2)}
        elif spec.positional is None:
            raise ArgError(f"'{spec.name}' takes no arguments (got '{raw}')")
        else:
            given = {spec.positional: raw}

    known = {a.name: a for a in spec.args}
    for key in given:
        if key not in known:
            hint = f" (valid: {', '.join(known)})" if known else " (it takes no arguments)"
            raise ArgError(f"'{spec.name}' has no argument '{key}'{hint}")
    out: dict = {}
    for a in spec.args:
        if a.name in given:
            out[a.name] = a.convert(given[a.name])
        elif a.required:
            raise ArgError(f"'{spec.name}' needs argument '{a.name}'")
        elif a.default is not None:
            out[a.name] = a.default
    if spec.one_of:
        present = [k for k in spec.one_of if k in out]
        if len(present) != 1:
            raise ArgError(f"'{spec.name}' needs exactly one of: {', '.join(spec.one_of)}")
    return out


def _spec(*specs: StepSpec) -> dict[str, StepSpec]:
    table = {}
    for s in specs:
        table[s.name] = s
        for alias in s.aliases:
            table[alias] = s
    return table


ROTATE_CHOICES = list(adb_utils.ROTATIONS) + ["auto"]

# Device-addressed steps: written as `<device>: <step>` (e.g. `a: rotate landscape`).
DEVICE_STEPS = _spec(
    StepSpec("launch_app", "Start the app like the launcher icon does (cold start if the process is gone, otherwise resume) and wait for its UI."),
    StepSpec("join_call", "From the app's Idle screen: type the scenario's meeting URL and tap Join. Does not wait for Connecting/Ongoing (use wait_for)."),
    StepSpec("leave_call", "Leave through the UI: taps 'Leave call' (Ongoing) or 'Cancel' (Connecting).", aliases=["hang_up"]),
    StepSpec(
        "rotate",
        "Pin the screen orientation (settings user_rotation; 'auto' returns control to the accelerometer).",
        [ArgSpec("to", "choice", ROTATE_CHOICES, required=True)],
        positional="to",
    ),
    StepSpec("power_button", "Press the physical power key: toggles the screen off/on (state depends on what it was)."),
    StepSpec("screen_off", "Idempotent screen-off (KEYCODE_SLEEP); a no-op if the screen is already off."),
    StepSpec("screen_on", "Idempotent screen-on (KEYCODE_WAKEUP); does not dismiss a lock screen -- see unlock."),
    StepSpec("unlock", "Wake the screen if needed and dismiss a swipe-style keyguard (no PIN/pattern support)."),
    StepSpec("press_home", "Press Home."),
    StepSpec("press_back", "Press Back."),
    StepSpec("open_recents", "Press the app-switcher key (opens Recents)."),
    StepSpec(
        "bring_to_foreground",
        "Return to the app: 'launcher' resumes its task like tapping the icon; 'notification' taps the call notification in the shade.",
        [ArgSpec("via", "choice", ["launcher", "notification"], default="launcher")],
        positional="via",
    ),
    StepSpec(
        "kill_process",
        "Genuine process death. 'force_stop' (default) = `am force-stop`; 'am_kill' = `am kill`, which Android ignores while a foreground service is running.",
        [ArgSpec("how", "choice", ["force_stop", "am_kill"], default="force_stop")],
        positional="how",
    ),
    StepSpec("swipe_notification", "Open the shade and swipe the call notification sideways (the dismiss gesture). Fails the step if the notification is not in the shade."),
    StepSpec(
        "tap",
        "Tap a UI node found via uiautomator: by resource id (a bare value, or id=) or by exact visible text (text=).",
        [ArgSpec("id"), ArgSpec("text")],
        positional="id",
        one_of=["id", "text"],
    ),
    StepSpec(
        "input_text",
        "Type text with `input text` (spaces become %s). With into=<resource id> the field is tapped first.",
        [ArgSpec("text", required=True), ArgSpec("into")],
        positional="text",
    ),
    StepSpec(
        "network",
        "Cut/restore connectivity. via=airplane (default): `cmd connectivity airplane-mode`; wifi/data: `svc`; all: both svc toggles.",
        [ArgSpec("state", "choice", ["off", "on"], required=True), ArgSpec("via", "choice", ["airplane", "wifi", "data", "all"], default="airplane")],
        positional="state",
    ),
    StepSpec(
        "low_memory",
        "`am send-trim-memory`: deliver onTrimMemory(level) to the app, as the OS does under memory pressure.",
        [ArgSpec("level", "choice", adb_utils.TRIM_LEVELS, default="RUNNING_CRITICAL")],
        positional="level",
    ),
    StepSpec("reboot", "Reboot the emulator and wait for boot (heavy: ~1 minute). The app process is gone afterwards.", heavy=True),
    StepSpec("screenshot", "Save a screenshot into the scenario's artifacts.", [ArgSpec("label")], positional="label"),
    StepSpec("memory_snapshot", "Record the app's PSS (dumpsys meminfo) into the timeline.", [ArgSpec("label")], positional="label"),
)

# Expectation conditions (used by `expect` and `wait_for`).
CONDITION_KEYS = {
    "call_state": "the call_state_label UI hook equals this (a name, or a list = any of): " + ", ".join(CALL_STATES),
    "notification": "posted | absent: the app's notification in `dumpsys notification`",
    "process": "alive | dead: the app's main process exists",
    "foreground_service": "running | stopped: a foreground ServiceRecord of the app in `dumpsys activity services`",
    "ui": "{id: <resource id>, equals|contains: <text>}  (or a list of these)",
}
EXPECT_ONLY_KEYS = {"device", "timeout"}

# Oracle names -> the options each accepts (name -> type). The oracle implementations live in
# oracles.py; the schema validator only needs the names/options, so they are listed here.
ORACLE_OPTIONS: dict[str, dict[str, type]] = {
    "crash": {},
    "anr": {},
    "leakcanary": {},
    "leaked_resources": {},
    "service_notification": {"settle": float},
    "stale_call_ui": {"settle": float},
    "wake_locks": {"settle": float},
    "process_death": {},
    "memory_growth": {"max_growth_mb": float, "min_iterations": int},
}
