"""Runs every qa/scenarios/*.yaml as a test.

  pytest -m emulator qa/                         # all scenarios on real emulators
  pytest -m emulator qa/ -k rotation             # select by scenario name
  QA_REPEAT=5 pytest -m emulator qa/ -k leak     # override every scenario's `repeat`

Each scenario writes qa/.artifacts/<name>/report.md + report.json. A scenario test fails when the
run did not behave as intended: any finding for a normal scenario, or *no* finding for an
`expect_issue: true` negative control.

test_scenario_file_is_valid is a plain offline test (no emulator) so a typo in a scenario file
is caught by an ordinary `pytest qa/` run.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from samvaad_qa.model import ScenarioValidationError
from samvaad_qa.report import write_report
from samvaad_qa.runner import RunConfig, run_scenario
from samvaad_qa.schema import load_scenario
from samvaad_qa.session import ARTIFACTS_DIR, SCENARIOS_DIR

FILES = sorted(SCENARIOS_DIR.glob("*.yaml"))


def _load(path: Path):
    try:
        return load_scenario(path)
    except ScenarioValidationError:
        return None  # reported by test_scenario_file_is_valid; the live test skips it


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_scenario_file_is_valid(path: Path):
    load_scenario(path)


@pytest.mark.emulator
@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_scenario(path: Path, request: pytest.FixtureRequest):
    scenario = _load(path)
    if scenario is None:
        pytest.skip("invalid scenario file; see test_scenario_file_is_valid")
    meeting_url = os.environ.get("QA_MEETING_URL")
    if scenario.meeting == "env" and any(s.action == "join_call" for s in scenario.steps) and not meeting_url:
        pytest.skip("Set QA_MEETING_URL to a real provider test-room URL to run call-join scenarios")

    fleet = request.getfixturevalue("fleet")  # only now: validation/skip paths must not boot emulators
    devices = fleet.ensure(scenario.devices)
    out = ARTIFACTS_DIR / scenario.name
    shutil.rmtree(out, ignore_errors=True)
    repeat = int(os.environ["QA_REPEAT"]) if os.environ.get("QA_REPEAT") else None
    result = run_scenario(
        scenario, devices,
        RunConfig(artifacts_dir=out, meeting_url=meeting_url, repeat=repeat, logcat_factory=fleet.logcat_factory),
    )
    if any(s.action == "join_call" for s in scenario.steps):
        fleet.note_call_activity()
    md, _ = write_report(result, out)

    summary = "\n".join(f"[{f.severity}] {f.oracle} @step {f.step_index}: {f.message}" for f in result.findings)
    if scenario.expect_issue:
        assert result.has_issue, f"negative control found NO issue -- the framework failed to detect it (report: {md})"
    else:
        assert not result.has_issue, f"{result.verdict}: {scenario.name}\n{summary}\nreport: {md}"
