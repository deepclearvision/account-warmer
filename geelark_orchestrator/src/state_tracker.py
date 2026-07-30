"""
state_tracker.py — Visited business tracker for warm-up flows.

Tracks which businesses have been visited during warm-up runs so the same
nearby listing isn't "discovered" repeatedly.  JSON-backed, per-account.

Usage:
    tracker = BusinessTracker(profile_key="sylviazimmer921", ttl_days=7)
    if tracker.is_visited("The Crown"):
        ...skip...
    tracker.record_visit("The Red Lion")
    tracker.save()
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

# Store alongside other orchestrator state
_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "logs" / "visited_businesses"


class BusinessTracker:
    """
    Track visited business names per account with TTL expiry.
    """

    def __init__(self, profile_key: str, ttl_days: int = 7,
                 storage_dir: Optional[Path] = None):
        self.profile_key = profile_key
        self.ttl_days = ttl_days
        self.storage_dir = storage_dir or _DEFAULT_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.storage_dir / f"{profile_key}.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not self._state_path.exists():
            return {"visited": {}, "version": 1}
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"visited": {}, "version": 1}
            # Clean expired entries on load
            return self._clean_expired(data)
        except (json.JSONDecodeError, OSError):
            return {"visited": {}, "version": 1}

    def _clean_expired(self, data: dict) -> dict:
        cutoff = time.time() - (self.ttl_days * 86400)
        visited = data.get("visited", {})
        fresh = {name: ts for name, ts in visited.items() if ts > cutoff}
        return {"visited": fresh, "version": data.get("version", 1)}

    def is_visited(self, business_name: str) -> bool:
        """Return True if this business was visited within TTL."""
        visited = self._data.get("visited", {})
        ts = visited.get(business_name)
        if ts is None:
            return False
        cutoff = time.time() - (self.ttl_days * 86400)
        return ts > cutoff

    def record_visit(self, business_name: str) -> None:
        """Record a visit timestamp for the business."""
        self._data.setdefault("visited", {})[business_name] = time.time()

    def save(self) -> None:
        """Persist state to disk."""
        self._data = self._clean_expired(self._data)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def list_visited(self) -> list[str]:
        """Return list of currently tracked (non-expired) business names."""
        self._data = self._clean_expired(self._data)
        return list(self._data.get("visited", {}).keys())

    def clear(self) -> None:
        """Clear all tracked visits for this account."""
        self._data = {"visited": {}, "version": 1}
        self.save()
