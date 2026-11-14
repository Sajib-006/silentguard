"""A small client for the filterable service of each domain (catalog for bio, orders for retail)."""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

import requests

KEYS = {"bio": ("catalog", "bench-catalog-key"), "retail": ("orders", "bench-orders-key")}


def _filters(conditions: Iterable[Any]) -> list[dict]:
    out = []
    for c in conditions:
        if isinstance(c, dict):
            out.append({"path": c.get("field", c.get("path")), "value": c["value"],
                        "negate": bool(c.get("negate", False))})
        else:                                     # silentguard.Condition
            out.append({"path": c.field, "value": c.value, "negate": bool(c.negate)})
    return out


class CatalogClient:
    """Count or list records that match conditions, and switch silent faults on or off."""

    def __init__(self, services, timeout: float = 15.0):
        self.domain = services.domain
        name, self.key = KEYS[self.domain]
        self.base = services.url(name)
        self.timeout = timeout

    def set_fault(self, fault: str = "off", catalog: Optional[str] = "large") -> dict:
        """Switch the silent fault (see ``agentorchbench.FAULTS``) and the seed data."""
        body = {"mode": fault}
        if catalog:
            body["catalog"] = catalog
        r = requests.post(f"{self.base}/set_sfr_mode", json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def search(self, conditions: Iterable[Any], limit: int = 1000) -> Optional[dict]:
        try:
            r = requests.post(f"{self.base}/search", timeout=self.timeout,
                              headers={"X-Api-Key": self.key, "Content-Type": "application/json"},
                              data=json.dumps({"json_metadata_filters": _filters(conditions),
                                               "limit": limit}))
            return r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None

    def count(self, conditions: Iterable[Any]) -> Optional[int]:
        """Number of matching records, as the API reports it. ``None`` if the request failed."""
        j = self.search(conditions, limit=50)
        return None if j is None else j.get("total_count")
