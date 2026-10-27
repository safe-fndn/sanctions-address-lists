"""Shared HTTP client configuration: explicit timeouts and bounded retries.

Validation of downloaded payloads happens in each adapter's ``parse()`` method
against the expected structure, not here -- official endpoints sometimes serve
generic content types (e.g. ``application/octet-stream``) for XML/XLSX
payloads, so gating on ``Content-Type`` would produce false failures.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 5.0
_USER_AGENT = "sanctions-address-lists/0.1 (+https://github.com/safe-fndn/sanctions-address-lists)"


def build_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """Construct an httpx client with an explicit timeout and redirect handling."""
    return httpx.Client(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    )


def get_with_retries[T](
    client: httpx.Client,
    url: str,
    *,
    parse: Callable[[bytes], T],
    retry_parse_errors: tuple[type[Exception], ...] = (),
    retries: int = DEFAULT_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[httpx.Response, T]:
    """GET and ``parse`` ``url``, retrying transient failures with linear backoff.

    Retries transport errors, undecodable bodies, 5xx responses, and payloads
    whose ``parse`` raises one of ``retry_parse_errors``. The last case covers a
    truncated download that HTTP can't detect: a chunked response terminated
    cleanly around a gzip stream that was cut short (httpx doesn't check that
    the gzip stream is complete), which only surfaces as a parse failure.

    Returns ``(response, parsed)``. Raises the last encountered exception (or
    ``httpx.HTTPStatusError``) if all attempts fail. Does not retry 4xx
    responses -- those are not transient.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = client.get(url)
        except (httpx.TransportError, httpx.DecodingError) as exc:
            last_exc = exc
            logger.warning("GET %s attempt %d/%d failed: %s", url, attempt, retries, exc)
        else:
            if response.status_code < 500:
                response.raise_for_status()
                if not response.content:
                    raise ValueError(f"GET {url} returned an empty body")
                try:
                    return response, parse(response.content)
                except retry_parse_errors as exc:
                    last_exc = exc
                    logger.warning(
                        "GET %s attempt %d/%d returned an unparseable payload: %s",
                        url,
                        attempt,
                        retries,
                        exc,
                    )
            else:
                last_exc = httpx.HTTPStatusError(
                    f"server error {response.status_code}",
                    request=response.request,
                    response=response,
                )
                logger.warning(
                    "GET %s attempt %d/%d returned %d", url, attempt, retries, response.status_code
                )
        if attempt < retries:
            sleep(_RETRY_BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    raise last_exc
