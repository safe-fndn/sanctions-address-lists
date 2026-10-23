"""Common interface every source adapter implements."""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import ClassVar

import httpx

from sanctions_address_lists.http import DEFAULT_RETRIES, get_with_retries
from sanctions_address_lists.models import AddressLink, SourceRecord, SourceResult
from sanctions_address_lists.sources.errors import EmptyResultError, SchemaError
from sanctions_address_lists.textscan import BOUNDARY_RE

logger = logging.getLogger(__name__)


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

    def fetch(
        self, client: httpx.Client, *, retries: int = DEFAULT_RETRIES
    ) -> tuple[bytes, str, list[SourceRecord], list[AddressLink]]:
        """Download and parse the source's payload.

        Returns ``(raw_bytes, final_url, records, links)``. Parsing happens inside
        the HTTP retry loop, so a payload that fails to parse (e.g. a truncated
        download) is re-fetched. Structure is validated by ``parse()`` rather than
        by Content-Type, since headers on official endpoints are not reliable
        enough to gate on.
        """
        response, (records, links) = get_with_retries(
            client,
            self.SOURCE_URL,
            parse=self.parse,
            retry_parse_errors=(SchemaError, EmptyResultError),
            retries=retries,
        )
        return response.content, str(response.url), records, links

    @abstractmethod
    def parse(self, raw: bytes) -> tuple[list[SourceRecord], list[AddressLink]]:
        """Parse raw bytes into records and any explicitly-published EVM addresses.

        Raises ``SchemaError`` if the expected structure isn't found, and
        ``EmptyResultError`` if zero records are parsed (a valid source may still
        yield zero *addresses* -- that is not an error).
        """

    def _warn_unextracted(self, raw: bytes, links: list[AddressLink]) -> None:
        """Warn about EVM addresses anywhere in the raw payload that ``parse()`` didn't extract.

        A safety net for adapters looking in the wrong place. A warning, not a
        failure: sections like change history can legitimately hold delisted
        addresses.
        """
        in_raw = {m.group(0).lower() for m in BOUNDARY_RE.finditer(raw.decode("utf-8", "replace"))}
        missing = sorted(in_raw - {link.address for link in links})
        if missing:
            logger.warning(
                "%s: %d address(es) in the raw payload were not extracted: %s",
                self.SOURCE_ID,
                len(missing),
                ", ".join(missing),
            )

    def run(self, client: httpx.Client, *, retries: int = DEFAULT_RETRIES) -> SourceResult:
        """Fetch, hash, and parse this source, producing a `SourceResult`."""
        retrieved_at = datetime.now(UTC).isoformat()
        try:
            raw, resolved_url, records, links = self.fetch(client, retries=retries)
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

        self._warn_unextracted(raw, links)
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
