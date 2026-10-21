"""One-line --flow form: parsing, validation, and the YAML round trip."""

from __future__ import annotations

import pytest

from samvaad_qa.compact import parse_flow, to_yaml
from samvaad_qa.model import ScenarioValidationError
from samvaad_qa.schema import parse_scenario_text

FLOW = ("a:launch_app, a:join_call, b:launch_app, b:join_call, wait_for a call_state=Ongoing timeout=45, "
        "a:rotate landscape, a:power_button, wait 5s, a:power_button, a:unlock, "
        "expect a call_state=Ongoing notification=posted")


def test_parses_steps_and_infers_devices():
    s = parse_flow(FLOW, name="x")
    assert s.devices == ["a", "b"]
    assert [st.describe() for st in s.steps] == [
        "a: launch_app", "a: join_call", "b: launch_app", "b: join_call",
        "wait_for a: call_state=Ongoing timeout=45s", "a: rotate to=landscape", "a: power_button",
        "wait 5s", "a: power_button", "a: unlock", "expect a: call_state=Ongoing notification=posted",
    ]


def test_yaml_round_trips_through_the_file_validator():
    s = parse_flow(FLOW, name="x")
    again = parse_scenario_text(to_yaml(s), "x.yaml")
    assert [st.describe() for st in again.steps] == [st.describe() for st in s.steps]
    assert again.devices == s.devices


def test_named_form_args_and_ui_conditions():
    s = parse_flow("a:launch_app, a:join_call, b:launch_app, b:join_call, a:tap text=Leave call, "
                   "a:network off, a:low_memory RUNNING_LOW, expect a ui.call_state_label~=Ong call_state=Idle|Error", name="x")
    assert s.steps[4].args == {"text": "Leave call"}
    assert s.steps[5].args == {"state": "off", "via": "airplane"}
    assert s.steps[7].args == {"ui": [{"id": "call_state_label", "contains": "Ong"}], "call_state": ["Idle", "Error"]}
    parse_scenario_text(to_yaml(s), "x.yaml")


def test_single_device_flow():
    s = parse_flow("a:launch_app, a:rotate landscape, a:press_home, a:bring_to_foreground")
    assert s.devices == ["a"] and s.name.startswith("adhoc-")


@pytest.mark.parametrize(
    "flow,fragment",
    [
        ("a:launch_app, a:rotat landscape", "item 2 'a:rotat landscape': unknown step 'rotat'"),
        ("a:launch_app, c:launch_app", "item 2 'c:launch_app': expected"),
        ("a:launch_app, wait forever", "item 2 'wait forever': 'forever' is not a duration"),
        ("a:rotate sideways", "'to' must be one of"),
        ("a:launch_app, expect a colour=red", "unknown condition 'colour'"),
        ("a:launch_app, expect a", "needs at least one condition"),
        ("a:launch_app, expect a call_state=Ongoing", "never runs join_call"),
        ("", "empty"),
    ],
)
def test_bad_flows_say_which_item(flow, fragment):
    with pytest.raises(ScenarioValidationError) as exc:
        parse_flow(flow)
    assert fragment in str(exc.value)
