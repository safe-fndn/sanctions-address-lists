import gzip
from pathlib import Path

import httpx
import pytest

from sanctions_address_lists import http
from sanctions_address_lists.http import DEFAULT_RETRIES
from sanctions_address_lists.sources.errors import EmptyResultError, SchemaError
from sanctions_address_lists.sources.eu import EuAdapter
from sanctions_address_lists.sources.ofac import OfacAdapter
from sanctions_address_lists.sources.seco import SecoAdapter
from sanctions_address_lists.sources.uk import UkAdapter
from sanctions_address_lists.sources.un import UnAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


ETH_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


# --- OFAC -------------------------------------------------------------------


def test_ofac_extracts_only_evm_formatted_digital_currency_addresses() -> None:
    records, links = OfacAdapter().parse(_read("ofac_sdn_sample.xml"))
    assert len(records) == 3
    addresses = {link.address for link in links}
    assert addresses == {ETH_ADDRESS}
    # rejects the digital-currency-tagged BTC address (wrong format) and the
    # EVM-looking value under a non-digital-currency idType (Passport).
    link = next(iter(links))
    assert link.source_record_id == "9000"
    assert link.jurisdiction == "US"
    assert link.source == "us-ofac"


def test_ofac_empty_sdn_list_raises_empty_result_error() -> None:
    with pytest.raises(EmptyResultError):
        OfacAdapter().parse(_read("ofac_sdn_empty.xml"))


def test_ofac_unexpected_root_raises_schema_error() -> None:
    with pytest.raises(SchemaError):
        OfacAdapter().parse(_read("ofac_sdn_malformed.xml"))


def test_ofac_non_xml_raises_schema_error() -> None:
    with pytest.raises(SchemaError):
        OfacAdapter().parse(b"not xml at all")


# --- EU -----------------------------------------------------------------


def test_eu_extracts_address_from_remark_only() -> None:
    records, links = EuAdapter().parse(_read("eu_sample.xml"))
    assert len(records) == 4
    addresses = {link.address for link in links}
    assert addresses == {ETH_ADDRESS, "0x2222222222222222222222222222222222222222"}
    by_record = {link.source_record_id: link.address for link in links}
    assert by_record["101"] == ETH_ADDRESS


def test_eu_extracts_remark_nested_under_name_alias() -> None:
    # Real-world case (Garantex): <remark> can be a child of <nameAlias>,
    # not just a direct child of <sanctionEntity>.
    _, links = EuAdapter().parse(_read("eu_sample.xml"))
    by_record = {link.source_record_id: link.address for link in links}
    assert by_record["104"] == "0x2222222222222222222222222222222222222222"


def test_eu_ignores_64_hex_char_value_in_remark() -> None:
    # entity 103's remark contains a 64-hex-char token -- must not be treated
    # as a 40-hex EVM address.
    _, links = EuAdapter().parse(_read("eu_sample.xml"))
    assert all(link.source_record_id != "103" for link in links)


def test_eu_missing_records_raises_empty_result_error() -> None:
    xml = b'<?xml version="1.0"?><export xmlns="http://eu.europa.ec/fpi/fsd/export"/>'
    with pytest.raises(EmptyResultError):
        EuAdapter().parse(xml)


def test_eu_wrong_root_raises_schema_error() -> None:
    xml = b'<?xml version="1.0"?><notexport/>'
    with pytest.raises(SchemaError):
        EuAdapter().parse(xml)


# --- UK -----------------------------------------------------------------


def test_uk_extracts_from_structured_field_and_fallback_remark() -> None:
    records, links = UkAdapter().parse(_read("uk_sample.xml"))
    assert len(records) == 3
    by_record = {link.source_record_id: link.address for link in links}
    assert by_record["TEST0001"] == ETH_ADDRESS
    assert by_record["TEST0002"] == "0x2222222222222222222222222222222222222222"
    assert "TEST0003" not in by_record


def test_uk_empty_raises_empty_result_error() -> None:
    xml = b'<?xml version="1.0"?><Designations><DateGenerated>x</DateGenerated></Designations>'
    with pytest.raises(EmptyResultError):
        UkAdapter().parse(xml)


def test_uk_wrong_root_raises_schema_error() -> None:
    with pytest.raises(SchemaError):
        UkAdapter().parse(b'<?xml version="1.0"?><NotDesignations/>')


