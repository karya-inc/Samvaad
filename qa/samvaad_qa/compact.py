"""The one-line form for reproducing a bug report straight from the command line, and the
YAML writer that turns any scenario (compact or not) into the canonical file format.

    a:launch_app, a:join_call, b:launch_app, b:join_call, a:rotate landscape, wait 5s,
    wait_for a call_state=Ongoing timeout=45, expect a notification=posted

Grammar: comma-separated items.
  <device>:<step> [arg]      a:rotate landscape     all:launch_app     a:tap text=Leave call
  wait <duration>            wait 5s
  wait_for <device> k=v ...  wait_for a call_state=Ongoing timeout=45
  expect <device> k=v ...    expect a call_state=Ongoing notification=posted ui.call_state_label=Ongoing
Limits: no commas or leading/trailing spaces inside a value (use a YAML file for those).
`ui.<id>=text` means equals, `ui.<id>~=text` means contains.
"""

from __future__ import annotations

import hashlib
import re

import yaml

from .model import Issue, Scenario, ScenarioValidationError, Step
from .schema import DEFAULT_WAIT_FOR_TIMEOUT, KNOWN_DEVICES, check_flow, normalize_condition
from .vocab import DEVICE_STEPS, ArgError, normalize_args, parse_duration

_PAIR_RE = re.compile(r"^([\w.]+?)(~?=)(.*)$", re.DOTALL)


def parse_flow(text: str, name: str | None = None, repeat: int = 1) -> Scenario:
    items = [t.strip() for t in text.split(",") if t.strip()]
    issues: list[Issue] = []
    steps: list[Step] = []

    def bad(pos: int, item: str, msg: str) -> None:
        issues.append(Issue("error", None, f"item {pos} '{item}': {msg}"))

    used = [d for d in KNOWN_DEVICES if re.search(rf"(^|[,\s]){d}(:|\s)", text) or re.search(rf"\ball:", text)]
    devices = used or ["a"]
    if re.search(r"\ball:", text):
        devices = ["a", "b"] if re.search(r"(^|[,\s])b[:\s]", text) else devices

    for pos, item in enumerate(items, 1):
        head, _, rest = item.partition(" ")
        if head == "wait" or item.startswith("wait="):
            try:
                steps.append(Step(len(steps) + 1, None, "wait", args={"seconds": parse_duration(rest.strip() or item.partition("=")[2])}))
            except ArgError as exc:
                bad(pos, item, str(exc))
        elif head in ("wait_for", "expect"):
            step = _condition(head, rest, len(steps) + 1, devices, lambda m: bad(pos, item, m))
            if step:
                steps.append(step)
        elif ":" in item and item.split(":", 1)[0].strip() in KNOWN_DEVICES + ["all"]:
            dev, _, action_text = item.partition(":")
            dev, action_text = dev.strip(), action_text.strip()
            name_, _, raw = action_text.partition(" ")
            spec = DEVICE_STEPS.get(name_)
            if spec is None:
                bad(pos, item, f"unknown step '{name_}' (valid: {', '.join(DEVICE_STEPS)})")
                continue
            try:
                args = normalize_args(spec, raw.strip() or None)
            except ArgError as exc:
                bad(pos, item, str(exc))
                continue
            for d in (devices if dev == "all" else [dev]):
                steps.append(Step(len(steps) + 1, None, "action", device=d, action=spec.name, args=dict(args)))
        else:
            bad(pos, item, "expected '<device>:<step>', 'wait <duration>', 'wait_for ...' or 'expect ...'")

    if not steps and not issues:
        issues.append(Issue("error", None, "the flow is empty"))
    if issues:
        raise ScenarioValidationError("--flow", issues)

    digest = hashlib.sha1(text.encode()).hexdigest()[:6]
    scenario = Scenario(
        name=name or f"adhoc-{digest}", steps=steps, description="Ad-hoc flow from --flow: " + text,
        tags=["adhoc"], devices=devices, repeat=repeat, source="<ad-hoc>",
    )
    flow_issues = check_flow(scenario)
    if any(i.level == "error" for i in flow_issues):
        raise ScenarioValidationError("--flow", flow_issues)
    scenario.warnings = flow_issues
    return scenario


