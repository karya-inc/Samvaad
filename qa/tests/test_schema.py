"""Scenario parser/validator: good files, and bad files with the exact file:line in the message."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from samvaad_qa.model import ScenarioValidationError
from samvaad_qa.schema import load_scenario, parse_scenario_text

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"


def parse(text: str, source: str = "demo.yaml"):
    return parse_scenario_text(textwrap.dedent(text).lstrip("\n"), source)


def errors(text: str, source: str = "demo.yaml") -> list[str]:
    with pytest.raises(ScenarioValidationError) as exc:
        parse(text, source)
    return str(exc.value).splitlines()


GOOD = """\
    name: demo
    description: d
    tags: [x]
    devices: [a, b]
    repeat: 2
    steps:
      - a: launch_app
      - a: join_call
      - b: launch_app
      - b: join_call
      - wait_for: {device: a, call_state: Ongoing, timeout: 45}
      - a: {rotate: landscape}
      - a: power_button
      - wait: 5s
      - a: tap text=Leave call
      - expect: {device: a, call_state: [Ongoing, Idle], notification: posted, ui: {id: call_state_label, equals: Ongoing}}
    checks: default
"""


def test_good_scenario_parses():
    s = parse(GOOD)
    assert s.name == "demo" and s.repeat == 2 and s.devices == ["a", "b"]
    assert [st.describe() for st in s.steps][:2] == ["a: launch_app", "a: join_call"]
    assert s.steps[4].timeout == 45 and s.steps[4].args == {"call_state": "Ongoing"}
    assert s.steps[5].args == {"to": "landscape"}
    assert s.steps[7].args == {"seconds": 5.0}
    assert s.steps[8].args == {"text": "Leave call"}
    assert s.steps[9].args["call_state"] == ["Ongoing", "Idle"]
    assert s.checks is None


def test_every_committed_scenario_is_valid():
    files = sorted(SCENARIOS.glob("*.yaml"))
    assert len(files) >= 6
    for f in files:
        load_scenario(f)


def test_all_expands_to_each_device():
    s = parse("""\
        name: demo
        steps:
          - all: launch_app
    """)
    assert [(st.device, st.action) for st in s.steps] == [("a", "launch_app"), ("b", "launch_app")]


def test_unknown_step_has_line_and_suggestion():
    msgs = errors("""\
        name: demo
        steps:
          - a: launch_app
          - a: rotat landscape
    """)
    assert msgs == ["demo.yaml:4: error: unknown step 'rotat' -- did you mean 'rotate'? (see qa/SCENARIOS.md for the vocabulary)"]


def test_unknown_device_line():
    msgs = errors("""\
        name: demo
        devices: [a]
        steps:
          - a: launch_app
          - b: launch_app
    """)
    assert msgs == ["demo.yaml:5: error: unknown device 'b' (this scenario declares devices: a)"]


def test_bad_arguments():
    msgs = errors("""\
        name: demo
        steps:
          - a: launch_app
          - a: {rotate: sideways}
          - wait: soon
          - a: {tap: {}}
          - a: {network: {state: sideways}}
          - a: launch_app extra
    """)
    assert any(m.startswith("demo.yaml:4:") and "'to' must be one of" in m for m in msgs)
    assert any(m.startswith("demo.yaml:5:") and "not a duration" in m for m in msgs)
    assert any(m.startswith("demo.yaml:6:") and "exactly one of: id, text" in m for m in msgs)
    assert any(m.startswith("demo.yaml:7:") and "'state' must be one of off, on" in m for m in msgs)
    assert any(m.startswith("demo.yaml:8:") and "takes no arguments" in m for m in msgs)


def test_multiple_errors_are_all_reported_sorted():
    msgs = errors("""\
        name: demo
        repeat: 0
        bogus: 1
        steps:
          - a: nope
    """)
    lines = [int(m.split(":")[1]) for m in msgs]
    assert lines == sorted(lines) and len(msgs) == 3


def test_unknown_condition_and_bad_values():
    msgs = errors("""\
        name: demo
        steps:
          - a: launch_app
          - expect: {device: a, colour: red}
          - expect: {device: a, call_state: Dancing}
          - expect: {device: a, notification: maybe}
          - expect: {device: a, ui: {id: x}}
    """)
    assert any(m.startswith("demo.yaml:4:") and "unknown condition 'colour'" in m for m in msgs)
    assert any(m.startswith("demo.yaml:5:") and "not a call state" in m for m in msgs)
    assert any(m.startswith("demo.yaml:6:") and "posted, absent" in m for m in msgs)
    assert any(m.startswith("demo.yaml:7:") and "exactly one of equals / contains" in m for m in msgs)


def test_unreachable_expectations_are_errors():
    msgs = errors("""\
        name: demo
        steps:
          - a: launch_app
          - expect: {device: a, call_state: Ongoing}
    """)
    assert any("unreachable: device a never runs join_call" in m and m.startswith("demo.yaml:4:") for m in msgs)

    msgs = errors("""\
        name: demo
        devices: [a]
        steps:
          - a: launch_app
          - a: join_call
          - wait_for: {device: a, call_state: Ongoing}
    """)
    assert any("second participant" in m for m in msgs)

    msgs = errors("""\
        name: demo
        steps:
          - a: join_call
    """)
    assert any("never launched" in m for m in msgs)

    msgs = errors("""\
        name: demo
        steps:
          - all: launch_app
          - all: join_call
          - a: leave_call
          - expect: {device: a, call_state: Ongoing}
    """)
    assert any("left the call at step 5" in m for m in msgs)


def test_kill_then_expect_is_only_a_warning():
    s = parse("""\
        name: demo
        expect_issue: true
        steps:
          - all: launch_app
          - all: join_call
          - a: kill_process
          - expect: {device: a, call_state: Ongoing}
    """)
    assert any("killed" in w.message for w in s.warnings)


def test_name_must_match_file_and_pattern():
    assert "must match the file name 'demo'" in errors("name: other\nsteps:\n  - a: launch_app\n")[0]
    assert "lowercase" in errors("name: Bad Name\nsteps:\n  - a: launch_app\n", "x.txt")[0]


def test_yaml_syntax_error_has_line():
    msgs = errors("name: demo\nsteps:\n  - a: [launch_app\n  - b: x\n")
    assert msgs[0].startswith("demo.yaml:") and "not valid YAML" in msgs[0]


def test_checks_variants():
    s = parse("""\
        name: demo
        steps: [{a: launch_app}]
        checks: [crash, anr]
    """)
    assert s.checks.enabled == ["crash", "anr"]
    s = parse("""\
        name: demo
        steps: [{a: launch_app}]
        checks:
          disable: [wake_locks]
          memory_growth: {max_growth_mb: 10}
    """)
    assert "wake_locks" not in s.checks.enabled and s.checks.options["memory_growth"] == {"max_growth_mb": 10}
    msgs = errors("""\
        name: demo
        steps: [{a: launch_app}]
        checks: [crash, cras]
    """)
    assert "unknown check 'cras' -- did you mean 'crash'?" in msgs[0] and msgs[0].startswith("demo.yaml:3:")
    msgs = errors("""\
        name: demo
        steps: [{a: launch_app}]
        checks:
          memory_growth: {threshold: 1}
    """)
    assert "no option 'threshold'" in msgs[0]


def test_meeting_auto_is_reserved_not_implemented():
    msgs = errors("""\
        name: demo
        meeting: auto
        steps: [{a: launch_app}]
    """)
    assert "meeting: auto is not implemented yet" in msgs[0] and msgs[0].startswith("demo.yaml:2:")


def test_unquoted_off_on_are_words():
    s = parse("""\
        name: demo
        steps:
          - a: launch_app
          - a: {network: off}
          - a: {network: on}
    """)
    assert [st.args["state"] for st in s.steps[1:]] == ["off", "on"]


def test_step_without_device_is_explained():
    msgs = errors("""\
        name: demo
        steps:
          - launch_app
    """)
    assert "must say which device" in msgs[0] and "write 'a: launch_app'" in msgs[0]
