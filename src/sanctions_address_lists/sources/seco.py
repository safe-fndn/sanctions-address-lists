"""Switzerland SECO consolidated sanctions list adapter.

Structure (live-verified): root ``swiss-sanctions-list`` holds one ``target``
per sanctioned subject (``ssid`` attribute). Each target has exactly one
subject element -- ``individual``, ``entity``, or ``object`` -- whose direct
``other-information`` children carry free text, and whose ``identity``
subtree holds ``remark`` fields (on ``address`` / ``identification-document``).
SECO has no dedicated crypto field, but does publish addresses in
``other-information`` (e.g. Garantex's ETH/BSC wallets).

Each target also carries ``modification`` history with ``added``/``removed``
copies of earlier versions; those are never scanned (and never counted as
records), since they can contain addresses that are no longer listed.

Earlier attempts from two independent networks (a dev sandbox and a
maintainer's own machine on a VPN) timed out at the TCP level; disabling the
VPN resolved it, suggesting SECO's infrastructure blocks some VPN/proxy IP
ranges. See FEASIBILITY.md for the full diagnosis -- if this source starts
failing again, check that history before assuming the code or schema broke.
"""

from __future__ import annotations

from sanctions_address_lists.models import AddressLink, SourceRecord
from sanctions_address_lists.sources.base import SourceAdapter
from sanctions_address_lists.sources.errors import EmptyResultError, SchemaError
from sanctions_address_lists.sources.xmlutil import (
    find_all,
    find_direct,
    local_name,
    parse_xml,
    text_of,
)
from sanctions_address_lists.textscan import scan_text_fields

_ROOT_TAG = "swiss-sanctions-list"
_TARGET_TAG = "target"
_SUBJECT_TAGS = ("individual", "entity", "object")
_OTHER_INFO_TAG = "other-information"
_REMARK_TAG = "remark"


class SecoAdapter(SourceAdapter):
    SOURCE_ID = "ch-seco"
    JURISDICTION = "CH"
    SOURCE_URL = (
        "https://www.sesam.search.admin.ch/sesam-search-web/pages/"
        "downloadXmlGesamtliste.xhtml?lang=en&action=downloadXmlGesamtlisteAction"
    )
    GATES_BUILD = True

    def parse(self, raw: bytes) -> tuple[list[SourceRecord], list[AddressLink]]:
        try:
            root = parse_xml(raw)
        except Exception as exc:
            raise SchemaError(f"SECO list did not parse as XML: {exc}") from exc

        if local_name(root.tag) != _ROOT_TAG:
            raise SchemaError(f"unexpected root element {root.tag!r}, expected {_ROOT_TAG}")

        targets = list(find_direct(root, _TARGET_TAG))
        if not targets:
            raise EmptyResultError(f"SECO list contained zero {_TARGET_TAG!r} records")

        records: list[SourceRecord] = []
        links: list[AddressLink] = []
        for target in targets:
            record_id = target.get("ssid")
            if not record_id:
                raise SchemaError("SECO target is missing its required ssid")
            records.append(SourceRecord(record_id=record_id))

            # Only the current subject -- never the target's modification history.
            text_fields: list[str | None] = []
            for subject in target:
                if local_name(subject.tag) not in _SUBJECT_TAGS:
                    continue
                text_fields += [text_of(el) for el in find_direct(subject, _OTHER_INFO_TAG)]
                text_fields += [text_of(el) for el in find_all(subject, _REMARK_TAG)]

            for address in scan_text_fields(
                text_fields, record_id=record_id, source_id=self.SOURCE_ID
            ):
                links.append(
                    AddressLink(
                        address=address,
                        jurisdiction=self.JURISDICTION,
                        source=self.SOURCE_ID,
                        source_record_id=record_id,
                        source_url=self.SOURCE_URL,
                    )
                )
        return records, links
