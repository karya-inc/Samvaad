"""Scenario file loading and validation.

YAML is walked as a *node tree* (yaml.compose), not a plain dict, so every message can say
`file:line:` -- including for a bad argument deep inside a flow-style step. Validation collects
every problem it can find rather than stopping at the first, so one edit fixes them all.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import yaml

from .model import ChecksConfig, Issue, Scenario, ScenarioValidationError, Step
from .vocab import (
    ACTIVE_CALL_STATES,
    CALL_STATES,
    CONDITION_KEYS,
    DEVICE_STEPS,
    ORACLE_OPTIONS,
    ArgError,
    normalize_args,
    parse_duration,
)

TOP_LEVEL_KEYS = ["name", "description", "tags", "devices", "meeting", "repeat", "steps", "checks", "expect_issue"]
KNOWN_DEVICES = ["a", "b"]  # a = primary actor, b = helper participant (max two emulators at once)
MEETING_PROVIDERS = ["env"]  # `env` = $QA_MEETING_URL; other providers plug in here later
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DEFAULT_WAIT_FOR_TIMEOUT = 30.0


def _suggest(word: str, options: list[str]) -> str:
    close = difflib.get_close_matches(word, options, n=1)
    return f" -- did you mean '{close[0]}'?" if close else ""


class _Ctx:
    """Collects issues and constructs plain Python values from YAML nodes."""

    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self._loader = yaml.SafeLoader("")

    def error(self, node_or_line: object, message: str) -> None:
        self.issues.append(Issue("error", _line(node_or_line), message))

    def warn(self, node_or_line: object, message: str) -> None:
        self.issues.append(Issue("warning", _line(node_or_line), message))

    def value(self, node: yaml.Node) -> object:
        return self._loader.construct_object(node, deep=True)


def _line(node_or_line: object) -> int | None:
    if isinstance(node_or_line, yaml.Node):
        return node_or_line.start_mark.line + 1
    return node_or_line  # type: ignore[return-value]


def _mapping_items(ctx: _Ctx, node: yaml.MappingNode) -> list[tuple[str, yaml.Node, yaml.Node]]:
    items, seen = [], set()
    for key_node, value_node in node.value:
        key = str(ctx.value(key_node))
        if key in seen:
            ctx.error(key_node, f"duplicate key '{key}'")
        seen.add(key)
        items.append((key, key_node, value_node))
    return items


# -- public entry points ---------------------------------------------------------------------


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    return parse_scenario_text(path.read_text(), str(path))


def parse_scenario_text(text: str, source: str) -> Scenario:
    """Parse + validate. Raises ScenarioValidationError (all errors, with line numbers) if the
    scenario is not valid; otherwise returns it with any warnings attached."""
    ctx = _Ctx()
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else None
        problem = getattr(exc, "problem", None) or str(exc)
        raise ScenarioValidationError(source, [Issue("error", line, f"not valid YAML: {problem}")]) from None
    if root is None:
        raise ScenarioValidationError(source, [Issue("error", None, "file is empty")])
    if not isinstance(root, yaml.MappingNode):
        raise ScenarioValidationError(source, [Issue("error", _line(root), "top level must be a mapping (name:, steps:, ...)")])

    scenario = _build(ctx, root, source)
    errors = [i for i in ctx.issues if i.level == "error"]
    if errors:
        raise ScenarioValidationError(source, sorted(ctx.issues, key=lambda i: (i.line or 0)))
    scenario.warnings = ctx.issues
    return scenario


# -- top level ---------------------------------------------------------------------------------


def _build(ctx: _Ctx, root: yaml.MappingNode, source: str) -> Scenario:
    fields: dict[str, tuple[yaml.Node, yaml.Node]] = {}
    for key, key_node, value_node in _mapping_items(ctx, root):
        if key not in TOP_LEVEL_KEYS:
            ctx.error(key_node, f"unknown top-level key '{key}'{_suggest(key, TOP_LEVEL_KEYS)} (valid: {', '.join(TOP_LEVEL_KEYS)})")
        else:
            fields[key] = (key_node, value_node)

    def scalar(key: str, default: object = None) -> object:
        return ctx.value(fields[key][1]) if key in fields else default

    name = scalar("name")
    if name is None:
        ctx.error(1, "missing required key 'name'")
        name = "?"
    elif not isinstance(name, str) or not NAME_RE.match(name):
        ctx.error(fields["name"][1], f"name '{name}' must be lowercase letters, digits, '-' or '_'")
    elif Path(source).suffix in (".yaml", ".yml") and Path(source).stem != name:
        ctx.error(fields["name"][1], f"name '{name}' must match the file name '{Path(source).stem}' (so `--run {name}` finds it)")

    description = str(scalar("description", "") or "")

    tags = scalar("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, (str, int)) for t in tags):
        ctx.error(fields["tags"][1] if "tags" in fields else 1, "tags must be a list of words, e.g. [rotation, power]")
        tags = []

    devices = scalar("devices", ["a", "b"])
    if not isinstance(devices, list) or not devices:
        ctx.error(fields["devices"][1] if "devices" in fields else 1, "devices must be a non-empty list, e.g. [a, b]")
        devices = ["a", "b"]
    else:
        for d in devices:
            if d not in KNOWN_DEVICES:
                ctx.error(fields["devices"][1], f"unknown device '{d}' (valid: {', '.join(KNOWN_DEVICES)})")
        if len(set(devices)) != len(devices):
            ctx.error(fields["devices"][1], "devices lists the same device twice")
        devices = [str(d) for d in devices]

    meeting = scalar("meeting", "env")
    if meeting == "auto":
        # Reserved syntax (roadmap): create a throwaway room from an API key. Rejected clearly
        # rather than silently treated as `env`.
        ctx.error(fields["meeting"][1], "meeting: auto is not implemented yet (roadmap: auto-create a room from an API key); use 'meeting: env' with QA_MEETING_URL")
        meeting = "env"
    elif meeting not in MEETING_PROVIDERS:
        ctx.error(fields["meeting"][1], f"unknown meeting source '{meeting}' (valid: {', '.join(MEETING_PROVIDERS)})")
        meeting = "env"

    repeat = scalar("repeat", 1)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        ctx.error(fields["repeat"][1], f"repeat must be a whole number >= 1 (got {repeat!r})")
        repeat = 1

    expect_issue = scalar("expect_issue", False)
    if not isinstance(expect_issue, bool):
        ctx.error(fields["expect_issue"][1], "expect_issue must be true or false")
        expect_issue = False

    checks = _build_checks(ctx, fields["checks"][1]) if "checks" in fields else None

    steps: list[Step] = []
    if "steps" not in fields:
        ctx.error(1, "missing required key 'steps'")
    elif not isinstance(fields["steps"][1], yaml.SequenceNode) or not fields["steps"][1].value:
        ctx.error(fields["steps"][1], "steps must be a non-empty list (each item starts with '- ')")
    else:
        for item in fields["steps"][1].value:
            steps.extend(_build_step_items(ctx, item, devices, len(steps) + 1))

    scenario = Scenario(
        name=str(name), steps=steps, description=description, tags=[str(t) for t in tags], devices=devices,
        meeting=str(meeting), repeat=repeat, checks=checks, expect_issue=expect_issue, source=source,
    )
    if not any(i.level == "error" for i in ctx.issues):
        for issue in check_flow(scenario):
            ctx.issues.append(issue)
    return scenario


def _build_checks(ctx: _Ctx, node: yaml.Node) -> ChecksConfig | None:
    names = list(ORACLE_OPTIONS)
    value = ctx.value(node)
    if value == "default":
        return None
    if isinstance(value, list):
        bad = [v for v in value if v not in names]
        for v in bad:
            ctx.error(node, f"unknown check '{v}'{_suggest(str(v), names)} (valid: {', '.join(names)})")
        return ChecksConfig(enabled=[v for v in value if v in names])
    if isinstance(node, yaml.MappingNode):
        enabled = list(names)
        options: dict[str, dict] = {}
        for key, key_node, value_node in _mapping_items(ctx, node):
            val = ctx.value(value_node)
            if key in ("enable", "disable"):
                if not isinstance(val, list):
                    ctx.error(value_node, f"checks.{key} must be a list of check names")
                    continue
                for v in val:
                    if v not in names:
                        ctx.error(value_node, f"unknown check '{v}'{_suggest(str(v), names)} (valid: {', '.join(names)})")
                if key == "enable":
                    enabled = [v for v in val if v in names]
                else:
                    enabled = [e for e in enabled if e not in val]
            elif key in ORACLE_OPTIONS:
                if not isinstance(val, dict):
                    ctx.error(value_node, f"checks.{key} must be a mapping of options ({', '.join(ORACLE_OPTIONS[key]) or 'none defined'})")
                    continue
                for opt, opt_value in val.items():
                    typ = ORACLE_OPTIONS[key].get(opt)
                    if typ is None:
                        ctx.error(value_node, f"check '{key}' has no option '{opt}' (valid: {', '.join(ORACLE_OPTIONS[key]) or 'none'})")
                    elif isinstance(opt_value, bool) or not isinstance(opt_value, (int, float)):
                        ctx.error(value_node, f"option '{key}.{opt}' must be a number (got {opt_value!r})")
                options[key] = {k: v for k, v in val.items() if k in ORACLE_OPTIONS[key]}
            else:
                ctx.error(key_node, f"unknown key '{key}' under checks{_suggest(key, names + ['enable', 'disable'])} (use enable/disable or a check name)")
        return ChecksConfig(enabled=enabled, options=options)
    ctx.error(node, "checks must be 'default', a list of check names, or a mapping (enable/disable/<check>: {options})")
    return None


# -- steps ---------------------------------------------------------------------------------------


def _build_step_items(ctx: _Ctx, item: yaml.Node, devices: list[str], next_index: int) -> list[Step]:
    if isinstance(item, yaml.ScalarNode):
        word = str(ctx.value(item))
        first = word.split()[0] if word.split() else word
        if first in DEVICE_STEPS and len(devices) == 1:
            return _device_steps(ctx, item, devices[0], item, next_index)
        hint = f" -- write '{devices[0]}: {word}'" if first in DEVICE_STEPS else ""
        ctx.error(item, f"step '{word}' must say which device it runs on{hint}")
        return []
    if not isinstance(item, yaml.MappingNode) or len(item.value) != 1:
        ctx.error(item, "each step is a single 'key: value' pair, e.g. '- a: launch_app' or '- wait: 5s'")
        return []
    key_node, value_node = item.value[0]
    key = str(ctx.value(key_node))

    if key == "wait":
        try:
            seconds = parse_duration(ctx.value(value_node))
        except ArgError as exc:
            ctx.error(value_node, f"wait: {exc}")
            return []
        return [Step(next_index, _line(item), "wait", args={"seconds": seconds})]
    if key in ("wait_for", "expect"):
        step = _condition_step(ctx, key, value_node, devices, next_index)
        return [step] if step else []
    if key == "all":
        return _device_steps(ctx, value_node, None, key_node, next_index, devices)
    if key in devices:
        return _device_steps(ctx, value_node, key, key_node, next_index)
    if key in KNOWN_DEVICES or (len(key) == 1 and key.isalpha()):
        ctx.error(key_node, f"unknown device '{key}' (this scenario declares devices: {', '.join(devices)})")
        return []
    vocabulary = list(DEVICE_STEPS) + ["wait", "wait_for", "expect"]
    hint = f" -- steps that act on a device are written '{devices[0]}: {key}'" if key in DEVICE_STEPS else _suggest(key, vocabulary)
    ctx.error(key_node, f"unknown step '{key}'{hint}")
    return []


def _split_action(ctx: _Ctx, node: yaml.Node) -> tuple[str, object] | None:
    """The `<device>:` value -> (action name, raw args). Accepts 'rotate landscape', a bare
    'launch_app', or {rotate: landscape} / {tap: {text: Leave call}}."""
    if isinstance(node, yaml.ScalarNode):
        value = ctx.value(node)
        if value is None:
            ctx.error(node, "empty step: name an action, e.g. 'a: launch_app'")
            return None
        parts = str(value).strip().split(None, 1)
        return parts[0], (parts[1] if len(parts) > 1 else None)
    if isinstance(node, yaml.MappingNode) and len(node.value) == 1:
        action_node, arg_node = node.value[0]
        raw = ctx.value(arg_node)
        return str(ctx.value(action_node)), raw
    ctx.error(node, "a step's action is a name ('launch_app'), 'name arg' ('rotate landscape') or {name: args}")
    return None


def _device_steps(
    ctx: _Ctx, value_node: yaml.Node, device: str | None, line_node: yaml.Node, next_index: int,
    all_devices: list[str] | None = None,
) -> list[Step]:
    parsed = _split_action(ctx, value_node)
    if parsed is None:
        return []
    name, raw = parsed
    spec = DEVICE_STEPS.get(name)
    if spec is None:
        ctx.error(value_node, f"unknown step '{name}'{_suggest(name, list(DEVICE_STEPS) + ['wait', 'wait_for', 'expect'])} (see qa/SCENARIOS.md for the vocabulary)")
        return []
    try:
        args = normalize_args(spec, raw)
    except ArgError as exc:
        ctx.error(value_node, str(exc))
        return []
    targets = all_devices if device is None else [device]
    return [
        Step(next_index + i, _line(line_node), "action", device=d, action=spec.name, args=dict(args))
        for i, d in enumerate(targets or [])
    ]


def _condition_step(ctx: _Ctx, kind: str, node: yaml.Node, devices: list[str], index: int) -> Step | None:
    if not isinstance(node, yaml.MappingNode):
        ctx.error(node, f"{kind}: expects a mapping, e.g. {{device: a, call_state: Ongoing}}")
        return None
    items = _mapping_items(ctx, node)
    raw = {k: (ctx.value(v), kn, v) for k, kn, v in items}
    device = raw.get("device", (None,))[0]
    if device is None:
        if len(devices) == 1:
            device = devices[0]
        else:
            ctx.error(node, f"{kind}: needs 'device: <{'|'.join(devices)}>'")
            return None
    elif device not in devices:
        ctx.error(raw["device"][2], f"{kind}: unknown device '{device}' (this scenario declares devices: {', '.join(devices)})")
        return None
    allowed = list(CONDITION_KEYS) + (["device", "timeout"] if kind == "wait_for" else ["device"])
    conds: dict = {}
    timeout = None
    ok = True
    for key, (value, key_node, value_node) in raw.items():
        if key == "device":
            continue
        if key == "timeout" and kind == "wait_for":
            try:
                timeout = parse_duration(value)
            except ArgError as exc:
                ctx.error(value_node, f"{kind}.timeout: {exc}")
                ok = False
            continue
        if key not in CONDITION_KEYS:
            ctx.error(key_node, f"{kind}: unknown condition '{key}'{_suggest(key, allowed)} (valid: {', '.join(allowed)})")
            ok = False
            continue
        try:
            conds[key] = normalize_condition(key, value)
        except ArgError as exc:
            ctx.error(value_node, f"{kind}.{key}: {exc}")
            ok = False
    if ok and not conds:
        ctx.error(node, f"{kind}: needs at least one condition ({', '.join(CONDITION_KEYS)})")
        ok = False
    if not ok:
        return None
    if kind == "wait_for" and timeout is None:
        timeout = DEFAULT_WAIT_FOR_TIMEOUT
    return Step(index, _line(node), kind, device=device, args=conds, timeout=timeout)


def normalize_condition(key: str, value: object) -> object:
    """Validate/canonicalize one expectation condition; raises ArgError."""
    if key == "call_state":
        values = value if isinstance(value, list) else [value]
        out = []
        for v in values:
            match = next((s for s in CALL_STATES if s.lower() == str(v).strip().lower()), None)
            if match is None:
                raise ArgError(f"'{v}' is not a call state (valid: {', '.join(CALL_STATES)})")
            out.append(match)
        return out[0] if len(out) == 1 else out
    choices = {"notification": ["posted", "absent"], "process": ["alive", "dead"], "foreground_service": ["running", "stopped"]}
    if key in choices:
        v = str(value).strip().lower()
        if v not in choices[key]:
            raise ArgError(f"must be one of {', '.join(choices[key])} (got '{value}')")
        return v
    if key == "ui":
        entries = value if isinstance(value, list) else [value]
        out = []
        for e in entries:
            if not isinstance(e, dict) or "id" not in e:
                raise ArgError("must be {id: <resource id>, equals: <text>} or {id: ..., contains: <text>}")
            extra = set(e) - {"id", "equals", "contains"}
            if extra:
                raise ArgError(f"unknown key(s) {', '.join(sorted(extra))} (valid: id, equals, contains)")
            if ("equals" in e) == ("contains" in e):
                raise ArgError("needs exactly one of equals / contains")
            out.append({k: str(v) for k, v in e.items()})
        return out
    raise ArgError(f"unsupported condition '{key}'")


# -- static reachability ---------------------------------------------------------------------------

_UI_ACTIONS = {"join_call", "leave_call", "tap", "input_text"}


def check_flow(scenario: Scenario) -> list[Issue]:
    """Walks the flow once without touching a device, tracking what each device must be doing,
    and flags what can never work (errors) or probably isn't intended (warnings).

    Errors are reserved for the impossible: a call-state expectation on a device that never joined,
    `Ongoing` with nobody else in the room, join before launch. Expecting a live call after
    kill_process is only a *warning*: it can never pass, but writing it down is exactly how a
    negative control (`expect_issue: true`) proves the framework can fail a flow."""
    issues: list[Issue] = []
    launched = {d: False for d in scenario.devices}
    killed_at: dict[str, int] = {}
    joined = {d: False for d in scenario.devices}
    left_at: dict[str, int] = {}
    ever_joined = {d: False for d in scenario.devices}
    screen_on = {d: True for d in scenario.devices}

    def err(step: Step, msg: str) -> None:
        issues.append(Issue("error", step.line, f"step {step.index} ({step.describe()}): {msg}"))

    def warn(step: Step, msg: str) -> None:
        issues.append(Issue("warning", step.line, f"step {step.index} ({step.describe()}): {msg}"))

    for step in scenario.steps:
        d = step.device
        if step.kind == "action":
            a = step.action
            if a in _UI_ACTIONS:
                if not launched[d] and d not in killed_at:
                    err(step, f"device {d}: the app was never launched before this step (add '{d}: launch_app' first)")
                elif d in killed_at and not launched[d]:
                    warn(step, f"device {d}: the process was killed at step {killed_at[d]} and not relaunched; this UI step will fail")
                if not screen_on[d]:
                    warn(step, f"device {d}: the screen is off here (power_button/screen_off); a UI step needs it on")
            if a == "launch_app":
                launched[d] = True
                killed_at.pop(d, None)
            elif a == "join_call":
                joined[d] = ever_joined[d] = True
                left_at.pop(d, None)
            elif a == "leave_call":
                joined[d] = False
                left_at[d] = step.index
            elif a in ("kill_process", "reboot"):
                launched[d] = False
                joined[d] = False
                killed_at[d] = step.index
            elif a == "power_button":
                screen_on[d] = not screen_on[d]
            elif a == "screen_off":
                screen_on[d] = False
            elif a in ("screen_on", "unlock"):
                screen_on[d] = True
        elif step.kind in ("wait_for", "expect"):
            conds = step.args
            states = conds.get("call_state")
            states = states if isinstance(states, list) else ([states] if states else [])
            needs_call = any(s in ACTIVE_CALL_STATES for s in states) and not (
                step.kind == "expect" and any(s not in ACTIVE_CALL_STATES for s in states)
            )
            if needs_call or conds.get("notification") == "posted" or conds.get("foreground_service") == "running":
                if not ever_joined[d]:
                    err(step, f"unreachable: device {d} never runs join_call before this, so it cannot be in a call")
                elif d in left_at and not joined[d]:
                    err(step, f"unreachable: device {d} left the call at step {left_at[d]} and has not rejoined")
                elif not joined[d]:
                    warn(step, f"device {d} was killed at step {killed_at.get(d, '?')} and not relaunched/rejoined; this can never hold")
            if "Ongoing" in states and needs_call:
                others = [o for o in scenario.devices if o != d and ever_joined[o]]
                if joined[d] and not others:
                    err(step, "unreachable: the sample app only reaches Ongoing once a second participant is in the room; "
                              "have another device launch_app + join_call earlier (see qa/SCENARIOS.md)")
            if step.kind == "expect" and d in killed_at and not launched[d]:
                if "call_state" in conds or "ui" in conds:
                    warn(step, f"device {d}: its process was killed at step {killed_at[d]} and not relaunched; the UI is not readable")
    return issues
