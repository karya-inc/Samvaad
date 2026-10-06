from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import adb_utils
from adb_utils import Device, EmulatorProcess, LogcatCapture, boot_emulator, boot_emulators

PACKAGE = "com.daiatech.samvaad.app"
REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = Path(os.environ.get("QA_ARTIFACTS_DIR", REPO_ROOT / "qa" / ".artifacts"))

# TODO: if you add a third low-end profile (qa/scripts/create_low_end_avds.sh), list it here too.
LOW_END_AVDS = ["leak_test_low_1", "leak_test_low_2"]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "emulator: requires a real Android emulator (adb/AVDs); see qa/README.md")


@pytest.fixture(scope="session")
def android_sdk() -> str:
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not home:
        pytest.skip("ANDROID_HOME/ANDROID_SDK_ROOT not set -- see qa/README.md")
    adb_utils.ANDROID_HOME = home
    if shutil.which("adb", path=f"{home}/platform-tools") is None:
        pytest.skip(f"adb not found under {home}/platform-tools")
    if shutil.which("emulator", path=f"{home}/emulator") is None:
        pytest.skip(f"emulator binary not found under {home}/emulator")
    return home


@pytest.fixture(scope="session")
def apk_path(android_sdk: str) -> str:
    """Builds the sample app's debug APK once per test session. Set QA_SKIP_BUILD=1 to reuse
    whatever APK is already on disk (e.g. iterating on test flows without rebuilding each time)."""
    built = REPO_ROOT / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if os.environ.get("QA_SKIP_BUILD") == "1" and built.exists():
        return str(built)

    subprocess.run(
        ["./gradlew", ":app:assembleDebug"],
        cwd=REPO_ROOT,
        check=True,
        timeout=600,
    )
    assert built.exists(), f"build succeeded but {built} is missing"
    return str(built)


@pytest.fixture
def artifacts_dir(request: pytest.FixtureRequest) -> Path:
    """Per-test directory for logcat captures / screenshots -- kept on disk after the run so a
    failure can be diaged without having to reproduce it live."""
    test_dir = ARTIFACTS_DIR / request.node.name.replace("/", "_")
    test_dir.mkdir(parents=True, exist_ok=True)
    return test_dir


def _prepared_device(emulator: EmulatorProcess, apk_path: str) -> Device:
    device = Device(emulator.serial, PACKAGE)
    device.install(apk_path)
    device.grant("android.permission.RECORD_AUDIO")
    device.grant("android.permission.POST_NOTIFICATIONS")
    device.disable_animations()
    return device


@pytest.fixture
def one_device(android_sdk: str, apk_path: str):
    """A single booted, app-installed low-end emulator. Torn down after the test unless
    QA_KEEP_EMULATORS=1 (handy when iterating -- leaves it running so you can poke at it, same
    as the manual investigation this suite automates)."""
    emulator = boot_emulator(LOW_END_AVDS[0])
    device = _prepared_device(emulator, apk_path)
    try:
        yield device
    finally:
        if os.environ.get("QA_KEEP_EMULATORS") != "1":
            emulator.shutdown()


@pytest.fixture
def two_devices(android_sdk: str, apk_path: str):
    """Both low-end profiles, booted concurrently -- mirrors the prior manual investigation's
    setup exactly (one 512MB/1-core, one 768MB/2-core)."""
    emulators = boot_emulators(LOW_END_AVDS)
    devices = [_prepared_device(e, apk_path) for e in emulators]
    try:
        yield devices
    finally:
        if os.environ.get("QA_KEEP_EMULATORS") != "1":
            for e in emulators:
                e.shutdown()


class LeakWatch:
    """Starts a LeakCanary/crash logcat capture around a block of test actions, stops it when
    the `with` block exits, and leaves the parsed result + raw log available for assertions."""

    def __init__(self, device: Device, out_path: Path):
        self._device = device
        self._out_path = out_path
        self.result = None

    def __enter__(self) -> "LeakWatch":
        self._capture = LogcatCapture(self._device.serial, self._out_path)
        return self

    def __exit__(self, *exc) -> None:
        text = self._capture.stop()
        self.result = adb_utils.scan_logcat_for_leaks_and_crashes(text)


@pytest.fixture
def leak_watch(artifacts_dir: Path):
    """Usage: `with leak_watch(device, "label") as watch: ...; assert watch.result.ok`"""

    def _start(device: Device, label: str) -> LeakWatch:
        return LeakWatch(device, artifacts_dir / f"logcat_{label}.log")

    return _start