def _condition(kind: str, rest: str, index: int, devices: list[str], fail) -> Step | None:
    words = rest.split()
    # A value may contain spaces ("ui.x~=In call"): re-join words that are not themselves k=v / device.
    device = None
    if words and words[0] in KNOWN_DEVICES:
        device = words.pop(0)
    elif len(devices) == 1:
        device = devices[0]
    if device is None:
        fail(f"{kind} needs a device first, e.g. '{kind} a call_state=Ongoing'")
        return None
    pairs: list[list[str]] = []
    for w in words:
        if _PAIR_RE.match(w) and not (pairs and w.startswith("-")):
            pairs.append([w])
        elif pairs:
            pairs[-1].append(w)
        else:
            fail(f"'{w}' is not key=value")
            return None
    conds: dict = {}
    timeout = None
    for parts in pairs:
        m = _PAIR_RE.match(" ".join(parts))
        key, op, value = m.group(1), m.group(2), m.group(3)
        try:
            if key == "timeout" and kind == "wait_for":
                timeout = parse_duration(value)
            elif key.startswith("ui."):
                entry = {"id": key[3:], "contains" if op == "~=" else "equals": value}
                conds.setdefault("ui", []).extend(normalize_condition("ui", entry))
            elif key == "call_state" and "|" in value:
                conds[key] = normalize_condition(key, value.split("|"))
            elif key in ("call_state", "notification", "process", "foreground_service"):
                conds[key] = normalize_condition(key, value)
            else:
                fail(f"unknown condition '{key}' (valid: call_state, notification, process, foreground_service, ui.<id>)")
                return None
        except ArgError as exc:
            fail(f"{key}: {exc}")
            return None
    if not conds:
        fail(f"{kind} needs at least one condition")
        return None
    if kind == "wait_for" and timeout is None:
        timeout = DEFAULT_WAIT_FOR_TIMEOUT
    return Step(index, None, kind, device=device, args=conds, timeout=timeout)


# -- YAML writer --------------------------------------------------------------------------------


def _flow(obj: object) -> str:
    return yaml.safe_dump(obj, default_flow_style=True, width=10_000, sort_keys=False).strip().removesuffix("...").strip()


def _seconds_text(seconds: float) -> str:
    return f"{int(seconds)}s" if float(seconds).is_integer() else f"{seconds:g}s"


def step_yaml(step: Step) -> str:
    if step.kind == "wait":
        return f"wait: {_seconds_text(step.args['seconds'])}"
    if step.kind in ("wait_for", "expect"):
        body: dict = {"device": step.device, **step.args}
        if step.timeout is not None:
            body["timeout"] = int(step.timeout) if float(step.timeout).is_integer() else step.timeout
        return f"{step.kind}: {_flow(body)}"
    spec = DEVICE_STEPS[step.action]
    if not step.args:
        return f"{step.device}: {step.action}"
    if len(step.args) == 1 and spec.positional in step.args and " " not in str(step.args[spec.positional]) and ":" not in str(step.args[spec.positional]):
        return f"{step.device}: {step.action} {step.args[spec.positional]}"
    return f"{step.device}: {_flow({step.action: step.args})}"


def to_yaml(scenario: Scenario) -> str:
    """Canonical, human-editable YAML for a scenario; parse_scenario_text(to_yaml(s)) round-trips."""
    lines = [f"name: {scenario.name}", f"description: {_flow(scenario.description)}"]
    lines.append(f"tags: {_flow(scenario.tags)}")
    lines.append(f"devices: {_flow(scenario.devices)}")
    lines += [f"meeting: {scenario.meeting}", f"repeat: {scenario.repeat}"]
    if scenario.expect_issue:
        lines.append("expect_issue: true")
    lines.append("steps:")
    lines += [f"  - {step_yaml(s)}" for s in scenario.steps]
    lines.append("checks: default")
    return "\n".join(lines) + "\n"
