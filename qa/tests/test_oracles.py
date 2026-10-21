"""Each oracle's detector against canned logcat/snapshot samples: a positive and a negative case."""

from __future__ import annotations

from samvaad_qa import oracles as o
from samvaad_qa.model import ChecksConfig, Snapshot

PKG = "com.daiatech.samvaad.app"

CRASH = f"""\
10-22 01:00:00.000 E/AndroidRuntime( 3281): FATAL EXCEPTION: main
10-22 01:00:00.000 E/AndroidRuntime( 3281): Process: {PKG}, PID: 3281
10-22 01:00:00.000 E/AndroidRuntime( 3281): java.lang.IllegalStateException: boom
10-22 01:00:00.000 E/AndroidRuntime( 3281): \tat com.daiatech.samvaad.app.MainActivity.onCreate(MainActivity.kt:10)
"""
OTHER_CRASH = """\
10-22 01:00:00.000 E/AndroidRuntime( 999): FATAL EXCEPTION: main
10-22 01:00:00.000 E/AndroidRuntime( 999): Process: com.google.android.gms, PID: 999
10-22 01:00:00.000 E/AndroidRuntime( 999): java.lang.RuntimeException
"""
NATIVE = f"10-22 01:00:00.000 F/libc    ( 3281): Fatal signal 11 (SIGSEGV), code 1, fault addr 0x0 in tid 3300 (RenderThread), pid 3281 ({PKG})\n"
ANR = f"10-22 01:00:00.000 E/ActivityManager( 500): ANR in {PKG} (com.daiatech.samvaad.app/.MainActivity)\n10-22 01:00:00.000 E/ActivityManager( 500): Reason: Input dispatching timed out\n"
LEAKED = f"10-22 01:00:00.000 E/WindowManager( 3281): android.view.WindowLeaked: Activity {PKG}.MainActivity has leaked window DecorView@1234[MainActivity] that was originally added here\n"
LEAKED_SVC = f"10-22 01:00:00.000 E/ActivityThread( 3281): Activity {PKG}.MainActivity has leaked ServiceConnection x@1 that was originally bound here\n"
LEAKED_OTHER = "10-22 01:00:00.000 E/WindowManager( 7): Activity com.other.App has leaked window DecorView@1\n"
LEAKCANARY = """\
10-22 01:00:00.000 D/LeakCanary( 3281): ====================================
10-22 01:00:00.000 D/LeakCanary( 3281): HEAP ANALYSIS RESULT
10-22 01:00:00.000 D/LeakCanary( 3281): 1 APPLICATION LEAKS
10-22 01:00:00.000 D/LeakCanary( 3281): * leaking: true (Activity#mDestroyed)
"""
LEAKCANARY_NOISE = """\
10-22 01:00:00.000 D/LeakCanary( 3281): Found 1 object retained, not dumping heap yet (app is visible & < 5 threshold)
10-22 01:00:00.000 D/LeakCanary( 3281): All retained objects have been garbage collected
10-22 01:00:00.000 D/LeakCanary( 3281): 0 APPLICATION LEAKS
"""


def test_crash_positive_negative():
    assert [p.oracle for p in o.detect_crashes(CRASH, PKG)] == ["crash"]
    assert "FATAL EXCEPTION" in o.detect_crashes(CRASH, PKG)[0].evidence[0]
    assert o.detect_crashes(OTHER_CRASH, PKG) == []
    assert o.detect_crashes("E/Foo: handled exception", PKG) == []
    assert len(o.detect_crashes(NATIVE, PKG)) == 1


def test_anr():
    assert len(o.detect_anr(ANR, PKG)) == 1
    assert o.detect_anr(ANR.replace(PKG, "com.other", 1).replace("com.daiatech.samvaad.app/", "com.other/"), PKG) == []
    assert o.detect_anr("I/ActivityManager: Start proc", PKG) == []


