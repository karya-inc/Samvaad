"""A recording stand-in for adb_utils.Device, so the runner/executor/oracles can be tested
end-to-end without an emulator. Every method call is appended to `calls`; behaviour is driven by
plain attributes (`label`, `pid_value`, `services_value` ...) that tests, or per-method `hooks`,
mutate to script what the "device" does."""

from __future__ import annotations

from pathlib import Path


class FakeDevice:
    def __init__(self, name: str = "a", pid: str | None = "100"):
        self.name = name
        self.calls: list[tuple] = []
        self.hooks: dict = {}
        self.pid_value = pid
        self.label: str | None = "Idle"
        self.notif = False
        self.services_value: dict = {}
        self.awake = True
        self.locks: list[str] = []
        self.network = True
        self.texts: dict = {}
        self.missing_nodes: set = set()
        self.pss_values: list[int] = []
        self.serial = f"fake-{name}"

    # -- helpers ------------------------------------------------------------------------------
    def _rec(self, method: str, *args):
        self.calls.append((method, *args))
        hook = self.hooks.get(method)
        if hook:
            hook(self, *args)

    def called(self, method: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == method]

    def in_call(self):
        self.label, self.notif, self.services_value = "Ongoing", True, {"pkg/.Svc": True}

    def idle(self):
        self.label, self.notif, self.services_value = "Idle", False, {}

    # -- observation ----------------------------------------------------------------------------
    def pid(self):
        return self.pid_value

    def screen_awake(self):
        return self.awake

    def call_state_label(self, timeout=2):
        return self.label if (self.pid_value and self.awake) else None

    def text_of_resource_id(self, rid, timeout=10):
        if rid == "call_state_label":
            return self.call_state_label()
        return self.texts.get(rid)

    def find_by_resource_id(self, rid, timeout=10):
        return None if (rid in self.missing_nodes or not self.pid_value) else object()

    def notification_posted(self, tag=""):
        return self.notif

    def services(self):
        return dict(self.services_value)

    def wake_locks(self):
        return list(self.locks)

    def pss_kb(self):
        return self.pss_values.pop(0) if self.pss_values else None

    def has_network(self):
        return self.network

    # -- actions --------------------------------------------------------------------------------
    def launch_app(self):
        self._rec("launch_app")
        if not self.pid_value:
            self.pid_value = str(int(self.name.encode()[0]) * 100 + len(self.calls))

    def join_meeting(self, url):
        self._rec("join_meeting", url)

    def tap_resource_id(self, rid, timeout=10):
        if rid in self.missing_nodes:
            raise AssertionError(f"no node with resource-id='{rid}'")
        self._rec("tap_resource_id", rid)

    def tap_text(self, text, timeout=10):
        self._rec("tap_text", text)

    def input_text(self, text):
        self._rec("input_text", text)

    def set_rotation(self, mode):
        self._rec("set_rotation", mode)

    def press_power(self):
        self.awake = not self.awake
        self._rec("press_power")

    def screen_off(self):
        self.awake = False
        self._rec("screen_off")

    def screen_on(self):
        self.awake = True
        self._rec("screen_on")

    def unlock(self):
        self.awake = True
        self._rec("unlock")

    def press_home(self):
        self._rec("press_home")

    def press_back(self):
        self._rec("press_back")

    def open_recents(self):
        self._rec("open_recents")

    def tap_notification(self, title=""):
        present = self.notif
        self._rec("tap_notification")
        return present

    def swipe_notification(self, title=""):
        present = self.notif  # was it in the shade when the swipe started?
        self._rec("swipe_notification")
        return "fake" if present else None

    def kill_process(self, how="force_stop"):
        self._rec("kill_process", how)
        self.pid_value, self.label, self.notif, self.services_value = None, None, False, {}

    def set_network(self, enabled, via="airplane"):
        self.network = enabled
        self._rec("set_network", enabled, via)

    def trim_memory(self, level="RUNNING_CRITICAL"):
        self._rec("trim_memory", level)
        return ""

    def reboot(self):
        self._rec("reboot")

    def screenshot(self, path):
        self._rec("screenshot", path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")

    def dump_ui_to(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("<hierarchy/>")
        return True


class FakeLog:
    """logcat stand-in: `chunks` are returned one per read_new() call (then empty strings)."""

    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.stopped = False

    def read_new(self):
        return self.chunks.pop(0) if self.chunks else ""

    def stop(self):
        self.stopped = True
        return ""


class FakeClock:
    """Deterministic time: sleep() advances it, so wait_for/settle logic runs instantly."""

    def __init__(self):
        self.t = 1_700_000_000.0
        self.on_sleep = None

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(seconds)
