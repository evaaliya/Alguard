"""Narrow data access for the Tier 2 (human-approval) service.

Tier 2 must not touch the actions ledger directly. It talks to the internal ledger
API (web_api/ledger_api.py) over HTTP — read-only pending/receipts plus the single
atomic resolve. Removing the filesystem coupling means a compromised Tier 2 process
cannot open or mutate the ledger file at all.

Resilience: every call is retried with exponential backoff on transport errors and 5xx
(the atomic resolve makes retries idempotent). An optional shared secret
(ALGUARD_LEDGER_API_TOKEN) authenticates Tier 2 to the ledger API so a third party on
the loopback interface cannot call the ledger directly.
"""
import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_LEDGER_API_URL = "http://127.0.0.1:8015"
_MAX_ATTEMPTS = 3
_BASE_BACKOFF = 0.25


class Tier2Store:
    """HTTP client for the internal ledger API (pending / receipt / resolve)."""

    def __init__(self, base_url: Optional[str] = None, transport: Optional[Any] = None):
        self.base_url = (base_url or os.environ.get("ALGUARD_LEDGER_API_URL") or DEFAULT_LEDGER_API_URL).rstrip("/")
        self._transport = transport
        self._token = os.environ.get("ALGUARD_LEDGER_API_TOKEN")

    def _headers(self) -> Dict[str, str]:
        return {"X-Ledger-Token": self._token} if self._token else {}

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport, timeout=10.0) as client:
                    r = await client.request(method, path, headers=self._headers(), **kwargs)
                if r.status_code < 500 or attempt == _MAX_ATTEMPTS - 1:
                    return r
                last_exc = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            except httpx.TransportError as e:
                last_exc = e
            await asyncio.sleep(_BASE_BACKOFF * (2 ** attempt))
        raise last_exc  # type: ignore[misc]

    async def pending(self, owner: str) -> List[Dict[str, Any]]:
        r = await self._request("GET", "/pending", params={"owner": owner})
        r.raise_for_status()
        return r.json()["items"]

    async def receipt(self, action_id: str, owner: str) -> Optional[Dict[str, Any]]:
        r = await self._request("GET", f"/receipts/{action_id}", params={"owner": owner})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def resolve(self, action_id: str, approved: bool, owner: str) -> Dict[str, Any]:
        r = await self._request("POST", f"/resolve/{action_id}", params={"approved": approved, "owner": owner})
        r.raise_for_status()
        return r.json()

    async def list_trusted(self, owner: str) -> List[str]:
        r = await self._request("GET", "/trusted-merchants", params={"owner": owner})
        r.raise_for_status()
        return r.json()["merchants"]

    async def add_trusted(self, owner: str, merchant: str) -> Dict[str, Any]:
        r = await self._request("POST", "/trusted-merchants", params={"owner": owner}, json={"merchant": merchant})
        r.raise_for_status()
        return r.json()

    async def remove_trusted(self, owner: str, merchant: str) -> Dict[str, Any]:
        r = await self._request("DELETE", f"/trusted-merchants/{merchant}", params={"owner": owner})
        r.raise_for_status()
        return r.json()

    async def list_banned(self, owner: str) -> List[str]:
        r = await self._request("GET", "/banned-merchants", params={"owner": owner})
        r.raise_for_status()
        return r.json()["merchants"]

    async def add_banned(self, owner: str, merchant: str) -> Dict[str, Any]:
        r = await self._request("POST", "/banned-merchants", params={"owner": owner}, json={"merchant": merchant})
        r.raise_for_status()
        return r.json()

    async def remove_banned(self, owner: str, merchant: str) -> Dict[str, Any]:
        r = await self._request("DELETE", f"/banned-merchants/{merchant}", params={"owner": owner})
        r.raise_for_status()
        return r.json()


