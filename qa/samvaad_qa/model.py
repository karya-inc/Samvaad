"""Plain data classes shared by the parser, executor, oracles and report writer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Issue:
    """One validation message. level: 'error' (scenario is rejected) or 'warning'."""

    level: str
    line: int | None
    message: str


class ScenarioValidationError(Exception):
    def __init__(self, source: str, issues: list[Issue]):
        self.source = source
        self.issues = issues
        super().__init__("\n".join(format_issue(source, i) for i in issues))


def format_issue(source: str, issue: Issue) -> str:
    where = f"{source}:{issue.line}" if issue.line else source
    return f"{where}: {issue.level}: {issue.message}"


@dataclass
class Step:
    index: int  # 1-based position after `all:` expansion; what reports and findings cite
    line: int | None  # line in the scenario file (None for an ad-hoc --flow)
    kind: str  # action | wait | wait_for | expect
    device: str | None = None
    action: str | None = None  # canonical action name (aliases resolved)
    args: dict = field(default_factory=dict)  # actions: normalized args; wait: {"seconds"}; conditions: see vocab
    timeout: float | None = None  # wait_for only

    def describe(self) -> str:
        if self.kind == "wait":
            return f"wait {self.args['seconds']:g}s"
        if self.kind in ("wait_for", "expect"):
            conds = " ".join(f"{k}={_short(v)}" for k, v in self.args.items())
            extra = f" timeout={self.timeout:g}s" if self.timeout is not None else ""
            return f"{self.kind} {self.device}: {conds}{extra}"
        args = " ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.device}: {self.action}" + (f" {args}" if args else "")


def _short(value: object) -> str:
    if isinstance(value, list):
        return "|".join(_short(v) for v in value)
    if isinstance(value, dict):
        return ",".join(f"{k}:{v}" for k, v in value.items())
    return str(value)


@dataclass
class ChecksConfig:
    enabled: list[str]
    options: dict[str, dict] = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    steps: list[Step]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=lambda: ["a", "b"])
    meeting: str = "env"
    repeat: int = 1
    checks: ChecksConfig | None = None
    expect_issue: bool = False  # negative control: the run must find an issue (pytest passes iff it does)
    source: str = "<ad-hoc>"
    warnings: list[Issue] = field(default_factory=list)

    @property
    def source_name(self) -> str:
        return Path(self.source).name if self.source != "<ad-hoc>" else self.source


@dataclass
class Snapshot:
    """What the harness can observe about one device at one instant. Everything the
    snapshot-based oracles judge comes from here, so they can be unit-tested with canned values."""

    device: str
    time: float  # epoch seconds
    pid: str | None = None
    call_state: str | None = None  # call_state_label text; None = app UI not on screen / unreadable
    notification: bool = False
    services: dict = field(default_factory=dict)  # {component: is_foreground}
    awake: bool | None = None
    wake_locks: list = field(default_factory=list)  # descriptions of app-owned held wake locks

    @property
    def service_running(self) -> bool:
        return bool(self.services)

    @property
    def foreground_service(self) -> bool:
        return any(self.services.values())

    def brief(self) -> dict:
        return {
            "call_state": self.call_state, "notification": self.notification, "pid": self.pid,
            "foreground_service": self.foreground_service, "service_running": self.service_running,
            "screen_awake": self.awake, "wake_locks": len(self.wake_locks),
        }


@dataclass
class Problem:
    """A raw oracle hit, before the runner decides which step/iteration it belongs to."""

    oracle: str
    severity: str
    key: str  # dedupe key: the same problem seen again is one finding with more occurrences
    message: str
    evidence: list = field(default_factory=list)


@dataclass
class Finding:
    oracle: str
    severity: str  # critical | high | medium | low
    device: str | None
    step_index: int | None
    iteration: int
    timestamp: str
    message: str
    evidence: list = field(default_factory=list)
    screenshot: str | None = None
    ui_dump: str | None = None
    occurrences: int = 1
