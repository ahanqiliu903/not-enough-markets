"""Shared HTTP for feeds. Tests swap `TRANSPORT` for an httpx.MockTransport."""

from typing import Any

import httpx

USER_AGENT = "not-enough-markets (https://github.com/ahanqiliu903/not-enough-markets)"
TRANSPORT: httpx.BaseTransport | None = None


def get_json(url: str, user_agent: str = USER_AGENT, timeout: float = 10.0) -> Any:
    with httpx.Client(transport=TRANSPORT, timeout=timeout) as client:
        r = client.get(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        r.raise_for_status()
        return r.json()
