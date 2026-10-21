"""Host-side helpers for driving a real emulator over adb/uiautomator.

This exists because the flows qa/ tests (orchestrating two emulators, OS-level screen rotation,
swiping a system notification, backgrounding/foregrounding, reading LeakCanary's logcat output)
are host/OS-level concerns -- none of them are reachable from an on-device instrumented test,
which only ever sees one app process on one device. Everything here shells out to `adb`/
`emulator`, exactly like a human tester would.
"""

from __future__ import annotations

import os
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
    command = [
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
    ]
    # QA_HEADLESS=1: no emulator window and no audio device -- for machines without a display
    # (CI, ssh sessions). Off by default so a developer at a desk can watch the flow happen.
    if os.environ.get("QA_HEADLESS") == "1":
        command += ["-no-window", "-no-audio"]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def _boot_completed_safe(serial: str) -> bool:
    """_boot_completed() that treats a not-yet-answering adb (mid-reboot) as 'not booted'."""
    try:
        return _boot_completed(serial)
    except (subprocess.TimeoutExpired, OSError):
        return False


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


# -- dumpsys / ps parsers ------------------------------------------------------------------
# Pure text -> value functions, deliberately kept outside Device so the offline unit tests can feed
# them canned dumpsys output (qa/tests/) without an emulator. Device methods below are thin
# "run the command, parse the text" wrappers around these.

_WAKE_LOCK_RE = re.compile(r"^\s*(\w*WAKE_LOCK)\s+'(.*?)'(.*)$", re.MULTILINE)
_SERVICE_RECORD_RE = re.compile(r"\* ServiceRecord\{[0-9a-f]+ u\d+ ([^ }]+)")


@dataclass
class WakeLock:
    kind: str  # e.g. PARTIAL_WAKE_LOCK
    tag: str
    uids: tuple[int, ...]  # owner uid plus any WorkSource-attributed uids

    def __str__(self) -> str:
        return f"{self.kind} '{self.tag}' uids={list(self.uids)}"


def parse_wake_locks(dumpsys_power: str, uid: int | None = None) -> list[WakeLock]:
    """Held wake locks from `dumpsys power` ("Wake Locks: size=N" section). With `uid`, only locks
    owned by that uid -- either the lock's own `uid=` or a uid named in its WorkSource (a lock
    acquired by system_server *on behalf of* the app, e.g. a JobScheduler job, is attributed that
    way)."""
    locks = []
    for kind, tag, rest in _WAKE_LOCK_RE.findall(dumpsys_power):
        uids = [int(u) for u in re.findall(r"uid=(\d+)", rest)]
        work_source = re.search(r"WorkSource\{([^}]*)", rest)
        if work_source:
            uids += [int(u) for u in re.findall(r"\b(\d{4,6})\b", work_source.group(1))]
        locks.append(WakeLock(kind, tag, tuple(dict.fromkeys(uids))))
    if uid is not None:
        locks = [lock for lock in locks if uid in lock.uids]
    return locks


def parse_services(dumpsys_services: str, package: str) -> dict[str, bool]:
    """{service component: is_foreground} for every running ServiceRecord of `package` in
    `dumpsys activity services` output. A record's `isForeground=true` line appears inside that
    record's block, before the next `* ServiceRecord`."""
    result: dict[str, bool] = {}
    blocks = re.split(r"(?=\* ServiceRecord\{)", dumpsys_services)
    for block in blocks:
        match = _SERVICE_RECORD_RE.match(block)
        if not match or not match.group(1).startswith(package + "/"):
            continue
        result[match.group(1)] = "isForeground=true" in block
    return result


def parse_pss_total_kb(dumpsys_meminfo: str) -> int | None:
    """TOTAL PSS (kB) from `dumpsys meminfo <pkg>`; None if the process wasn't running."""
    match = re.search(r"TOTAL PSS:\s+(\d+)", dumpsys_meminfo)
    if match:
        return int(match.group(1))
    match = re.search(r"^\s*TOTAL\s+(\d+)", dumpsys_meminfo, re.MULTILINE)  # older format
    return int(match.group(1)) if match else None


