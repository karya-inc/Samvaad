"""Host-side helpers for driving a real emulator over adb/uiautomator.

This exists because the flows qa/ tests (orchestrating two emulators, OS-level screen rotation,
swiping a system notification, backgrounding/foregrounding, reading LeakCanary's logcat output)
are host/OS-level concerns -- none of them are reachable from an on-device instrumented test,
which only ever sees one app process on one device. Everything here shells out to `adb`/
`emulator`, exactly like a human tester would.
"""

from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ANDROID_HOME = None  # set by conftest.py's android_sdk fixture before any Device is constructed


def _bin(name: str) -> str:
    if ANDROID_HOME is None:
        return name
    for sub in ("platform-tools", "emulator"):
        candidate = Path(ANDROID_HOME) / sub / name
        if candidate.exists():
            return str(candidate)
    return name


class BootTimeout(RuntimeError):
    pass


class EmulatorProcess:
    """A booted emulator this test session owns -- tracks the subprocess so teardown can kill it
    (unless QA_KEEP_EMULATORS asks to leave it running for manual poking afterward)."""

    def __init__(self, avd_name: str, serial: str, process: subprocess.Popen):
        self.avd_name = avd_name
        self.serial = serial
        self.process = process

    def shutdown(self) -> None:
        subprocess.run([_bin("adb"), "-s", self.serial, "emu", "kill"], capture_output=True, timeout=15)
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()


