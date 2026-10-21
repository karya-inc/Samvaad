"""The runner end-to-end against FakeDevices: step dispatch, expectations, finding attribution
(step index + evidence + artifacts), abort rules, debounce, repeat/memory, and negative controls."""

from __future__ import annotations

import json

import pytest

from fake_device import FakeClock, FakeDevice, FakeLog
from samvaad_qa.compact import parse_flow
from samvaad_qa.model import ChecksConfig
from samvaad_qa.report import render_markdown, to_dict, write_report
from samvaad_qa.runner import RunConfig, run_scenario
from samvaad_qa.schema import parse_scenario_text

URL = "https://example.invalid/room"


def scenario(steps_yaml: str, header: str = "devices: [a, b]\n", name: str = "demo"):
    return parse_scenario_text(f"name: {name}\n{header}steps:\n{steps_yaml}", f"{name}.yaml")


PAIR = """\
  - all: launch_app
  - all: join_call
  - wait_for: {device: a, call_state: Ongoing, timeout: 10}
"""


def make(tmp_path, logs=None, repeat=None):
    clock = FakeClock()
    a, b = FakeDevice("a"), FakeDevice("b")
    for d in (a, b):
        d.hooks["join_meeting"] = lambda dev, url: dev.in_call()
    logs = logs or {}
    cfg = RunConfig(
        artifacts_dir=tmp_path / "out", meeting_url=URL, repeat=repeat,
        logcat_factory=lambda name, path: logs.setdefault(name, FakeLog()),
        sleep=clock.sleep, now=clock.now, clock=clock.now,
    )
    return {"a": a, "b": b}, cfg, clock


def test_happy_flow_dispatches_every_step_and_passes(tmp_path):
    devices, cfg, _ = make(tmp_path)
    s = scenario(PAIR + """\
  - a: {rotate: landscape}
  - a: power_button
  - wait: 5s
  - a: power_button
  - a: unlock
  - a: swipe_notification
  - a: {tap: {text: Leave call}}
  - a: {low_memory: RUNNING_LOW}
  - a: {network: off}
  - a: {network: on}
  - a: {screenshot: shot1}
  - a: {kill_process: force_stop}
  - expect: {device: a, process: dead, notification: absent, foreground_service: stopped}
""")
    result = run_scenario(s, devices, cfg)
    a = devices["a"]
    assert result.verdict == "PASS", [f.message for f in result.findings]
    assert a.called("join_meeting") == [("join_meeting", URL)]
    assert a.called("set_rotation") == [("set_rotation", "landscape")]
    assert len(a.called("press_power")) == 2 and a.called("unlock")
    assert a.called("tap_text") == [("tap_text", "Leave call")]
    assert a.called("trim_memory") == [("trim_memory", "RUNNING_LOW")]
    assert [c[1] for c in a.called("set_network")] == [False, True]
    assert a.called("kill_process") == [("kill_process", "force_stop")]
    assert (tmp_path / "out" / "screenshots" / "i1-a-shot1.png").exists()
    assert [r.status for r in result.records if r.status != "ok"] == []
    assert devices["b"].called("swipe_notification") == []  # steps only touch their own device


def test_failed_expectation_is_a_finding_with_step_and_artifacts(tmp_path):
    devices, cfg, _ = make(tmp_path)
    devices["a"].hooks["set_rotation"] = lambda d, mode: setattr(d, "label", "Error")
    s = scenario(PAIR + """\
  - a: {rotate: landscape}
  - expect: {device: a, call_state: Ongoing}
  - a: press_home
""")
    result = run_scenario(s, devices, cfg)
    assert result.verdict == "ISSUE FOUND" and not result.outcome_ok
    f = next(f for f in result.findings if f.oracle == "expectation")
    assert f.step_index == 7 and f.device == "a"  # all: expands launch/join to 2 steps each, wait_for is 5, rotate 6
    assert "expected Ongoing, got Error" in f.message
    assert f.screenshot and (cfg.artifacts_dir / f.screenshot).exists()
    assert f.ui_dump and (cfg.artifacts_dir / f.ui_dump).exists()
    assert result.records[-1].status == "ok"  # a failed expect does not abort the flow
    assert result.aborted is None


def test_wait_for_timeout_aborts_and_skips_the_rest(tmp_path):
    devices, cfg, clock = make(tmp_path)
    devices["a"].hooks["join_meeting"] = lambda d, u: setattr(d, "label", "Idle")  # never reaches Ongoing
    s = scenario(PAIR + "  - a: press_home\n")
    result = run_scenario(s, devices, cfg)
    statuses = [r.status for r in result.records]
    assert statuses[-2:] == ["failed", "skipped"]
    assert "timed out after 10s" in result.findings[0].message and result.findings[0].oracle == "expectation"
    assert result.aborted and clock.t - 1_700_000_000.0 >= 10
    assert devices["a"].called("press_home") == []


