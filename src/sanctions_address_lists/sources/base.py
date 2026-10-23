"""Common interface every source adapter implements."""

from __future__ import annotations

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import ClassVar

import httpx

from sanctions_address_lists.http import DEFAULT_RETRIES, get_with_retries
from sanctions_address_lists.models import AddressLink, SourceRecord, SourceResult
from sanctions_address_lists.sources.errors import EmptyResultError, SchemaError

logger = logging.getLogger(__name__)

# A truncated download (e.g. a gzip/chunked stream cut short without a
# transport error) only surfaces as a parse failure, so fetch+parse is retried
# as a unit.
PARSE_ATTEMPTS = 3
_PARSE_RETRY_BACKOFF_SECONDS = 5.0


class SourceAdapter(ABC):
    """Base class for a single official sanctions source.

    Subclasses declare ``SOURCE_ID``/``JURISDICTION``/``SOURCE_URL`` and implement
    ``parse()``. ``GATES_BUILD`` controls whether a "failed" status from this
    source should fail the overall CLI run (True for sources expected to work
    reliably; False for sources that are unsupported or not yet verified against
    live data).
    """

    SOURCE_ID: ClassVar[str]
    JURISDICTION: ClassVar[str]
    SOURCE_URL: ClassVar[str]
    GATES_BUILD: ClassVar[bool] = True

    def fetch(self, client: httpx.Client, *, retries: int = DEFAULT_RETRIES) -> tuple[bytes, str]:
        """Download the source's payload.

        Returns ``(raw_bytes, final_url)``. Does not validate structure -- that
        happens in ``parse()``, since Content-Type headers on official endpoints
        are not reliable enough to gate on.
        """
        response = get_with_retries(client, self.SOURCE_URL, retries=retries)
        return response.content, str(response.url)

    @abstractmethod
    def parse(self, raw: bytes) -> tuple[list[SourceRecord], list[AddressLink]]:
        """Parse raw bytes into records and any explicitly-published EVM addresses.

        Raises ``SchemaError`` if the expected structure isn't found, and
        ``EmptyResultError`` if zero records are parsed (a valid source may still
        yield zero *addresses* -- that is not an error).
        """

    def _fetch_and_parse(
        self, client: httpx.Client, *, retries: int
    ) -> tuple[bytes, str, list[SourceRecord], list[AddressLink]]:
        """Fetch and parse, re-fetching up to ``PARSE_ATTEMPTS`` times on a parse failure."""
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            raw, resolved_url = self.fetch(client, retries=retries)
            try:
                records, links = self.parse(raw)
            except (SchemaError, EmptyResultError) as exc:
                if attempt == PARSE_ATTEMPTS:
                    raise
                logger.warning(
                    "%s: parse attempt %d/%d failed, re-fetching: %s",
                    self.SOURCE_ID,
                    attempt,
                    PARSE_ATTEMPTS,
                    exc,
                )
                time.sleep(_PARSE_RETRY_BACKOFF_SECONDS * attempt)
                continue
            return raw, resolved_url, records, links
        raise AssertionError("unreachable")

    def run(self, client: httpx.Client, *, retries: int = DEFAULT_RETRIES) -> SourceResult:
        """Fetch, hash, and parse this source, producing a `SourceResult`."""
        retrieved_at = datetime.now(UTC).isoformat()
        try:
            raw, resolved_url, records, links = self._fetch_and_parse(client, retries=retries)
            content_hash = hashlib.sha256(raw).hexdigest()
        except (httpx.HTTPError, SchemaError, EmptyResultError, ValueError) as exc:
            logger.error("%s: failed - %s", self.SOURCE_ID, exc)
            return SourceResult(
                source_id=self.SOURCE_ID,
                jurisdiction=self.JURISDICTION,
                source_url=self.SOURCE_URL,
                status="failed",
                retrieved_at=retrieved_at,
                error=str(exc),
            )

        deduped = sorted(
            {link.dedup_key: link for link in links}.values(),
            key=lambda link: link.sort_key,
        )
        result = SourceResult(
            source_id=self.SOURCE_ID,
            jurisdiction=self.JURISDICTION,
            source_url=self.SOURCE_URL,
            status="success",
            resolved_url=resolved_url,
            retrieved_at=retrieved_at,
            content_hash=content_hash,
            record_count=len(records),
            links=tuple(deduped),
        )
        logger.debug(
            "%s: success - %d records, %d addresses",
            self.SOURCE_ID,
            result.record_count,
            result.address_count,
        )
        return result