def launch_emulator(avd_name: str, _before: set[str] | None = None) -> tuple[subprocess.Popen, set[str]]:
    """Starts the emulator process without waiting for it to boot -- separated from
    boot_emulator() so multiple emulators can be launched back-to-back and then waited on
    concurrently (max(boot times), not sum(boot times) -- matters more than it sounds: a cold
    `-no-snapshot` boot under swiftshader software rendering can take over a minute on its own,
    and waiting for two of them sequentially both wastes time and makes the second one look
    "slow" under the same boot_timeout_s when it's really just queued behind the first)."""
    before = _before if _before is not None else _list_devices()
    process = subprocess.Popen(
        [
            _bin("emulator"),
            "-avd",
            avd_name,
            "-no-snapshot",
            "-gpu",
            "swiftshader_indirect",
            "-no-boot-anim",
            "-netdelay",
            "none",
            "-netspeed",
            "full",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, before


def _avd_name_of(serial: str) -> str | None:
    result = subprocess.run(
        [_bin("adb"), "-s", serial, "emu", "avd", "name"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip() and line.strip() != "OK"]
    return lines[0] if lines else None


def _boot_completed(serial: str) -> bool:
    result = subprocess.run(
        [_bin("adb"), "-s", serial, "shell", "getprop", "sys.boot_completed"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() == "1"


def boot_emulator(avd_name: str, boot_timeout_s: int = 300) -> EmulatorProcess:
    return boot_emulators([avd_name], boot_timeout_s)[0]


def boot_emulators(avd_names: list[str], boot_timeout_s: int = 300) -> list[EmulatorProcess]:
    """Launches every AVD in `avd_names` immediately, back-to-back, then waits for all of them to
    finish booting concurrently -- max(boot times), not sum(boot times). A cold `-no-snapshot`
    boot under swiftshader software rendering can take over a minute on its own; waiting for two
    of them one after another both wastes time and makes the second one look "slow" under the
    same boot_timeout_s when it's really just queued behind the first.

    `adb devices` alone can't tell two concurrently-booting emulators apart by name -- a naive
    "whichever new serial shows up first" risks matching the wrong process to the wrong AVD if
    the second one happens to register with adb before the first. Each new serial is resolved to
    its real AVD name via `adb emu avd name` before anything is matched up.
    """
    before = _list_devices()
    processes = {}
    for name in avd_names:
        process, _ = launch_emulator(name, _before=before)
        processes[name] = process
        time.sleep(2)  # stagger starts -- avoids every emulator hitting the same port-allocation step at once

    deadline = time.monotonic() + boot_timeout_s
    serial_by_name: dict[str, str] = {}
    while time.monotonic() < deadline and len(serial_by_name) < len(avd_names):
        for serial in _list_devices() - before:
            if serial in serial_by_name.values():
                continue
            resolved = _avd_name_of(serial)
            if resolved in processes and resolved not in serial_by_name:
                serial_by_name[resolved] = serial
        if len(serial_by_name) < len(avd_names):
            time.sleep(1)

    missing = [n for n in avd_names if n not in serial_by_name]
    if missing:
        for process in processes.values():
            process.kill()
        raise BootTimeout(f"{missing}: no new adb device resolved to this AVD within {boot_timeout_s}s")

    # Round-robin poll every pending device's boot_completed flag, rather than waiting for one
    # fully before even checking the next -- same max(times) reasoning as the launch phase above.
    unbooted = set(avd_names)
    while time.monotonic() < deadline and unbooted:
        for name in list(unbooted):
            if _boot_completed(serial_by_name[name]):
                unbooted.discard(name)
        if unbooted:
            time.sleep(2)

    if unbooted:
        for name in unbooted:
            processes[name].kill()
        raise BootTimeout(f"{unbooted}: sys.boot_completed never reached 1 within {boot_timeout_s}s")

    return [EmulatorProcess(name, serial_by_name[name], processes[name]) for name in avd_names]


def _list_devices() -> set[str]:
    result = subprocess.run([_bin("adb"), "devices"], capture_output=True, text=True, timeout=10)
    serials = set()
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if line.endswith("\tdevice"):
            serials.add(line.split("\t")[0])
    return serials


@dataclass
class UiNode:
    text: str
    resource_id: str
    bounds: tuple[int, int, int, int]  # left, top, right, bottom

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return (left + right) // 2, (top + bottom) // 2


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class Device:
    def __init__(self, serial: str, package: str):
        self.serial = serial
        self.package = package

    # -- low-level -----------------------------------------------------------------------

    def adb(self, *args: str, timeout: int = 30) -> str:
        result = subprocess.run(
            [_bin("adb"), "-s", self.serial, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout

    def shell(self, *args: str, timeout: int = 30) -> str:
        return self.adb("shell", *args, timeout=timeout)

    # -- app lifecycle ---------------------------------------------------------------------

    def install(self, apk_path: str) -> None:
        self.adb("install", "-r", apk_path, timeout=120)

    def grant(self, permission: str) -> None:
        self.shell("pm", "grant", self.package, permission)

    def disable_animations(self) -> None:
        for key in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
            self.shell("settings", "put", "global", key, "0")

    def launch_main_activity(self, activity: str = ".MainActivity") -> None:
        self.shell("am", "start", "-n", f"{self.package}/{activity}")

    def force_stop(self) -> None:
        self.shell("am", "force-stop", self.package)

    def kill(self) -> None:
        """`am kill` -- a no-op if the app is running a foreground service (Android protects it).
        Use force_stop() for a guaranteed, genuine process death instead."""
        self.shell("am", "kill", self.package)

    def pid(self) -> str | None:
        out = self.shell("pidof", self.package).strip()
        return out or None

    # -- OS-level interactions the matrix-driven flows need ---------------------------------

    def press_home(self) -> None:
        self.shell("input", "keyevent", "KEYCODE_HOME")

    def rotate_landscape(self) -> None:
        self.shell("settings", "put", "system", "accelerometer_rotation", "0")
        self.shell("settings", "put", "system", "user_rotation", "1")

    def rotate_portrait(self) -> None:
        self.shell("settings", "put", "system", "user_rotation", "0")

    def expand_notifications(self) -> None:
        self.shell("cmd", "statusbar", "expand-notifications")

    def collapse_notifications(self) -> None:
        self.shell("cmd", "statusbar", "collapse")

    def notification_posted(self, tag_substring: str = "") -> bool:
        """True if this package currently has an active status-bar notification. Checked via
        `dumpsys notification`, not a UI screenshot -- robust to shade layout/animation."""
        dump = self.shell("dumpsys", "notification", "--noredact")
        for block in dump.split("NotificationRecord"):
            if f"pkg={self.package}" in block and tag_substring in block:
                return True
        return False

    def swipe_notification_shade_dismiss_attempt(self) -> None:
        """Opens the shade and swipes horizontally across the topmost notification's expected
        position -- the actual swipe-to-dismiss gesture a user performs. Whether it actually
        disappears is for the caller to check via notification_posted()."""
        self.expand_notifications()
        time.sleep(1)
        # The first notification in an expanded shade sits a short way below the status bar on
        # every API level this suite targets -- approximate, not pixel-perfect: a real dismiss
        # gesture doesn't need to hit an exact widget, just register as a horizontal swipe over it.
        self.shell("input", "swipe", "100", "300", "900", "300", "150")
        time.sleep(1)
        self.collapse_notifications()

    # -- UiAutomator (resource-id based, NOT pixel coordinates) ------------------------------

    def dump_ui(self) -> list[UiNode]:
        self.shell("uiautomator", "dump", "/sdcard/qa_ui.xml")
        xml_text = self.shell("cat", "/sdcard/qa_ui.xml")
        nodes = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return nodes
        for el in root.iter("node"):
            bounds_match = _BOUNDS_RE.match(el.get("bounds", ""))
            if not bounds_match:
                continue
            nodes.append(
                UiNode(
                    text=el.get("text", ""),
                    resource_id=el.get("resource-id", ""),
                    bounds=tuple(int(g) for g in bounds_match.groups()),  # type: ignore[arg-type]
                )
            )
        return nodes

    def find_by_resource_id(self, resource_id: str, timeout: float = 10) -> UiNode | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for node in self.dump_ui():
                if node.resource_id == resource_id:
                    return node
            time.sleep(0.5)
        return None

    def tap_resource_id(self, resource_id: str, timeout: float = 10) -> None:
        node = self.find_by_resource_id(resource_id, timeout=timeout)
        if node is None:
            raise AssertionError(f"{self.serial}: no node with resource-id='{resource_id}' within {timeout}s")
        x, y = node.center
        self.shell("input", "tap", str(x), str(y))

    def text_of_resource_id(self, resource_id: str, timeout: float = 10) -> str | None:
        node = self.find_by_resource_id(resource_id, timeout=timeout)
        return node.text if node else None

    def input_text(self, text: str) -> None:
        # adb's `input text` can't send spaces/most punctuation reliably -- not needed for a
        # meeting URL, but worth knowing if a future flow needs free text.
        self.shell("input", "text", text)

    def wait_for_call_state(self, expected: str, timeout: float = 30) -> None:
        """Polls the `call_state_label` test hook (MainActivity.kt) -- the Kotlin sealed class's
        own simple name (e.g. "Ongoing", "Idle", "Connecting"), not a human-facing string that
        changes whenever someone tweaks copy."""
        deadline = time.monotonic() + timeout
        last_seen = None
        while time.monotonic() < deadline:
            last_seen = self.text_of_resource_id("call_state_label", timeout=2)
            if last_seen == expected:
                return
            time.sleep(1)
        raise AssertionError(f"{self.serial}: call_state_label never became '{expected}' (last saw '{last_seen}')")

    def screenshot(self, out_path: str) -> None:
        remote = "/sdcard/qa_screenshot.png"
        self.shell("screencap", "-p", remote)
        self.adb("pull", remote, out_path, timeout=30)

    # -- app-specific flow (MainActivity's test tags) ---------------------------------------

    def join_meeting(self, meeting_url: str) -> None:
        """Types a meeting URL into the sample app's Idle screen and taps Join. Call this from
        Idle; it does not itself wait for the resulting Connecting/Ongoing transition."""
        self.tap_resource_id("meeting_link_field")
        self.input_text(meeting_url)
        # The on-screen keyboard covers the Join button on a small low-end display -- dismiss it
        # first rather than guessing a tap position underneath it.
        self.shell("input", "keyevent", "KEYCODE_BACK")
        self.tap_resource_id("join_button")


# -- logcat capture / LeakCanary parsing ---------------------------------------------------


class LogcatCapture:
    def __init__(self, serial: str, out_path: Path):
        self.out_path = out_path
        subprocess.run([_bin("adb"), "-s", serial, "logcat", "-c"], capture_output=True, timeout=15)
        self._file = open(out_path, "w")
        self._process = subprocess.Popen(
            # LeakCanary:V -- full leak-detection output, whatever tag it logs under.
            # System.err:W -- uncaught exceptions' default stack-trace printout.
            # *:E -- every Timber.e(...) call anywhere in the app (DailyCoVoipSdkClient,
            # AbstractVoipCallService, etc. all log under their own class-name tag, not a shared
            # one -- "*:S" here previously silenced all of them, which is exactly the gap that
            # made a real test failure's root cause invisible in the captured log). Subsumes the
            # old explicit AndroidRuntime:E (crash) entry too.
            [_bin("adb"), "-s", serial, "logcat", "-v", "brief", "LeakCanary:V", "System.err:W", "*:E"],
            stdout=self._file,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> str:
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._file.close()
        return self.out_path.read_text(errors="replace")


_LEAK_COUNT_RE = re.compile(r"(\d+) APPLICATION LEAKS")
_FATAL_RE = re.compile(r"FATAL EXCEPTION")


@dataclass
class LeakScanResult:
    confirmed_leak_count: int
    fatal_exceptions: int
    raw: str

    @property
    def ok(self) -> bool:
        return self.confirmed_leak_count == 0 and self.fatal_exceptions == 0


def scan_logcat_for_leaks_and_crashes(logcat_text: str) -> LeakScanResult:
    """Sums every "N APPLICATION LEAKS" LeakCanary reported across the capture (there can be
    more than one heap dump per run) and counts crashes. Deliberately does NOT treat "Found N
    object(s) retained, not dumping heap yet" or "All retained objects have been garbage
    collected" as failures -- those are normal GC-timing noise, confirmed transient in the prior
    manual investigation (see qa/README.md)."""
    leak_count = sum(int(n) for n in _LEAK_COUNT_RE.findall(logcat_text))
    fatal_count = len(_FATAL_RE.findall(logcat_text))
    return LeakScanResult(confirmed_leak_count=leak_count, fatal_exceptions=fatal_count, raw=logcat_text)
