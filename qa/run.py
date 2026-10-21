#!/usr/bin/env python3
"""Standalone CLI for the scenario suite.  See qa/SCENARIOS.md.

  python3 qa/run.py --list
  python3 qa/run.py --dry-run --run rotate-then-power-button-during-call
  QA_MEETING_URL=... python3 qa/run.py --run rotate-then-power-button-during-call
  QA_MEETING_URL=... python3 qa/run.py --flow "a:launch_app, a:join_call, b:launch_app, b:join_call, a:rotate landscape"

Exit status: 0 = every scenario behaved as intended (PASS, or ISSUE FOUND for an `expect_issue`
negative control); 1 = an issue was found (or a negative control found none); 2 = bad input.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from samvaad_qa.compact import parse_flow, to_yaml  # noqa: E402
from samvaad_qa.model import Scenario, ScenarioValidationError, format_issue  # noqa: E402
from samvaad_qa.oracles import ORACLE_DOCS  # noqa: E402
from samvaad_qa.schema import load_scenario  # noqa: E402
from samvaad_qa.vocab import DEVICE_STEPS  # noqa: E402

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def discover() -> tuple[list[Scenario], list[ScenarioValidationError]]:
    good, bad = [], []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        try:
            good.append(load_scenario(path))
        except ScenarioValidationError as exc:
            bad.append(exc)
    return good, bad


def resolve(ref: str) -> list[Scenario]:
    """A --run argument: 'all', a scenario name, or a path to a YAML file."""
    if ref == "all":
        good, bad = discover()
        if bad:
            raise bad[0]
        return good
    path = Path(ref)
    if not path.exists():
        path = SCENARIOS_DIR / f"{ref}.yaml"
    if not path.exists():
        names = ", ".join(p.stem for p in sorted(SCENARIOS_DIR.glob("*.yaml")))
        raise SystemExit(f"no scenario '{ref}' (not a file, and not one of: {names})")
    return [load_scenario(path)]


def print_plan(s: Scenario, repeat: int | None) -> None:
    print(f"scenario {s.name}  ({s.source_name})")
    print(f"  {s.description}")
    print(f"  devices={','.join(s.devices)} meeting={s.meeting} repeat={repeat or s.repeat} tags={','.join(s.tags) or '-'}"
          + ("  [negative control: an issue is expected]" if s.expect_issue else ""))
    checks = "default (all oracles)" if s.checks is None else ", ".join(s.checks.enabled) or "none"
    print(f"  checks: {checks}")
    for st in s.steps:
        print(f"  {st.index:>3}. {st.describe()}" + (f"    (line {st.line})" if st.line else ""))
    for w in s.warnings:
        print("  " + format_issue(s.source_name, w))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run Samvaad emulator scenarios.", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--list", action="store_true", help="list scenarios in qa/scenarios/")
    p.add_argument("--list-steps", action="store_true", help="print the step vocabulary and the oracles")
    p.add_argument("--run", action="append", default=[], metavar="NAME|PATH|all", help="scenario name, YAML path, or 'all' (repeatable)")
    p.add_argument("--flow", metavar='"a:launch_app, ..."', help="run an ad-hoc one-line flow")
    p.add_argument("--name", help="name for the --flow scenario (default adhoc-<hash>)")
    p.add_argument("--save", metavar="PATH", help="with --flow: write the equivalent scenario YAML here (e.g. qa/scenarios/my-bug.yaml)")
    p.add_argument("--dry-run", action="store_true", help="validate and print the resolved plan; touches no device")
    p.add_argument("--repeat", type=int, metavar="N", help="override the scenario's repeat")
    p.add_argument("--keep-emulators", action="store_true", help="leave emulators running afterwards (QA_KEEP_EMULATORS=1)")
    p.add_argument("--skip-build", action="store_true", help="reuse the APK on disk (QA_SKIP_BUILD=1)")
    p.add_argument("--headless", action="store_true", help="boot emulators without a window (QA_HEADLESS=1)")
    p.add_argument("--meeting-url", help="meeting URL (default: $QA_MEETING_URL)")
    p.add_argument("--artifacts-dir", help="where reports go (default qa/.artifacts, or $QA_ARTIFACTS_DIR)")
    args = p.parse_args(argv)

    if args.list_steps:
        for name, spec in DEVICE_STEPS.items():
            if name == spec.name:
                print(f"{spec.usage():<60} {spec.summary}")
        print("\noracles:")
        for name, (sev, rel, text) in ORACLE_DOCS.items():
            print(f"  {name:<22} severity={sev:<8} reliability={rel:<6} {text}")
        return 0

    if args.list:
        good, bad = discover()
        for s in good:
            print(f"{s.name:<45} devices={','.join(s.devices):<4} steps={len(s.steps):<3} repeat={s.repeat} tags={','.join(s.tags)}")
            print(f"    {s.description}")
        for exc in bad:
            print(f"INVALID {exc.source}:\n  " + str(exc).replace("\n", "\n  "))
        return 2 if bad else 0

    scenarios: list[Scenario] = []
    try:
        for ref in args.run:
            scenarios += resolve(ref)
        if args.flow:
            flow = parse_flow(args.flow, name=args.name, repeat=args.repeat or 1)
            print("# Equivalent scenario file (commit it as qa/scenarios/<name>.yaml for a regression test):")
            print(to_yaml(flow))
            if args.save:
                Path(args.save).write_text(to_yaml(flow))
                print(f"# saved to {args.save}")
            scenarios.append(flow)
    except ScenarioValidationError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not scenarios:
        p.print_usage(sys.stderr)
        print("nothing to do: pass --list, --run, --flow or --list-steps", file=sys.stderr)
        return 2

    if args.dry_run:
        for s in scenarios:
            print_plan(s, args.repeat)
        print(f"dry run: {len(scenarios)} scenario(s) valid, no device touched")
        return 0

    if args.headless:
        os.environ["QA_HEADLESS"] = "1"
    meeting_url = args.meeting_url or os.environ.get("QA_MEETING_URL")
    if not meeting_url and any(st.action == "join_call" for s in scenarios for st in s.steps):
        print("a scenario joins a call but no meeting URL is set: export QA_MEETING_URL=<room url> (or --meeting-url)", file=sys.stderr)
        return 2

    from samvaad_qa.report import write_report
    from samvaad_qa.runner import RunConfig, run_scenario
    from samvaad_qa.session import ARTIFACTS_DIR, Fleet, SetupError, build_apk, locate_sdk

    artifacts_root = Path(args.artifacts_dir) if args.artifacts_dir else ARTIFACTS_DIR
    try:
        locate_sdk()
        fleet = Fleet(build_apk(args.skip_build), keep=args.keep_emulators)
    except SetupError as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2

    worst = 0
    try:
        for s in scenarios:
            devices = fleet.ensure(s.devices)
            out = artifacts_root / s.name
            _clean(out)
            print(f"\n=== {s.name} ({len(s.steps)} steps x {args.repeat or s.repeat}) ===", flush=True)
            result = run_scenario(s, devices, RunConfig(
                artifacts_dir=out, meeting_url=meeting_url, repeat=args.repeat, logcat_factory=fleet.logcat_factory,
                on_step_done=lambda r: print(f"  [{r.iteration}.{r.index:>2}] {r.status:<7} {r.text}" + (f"  -> {r.detail}" if r.status == "failed" else ""), flush=True),
            ))
            md, _ = write_report(result, out)
            print(f"VERDICT {s.name}: {result.verdict}" + (" (expected: negative control)" if s.expect_issue and result.has_issue else ""))
            for f in result.findings:
                print(f"  - [{f.severity}] {f.oracle} @step {f.step_index}: {f.message}")
            print(f"  report: {md}")
            if not result.outcome_ok:
                worst = 1
    finally:
        fleet.shutdown()
    return worst


def _clean(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