def test_step_error_is_a_finding_and_aborts(tmp_path):
    devices, cfg, _ = make(tmp_path)
    devices["a"].missing_nodes.add("leave_button")
    devices["a"].missing_nodes.add("cancel_button")
    s = scenario(PAIR + "  - a: leave_call\n  - a: press_home\n")
    result = run_scenario(s, devices, cfg)
    assert [f.oracle for f in result.findings] == ["step_error"]
    assert "neither leave_button nor cancel_button" in result.findings[0].message
    assert result.records[-1].status == "skipped"


def test_crash_in_logcat_is_attributed_to_the_step_it_appeared_in(tmp_path):
    crash = ("10-22 01:00:00.000 E/AndroidRuntime( 1): FATAL EXCEPTION: main\n"
             "10-22 01:00:00.000 E/AndroidRuntime( 1): Process: com.daiatech.samvaad.app, PID: 1\n")
    # read_new() is called once per step per device (a, b order); craft chunks so it lands on step 7's read of device a
    devices, cfg, _ = make(tmp_path, logs={"a": FakeLog([""] * 6 + [crash]), "b": FakeLog()})
    s = scenario(PAIR + "  - a: {rotate: landscape}\n  - a: press_home\n")
    result = run_scenario(s, devices, cfg)
    crashes = [f for f in result.findings if f.oracle == "crash"]
    assert len(crashes) == 1
    assert crashes[0].step_index == 7 and crashes[0].severity == "critical"
    assert any("FATAL EXCEPTION" in e for e in crashes[0].evidence)
    assert crashes[0].screenshot


def test_oracle_can_be_disabled(tmp_path):
    crash = "E/AndroidRuntime( 1): FATAL EXCEPTION: main\nE/AndroidRuntime( 1): Process: com.daiatech.samvaad.app, PID: 1\n"
    devices, cfg, _ = make(tmp_path, logs={"a": FakeLog([crash]), "b": FakeLog()})
    s = scenario(PAIR, header="devices: [a, b]\nchecks: {disable: [crash]}\n")
    assert run_scenario(s, devices, cfg).verdict == "PASS"


def test_unexpected_process_death_flagged_but_kill_process_is_not(tmp_path):
    devices, cfg, _ = make(tmp_path)
    devices["a"].hooks["set_rotation"] = lambda d, m: setattr(d, "pid_value", None)
    result = run_scenario(scenario(PAIR + "  - a: {rotate: landscape}\n"), devices, cfg)
    f = [f for f in result.findings if f.oracle == "process_death"]
    assert len(f) == 1 and f[0].step_index == 6 and "unexpectedly" in f[0].message

    devices, cfg, _ = make(tmp_path / "2")
    result = run_scenario(scenario(PAIR + "  - a: kill_process\n  - a: launch_app\n"), devices, cfg)
    assert not [f for f in result.findings if f.oracle == "process_death"]


def test_helper_dying_during_another_devices_step_is_caught(tmp_path):
    devices, cfg, _ = make(tmp_path)
    devices["a"].hooks["set_rotation"] = lambda d, m: setattr(devices["b"], "pid_value", None)
    result = run_scenario(scenario(PAIR + "  - a: {rotate: landscape}\n"), devices, cfg)
    f = [f for f in result.findings if f.oracle == "process_death"]
    assert len(f) == 1 and f[0].device == "b" and f[0].step_index == 6


def test_stale_in_call_ui_after_service_gone_persists_through_debounce(tmp_path):
    devices, cfg, _ = make(tmp_path)
    devices["a"].hooks["press_home"] = lambda d: setattr(d, "services_value", {})  # service gone, UI still Ongoing
    result = run_scenario(scenario(PAIR + "  - a: press_home\n  - wait: 1s\n"), devices, cfg)
    f = [f for f in result.findings if f.oracle == "stale_call_ui"]
    assert len(f) == 1 and f[0].step_index == 6
    assert f[0].occurrences >= 1


def test_transient_inconsistency_is_debounced_away(tmp_path):
    devices, cfg, clock = make(tmp_path)
    a = devices["a"]

    def blip(dev):
        dev.notif = False  # notification momentarily missing...
        clock.on_sleep = lambda s: setattr(dev, "notif", True)  # ...back by the settle re-check

    a.hooks["press_home"] = blip
    result = run_scenario(scenario(PAIR + "  - a: press_home\n"), devices, cfg)
    assert result.verdict == "PASS", [f.message for f in result.findings]


def test_notification_missing_while_ongoing_is_flagged(tmp_path):
    devices, cfg, _ = make(tmp_path)
    devices["a"].hooks["swipe_notification"] = lambda d: setattr(d, "notif", False)
    result = run_scenario(scenario(PAIR + "  - a: swipe_notification\n"), devices, cfg)
    assert [(f.oracle, f.step_index) for f in result.findings] == [("service_notification", 6)]


