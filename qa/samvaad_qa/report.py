"""report.json (machine-readable) and report.md (human-readable) for one scenario run."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .compact import step_yaml
from .runner import RunResult


def to_dict(result: RunResult) -> dict:
    s = result.scenario
    return {
        "scenario": s.name,
        "source": s.source,
        "description": s.description,
        "tags": s.tags,
        "devices": s.devices,
        "repeat": result.iterations_run,
        "expect_issue": s.expect_issue,
        "verdict": result.verdict,
        "outcome_as_intended": result.outcome_ok,
        "aborted": result.aborted,
        "started": result.started,
        "finished": result.finished,
        "duration_s": round(result.duration_s, 1),
        "plan": [step_yaml(st) for st in s.steps],
        "timeline": [asdict(r) for r in result.records],
        "findings": [asdict(f) for f in result.findings],
        "notes": result.notes + [f"warning: {w.message}" for w in s.warnings],
        "memory": result.memory,
        "logcats": result.logcats,
    }


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(result: RunResult) -> str:
    s = result.scenario
    verdict = result.verdict
    if s.expect_issue:
        verdict += " (negative control: an issue was expected -- " + ("as intended)" if result.has_issue else "BUT NONE FOUND: the framework failed to detect it)")
    lines = [
        f"# Scenario `{s.name}`: {verdict}",
        "",
        f"- Source: `{s.source}`",
        f"- Description: {s.description or '-'}",
        f"- Devices: {', '.join(s.devices)} | iterations run: {result.iterations_run} | duration: {result.duration_s:.0f}s",
        f"- Started: {result.started}",
    ]
    if result.aborted:
        lines.append(f"- **Run aborted early:** {result.aborted}")
    lines += ["", f"## Findings ({len(result.findings)})", ""]
    if not result.findings:
        lines += ["None.", ""]
    for i, f in enumerate(result.findings, 1):
        where = f"step {f.step_index}" if f.step_index else "end of run"
        lines += [
            f"### {i}. [{f.severity.upper()}] {f.oracle}: {f.message}",
            "",
            f"- First appeared: {where}, iteration {f.iteration}, {f.timestamp}, device `{f.device}`"
            + (f" ({f.occurrences} occurrences)" if f.occurrences > 1 else ""),
        ]
        if f.screenshot:
            lines.append(f"- Screenshot: [{f.screenshot}]({f.screenshot})")
        if f.ui_dump:
            lines.append(f"- UI dump: [{f.ui_dump}]({f.ui_dump})")
        if f.evidence:
            lines += ["- Evidence:", "", "```"] + [e for e in f.evidence] + ["```"]
        lines.append("")
    lines += [
        "## Timeline", "",
        "| iter | step | time | action | status | device state after step | detail |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in result.records:
        state = "; ".join(
            f"{d}: {v['call_state'] or 'ui?'}, notif={'y' if v['notification'] else 'n'}, pid={v['pid'] or '-'}, fgs={'y' if v['foreground_service'] else 'n'}"
            for d, v in r.snapshots.items()
        )
        flag = f" (findings {', '.join(str(i + 1) for i in r.findings)})" if r.findings else ""
        lines.append(f"| {r.iteration} | {r.index} | +{r.elapsed_s:.1f}s | `{_md_escape(r.text)}` | {r.status}{flag} | {_md_escape(state)} | {_md_escape(r.detail)} |")
    if result.memory:
        lines += ["", "## Memory (PSS)", "", "| device | label | iteration | PSS MB |", "|---|---|---|---|"]
        for dev, rows in result.memory.items():
            for row in rows:
                lines.append(f"| {dev} | {row['label']} | {row['iteration']} | {row['pss_kb'] / 1024:.1f} |")
    notes = result.notes + [f"warning: {w.message}" for w in s.warnings]
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    lines += ["", "## Artifacts", ""]
    for dev, files in result.logcats.items():
        lines += [f"- logcat `{dev}`: " + ", ".join(f"[{f}]({f})" for f in files)]
    lines += ["- screenshots: [screenshots/](screenshots/), findings: [findings/](findings/)", "", "## Equivalent scenario steps", "", "```yaml"]
    lines += [f"  - {step_yaml(st)}" for st in s.steps] + ["```", ""]
    return "\n".join(lines)


def write_report(result: RunResult, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / "report.md", out_dir / "report.json"
    md.write_text(render_markdown(result))
    js.write_text(json.dumps(to_dict(result), indent=2, default=str))
    return md, js