# --- UN -----------------------------------------------------------------


def test_un_extracts_from_comments_across_individuals_and_entities() -> None:
    records, links = UnAdapter().parse(_read("un_sample.xml"))
    assert len(records) == 3
    addresses = {link.address for link in links}
    assert addresses == {ETH_ADDRESS}
    link = next(iter(links))
    assert link.source_record_id == "1000001"
    assert link.jurisdiction == "UN"


def test_un_empty_raises_empty_result_error() -> None:
    xml = (
        b'<?xml version="1.0"?><CONSOLIDATED_LIST>' b"<INDIVIDUALS/><ENTITIES/></CONSOLIDATED_LIST>"
    )
    with pytest.raises(EmptyResultError):
        UnAdapter().parse(xml)


def test_un_wrong_root_raises_schema_error() -> None:
    with pytest.raises(SchemaError):
        UnAdapter().parse(b'<?xml version="1.0"?><NOT_CONSOLIDATED/>')


# --- SECO (live-verified; see FEASIBILITY.md for the connectivity history) --


def test_seco_extracts_from_remark_and_other_information() -> None:
    records, links = SecoAdapter().parse(_read("seco_sample.xml"))
    assert len(records) == 2
    addresses = {link.address for link in links}
    assert addresses == {ETH_ADDRESS}


def test_seco_empty_raises_empty_result_error() -> None:
    xml = b'<?xml version="1.0"?><sanctions-list xmlns="urn:seco:sanctions"/>'
    with pytest.raises(EmptyResultError):
        SecoAdapter().parse(xml)


def _client(
    monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response | Exception]
) -> tuple[httpx.Client, list[int]]:
    """A client whose requests yield ``responses`` in order (the last one repeats).

    Returns the client and a request counter. Retry backoff is disabled.
    """
    calls: list[int] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        response = responses[min(len(calls), len(responses)) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(http, "_RETRY_BACKOFF_SECONDS", 0.0)
    return httpx.Client(transport=httpx.MockTransport(_handler)), calls


def _gzip_response(body: bytes) -> httpx.Response:
    """A gzip-encoded response whose body is only decoded when the client reads it."""
    return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(body))


def test_seco_run_reports_success_on_successful_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [httpx.Response(200, content=_read("seco_sample.xml"))])
    result = SecoAdapter().run(client=client)
    assert result.status == "success"
    assert result.address_count == 1
    assert SecoAdapter.GATES_BUILD is True


def test_seco_run_reports_failed_when_fetch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = _client(monkeypatch, [httpx.ConnectTimeout("simulated timeout")])
    result = SecoAdapter().run(client=client)
    assert result.status == "failed"
    assert len(calls) == DEFAULT_RETRIES


# --- parse failures are retried in the HTTP retry loop ------------------------


def test_run_refetches_after_truncated_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _read("seco_sample.xml")
    client, calls = _client(
        monkeypatch,
        [httpx.Response(200, content=good[: len(good) // 2]), httpx.Response(200, content=good)],
    )
    result = SecoAdapter().run(client=client)
    assert result.status == "success"
    assert result.address_count == 1
    assert len(calls) == 2


def test_run_fails_after_all_attempts_return_unparseable_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch, [httpx.Response(200, content=_read("seco_sample.xml")[:100])]
    )
    result = SecoAdapter().run(client=client)
    assert result.status == "failed"
    assert len(calls) == DEFAULT_RETRIES


def test_run_refetches_after_truncated_gzip_in_complete_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A complete HTTP response around a gzip stream that was cut short: httpx
    # decodes it without error, so only the parse failure reveals it.
    good = _read("seco_sample.xml")
    client, calls = _client(
        monkeypatch,
        [
            _gzip_response(gzip.compress(good)[:-200]),
            _gzip_response(gzip.compress(good)),
        ],
    )
    result = SecoAdapter().run(client=client)
    assert result.status == "success"
    assert len(calls) == 2


def test_run_refetches_after_undecodable_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = _client(
        monkeypatch,
        [
            _gzip_response(b"not gzip"),
            httpx.Response(200, content=_read("seco_sample.xml")),
        ],
    )
    result = SecoAdapter().run(client=client)
    assert result.status == "success"
    assert len(calls) == 2