def test_wake_lock_held_after_call_ended(tmp_path):
    devices, cfg, _ = make(tmp_path)

    def hang_up(d, rid, *a):
        d.idle()
        d.locks = ["PARTIAL_WAKE_LOCK 'AudioIn' uids=[1041, 10217]"]

    devices["a"].hooks["tap_resource_id"] = hang_up
    result = run_scenario(scenario(PAIR + "  - a: leave_call\n"), devices, cfg)
    f = [f for f in result.findings if f.oracle == "wake_locks"]
    assert len(f) == 1 and "AudioIn" in f[0].evidence[0]


def test_repeat_runs_the_flow_n_times_and_checks_memory_growth(tmp_path):
    devices, cfg, _ = make(tmp_path, repeat=3)
    devices["a"].pss_values = [100 * 1024, 130 * 1024, 200 * 1024]
    devices["b"].pss_values = [100 * 1024, 101 * 1024, 102 * 1024]
    s = scenario(PAIR + "  - a: press_home\n")
    result = run_scenario(s, devices, cfg)
    assert len(devices["a"].called("press_home")) == 3 and result.iterations_run == 3
    mem = [f for f in result.findings if f.oracle == "memory_growth"]
    assert len(mem) == 1 and mem[0].device == "a" and "100.0 MB" in mem[0].evidence[0]
    assert any("memory_growth on b" in n and "ok" in n for n in result.notes)


def test_memory_growth_skipped_note_with_too_few_iterations(tmp_path):
    devices, cfg, _ = make(tmp_path, repeat=2)
    devices["a"].pss_values = [1, 2]
    devices["b"].pss_values = [1, 2]
    result = run_scenario(scenario(PAIR), devices, cfg)
    assert result.verdict == "PASS" and any("skipped" in n for n in result.notes)


def test_expect_issue_negative_control_outcome(tmp_path):
    devices, cfg, _ = make(tmp_path)
    s = scenario(PAIR + "  - a: kill_process\n  - expect: {device: a, call_state: Ongoing}\n", header="devices: [a, b]\nexpect_issue: true\n")
    result = run_scenario(s, devices, cfg)
    assert result.verdict == "ISSUE FOUND" and result.outcome_ok
    # ...and a control that unexpectedly finds nothing is NOT ok
    devices, cfg, _ = make(tmp_path / "2")
    s = scenario(PAIR, header="devices: [a, b]\nexpect_issue: true\n")
    result = run_scenario(s, devices, cfg)
    assert result.verdict == "PASS" and not result.outcome_ok


def test_missing_device_and_url_are_reported(tmp_path):
    devices, cfg, _ = make(tmp_path)
    with pytest.raises(ValueError, match="needs devices"):
        run_scenario(scenario(PAIR), {"a": devices["a"]}, cfg)
    cfg.meeting_url = None
    result = run_scenario(scenario(PAIR), devices, cfg)
    assert result.findings[0].oracle == "step_error" and "QA_MEETING_URL" in result.findings[0].message


def test_single_device_flow_and_compact_flow_run(tmp_path):
    devices, cfg, _ = make(tmp_path)
    s = parse_flow("a:launch_app, a:rotate landscape, a:power_button, wait 2s, a:power_button, a:unlock, a:press_home, a:bring_to_foreground")
    result = run_scenario(s, devices, cfg)
    assert result.verdict == "PASS" and "b" not in {k for r in result.records for k in r.snapshots}


def test_reports_render_and_serialise(tmp_path):
    devices, cfg, _ = make(tmp_path)
    devices["a"].hooks["set_rotation"] = lambda d, m: setattr(d, "label", "Error")
    s = scenario(PAIR + "  - a: {rotate: landscape}\n  - expect: {device: a, call_state: Ongoing}\n")
    result = run_scenario(s, devices, cfg)
    md_path, json_path = write_report(result, tmp_path / "rep")
    md = md_path.read_text()
    assert md.startswith("# Scenario `demo`: ISSUE FOUND")
    assert "| 1 | 7 |" in md and "step 7" in md and "[findings/01-expectation-a.png]" in md
    assert "expected Ongoing, got Error" in md and "## Timeline" in md and "## Equivalent scenario steps" in md
    data = json.loads(json_path.read_text())
    assert data["verdict"] == "ISSUE FOUND" and data["findings"][0]["step_index"] == 7
    assert data["timeline"][0]["snapshots"]["a"]["pid"] == "100" or data["timeline"][0]["snapshots"]
    assert data["plan"][0] == "a: launch_app" and data["devices"] == ["a", "b"]
    assert to_dict(result)["findings"][0]["oracle"] == "expectation"
    assert "PASS" in render_markdown(run_scenario(scenario(PAIR), make(tmp_path / "x")[0], make(tmp_path / "x")[1])).splitlines()[0]
