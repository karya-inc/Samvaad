"""CLI behaviour that must work with no device: --list, --dry-run, --flow, --save, errors."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import run

RUN_PY = Path(run.__file__)


def cli(*args: str) -> subprocess.CompletedProcess:
    # A subprocess with no ANDROID_HOME/adb on PATH proves --dry-run never touches a device.
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
    return subprocess.run([sys.executable, str(RUN_PY), *args], capture_output=True, text=True, env=env, timeout=60)


def test_list_shows_committed_scenarios():
    r = cli("--list")
    assert r.returncode == 0
    assert "rotate-then-power-button-during-call" in r.stdout and "negative-control-kill-mid-call" in r.stdout


def test_dry_run_by_name_prints_resolved_plan():
    r = cli("--dry-run", "--run", "rotate-then-power-button-during-call")
    assert r.returncode == 0, r.stderr
    assert "a: rotate to=landscape" in r.stdout and "no device touched" in r.stdout
    assert "wait_for a: call_state=Ongoing timeout=45s" in r.stdout


def test_dry_run_flow_prints_equivalent_yaml_and_saves(tmp_path):
    out = tmp_path / "my-bug.yaml"
    r = cli("--dry-run", "--flow", "a:launch_app, a:rotate landscape, a:power_button", "--name", "my-bug", "--save", str(out))
    assert r.returncode == 0, r.stderr
    assert "  - a: rotate landscape" in r.stdout
    assert out.read_text().startswith("name: my-bug")
    # the saved file is itself a valid scenario
    assert cli("--dry-run", "--run", str(out)).returncode == 0


def test_dry_run_repeat_override():
    r = cli("--dry-run", "--repeat", "7", "--run", "rotation-no-leak")
    assert "repeat=7" in r.stdout


def test_invalid_scenario_exits_2_with_file_and_line(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\nsteps:\n  - a: launch_app\n  - a: rotat landscape\n")
    r = cli("--dry-run", "--run", str(bad))
    assert r.returncode == 2 and f"{bad}:4: error: unknown step 'rotat'" in r.stderr


def test_bad_flow_exits_2():
    r = cli("--dry-run", "--flow", "a:launch_app, a:explode")
    assert r.returncode == 2 and "unknown step 'explode'" in r.stderr


def test_nothing_to_do_and_list_steps():
    assert cli().returncode == 2
    r = cli("--list-steps")
    assert r.returncode == 0 and "power_button" in r.stdout and "memory_growth" in r.stdout


def test_run_without_meeting_url_is_a_clear_error():
    r = cli("--run", "rotation-no-leak")
    assert r.returncode == 2 and "QA_MEETING_URL" in r.stderr
