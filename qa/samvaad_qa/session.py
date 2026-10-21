"""The live environment: find the SDK, build/install the APK, boot the emulators a scenario needs,
reset them to a known state between scenarios, and shut them down.

Shared by the CLI (qa/run.py) and the pytest integration (qa/conftest.py) so both behave the same.
Emulators are booted lazily -- a scenario that only declares device `a` never boots `b` -- and
reused across scenarios in one session (state is reset between them, not re-booted).
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path

import adb_utils
from adb_utils import Device, EmulatorProcess, LogcatCapture, boot_emulators

PACKAGE = "com.daiatech.samvaad.app"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_DIR = Path(os.environ.get("QA_ARTIFACTS_DIR", REPO_ROOT / "qa" / ".artifacts"))
SCENARIOS_DIR = REPO_ROOT / "qa" / "scenarios"

# Scenario device name -> AVD. a = the primary actor, b = the helper participant. Override with
# QA_AVD_A / QA_AVD_B (see qa/scripts/create_low_end_avds.sh for how these AVDs are made).
DEVICE_AVDS = {
    "a": os.environ.get("QA_AVD_A", "leak_test_low_1"),
    "b": os.environ.get("QA_AVD_B", "leak_test_low_2"),
}


class SetupError(RuntimeError):
    """The environment cannot run live scenarios (message says what to fix)."""


def locate_sdk() -> str:
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not home:
        raise SetupError("ANDROID_HOME/ANDROID_SDK_ROOT not set -- see qa/README.md")
    adb_utils.ANDROID_HOME = home
    if shutil.which("adb", path=f"{home}/platform-tools") is None:
        raise SetupError(f"adb not found under {home}/platform-tools")
    if shutil.which("emulator", path=f"{home}/emulator") is None:
        raise SetupError(f"emulator binary not found under {home}/emulator")
    return home


def build_apk(skip_build: bool = False) -> str:
    """Builds the sample app's debug APK. skip_build (or QA_SKIP_BUILD=1) reuses the one on disk."""
    built = REPO_ROOT / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if (skip_build or os.environ.get("QA_SKIP_BUILD") == "1") and built.exists():
        return str(built)
    subprocess.run(["./gradlew", ":app:assembleDebug"], cwd=REPO_ROOT, check=True, timeout=900)
    if not built.exists():
        raise SetupError(f"build succeeded but {built} is missing")
    return str(built)


def reset_device(dev: Device) -> None:
    """Back to a known state between scenarios: app data cleared (and permissions re-granted),
    portrait/auto rotation, network on, screen on, at the home screen."""
    dev.force_stop()
    dev.shell("pm", "clear", dev.package)
    dev.grant("android.permission.RECORD_AUDIO")
    dev.grant("android.permission.POST_NOTIFICATIONS")
    dev.set_network(True)
    dev.set_rotation("portrait")
    dev.unlock()
    dev.press_home()
    time.sleep(1)


class Fleet:
    """The emulators of one session. ensure(["a","b"]) boots whichever are not up yet."""

    def __init__(self, apk_path: str, keep: bool = False):
        self.apk_path = apk_path
        self.keep = keep or os.environ.get("QA_KEEP_EMULATORS") == "1"
        self.emulators: dict[str, EmulatorProcess] = {}
        self.devices: dict[str, Device] = {}
        atexit.register(self.shutdown)  # never leave emulators behind on an unexpected exit

    def ensure(self, names: list[str]) -> dict[str, Device]:
        missing = [n for n in names if n not in self.devices]
        if missing:
            booted = boot_emulators([DEVICE_AVDS[n] for n in missing])
            for name, emu in zip(missing, booted):
                self.emulators[name] = emu
                dev = Device(emu.serial, PACKAGE)
                dev.install(self.apk_path)
                dev.disable_animations()
                self.devices[name] = dev
        for n in names:
            reset_device(self.devices[n])
        return {n: self.devices[n] for n in names}

    def logcat_factory(self, name: str, path: Path) -> LogcatCapture:
        return LogcatCapture(self.devices[name].serial, path)

    def shutdown(self) -> None:
        if self.keep:
            return
        for emu in self.emulators.values():
            try:
                emu.shutdown()
            except Exception:
                emu.process.kill()
        self.emulators.clear()
        self.devices.clear()
