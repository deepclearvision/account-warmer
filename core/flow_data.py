"""
Geelark Flow Data loader

Provides easy access to the master flow data sheet for Geelark automation.
"""

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

from core.paths import DATA_DIR

FLOW_DATA_FILE = DATA_DIR / "geelark_flow_data.yaml"


def load_flow_data() -> list[dict[str, Any]]:
    """Load all flow rows from the YAML sheet."""
    if not FLOW_DATA_FILE.exists():
        return []
    if yaml is None:
        raise RuntimeError("PyYAML is required to load flow data")
    data = yaml.safe_load(FLOW_DATA_FILE.read_text(encoding="utf-8")) or {}
    return data.get("flows", [])


def get_flow_for_account(account_id: str) -> dict[str, Any] | None:
    """Return the flow row for a specific account, or None."""
    for row in load_flow_data():
        if row.get("account_id") == account_id:
            return row
    return None


def get_flow_for_phone(phone_id: str) -> dict[str, Any] | None:
    """Return the flow row for a specific Geelark phone ID, or None."""
    for row in load_flow_data():
        if str(row.get("geelark_phone_id", "")) == str(phone_id):
            return row
    return None


def get_business_coords(account_id: str) -> tuple[float, float] | None:
    """Return (lat, lng) for the account's associated business."""
    row = get_flow_for_account(account_id)
    if not row:
        return None
    lat = row.get("business_lat")
    lng = row.get("business_lng")
    if lat is not None and lng is not None:
        return float(lat), float(lng)
    return None


def get_home_coords(account_id: str) -> tuple[float, float] | None:
    row = get_flow_for_account(account_id)
    if not row:
        return None
    lat = row.get("home_lat")
    lng = row.get("home_lng")
    if lat is not None and lng is not None:
        return float(lat), float(lng)
    return None


def get_work_coords(account_id: str) -> tuple[float, float] | None:
    row = get_flow_for_account(account_id)
    if not row:
        return None
    lat = row.get("work_lat")
    lng = row.get("work_lng")
    if lat is not None and lng is not None:
        return float(lat), float(lng)
    return None


def get_nearby_points(account_id: str) -> list[dict]:
    """Return the ~20 nearby lat/lng points for the account's business."""
    row = get_flow_for_account(account_id)
    if not row:
        return []
    return row.get("nearby_points", [])


def get_onsite_points(account_id: str) -> list[dict]:
    """Return the ~10 on-site lat/lng points for the account's business."""
    row = get_flow_for_account(account_id)
    if not row:
        return []
    return row.get("onsite_points", [])


def get_search_terms(account_id: str) -> list[str]:
    """Return localized search terms for the account's business area."""
    row = get_flow_for_account(account_id)
    if not row:
        return []
    return row.get("search_terms", [])


def get_branded_searches(account_id: str) -> list[str]:
    """Return ~20 branded search queries for the account's business."""
    row = get_flow_for_account(account_id)
    if not row:
        return []
    return row.get("branded_searches", [])


def get_local_searches(account_id: str) -> list[str]:
    """Return ~29 local keyword searches for the account's business area."""
    row = get_flow_for_account(account_id)
    if not row:
        return []
    return row.get("local_searches", [])


def get_login_details(account_id: str) -> dict[str, str] | None:
    """Return email, password, totp for the account."""
    row = get_flow_for_account(account_id)
    if not row:
        return None
    return {
        "email": row.get("account_email", ""),
        "password": row.get("account_password", ""),
        "totp_secret": row.get("account_totp", ""),
    }


def get_all_account_ids() -> list[str]:
    """Return all account IDs in the flow sheet."""
    return [row["account_id"] for row in load_flow_data() if row.get("account_id")]
