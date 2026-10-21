"""pytest wiring for the scenario suite: session-scoped emulators shared by every scenario test.

The offline unit tests (qa/tests/) never request these fixtures, so they run anywhere. The live
tests (test_scenarios.py) request `fleet`, which skips with a reason if ANDROID_HOME is missing.
"""

from __future__ import annotations

import pytest

from samvaad_qa import session


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "emulator: requires a real Android emulator (adb/AVDs); see qa/README.md")


@pytest.fixture(scope="session")
def android_sdk() -> str:
    try:
        return session.locate_sdk()
    except session.SetupError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def apk_path(android_sdk: str) -> str:
    """The sample app's debug APK, built once per session (QA_SKIP_BUILD=1 reuses the one on disk)."""
    return session.build_apk()


@pytest.fixture(scope="session")
def fleet(apk_path: str):
    """The session's emulators, booted lazily as scenarios ask for devices a/b and reset between
    scenarios. Shut down at the end unless QA_KEEP_EMULATORS=1."""
    fleet = session.Fleet(apk_path)
    try:
        yield fleet
    finally:
        fleet.shutdown()
