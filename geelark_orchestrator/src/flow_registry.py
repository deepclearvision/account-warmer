"""
flow_registry.py — Persistent mapping of phone_id → reusable flow IDs.

Since GeelarK has no delete-flow endpoint, we avoid accumulation by
updating existing flows in-place instead of creating new ones every run.
This caps total flows at 2 × phone_count.

On first run for a phone, a new flow is created and its ID is stored here.
On subsequent runs, the stored ID is passed to import_rpa_flow(flow_id=...),
which overwrites the flow content in-place (confirmed by API test).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("flow_registry")

# Default path inside the orchestrator repo
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "reusable_flows.json"


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_reusable_flow_ids(phone_id: str, registry_path: Optional[Path] = None) -> dict[str, str | None]:
    """Return {"gps_flow_id": ..., "maps_flow_id": ...} for a phone, or None if not yet registered."""
    data = _load(registry_path or _DEFAULT_PATH)
    entry = data.get(phone_id, {})
    return {
        "gps_flow_id": entry.get("gps_flow_id"),
        "maps_flow_id": entry.get("maps_flow_id"),
    }


def register_flow_id(
    phone_id: str,
    gps_flow_id: Optional[str] = None,
    maps_flow_id: Optional[str] = None,
    registry_path: Optional[Path] = None,
) -> None:
    """Store (or update) reusable flow IDs for a phone."""
    path = registry_path or _DEFAULT_PATH
    data = _load(path)
    if phone_id not in data:
        data[phone_id] = {}
    if gps_flow_id:
        data[phone_id]["gps_flow_id"] = gps_flow_id
    if maps_flow_id:
        data[phone_id]["maps_flow_id"] = maps_flow_id
    _save(path, data)
    log.info("Registered reusable flows for phone %s: gps=%s maps=%s", phone_id, gps_flow_id, maps_flow_id)
