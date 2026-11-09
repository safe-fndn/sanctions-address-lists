"""Track how long each source has been unavailable upstream, across runs.

A source that's only unavailable (network error, 5xx after all retries)
doesn't fail a run on its own -- but if it stays unavailable for too long, its
published files go stale, so the run should fail after all. Each run can't see
that on its own, so the state is kept in a small JSON file the caller persists
between runs::

    {"ch-seco": {"unavailable_since": "2026-11-09T04:17:00+00:00"}}

A source is listed only while it's unavailable. The clock starts at the first
run that sees it unavailable and is cleared by the next successful run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from sanctions_address_lists.models import SourceResult

DEFAULT_MAX_UNAVAILABLE_DAYS = 7.0


class AvailabilityState:
    def __init__(self, unavailable_since: dict[str, datetime] | None = None) -> None:
        self.unavailable_since: dict[str, datetime] = dict(unavailable_since or {})

    @classmethod
    def load(cls, path: Path) -> AvailabilityState:
        """Load state from ``path``; a missing file is an empty state (first run)."""
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        return cls(
            {
                source_id: datetime.fromisoformat(entry["unavailable_since"])
                for source_id, entry in raw.items()
            }
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            source_id: {"unavailable_since": since.isoformat()}
            for source_id, since in sorted(self.unavailable_since.items())
        }
        path.write_text(json.dumps(payload, indent=2) + "\n")

    def record(self, result: SourceResult, now: datetime) -> timedelta | None:
        """Update state with ``result``; return how long it's been unavailable, if it is."""
        if result.status == "success":
            self.unavailable_since.pop(result.source_id, None)
            return None
        if result.status == "failed" and result.transient:
            since = self.unavailable_since.setdefault(result.source_id, now)
            return now - since
        return None
