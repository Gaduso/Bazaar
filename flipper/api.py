"""Thin, cached client for the public Hypixel Bazaar API.

The endpoint requires no authentication and returns the full snapshot of every
bazaar product in one ~1 MB JSON response. We cache the response in-memory for
a configurable TTL so the web UI can refresh aggressively without hammering
Hypixel.
"""

from __future__ import annotations

import time
from typing import Any

import requests


BAZAAR_API_URL = "https://api.hypixel.net/v2/skyblock/bazaar"
ITEMS_API_URL = "https://api.hypixel.net/v2/resources/skyblock/items"


class ItemsAPI:
    """Cached client for the Hypixel items resource.

    We only care about the fixed ``npc_sell_price`` per item, which does not
    change between game updates, so this is cached for a long TTL (default 1h).
    Not every item has an ``npc_sell_price`` — those are simply omitted from
    the returned map.
    """

    def __init__(
        self,
        url: str = ITEMS_API_URL,
        cache_ttl_seconds: int = 3600,
        timeout: int = 15,
    ) -> None:
        self.url = url
        self.cache_ttl = cache_ttl_seconds
        self.timeout = timeout
        self._cache: dict[str, float] | None = None
        self._cache_time: float = 0.0

    def npc_sell_prices(self, force: bool = False) -> dict[str, float]:
        """Return a ``{item_id: npc_sell_price}`` map, using the cache when fresh."""
        now = time.time()
        if (
            not force
            and self._cache is not None
            and (now - self._cache_time) < self.cache_ttl
        ):
            return self._cache

        response = requests.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise RuntimeError("Hypixel items API returned success=false")

        prices: dict[str, float] = {}
        for item in data.get("items", []) or []:
            item_id = item.get("id")
            price = item.get("npc_sell_price")
            if item_id is not None and price is not None:
                prices[item_id] = float(price)

        self._cache = prices
        self._cache_time = now
        return prices


class BazaarAPI:
    """Cached client for the Hypixel Bazaar endpoint."""

    def __init__(
        self,
        url: str = BAZAAR_API_URL,
        cache_ttl_seconds: int = 30,
        timeout: int = 10,
    ) -> None:
        self.url = url
        self.cache_ttl = cache_ttl_seconds
        self.timeout = timeout
        self._cache: dict[str, Any] | None = None
        self._cache_time: float = 0.0

    def fetch(self, force: bool = False) -> dict[str, Any]:
        """Return the latest bazaar payload, using the in-memory cache when fresh.

        Raises:
            requests.HTTPError: on non-2xx responses
            RuntimeError: if the API responds with success=false
        """
        now = time.time()
        if (
            not force
            and self._cache is not None
            and (now - self._cache_time) < self.cache_ttl
        ):
            return self._cache

        response = requests.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise RuntimeError("Hypixel Bazaar API returned success=false")

        self._cache = data
        self._cache_time = now
        return data

    def cache_age_seconds(self) -> float | None:
        if self._cache is None:
            return None
        return time.time() - self._cache_time
