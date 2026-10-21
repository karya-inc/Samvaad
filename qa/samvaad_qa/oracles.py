"""Oracles: always-on checks that decide "this flow has an issue", independent of what the
scenario's own expectations say.

Each detector is a *pure function* over text or a Snapshot (so it is unit-tested against canned
logcat/dumpsys samples in qa/tests/); the Oracle classes only say when the runner applies it.
Two kinds:
  LogOracle       -- scans the logcat text that appeared during a step.
  SnapshotOracle  -- judges the device-state snapshot taken after a step. Real transitions are
                     asynchronous (service stop lags the UI by a moment), so a hit is only
                     reported if it still holds `settle` seconds later.
  MemoryGrowthOracle -- end-of-run, compares PSS across `repeat` iterations.

Reliability notes for each oracle are in ORACLE_DOCS (rendered into qa/SCENARIOS.md).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable

import adb_utils

from .model import ChecksConfig, Problem, Snapshot
from .vocab import ACTIVE_CALL_STATES, ORACLE_OPTIONS


def _excerpt(lines: list[str], start: int, after: int = 4) -> list[str]:
    return [ln.rstrip() for ln in lines[start : start + after + 1]]


# -- log detectors --------------------------------------------------------------------------------

_NATIVE_FATAL_RE = re.compile(r"Fatal signal \d+ \((SIG\w+)\).*?pid \d+ \(([^)]*)\)")


def detect_crashes(text: str, package: str) -> list[Problem]:
    """FATAL EXCEPTION (AndroidRuntime) whose `Process:` line names this app, and native
    `Fatal signal` of a process named after it. Another app's crash on the same device is ignored."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if "FATAL EXCEPTION" in line:
            window = lines[i : i + 6]
            if any(f"Process: {package}" in w for w in window):
                out.append(Problem("crash", "critical", f"fatal:{lines[i + 2].strip()[-80:] if i + 2 < len(lines) else i}",
                                   "app crashed with an uncaught exception", _excerpt(lines, i, 8)))
        m = _NATIVE_FATAL_RE.search(line)
        if m and package in m.group(2):
            out.append(Problem("crash", "critical", f"native:{m.group(1)}", f"native crash ({m.group(1)})", _excerpt(lines, i, 2)))
    return out


def detect_anr(text: str, package: str) -> list[Problem]:
    lines = text.splitlines()
    return [
        Problem("anr", "critical", "anr", "ANR: the app stopped responding", _excerpt(lines, i, 3))
        for i, line in enumerate(lines)
        if re.search(rf"\bANR in {re.escape(package)}\b", line)
    ]


_LEAKED_RESOURCE_RE = re.compile(r"has leaked (window|ServiceConnection|IntentReceiver)")


def detect_leaked_resources(text: str, package: str) -> list[Problem]:
    """'Activity X has leaked window / ServiceConnection / IntentReceiver' warnings -- the
    platform's own report that a view was left attached, a bind was never unbound, or a receiver
    was never unregistered when its Activity died."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = _LEAKED_RESOURCE_RE.search(line)
        if m and package in line:
            out.append(Problem("leaked_resources", "high", f"leaked:{m.group(1)}", f"leaked {m.group(1)} reported by the platform", _excerpt(lines, i, 2)))
    return out


def detect_leakcanary(text: str, package: str = "") -> list[Problem]:
    """A *confirmed* LeakCanary leak ("N APPLICATION LEAKS", N>0) via the suite's existing
    scanner. Transient 'retained, not dumping heap yet' lines are deliberately not findings."""
    result = adb_utils.scan_logcat_for_leaks_and_crashes(text)
    if result.confirmed_leak_count == 0:
        return []
    lines = text.splitlines()
    idx = next((i for i, ln in enumerate(lines) if re.search(r"[1-9]\d* APPLICATION LEAKS", ln)), 0)
    return [Problem("leakcanary", "high", "leakcanary", f"LeakCanary confirmed {result.confirmed_leak_count} application leak(s)", _excerpt(lines, idx, 12))]


# -- snapshot detectors ----------------------------------------------------------------------------


def check_service_notification(snap: Snapshot) -> list[Problem]:
    """Foreground service + notification must track the call state the UI shows: while
    Connecting/Ongoing the notification is posted and any running service is foreground; at Idle
    neither remains. UI state unreadable (screen off, other app in front) -> no judgement."""
    if snap.pid is None or snap.call_state is None:
        return []
    sev, out = "high", []
    desc = f"call_state={snap.call_state} notification={snap.notification} services={snap.services}"
    if snap.call_state in ACTIVE_CALL_STATES:
        if not snap.notification:
            out.append(Problem("service_notification", sev, "active-no-notification", f"call is {snap.call_state} but no call notification is posted", [desc]))
        if snap.service_running and not snap.foreground_service:
            out.append(Problem("service_notification", sev, "active-not-foreground", f"call is {snap.call_state} but the call service is not in the foreground", [desc]))
    elif snap.call_state == "Idle":
        if snap.foreground_service:
            out.append(Problem("service_notification", sev, "idle-service-running", "UI is Idle but the foreground call service is still running", [desc]))
        if snap.notification:
            out.append(Problem("service_notification", sev, "idle-notification", "UI is Idle but the call notification is still posted", [desc]))
    return out


def check_stale_ui(snap: Snapshot) -> list[Problem]:
    """UI claims an in-call state while the service that owns the call is gone and the process is
    still alive -- the stale 'in call' screen a user can be left staring at."""
    if snap.pid is None or snap.call_state not in ACTIVE_CALL_STATES or snap.service_running:
        return []
    return [Problem("stale_call_ui", "high", f"stale-{snap.call_state}", f"UI shows '{snap.call_state}' but no call service is running (stale in-call UI)",
                    [f"call_state={snap.call_state} pid={snap.pid} services={snap.services}"])]


def check_wake_locks(snap: Snapshot, ever_in_call: bool) -> list[Problem]:
    """After a call that has ended (service gone, UI not in-call) the app must hold no wake lock."""
    if not ever_in_call or snap.pid is None or snap.service_running or snap.call_state in ACTIVE_CALL_STATES:
        return []
    if not snap.wake_locks:
        return []
    return [Problem("wake_locks", "medium", "wakelock:" + "|".join(sorted(snap.wake_locks))[:80], "wake lock still held after the call ended", list(snap.wake_locks))]


def check_process_death(prev_pid: str | None, snap: Snapshot, action: str | None) -> list[Problem]:
    """The app's pid changing or vanishing is only legitimate on a kill_process/reboot step."""
    if action in ("kill_process", "reboot") or prev_pid is None or snap.pid == prev_pid:
        return []
    now = snap.pid or "gone"
    return [Problem("process_death", "critical", f"death:{prev_pid}->{now}", f"app process died unexpectedly (pid {prev_pid} -> {now}) with no kill_process step",
                    [f"pid before: {prev_pid}, after: {now}"])]


