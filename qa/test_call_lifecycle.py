"""E2E lifecycle/memory-leak flows against real emulators -- automates the manual investigation
from the prior memory-leak session (see qa/README.md for the full writeup of what was found).

Run with:  pytest -m emulator qa/
Everything here is skipped automatically if ANDROID_HOME isn't set (see conftest.py).

Each test is independent and boots its own emulator(s) -- slower than sharing one across a
session, but a failure in one test can never leave stale app/device state for the next one to
trip over. If these get slow enough to matter, consider moving to session-scoped device fixtures
in conftest.py (TODO, not needed yet at 4 tests).

Every test here uses `two_devices`, even the ones that only assert against one of them -- see
join_as_pair()'s docstring for why: the sample app only reports VoipCallState.Ongoing once a
second participant joins the room, a real discovery made while building this suite (not an
assumption). `one_device` is still available in conftest.py for a future flow that genuinely
doesn't need a connected call (e.g. Idle-screen-only behavior).
"""

from __future__ import annotations

import os
import time

import pytest

from adb_utils import Device

pytestmark = pytest.mark.emulator

# TODO: point this at your own Daily.co test room (or any provider's test room, once this repo
# has more than one VoipSdkClient implementation wired into the sample app). Tests that need a
# real room are skipped with a clear reason if this isn't set -- never hardcode a real external
# room URL into checked-in test code.
MEETING_URL = os.environ.get("QA_MEETING_URL")

requires_meeting_url = pytest.mark.skipif(
    not MEETING_URL,
    reason="Set QA_MEETING_URL to a real provider test-room URL to run call-join flows",
)


def join_as_pair(devices: list[Device], meeting_url: str, timeout: float = 45) -> tuple[Device, Device]:
    """Joins both devices to the same room and waits for the first to reach Ongoing, then
    returns (primary, helper).

    This exists because of a real discovery made while building this suite: the sample app only
    ever reports VoipCallState.Ongoing once a *second* participant is detected in the room (see
    DailyCoVoipSdkClient.onCallStateUpdated's remoteAlreadyPresent / onParticipantJoined) -- a
    single device joining alone gets a fully successful local WebRTC join (confirmed in logcat:
    "Completed request join: Success") but sits in Connecting forever, because nobody else is in
    the room. Any flow that needs a real Ongoing call -- the notification-dismiss test, the
    backgrounding test, the process-death test -- therefore needs a second device present too,
    even though the test itself only asserts against the first one.
    """
    primary, helper = devices[0], devices[1]
    for d in devices:
        d.launch_main_activity()
    for d in devices:
        d.join_meeting(meeting_url)
    primary.wait_for_call_state("Ongoing", timeout=timeout)
    return primary, helper


# ---------------------------------------------------------------------------------------------
# Flow 1: two emulators join the same call, rotate, check LeakCanary -- the original manual
# investigation's main scenario (see qa/README.md). No confirmed "N APPLICATION LEAKS" allowed;
# transient "retained, not dumping yet" / "garbage collected" lines are expected and fine.
# ---------------------------------------------------------------------------------------------


@requires_meeting_url
def test_two_devices_join_same_call_survive_rotation_without_leak(two_devices, leak_watch):
    device_a, device_b = two_devices
    watches = [leak_watch(d, f"rotation_{i}") for i, d in enumerate(two_devices)]

    for d in two_devices:
        d.launch_main_activity()

    with watches[0], watches[1]:
        for d in two_devices:
            d.join_meeting(MEETING_URL)
        for d in two_devices:
            d.wait_for_call_state("Ongoing", timeout=30)

        # TODO: add more rotation cycles here (e.g. range(3)) if you want higher confidence --
        # one cycle was enough to catch the real leak this suite is modeled on, but a flakier
        # race might need more iterations to reproduce reliably.
        for d in two_devices:
            d.rotate_landscape()
        time.sleep(2)
        for d in two_devices:
            d.rotate_portrait()
        time.sleep(2)

        for d in two_devices:
            assert d.text_of_resource_id("call_state_label") == "Ongoing", (
                f"{d.serial}: call dropped out of Ongoing across rotation"
            )

        for d in two_devices:
            d.press_home()
        time.sleep(2)
        for d in two_devices:
            d.launch_main_activity()
        time.sleep(2)

    for watch in watches:
        assert watch.result.fatal_exceptions == 0, f"crash in logcat:\n{watch.result.raw}"
        assert watch.result.confirmed_leak_count == 0, (
            f"LeakCanary confirmed {watch.result.confirmed_leak_count} leak(s) -- see captured logcat"
        )