def parse_pid_from_ps(ps_output: str, package: str) -> str | None:
    """The main-process pid of `package` from `ps -A -o PID,NAME` output. Matches the process
    name exactly, so a secondary `package:remote` process can't be mistaken for it."""
    for line in ps_output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == package and parts[0].isdigit():
            return parts[0]
    return None


def parse_notification_records(dumpsys_notification: str, package: str) -> list[str]:
    """One text block per active notification record posted by `package`."""
    return [
        block
        for block in dumpsys_notification.split("NotificationRecord")[1:]
        if f"pkg={package} " in block
    ]


def parse_wakefulness(dumpsys_power: str) -> str | None:
    """'Awake' / 'Asleep' / 'Dozing' / 'Dreaming' from `dumpsys power`."""
    match = re.search(r"mWakefulness=(\w+)", dumpsys_power)
    return match.group(1) if match else None


# `settings put system user_rotation N` values.
ROTATIONS = {"portrait": 0, "landscape": 1, "reverse_portrait": 2, "reverse_landscape": 3}

# `am send-trim-memory` levels (ComponentCallbacks2.TRIM_MEMORY_*), mildest to most severe.
TRIM_LEVELS = ["HIDDEN", "RUNNING_MODERATE", "BACKGROUND", "RUNNING_LOW", "MODERATE", "RUNNING_CRITICAL", "COMPLETE"]


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


    # -- extensions for the scenario DSL (qa/samvaad_qa/) ------------------------------------

    def launch_app(self) -> None:
        """Starts the app exactly the way the launcher icon does (MAIN/LAUNCHER intent): a cold
        start if the process is gone, otherwise it resumes the existing task instead of stacking
        a second MainActivity (which plain `am start -n` can do)."""
        self.shell(
            "am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER",
            "-n", f"{self.package}/.MainActivity",
        )

    def press_back(self) -> None:
        self.shell("input", "keyevent", "KEYCODE_BACK")

    def open_recents(self) -> None:
        self.shell("input", "keyevent", "KEYCODE_APP_SWITCH")

    # Power / screen -----------------------------------------------------------------------

    def wakefulness(self) -> str | None:
        return parse_wakefulness(self.shell("dumpsys", "power"))

    def screen_awake(self) -> bool:
        return self.wakefulness() == "Awake"

    def press_power(self) -> None:
        """The physical power key: toggles screen off/on, exactly like pressing it."""
        self.shell("input", "keyevent", "KEYCODE_POWER")

    def screen_off(self) -> None:
        """Idempotent screen-off (KEYCODE_SLEEP) -- unlike press_power(), a no-op if already off."""
        self.shell("input", "keyevent", "KEYCODE_SLEEP")

    def screen_on(self) -> None:
        """Idempotent screen-on (KEYCODE_WAKEUP) -- does not dismiss a lock screen; see unlock()."""
        self.shell("input", "keyevent", "KEYCODE_WAKEUP")

    def keyguard_showing(self) -> bool:
        return "isKeyguardShowing=true" in self.shell("dumpsys", "window")

    def unlock(self) -> None:
        """Wakes the screen if needed and dismisses a swipe/no-security keyguard. A device with
        a PIN/pattern set cannot be unlocked this way (the emulators this suite creates have
        none)."""
        if not self.screen_awake():
            self.screen_on()
            time.sleep(1)
        if self.keyguard_showing():
            self.shell("wm", "dismiss-keyguard")
            time.sleep(1)
        if self.keyguard_showing():  # swipe-up fallback for a keyguard `wm dismiss-keyguard` skipped
            self.shell("input", "swipe", "240", "700", "240", "100", "200")

    # Rotation -----------------------------------------------------------------------------

    def set_rotation(self, mode: str) -> None:
        """mode: one of ROTATIONS (portrait/landscape/reverse_*) or 'auto' (hand control back to
        the accelerometer). Pins user_rotation, which is what a test can do deterministically;
        a real accelerometer flip is not reproducible over adb."""
        if mode == "auto":
            self.shell("settings", "put", "system", "accelerometer_rotation", "1")
            return
        self.shell("settings", "put", "system", "accelerometer_rotation", "0")
        self.shell("settings", "put", "system", "user_rotation", str(ROTATIONS[mode]))

    # Process / services / memory ------------------------------------------------------------

    def pid(self) -> str | None:
        """Main-process pid, None if not running. Via `ps` (exact process-name match) rather than
        `pidof`, which would also list a secondary `<package>:remote` process."""
        pid = parse_pid_from_ps(self.shell("ps", "-A", "-o", "PID,NAME"), self.package)
        return pid or (self.shell("pidof", "-s", self.package).strip() or None)

    def uid(self) -> int | None:
        match = re.search(r"appId=(\d+)", self.shell("dumpsys", "package", self.package))
        return int(match.group(1)) if match else None

    def services(self) -> dict[str, bool]:
        """{service component: is_foreground} for the app's currently running services."""
        return parse_services(self.shell("dumpsys", "activity", "services", self.package), self.package)

    def foreground_service_running(self) -> bool:
        return any(self.services().values())

    def wake_locks(self) -> list[WakeLock]:
        """Wake locks currently held by this app's uid."""
        return parse_wake_locks(self.shell("dumpsys", "power"), self.uid())

    def pss_kb(self) -> int | None:
        return parse_pss_total_kb(self.shell("dumpsys", "meminfo", self.package))

    def trim_memory(self, level: str = "RUNNING_CRITICAL") -> str:
        """`am send-trim-memory`: the app gets onTrimMemory(level) as if the OS were under
        memory pressure. Fails (returns the shell's error text) if the process isn't running."""
        return self.shell("am", "send-trim-memory", self.package, level).strip()

    def kill_process(self, how: str = "force_stop") -> None:
        if how == "am_kill":
            self.kill()
        else:
            self.force_stop()

    # Network ------------------------------------------------------------------------------

    def set_network(self, enabled: bool, via: str = "airplane") -> None:
        """via='airplane': `cmd connectivity airplane-mode` (API 31+); 'wifi' / 'data': `svc`;
        'all': both svc toggles. Emulator caveat: the virtual wifi is the device's default
        network, so wifi off with mobile data on just falls back to the (equally fast) emulated
        cellular link -- use 'airplane' or 'all' to genuinely cut connectivity."""
        verb = "enable" if enabled else "disable"
        if via == "airplane":
            self.shell("cmd", "connectivity", "airplane-mode", "disable" if enabled else "enable")
        if via in ("wifi", "all"):
            self.shell("svc", "wifi", verb)
        if via in ("data", "all"):
            self.shell("svc", "data", verb)

    def has_network(self) -> bool:
        """True if the device has a validated default network (`dumpsys connectivity`)."""
        out = self.shell("dumpsys", "connectivity")
        return bool(re.search(r"Active default network: \d+", out))

    # Reboot -------------------------------------------------------------------------------

    def reboot(self, timeout: float = 240) -> None:
        """`adb reboot` and wait for sys.boot_completed. Heavy (a full emulator OS restart); the
        app process is gone afterwards and animations must be re-disabled."""
        self.adb("reboot")
        time.sleep(5)
        self.adb("wait-for-device", timeout=int(timeout))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _boot_completed_safe(self.serial):
                self.disable_animations()
                return
            time.sleep(2)
        raise BootTimeout(f"{self.serial}: sys.boot_completed not reached {timeout}s after reboot")

    # UI by text / notification ------------------------------------------------------------

    def find_by_text(self, text: str, timeout: float = 10, contains: bool = False) -> UiNode | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for node in self.dump_ui():
                if (text in node.text) if contains else (node.text == text):
                    return node
            time.sleep(0.5)
        return None

    def tap_text(self, text: str, timeout: float = 10) -> None:
        node = self.find_by_text(text, timeout=timeout)
        if node is None:
            raise AssertionError(f"{self.serial}: no node with text='{text}' within {timeout}s")
        x, y = node.center
        self.shell("input", "tap", str(x), str(y))

    def dump_ui_to(self, out_path: str) -> bool:
        """Saves the raw uiautomator XML to `out_path` (evidence for a finding)."""
        self.shell("uiautomator", "dump", "/sdcard/qa_ui.xml")
        self.adb("pull", "/sdcard/qa_ui.xml", out_path, timeout=30)
        return Path(out_path).exists()

    def screen_size(self) -> tuple[int, int]:
        match = re.search(r"(\d+)x(\d+)", self.shell("wm", "size"))
        return (int(match.group(1)), int(match.group(2))) if match else (480, 800)

    def swipe_notification(self, title: str = "Samvaad sample call") -> bool:
        """Opens the shade and swipes the row whose title contains `title` horizontally across
        its width -- the real swipe-to-dismiss gesture. Returns False if no such row was found
        (nothing was swiped). Whether it vanished is for the caller to check."""
        self.expand_notifications()
        time.sleep(1)
        nodes = self.dump_ui()
        rows = [n for n in nodes if n.resource_id.endswith("expandableNotificationRow")]
        titled = [n for n in nodes if title in n.text]
        target = None
        for row in rows:
            for t in titled:
                if row.bounds[0] <= t.bounds[0] and row.bounds[1] <= t.bounds[1] and t.bounds[2] <= row.bounds[2] and t.bounds[3] <= row.bounds[3]:
                    target = row
        if target is None:
            self.collapse_notifications()
            return False
        left, top, right, bottom = target.bounds
        y = (top + bottom) // 2
        self.shell("input", "swipe", str(left + 20), str(y), str(right - 4), str(y), "150")
        time.sleep(1)
        self.collapse_notifications()
        return True

    def tap_notification(self, title: str = "Samvaad sample call") -> bool:
        """Opens the shade and taps the notification titled `title` (what a user does to return
        to the call). False if it isn't in the shade."""
        self.expand_notifications()
        time.sleep(1)
        node = self.find_by_text(title, timeout=3, contains=True)
        if node is None:
            self.collapse_notifications()
            return False
        x, y = node.center
        self.shell("input", "tap", str(x), str(y))
        return True

    def call_state_label(self, timeout: float = 2) -> str | None:
        """The `call_state_label` test hook's text, or None when the app UI is not what's on screen
        (screen off, another app in front, process dead)."""
        return self.text_of_resource_id("call_state_label", timeout=timeout)


