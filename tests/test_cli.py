from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from sanctions_address_lists import cli
from sanctions_address_lists.models import AddressLink, SourceResult
from sanctions_address_lists.sources.base import SourceAdapter


class _StubAdapter(SourceAdapter):
    """A fake adapter for CLI-level tests -- never touches the network."""

    SOURCE_ID = "stub-ok"
    JURISDICTION = "ZZ"
    SOURCE_URL = "https://example.org/stub"
    GATES_BUILD = True
    RESULT_STATUS: ClassVar[str] = "success"

    def parse(self, raw: bytes) -> tuple[list, list]:  # type: ignore[type-arg]
        raise NotImplementedError

    def run(self, client: httpx.Client, *, retries: int = 3) -> SourceResult:
        return SourceResult(
            source_id=self.SOURCE_ID,
            jurisdiction=self.JURISDICTION,
            source_url=self.SOURCE_URL,
            status=self.RESULT_STATUS,  # type: ignore[arg-type]
            retrieved_at="2026-08-20T00:00:00+00:00",
            content_hash="deadbeef",
            record_count=1,
            links=(
                AddressLink(
                    "0x1111111111111111111111111111111111111111",
                    self.JURISDICTION,
                    self.SOURCE_ID,
                    "r1",
                    self.SOURCE_URL,
                ),
            )
            if self.RESULT_STATUS == "success"
            else (),
            error=None if self.RESULT_STATUS == "success" else "boom",
        )


class _StubFailing(_StubAdapter):
    SOURCE_ID = "stub-failing"
    RESULT_STATUS = "failed"


class _StubUnavailable(_StubAdapter):
    SOURCE_ID = "stub-unavailable"
    RESULT_STATUS = "failed"

    def run(self, client: httpx.Client, *, retries: int = 3) -> SourceResult:
        result = super().run(client, retries=retries)
        result.transient = True
        return result


class _StubUnverified(_StubAdapter):
    SOURCE_ID = "stub-unverified"
    GATES_BUILD = False
    RESULT_STATUS = "unverified"


class _StubUnsupported(_StubAdapter):
    SOURCE_ID = "stub-unsupported"
    GATES_BUILD = False
    RESULT_STATUS = "unsupported"


@pytest.fixture
def patched_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = (_StubAdapter, _StubUnverified, _StubUnsupported)
    monkeypatch.setattr(cli, "ALL_SOURCES", sources)

    def fake_get_source(source_id: str) -> type[SourceAdapter]:
        for adapter_cls in sources:
            if source_id == adapter_cls.SOURCE_ID:
                return adapter_cls
        raise KeyError(source_id)

    monkeypatch.setattr(cli, "get_source", fake_get_source)


def test_exit_code_zero_when_all_gating_sources_succeed(
    tmp_path: Path, patched_registry: None
) -> None:
    exit_code = cli.main(["run", "--output-dir", str(tmp_path)])
    assert exit_code == 0


def test_unsupported_and_unverified_never_gate(tmp_path: Path, patched_registry: None) -> None:
    # patched_registry has no failing source; unverified/unsupported present but must not fail.
    exit_code = cli.main(["run", "--output-dir", str(tmp_path)])
    assert exit_code == 0
    manifest = (tmp_path / "manifest.json").read_text()
    assert '"unverified"' in manifest
    assert '"unsupported"' in manifest


def test_exit_code_one_when_a_gating_source_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = (_StubAdapter, _StubFailing)
    monkeypatch.setattr(cli, "ALL_SOURCES", sources)
    exit_code = cli.main(["run", "--output-dir", str(tmp_path)])
    assert exit_code == 1


def test_exit_code_zero_when_a_gating_source_is_only_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "ALL_SOURCES", (_StubAdapter, _StubUnavailable))
    exit_code = cli.main(["run", "--output-dir", str(tmp_path), "--no-color"])
    assert exit_code == 0
    assert "UNAVAILABLE (not failing the run): stub-unavailable" in capsys.readouterr().out
    assert '"failed"' in (tmp_path / "manifest.json").read_text()


def test_source_filter_limits_which_adapters_run(tmp_path: Path, patched_registry: None) -> None:
    exit_code = cli.main(["run", "--output-dir", str(tmp_path), "--source", "stub-ok"])
    assert exit_code == 0
    assert (tmp_path / "csv" / "stub-ok.csv").exists()
    assert not (tmp_path / "csv" / "stub-unverified.csv").exists()


def test_unknown_source_filter_errors(tmp_path: Path, patched_registry: None) -> None:
    exit_code = cli.main(["run", "--output-dir", str(tmp_path), "--source", "nope"])
    assert exit_code == 2


def test_list_sources_runs_without_network(
    patched_registry: None, capsys: pytest.CaptureFixture
) -> None:
    exit_code = cli.main(["list-sources"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "stub-ok" in out