def check_memory_growth(series_kb: list[int], max_growth_mb: float, min_iterations: int) -> tuple[str, float]:
    """(status, growth_mb): PSS after the last iteration vs after the first (iteration 1 is the
    warm-up baseline: class loading, JIT, caches). status: 'ok' | 'growth' | 'skipped'."""
    if len(series_kb) < min_iterations:
        return "skipped", 0.0
    growth = (series_kb[-1] - series_kb[0]) / 1024
    return ("growth" if growth > max_growth_mb else "ok"), growth


# -- oracle objects ----------------------------------------------------------------------------------


@dataclass
class LogOracle:
    name: str
    detect: Callable[[str, str], list[Problem]]


@dataclass
class SnapshotOracle:
    name: str
    settle: float = 2.0  # re-check this long after a hit; report only if it persists
    options: dict = field(default_factory=dict)


@dataclass
class MemoryGrowthOracle:
    name: str = "memory_growth"
    max_growth_mb: float = 40.0
    min_iterations: int = 3


LOG_DETECTORS: dict[str, Callable[[str, str], list[Problem]]] = {
    "crash": detect_crashes,
    "anr": detect_anr,
    "leakcanary": detect_leakcanary,
    "leaked_resources": detect_leaked_resources,
}
SNAPSHOT_ORACLES = ["service_notification", "stale_call_ui", "wake_locks", "process_death"]


def build_oracles(checks: ChecksConfig | None) -> list:
    """Instantiate the enabled oracles with their options. `checks=None` means 'default': all."""
    enabled = list(ORACLE_OPTIONS) if checks is None else checks.enabled
    options = {} if checks is None else checks.options
    out: list = []
    for name in ORACLE_OPTIONS:
        if name not in enabled:
            continue
        opts = options.get(name, {})
        if name in LOG_DETECTORS:
            out.append(LogOracle(name, LOG_DETECTORS[name]))
        elif name in SNAPSHOT_ORACLES:
            out.append(SnapshotOracle(name, settle=float(opts.get("settle", 0.0 if name == "process_death" else 2.0))))
        elif name == "memory_growth":
            default_mb = float(os.environ.get("QA_MEMORY_GROWTH_MB", 40))
            out.append(MemoryGrowthOracle(max_growth_mb=float(opts.get("max_growth_mb", default_mb)), min_iterations=int(opts.get("min_iterations", 3))))
    return out


def apply_snapshot_oracle(oracle: SnapshotOracle, snap: Snapshot, prev_pid: str | None, action: str | None, ever_in_call: bool) -> list[Problem]:
    if oracle.name == "service_notification":
        return check_service_notification(snap)
    if oracle.name == "stale_call_ui":
        return check_stale_ui(snap)
    if oracle.name == "wake_locks":
        return check_wake_locks(snap, ever_in_call)
    if oracle.name == "process_death":
        return check_process_death(prev_pid, snap, action)
    return []


# One-paragraph truth about each oracle: what it catches, what it cannot, how far to trust it.
# Rendered verbatim into qa/SCENARIOS.md by hand -- keep the two in sync.
ORACLE_DOCS = {
    "crash": ("critical", "high", "FATAL EXCEPTION whose Process: is the app, or a native Fatal signal in an app-named process."),
    "anr": ("critical", "high", "'ANR in <package>' reported by ActivityManager."),
    "leakcanary": ("high", "high", "LeakCanary 'N APPLICATION LEAKS' with N>0. Needs the debug build (LeakCanary) and a heap dump to finish within the run."),
    "leaked_resources": ("high", "high", "'Activity ... has leaked window/ServiceConnection/IntentReceiver' for the app."),
    "service_notification": ("high", "medium", "Notification / foreground service inconsistent with the call state the UI shows (debounced)."),
    "stale_call_ui": ("high", "medium", "UI shows Connecting/Ongoing but no call service is running (debounced)."),
    "wake_locks": ("medium", "medium", "App-owned wake lock still held after the call ended (debounced)."),
    "process_death": ("critical", "high", "App pid changed/vanished without a kill_process or reboot step."),
    "memory_growth": ("medium", "low", "PSS growth across repeat iterations beyond a threshold; noisy on small repeat counts."),
}