def test_leaked_resources():
    assert [p.key for p in o.detect_leaked_resources(LEAKED, PKG)] == ["leaked:window"]
    assert [p.key for p in o.detect_leaked_resources(LEAKED_SVC, PKG)] == ["leaked:ServiceConnection"]
    assert o.detect_leaked_resources(LEAKED_OTHER, PKG) == []


def test_leakcanary_confirmed_only():
    hits = o.detect_leakcanary(LEAKCANARY)
    assert len(hits) == 1 and "1 application leak" in hits[0].message and any("APPLICATION LEAKS" in e for e in hits[0].evidence)
    assert o.detect_leakcanary(LEAKCANARY_NOISE) == []


def snap(**kw):
    base = dict(device="a", time=0.0, pid="100", call_state="Ongoing", notification=True, services={"s": True}, awake=True)
    base.update(kw)
    return Snapshot(**base)


def keys(problems):
    return sorted(p.key for p in problems)


def test_service_notification():
    assert o.check_service_notification(snap()) == []
    assert keys(o.check_service_notification(snap(notification=False))) == ["active-no-notification"]
    assert keys(o.check_service_notification(snap(services={"s": False}))) == ["active-not-foreground"]
    assert keys(o.check_service_notification(snap(call_state="Idle"))) == ["idle-notification", "idle-service-running"]
    assert o.check_service_notification(snap(call_state="Idle", notification=False, services={})) == []
    assert o.check_service_notification(snap(call_state=None, notification=False, services={})) == []  # UI unreadable: no verdict
    assert o.check_service_notification(snap(pid=None, call_state="Ongoing", notification=False)) == []
    assert o.check_service_notification(snap(call_state="Disconnecting", notification=True)) == []


def test_stale_ui():
    assert keys(o.check_stale_ui(snap(services={}))) == ["stale-Ongoing"]
    assert o.check_stale_ui(snap()) == []
    assert o.check_stale_ui(snap(call_state="Idle", services={})) == []
    assert o.check_stale_ui(snap(pid=None, services={})) == []  # process dead: that's process_death's business


def test_wake_locks():
    ended = snap(call_state="Idle", notification=False, services={}, wake_locks=["PARTIAL_WAKE_LOCK 'AudioIn' uids=[1041, 10217]"])
    assert len(o.check_wake_locks(ended, ever_in_call=True)) == 1
    assert o.check_wake_locks(ended, ever_in_call=False) == []
    assert o.check_wake_locks(snap(wake_locks=["x"]), ever_in_call=True) == []  # call still live
    assert o.check_wake_locks(snap(call_state="Idle", services={}, wake_locks=[]), ever_in_call=True) == []


def test_process_death():
    assert keys(o.check_process_death("100", snap(pid=None), None)) == ["death:100->gone"]
    assert keys(o.check_process_death("100", snap(pid="200"), "rotate")) == ["death:100->200"]
    assert o.check_process_death("100", snap(pid=None), "kill_process") == []
    assert o.check_process_death("100", snap(pid="100"), None) == []
    assert o.check_process_death(None, snap(pid="200"), "launch_app") == []


def test_memory_growth():
    mb = 1024
    assert o.check_memory_growth([100 * mb, 105 * mb, 108 * mb], 40, 3) == ("ok", 8.0)
    status, growth = o.check_memory_growth([100 * mb, 120 * mb, 160 * mb], 40, 3)
    assert status == "growth" and growth == 60
    assert o.check_memory_growth([100 * mb, 500 * mb], 40, 3)[0] == "skipped"


def test_build_oracles_respects_checks_config(monkeypatch):
    names = lambda objs: [x.name for x in objs]  # noqa: E731
    assert len(o.build_oracles(None)) == 9
    assert names(o.build_oracles(ChecksConfig(enabled=["crash", "wake_locks"]))) == ["crash", "wake_locks"]
    mem = o.build_oracles(ChecksConfig(enabled=["memory_growth"], options={"memory_growth": {"max_growth_mb": 5}}))[0]
    assert mem.max_growth_mb == 5
    monkeypatch.setenv("QA_MEMORY_GROWTH_MB", "12")
    assert o.build_oracles(None)[-1].max_growth_mb == 12
