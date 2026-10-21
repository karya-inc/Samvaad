"""adb_utils' dumpsys/ps parsers against canned output captured from a real API 36 emulator."""

from __future__ import annotations

import adb_utils as au

PKG = "com.daiatech.samvaad.app"

POWER = """\
  mWakefulness=Awake
  mWakefulnessChanging=false
Wake Locks: size=3
  PARTIAL_WAKE_LOCK                 '*job*r/com.google.android.providers.media.module/com.android.providers.media.MediaService' ACQ=-34s960ms (uid=1000 pid=540 ws=WorkSource{ chains=WorkChain{(10211), (1000, JobScheduler)}})
  PARTIAL_WAKE_LOCK                 'AudioIn' ACQ=-5s (uid=1041 pid=450 ws=WorkSource{10217})
  PARTIAL_WAKE_LOCK                 'CMWakeLock' ACQ=-34s274ms (uid=10148 pid=1213 ws=WorkSource{10148 com.google.android.gms})
Suspend Blockers: size=1
"""

SERVICES = """\
ACTIVITY MANAGER SERVICES (dumpsys activity services)
  User 0 active services:
  * ServiceRecord{d09997c u0 com.daiatech.samvaad.app/.SampleVoipCallService c:com.daiatech.samvaad.app}
    packageName=com.daiatech.samvaad.app
    isForeground=true foregroundId=4210 types=0x00000080
  * ServiceRecord{aaa1111 u0 com.other.app/.Svc}
    isForeground=true foregroundId=1
"""

SERVICES_BG = """\
  * ServiceRecord{d09997c u0 com.daiatech.samvaad.app/.SampleVoipCallService c:x}
    startRequested=true delayedStop=false
  * ServiceRecord{aaa1111 u0 com.other.app/.Svc}
    isForeground=true foregroundId=1
"""


def test_wakefulness():
    assert au.parse_wakefulness(POWER) == "Awake"
    assert au.parse_wakefulness("mWakefulness=Asleep") == "Asleep"
    assert au.parse_wakefulness("nothing") is None


def test_wake_locks_filtered_by_uid_including_worksource():
    assert len(au.parse_wake_locks(POWER)) == 3
    mine = au.parse_wake_locks(POWER, uid=10217)
    assert [lock.tag for lock in mine] == ["AudioIn"]  # attributed via WorkSource, owner uid is audioserver
    assert [lock.tag for lock in au.parse_wake_locks(POWER, uid=10211)] == ["*job*r/com.google.android.providers.media.module/com.android.providers.media.MediaService"]
    assert au.parse_wake_locks(POWER, uid=99999) == []


def test_services_only_for_the_package_and_foreground_flag():
    assert au.parse_services(SERVICES, PKG) == {f"{PKG}/.SampleVoipCallService": True}
    assert au.parse_services(SERVICES_BG, PKG) == {f"{PKG}/.SampleVoipCallService": False}
    assert au.parse_services("(nothing)", PKG) == {}


def test_pss():
    assert au.parse_pss_total_kb("           TOTAL PSS:    74869            TOTAL RSS:   166028") == 74869
    assert au.parse_pss_total_kb("        TOTAL    74869    57028") == 74869
    assert au.parse_pss_total_kb("No process found for: x") is None


def test_pid_from_ps_exact_name():
    ps = "  PID NAME\n 3281 com.daiatech.samvaad.app\n 3300 com.daiatech.samvaad.app:remote\n"
    assert au.parse_pid_from_ps(ps, PKG) == "3281"
    assert au.parse_pid_from_ps("  PID NAME\n 3300 com.daiatech.samvaad.app:remote\n", PKG) is None


def test_notification_records():
    dump = (
        "  NotificationRecord(0x1: pkg=com.daiatech.samvaad.app user=UserHandle{0} id=4210 tag=null importance=3)\n"
        "  NotificationRecord(0x2: pkg=com.android.vending user=UserHandle{0} id=1)\n"
    )
    assert len(au.parse_notification_records(dump, PKG)) == 1
    assert au.parse_notification_records(dump, "com.nothing") == []
