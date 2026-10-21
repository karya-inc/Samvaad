"""Runs a Scenario against already-prepared devices and produces a RunResult.

For each step: execute it, snapshot device state, feed the logcat that appeared during the step
to the log oracles and the snapshot to the snapshot oracles. A finding is stamped with the step
and time at which it *first* appeared; repeats of the same problem only bump `occurrences`.

Booting emulators, building the APK and writing the report are not done here (session.py,
report.py): this takes `devices` + a config, so it runs unchanged against fakes in unit tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .executor import ExecEnv, StepFailure, check_conditions, execute_action, wait_for_conditions
from .model import Finding, Problem, Scenario, Snapshot, Step
from .oracles import (
    LogOracle, MemoryGrowthOracle, SnapshotOracle, apply_snapshot_oracle, build_oracles, check_memory_growth,
)
from .vocab import ACTIVE_CALL_STATES


@dataclass
class RunConfig:
    artifacts_dir: Path
    meeting_url: str | None = None
    repeat: int | None = None  # overrides scenario.repeat when set
    package: str = "com.daiatech.samvaad.app"
    logcat_factory: Callable | None = None  # (device name, Path) -> object with read_new()/stop()
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    clock: Callable[[], float] = time.monotonic
    on_step_done: Callable | None = None  # hook: called with each StepRecord (a generator/shrinker can steer from it)


@dataclass
class StepRecord:
    iteration: int
    index: int
    text: str
    device: str | None
    started: str
    elapsed_s: float  # seconds since the run started, at the end of the step
    duration_s: float
    status: str  # ok | failed | skipped
    detail: str = ""
    snapshots: dict = field(default_factory=dict)  # device -> Snapshot.brief()
    findings: list = field(default_factory=list)  # indices into RunResult.findings


@dataclass
class RunResult:
    scenario: Scenario
    started: str
    finished: str = ""
    duration_s: float = 0.0
    iterations_run: int = 0
    records: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    memory: dict = field(default_factory=dict)
    logcats: dict = field(default_factory=dict)  # device -> relative path
    aborted: str | None = None

    @property
    def has_issue(self) -> bool:
        return bool(self.findings)

    @property
    def verdict(self) -> str:
        return "ISSUE FOUND" if self.findings else "PASS"

    @property
    def outcome_ok(self) -> bool:
        """Did the run behave as the scenario intended? (An expect_issue negative control is
        'ok' when it DID find an issue.)"""
        return self.has_issue == self.scenario.expect_issue


def take_snapshot(name: str, dev, now: float) -> Snapshot:
    pid = dev.pid()
    awake = dev.screen_awake()
    label = dev.call_state_label(timeout=3) if (pid and awake) else None
    return Snapshot(
        device=name, time=now, pid=pid, call_state=label, notification=dev.notification_posted(),
        services=dev.services(), awake=awake, wake_locks=[str(w) for w in dev.wake_locks()] if pid else [],
    )


class _Run:
    def __init__(self, scenario: Scenario, devices: dict, config: RunConfig):
        self.s, self.devices, self.cfg = scenario, devices, config
        self.dir = config.artifacts_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t0 = config.now()
        self.result = RunResult(scenario, self._iso(self.t0))
        self.oracles = build_oracles(scenario.checks)
        self.log_oracles = [o for o in self.oracles if isinstance(o, LogOracle)]
        self.snap_oracles = [o for o in self.oracles if isinstance(o, SnapshotOracle)]
        self.memory_oracle = next((o for o in self.oracles if isinstance(o, MemoryGrowthOracle)), None)
        self.env = ExecEnv(config.meeting_url, self.dir, sleep=config.sleep, clock=config.clock)
        self.prev_pid = {n: d.pid() for n, d in devices.items()}
        self.ever_in_call = {n: False for n in devices}
        self.logs: dict = {}
        self.finding_index: dict = {}  # (oracle, device, key) -> index in result.findings
        self.reboots = 0

    def _iso(self, t: float) -> str:
        return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")

    # -- logcat -------------------------------------------------------------------------------

    def start_logs(self, suffix: str = "") -> None:
        for name in self.devices:
            if self.cfg.logcat_factory:
                path = self.dir / f"logcat_{name}{suffix}.log"
                self.logs[name] = self.cfg.logcat_factory(name, path)
                self.result.logcats.setdefault(name, []).append(path.name)

    def drain_logs(self, step: Step | None, iteration: int) -> list[int]:
        new = []
        for name, stream in self.logs.items():
            text = stream.read_new()
            if not text:
                continue
            for oracle in self.log_oracles:
                for problem in oracle.detect(text, self.cfg.package):
                    new.append(self.add_finding(problem, name, step, iteration))
        return new

    # -- findings -----------------------------------------------------------------------------

    def add_finding(self, problem: Problem, device: str | None, step: Step | None, iteration: int, capture: bool = True) -> int:
        key = (problem.oracle, device, problem.key)
        if key in self.finding_index:
            self.result.findings[self.finding_index[key]].occurrences += 1
            return self.finding_index[key]
        n = len(self.result.findings) + 1
        finding = Finding(
            oracle=problem.oracle, severity=problem.severity, device=device, step_index=step.index if step else None,
            iteration=iteration, timestamp=self._iso(self.cfg.now()), message=problem.message, evidence=list(problem.evidence),
        )
        dev = self.devices.get(device) if device else None
        if capture and dev is not None:
            fdir = self.dir / "findings"
            fdir.mkdir(exist_ok=True)
            stem = f"{n:02d}-{problem.oracle}-{device}"
            try:
                dev.screenshot(str(fdir / f"{stem}.png"))
                finding.screenshot = f"findings/{stem}.png"
            except Exception:  # evidence capture must never mask the finding itself
                pass
            try:
                if dev.dump_ui_to(str(fdir / f"{stem}.xml")):
                    finding.ui_dump = f"findings/{stem}.xml"
            except Exception:
                pass
        self.result.findings.append(finding)
        self.finding_index[key] = n - 1
        return n - 1

    # -- snapshot oracles ---------------------------------------------------------------------

    def judge(self, name: str, snap: Snapshot, step: Step, iteration: int, with_settle: bool = True) -> list[int]:
        if snap.call_state in ACTIVE_CALL_STATES or snap.service_running:
            self.ever_in_call[name] = True
        action = step.action if step.device == name else None
        hits = {}
        for o in self.snap_oracles:
            for p in apply_snapshot_oracle(o, snap, self.prev_pid[name], action, self.ever_in_call[name]):
                hits[(o.name, p.key)] = (o, p)
        self.prev_pid[name] = snap.pid
        if not hits:
            return []
        # Debounce: real transitions are async (the service stops a moment after the UI says
        # Idle). Re-snapshot after the longest settle and keep only what still holds.
        settle = max(o.settle for o, _ in hits.values())
        if with_settle and settle > 0:
            self.cfg.sleep(settle)
            snap2 = take_snapshot(name, self.devices[name], self.cfg.now())
            still = {}
            for o in self.snap_oracles:
                for p in apply_snapshot_oracle(o, snap2, snap.pid, action, self.ever_in_call[name]):
                    still[(o.name, p.key)] = (o, p)
            hits = {k: v for k, v in hits.items() if k in still}
            self.prev_pid[name] = snap2.pid
        return [self.add_finding(p, name, step, iteration) for _, p in hits.values()]

    # -- the loop -----------------------------------------------------------------------------

    def execute(self, step: Step, iteration: int) -> tuple[str, str, list[int]]:
        """Returns (status, detail, finding indices) for one step."""
        new: list[int] = []
        if step.kind == "wait":
            self.cfg.sleep(step.args["seconds"])
            return "ok", f"waited {step.args['seconds']:g}s", new
        dev = self.devices[step.device]
        if step.kind == "action":
            try:
                detail = execute_action(step, dev, self.env) or ""
            except StepFailure as exc:
                new.append(self.add_finding(Problem("step_error", "high", f"step:{step.index}", f"step could not be executed: {exc}"), step.device, step, iteration))
                self.result.aborted = f"step {step.index} ({step.describe()}): {exc}"
                return "failed", str(exc), new
            if step.action == "reboot":
                self.reboots += 1
                for stream in self.logs.values():
                    stream.stop()
                self.start_logs(f".after-reboot{self.reboots}")
            return "ok", detail, new
        if step.kind == "expect":
            failures = check_conditions(dev, step.args)
        else:
            failures = wait_for_conditions(dev, step.args, step.timeout or 30.0, self.env)
        if not failures:
            return "ok", "holds", new
        verb = "expectation failed" if step.kind == "expect" else f"wait_for timed out after {step.timeout:g}s"
        message = f"{step.device}: {verb}: " + "; ".join(failures)
        new.append(self.add_finding(Problem("expectation", "high", f"expect:{step.index}", message, failures), step.device, step, iteration))
        if step.kind == "wait_for":  # a failed precondition: the rest of the flow is meaningless
            self.result.aborted = f"step {step.index} ({step.describe()}) timed out"
            return "failed", message, new
        return "failed", message, new

    def snapshot_targets(self, step: Step) -> list[str]:
        if step.kind == "wait" or step.device is None:
            return list(self.devices)
        return [step.device]

    def run(self) -> RunResult:
        self.start_logs()
        iterations = self.cfg.repeat or self.s.repeat
        for iteration in range(1, iterations + 1):
            self.env.iteration = iteration
            for step in self.s.steps:
                started = self.cfg.now()
                if self.result.aborted:
                    self.result.records.append(StepRecord(iteration, step.index, step.describe(), step.device, self._iso(started), started - self.t0, 0.0, "skipped", "skipped: earlier step aborted the run"))
                    continue
                status, detail, new = self.execute(step, iteration)
                snaps = {}
                for name in self.snapshot_targets(step):
                    snap = take_snapshot(name, self.devices[name], self.cfg.now())
                    snaps[name] = snap.brief()
                    new += self.judge(name, snap, step, iteration)
                # Cheap pid poll for devices we did not snapshot: a helper dying during someone
                # else's step must still be caught at the step where it happened.
                for name, dev in self.devices.items():
                    if name not in snaps:
                        pid = dev.pid()
                        if pid != self.prev_pid[name]:
                            snap = Snapshot(name, self.cfg.now(), pid=pid, awake=None)
                            hits = [p for o in self.snap_oracles if o.name == "process_death" for p in apply_snapshot_oracle(o, snap, self.prev_pid[name], None, False)]
                            new += [self.add_finding(p, name, step, iteration) for p in hits]
                            self.prev_pid[name] = pid
                new += self.drain_logs(step, iteration)
                end = self.cfg.now()
                rec = StepRecord(iteration, step.index, step.describe(), step.device, self._iso(started), end - self.t0, end - started, status, detail, snaps, sorted(set(new)))
                self.result.records.append(rec)
                if self.cfg.on_step_done:
                    self.cfg.on_step_done(rec)
            self.result.iterations_run = iteration if not self.result.aborted else iteration
            if self.memory_oracle and not self.result.aborted:
                for name, dev in self.devices.items():
                    pss = dev.pss_kb()
                    if pss is not None:
                        self.env.memory.setdefault(name, []).append({"label": f"iteration-{iteration}-end", "iteration": iteration, "pss_kb": pss, "auto": True})
            if self.result.aborted:
                break
        self.finish()
        return self.result

    def finish(self) -> None:
        self.cfg.sleep(1.0)  # let the last logcat lines land
        last = self.s.steps[-1] if self.s.steps else None
        self.drain_logs(last, self.result.iterations_run)
        for stream in self.logs.values():
            stream.stop()
        self.result.memory = self.env.memory
        if self.memory_oracle:
            o = self.memory_oracle
            for name, rows in self.env.memory.items():
                series = [r["pss_kb"] for r in rows if r.get("auto")]
                status, growth = check_memory_growth(series, o.max_growth_mb, o.min_iterations)
                if status == "skipped":
                    if (self.cfg.repeat or self.s.repeat) > 1 or any(not r.get("auto") for r in rows):
                        self.result.notes.append(f"memory_growth on {name}: skipped, needs >= {o.min_iterations} completed iterations (have {len(series)})")
                elif status == "growth":
                    msg = f"PSS grew {growth:.1f} MB across {len(series)} iterations (threshold {o.max_growth_mb:g} MB)"
                    ev = [f"{r['label']}: {r['pss_kb'] / 1024:.1f} MB" for r in rows if r.get("auto")]
                    self.add_finding(Problem("memory_growth", "medium", "growth", msg, ev), name, last, self.result.iterations_run, capture=False)
                else:
                    self.result.notes.append(f"memory_growth on {name}: {growth:+.1f} MB over {len(series)} iterations (threshold {o.max_growth_mb:g} MB): ok")
        self.result.finished = self._iso(self.cfg.now())
        self.result.duration_s = self.cfg.now() - self.t0


def run_scenario(scenario: Scenario, devices: dict, config: RunConfig) -> RunResult:
    """devices: {"a": Device, "b": Device} for exactly the names scenario.devices declares."""
    missing = [d for d in scenario.devices if d not in devices]
    if missing:
        raise ValueError(f"scenario {scenario.name} needs devices {missing} that were not provided")
    return _Run(scenario, {d: devices[d] for d in scenario.devices}, config).run()