# ---------------------------------------------------------------------------------------------
# Flow 2: the call notification must NOT be swipe-dismissable while the call is live -- this is
# what the CallStyle fix (AbstractVoipCallService.ACTION_HANG_UP, SampleVoipCallService's
# CallStyle.forOngoingCall) is specifically for. A regression here would mean a user can
# accidentally swipe away an active call's only visible indicator.
# ---------------------------------------------------------------------------------------------


@requires_meeting_url
def test_call_notification_is_not_swipe_dismissable_while_ongoing(two_devices):
    # Needs two_devices, not one_device -- see join_as_pair's docstring: Ongoing never happens
    # with only one participant in the room.
    device, _helper = join_as_pair(two_devices, MEETING_URL)

    assert device.notification_posted(), "expected an active call notification before attempting to dismiss it"

    device.swipe_notification_shade_dismiss_attempt()

    assert device.notification_posted(), (
        "call notification was swipe-dismissed while the call was still Ongoing -- "
        "CallStyle regression (see AbstractVoipCallService.ACTION_HANG_UP / SampleVoipCallService)"
    )
    # The call itself must also be unaffected by the (failed) dismiss attempt.
    assert device.text_of_resource_id("call_state_label") == "Ongoing"


# ---------------------------------------------------------------------------------------------
# Flow 3: background the app mid-call, wait, bring it back -- must still show the live call
# (not a stale/blank screen) with no crash and no confirmed leak. Mirrors the manual
# investigation's background/foreground stress cycles.
# ---------------------------------------------------------------------------------------------


@requires_meeting_url
@pytest.mark.parametrize(
    "background_seconds",
    [
        5,
        # TODO: add a longer duration (e.g. 60+) once you want to specifically probe Android's
        # own background-process memory trimming / possible OS-initiated reclaim, not just a
        # quick backgrounding. Keep short durations in the default run so the suite stays fast.
    ],
)
def test_backgrounding_then_foreground_preserves_ongoing_call(two_devices, leak_watch, background_seconds):
    # Needs two_devices, not one_device -- see join_as_pair's docstring.
    watch = leak_watch(two_devices[0], f"background_{background_seconds}s")
    device, _helper = join_as_pair(two_devices, MEETING_URL)

    with watch:
        device.press_home()
        time.sleep(background_seconds)
        device.launch_main_activity()

        device.wait_for_call_state("Ongoing", timeout=15)
        assert device.notification_posted(), "call notification disappeared while backgrounded"

    assert watch.result.fatal_exceptions == 0, f"crash in logcat:\n{watch.result.raw}"
    assert watch.result.confirmed_leak_count == 0, (
        f"LeakCanary confirmed {watch.result.confirmed_leak_count} leak(s) across backgrounding"
    )


# ---------------------------------------------------------------------------------------------
# Flow 4: genuine process death (force-stop, not `am kill` -- see adb_utils.Device.kill's
# docstring for why that distinction matters) mid-call, then relaunch. Matches the earlier manual
# finding: the app must come back to a clean Idle screen, never a stale/crashed "in call" UI --
# there's no local media session left to reattach to after a real process death.
# ---------------------------------------------------------------------------------------------


@requires_meeting_url
def test_process_death_mid_call_relaunches_to_clean_idle(two_devices, leak_watch):
    # Needs two_devices, not one_device -- see join_as_pair's docstring.
    watch = leak_watch(two_devices[0], "process_death")
    device, _helper = join_as_pair(two_devices, MEETING_URL)

    with watch:
        assert device.pid() is not None
        device.force_stop()
        assert device.pid() is None, "force-stop did not actually kill the process"

        device.launch_main_activity()
        device.wait_for_call_state("Idle", timeout=15)

    assert watch.result.fatal_exceptions == 0, f"crash in logcat:\n{watch.result.raw}"


# TODO: more flows worth adding here as the library grows -- each one is a plain pytest function
# using the same `one_device`/`two_devices`/`leak_watch` fixtures, no new plumbing needed:
#   - mic/recording toggle races (rapid on/off taps) while rotating, watched for leaks
#   - a real OOM kill (as opposed to force-stop) via `adb shell am send-trim-memory` /
#     low-memory-killer simulation, if/when Android exposes a reliable way to trigger one
#   - multi-cycle join/leave stress (N repetitions in a loop) on a single low-end device, which
#     is what originally surfaced the one confirmed leak this suite's leak_watch fixture exists
#     to catch again if it ever regresses
