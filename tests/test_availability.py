from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sanctions_address_lists import cli
from sanctions_address_lists.availability import AvailabilityState
from sanctions_address_lists.models import SourceResult
from tests.test_cli import _StubAdapter, _StubFailing, _StubUnavailable

T0 = datetime(2026, 11, 1, 4, 17, tzinfo=UTC)


def _result(status: str, *, transient: bool = False) -> SourceResult:
    return SourceResult(
        source_id="ch-seco",
        jurisdiction="CH",
        source_url="https://example.org",
        status=status,  # type: ignore[arg-type]
        transient=transient,
    )


def test_clock_starts_at_first_unavailable_run_and_resets_on_success() -> None:
    state = AvailabilityState()
    assert state.record(_result("failed", transient=True), T0) == timedelta(0)
    later = T0 + timedelta(days=3)
    assert state.record(_result("failed", transient=True), later) == timedelta(days=3)
    assert state.record(_result("success"), later) is None
    assert state.unavailable_since == {}


def test_non_transient_failure_leaves_the_clock_alone() -> None:
    state = AvailabilityState({"ch-seco": T0})
    assert state.record(_result("failed"), T0 + timedelta(days=9)) is None
    assert state.unavailable_since == {"ch-seco": T0}


def test_state_round_trips_and_missing_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "state" / "availability.json"
    assert AvailabilityState.load(path).unavailable_since == {}
    AvailabilityState({"ch-seco": T0}).save(path)
    assert AvailabilityState.load(path).unavailable_since == {"ch-seco": T0}


@pytest.mark.parametrize(("days_ago", "exit_code"), [(6, 0), (8, 1)])
def test_run_fails_once_a_source_is_unavailable_too_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, days_ago: int, exit_code: int
) -> None:
    monkeypatch.setattr(cli, "ALL_SOURCES", (_StubAdapter, _StubUnavailable))
    state_file = tmp_path / "availability.json"
    since = datetime.now(UTC) - timedelta(days=days_ago)
    AvailabilityState({"stub-unavailable": since, "stub-ok": since}).save(state_file)

    args = ["run", "--output-dir", str(tmp_path / "out"), "--state-file", str(state_file)]
    assert cli.main(args) == exit_code

    # stub-ok succeeded, so its clock is cleared; stub-unavailable's keeps running.
    assert AvailabilityState.load(state_file).unavailable_since == {"stub-unavailable": since}
    manifest = (tmp_path / "out" / "manifest.json").read_text()
    assert ("unavailable for 8 days (limit 7)" in manifest) is (exit_code == 1)


def test_without_state_file_unavailability_never_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "ALL_SOURCES", (_StubAdapter, _StubUnavailable))
    assert cli.main(["run", "--output-dir", str(tmp_path)]) == 0
    monkeypatch.setattr(cli, "ALL_SOURCES", (_StubAdapter, _StubFailing))
    assert cli.main(["run", "--output-dir", str(tmp_path)]) == 1
