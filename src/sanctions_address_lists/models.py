"""Data models shared across source adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["success", "unsupported", "failed", "unverified"]


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A single parsed record from an official source, identified by its record id."""

    record_id: str


@dataclass(frozen=True, slots=True)
class AddressLink:
    """One address-to-source-record relationship; mirrors a canonical CSV row."""

    address: str
    jurisdiction: str
    source: str
    source_record_id: str
    source_url: str

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.address, self.source_record_id)

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        return (self.address, self.source_record_id, self.source_url)


@dataclass(slots=True)
class SourceResult:
    """The outcome of running a single source adapter."""

    source_id: str
    jurisdiction: str
    source_url: str
    status: Status
    resolved_url: str | None = None
    retrieved_at: str | None = None
    content_hash: str | None = None
    record_count: int = 0
    links: tuple[AddressLink, ...] = field(default_factory=tuple)
    error: str | None = None
    # True when the failure was the upstream being unavailable (network error,
    # 5xx) after all retries, rather than something needing a code fix.
    transient: bool = False

    @property
    def address_count(self) -> int:
        return len({link.address for link in self.links})