# -- logcat capture / LeakCanary parsing ---------------------------------------------------


class LogcatCapture:
    def __init__(self, serial: str, out_path: Path):
        self.out_path = out_path
        self._read_offset = 0
        subprocess.run([_bin("adb"), "-s", serial, "logcat", "-c"], capture_output=True, timeout=15)
        self._file = open(out_path, "w")
        self._process = subprocess.Popen(
            # LeakCanary:V -- full leak-detection output, whatever tag it logs under.
            # System.err:W -- uncaught exceptions' default stack-trace printout.
            # *:E -- every Timber.e(...) call anywhere in the app (DailyCoVoipSdkClient,
            # AbstractVoipCallService, etc. all log under their own class-name tag, not a shared
            # one -- "*:S" here previously silenced all of them, which is exactly the gap that
            # made a real test failure's root cause invisible in the captured log). Subsumes the
            # old explicit AndroidRuntime:E (crash) entry too, and the system's own error-level
            # reports the scenario oracles look for (ANR in ..., "has leaked window", native
            # crashes via the DEBUG tag).
            # `-v time` (not brief): a per-line device timestamp, which is what lets a finding
            # say *when* something happened, not just that it did.
            [_bin("adb"), "-s", serial, "logcat", "-v", "time", "LeakCanary:V", "System.err:W", "*:E"],
            stdout=self._file,
            stderr=subprocess.DEVNULL,
        )

    def read_new(self) -> str:
        """Text captured since the previous read_new() (or since the start) -- lets a runner
        attribute a logcat finding to the step during which it first appeared."""
        self._file.flush()
        with open(self.out_path, "rb") as f:
            f.seek(self._read_offset)
            data = f.read()
        # Only consume whole lines: a half-written trailing line is picked up by the next read.
        cut = data.rfind(b"\n") + 1
        self._read_offset += cut
        return data[:cut].decode(errors="replace")

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
