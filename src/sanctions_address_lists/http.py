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


def get_with_retries(
    client: httpx.Client,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """GET ``url``, retrying transport errors and 5xx responses with linear backoff.

    Raises the last encountered exception (or ``httpx.HTTPStatusError``) if all
    attempts fail. Does not retry 4xx responses -- those are not transient.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            last_exc = exc
            logger.warning("GET %s attempt %d/%d failed: %s", url, attempt, retries, exc)
        else:
            if response.status_code < 500:
                response.raise_for_status()
                if not response.content:
                    raise ValueError(f"GET {url} returned an empty body")
                return response
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
