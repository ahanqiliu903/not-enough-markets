"""Shared HTTP for feeds. Tests swap `TRANSPORT` for an httpx.MockTransport (and `SLEEP` /
`MONOTONIC` to skip the waits)."""

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

USER_AGENT = "not-enough-markets (https://github.com/ahanqiliu903/not-enough-markets)"
# Contact for the User-Agent (e.g. "nem paper trading (you@example.com)"), from the
# environment so it never lands in a tracked file. Feeds fall back to USER_AGENT.
USER_AGENT_ENV = "NEM_USER_AGENT"
TRANSPORT: httpx.BaseTransport | None = None
SLEEP: Callable[[float], None] = time.sleep
MONOTONIC: Callable[[], float] = time.monotonic

# Minimum seconds between requests to one host, shared by every feed in the process
# (IEM answers HTTP 429 above about one request a second per IP).
HOST_PAUSE: dict[str, float] = {"mesonet.agron.iastate.edu": 2.0, "open-meteo.com": 0.2}
_last_request: dict[str, float] = {}


class FeedHTTPError(RuntimeError):
    pass


def user_agent(configured: str | None) -> str:
    return configured or os.environ.get(USER_AGENT_ENV) or USER_AGENT


def get_json(url: str, user_agent: str = USER_AGENT, timeout: float = 10.0) -> Any:
    with httpx.Client(transport=TRANSPORT, timeout=timeout) as client:
        r = client.get(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        r.raise_for_status()
        return r.json()


def _throttle(host: str) -> None:
    pause = next((p for h, p in HOST_PAUSE.items() if host.endswith(h)), 0.0)
    last = _last_request.get(host)
    if last is not None:
        wait = pause - (MONOTONIC() - last)
        if wait > 0:
            SLEEP(wait)
    _last_request[host] = MONOTONIC()


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return min(float(r.headers.get("Retry-After", "")), 60.0)
    except ValueError:
        return None


def get(
    url: str,
    params: Mapping[str, str],
    user_agent: str,
    *,
    retries: int = 2,
    backoff: float = 2.0,
    timeout: float = 30.0,
) -> httpx.Response:
    """GET with a per-host pause, and retries with exponential backoff (or the server's
    Retry-After, capped at 60 s) on network errors, 429 and 5xx. Other statuses are
    returned for the caller to judge. Few retries on purpose: feeds run inside the trading
    loop, and a feed that fails is simply fetched again on its next `every`."""
    host = httpx.URL(url).host
    problem = ""
    with httpx.Client(transport=TRANSPORT, timeout=timeout) as client:
        for attempt in range(retries + 1):
            if attempt:
                SLEEP(backoff * 2 ** (attempt - 1))
            _throttle(host)
            try:
                r = client.get(url, params=dict(params), headers={"User-Agent": user_agent})
            except httpx.TransportError as e:
                problem = repr(e)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                problem = f"HTTP {r.status_code}"
                wait = _retry_after(r)
                if wait is not None and attempt < retries:
                    SLEEP(wait)
                continue
            return r
    raise FeedHTTPError(f"GET {url}: gave up after {retries + 1} tries ({problem})")
