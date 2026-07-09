"""
Account Store — Single Data Access Layer
========================================
All account data flows through this module.  No other code reads or writes
accounts.yaml / geelark_accounts.yaml directly.

Design
------
- **Read-time merge**:  data from accounts.yaml, geelark_accounts.yaml, and
  geelark_flow_data.yaml is combined in memory.  Mobile / flow_data values
  take precedence for shared fields.
- **Write-time isolation**:  writes target exactly one file.  Desktop code
  never touches the GeelarK files; mobile code never touches accounts.yaml.
- **Ephemeral merge**:  the merged view is never persisted — each file keeps
  its own independent state.

Field ownership (see CLAUDE.md § Architecture for rationale)
------------------------------------------------------------
Desktop-only   — accounts.yaml only
Mobile-only    — geelark_accounts.yaml only
Shared         — both files; mobile authoritative on merge
"""

import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from core.paths import (
    DATA_DIR, ACCOUNTS_FILE, GEELARK_ACCOUNTS_FILE,
    GEELARK_FLOW_DATA_FILE, LOGS_DIR,
)

log = logging.getLogger("account_store")

# ── Field classification ────────────────────────────────────────────────────────

_DESKTOP_ONLY_FIELDS = frozenset({
    "id",                          # acc_xxx (identity, but stored per-file)
    "multilogin_profile_id",
    "multilogin_folder_id",
    "proxy",
    "rotate_proxy",
    "timezone",
    "location",
    "language",
    "warmup_start_date",
    "active_hours",
    "goal",
    "profile_photo",
    "ml_setup_done",
    "tags",
})

# Fields stored in geelark_accounts.yaml (mobile platform + unified login status).
# Desktop login status lives here too so both sides' login state is in one place.
_MOBILE_ONLY_FIELDS = frozenset({
    "id",                          # gl_xxx / acc_xxx (identity, but stored per-file)
    "geelark_phone_id",
    "mobile_warming_enabled",
    "mobile_setup_done",
    "login_verified",              # mobile login
    "login_checked_at",            # mobile login
    "login_status",                # mobile login (legacy)
    "login_done",                  # mobile login (legacy)
    "login_issue",                 # mobile login
    "desktop_login_status",        # desktop login — "logged_in" | "login_failed" | null
    "desktop_login_checked_at",    # desktop login — ISO timestamp
    "apps_installed",
    "device_brand",
    "device_model",
    "os_version",
    "geelark_login_flow_id",
})

# Fields that live in both files — mobile / flow_data takes precedence on merge.
_SHARED_FIELDS = frozenset({
    "email",
    "password",
    "totp_secret",
    "geo_city",
    "geo_area",
    "strategy",
    "category",
    "target_businesses",
    "home_address",
    "home_lat",
    "home_lng",
    "work_address",
    "work_lat",
    "work_lng",
    "work_geo_area",
    "neighbourhood",
})


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    """Return parsed YAML or empty dict if file missing / corrupt."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        log.warning("Could not parse %s — treating as empty", path)
        return {}


def _save_yaml(path: Path, data: dict) -> None:
    """Atomically write a YAML dict to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True,
                  sort_keys=False),
        encoding="utf-8",
    )


def _has_value(val) -> bool:
    """Return True for non-None, non-empty values."""
    if val is None:
        return False
    if isinstance(val, str) and val == "":
        return False
    if isinstance(val, (list, dict)) and len(val) == 0:
        return False
    return True


# ── AccountStore ────────────────────────────────────────────────────────────────

class AccountStore:
    """Single access point for all account data."""

    def __init__(self):
        self._lock = threading.Lock()

    # ── Read: desktop ───────────────────────────────────────────────────────

    def get_desktop_accounts(self) -> list[dict]:
        """Return raw desktop account list from accounts.yaml."""
        data = _load_yaml(ACCOUNTS_FILE)
        return data.get("accounts", [])

    # ── Read: mobile ────────────────────────────────────────────────────────

    def get_mobile_accounts(self) -> list[dict]:
        """Return raw mobile account list from geelark_accounts.yaml."""
        data = _load_yaml(GEELARK_ACCOUNTS_FILE)
        return data.get("accounts", [])

    # ── Read: flow data ─────────────────────────────────────────────────────

    def get_flow_data(self) -> dict[str, dict]:
        """
        Return geelark_flow_data.yaml keyed by account_id.

        Each value is the flow entry dict with keys like:
        account_id, business_id, business_name, business_lat, business_lng,
        home_lat, home_lng, home_address, work_lat, work_lng, work_address, …
        """
        data = _load_yaml(GEELARK_FLOW_DATA_FILE)
        flows = data.get("flows", [])
        return {f.get("account_id", ""): f for f in flows if f.get("account_id")}

    # ── Read: merged ────────────────────────────────────────────────────────

    def get_all_accounts(self) -> list[dict]:
        """
        Return a merged list of every unique account.

        Each dict contains all desktop + mobile + flow_data fields, with
        mobile / flow_data taking precedence for shared fields.  Accounts
        that exist in only one file are included as-is.
        """
        desktop_list = self.get_desktop_accounts()
        mobile_list  = self.get_mobile_accounts()
        flow_map     = self.get_flow_data()

        # Build lookup by email (case-insensitive)
        by_email: dict[str, dict] = {}   # email → {"d": ..., "m": ...}

        for d in desktop_list:
            email = (d.get("email") or "").lower()
            if email:
                by_email.setdefault(email, {})["d"] = d

        for m in mobile_list:
            email = (m.get("email") or "").lower()
            if email:
                by_email.setdefault(email, {})["m"] = m

        result = []
        for email, pair in by_email.items():
            merged = self._merge_account(
                desktop = pair.get("d"),
                mobile  = pair.get("m"),
                flow    = flow_map.get(pair.get("d", {}).get("id", ""))
                       or flow_map.get(pair.get("m", {}).get("id", "")),
            )
            result.append(merged)

        return result

    def _merge_account(self, desktop: dict | None, mobile: dict | None,
                       flow: dict | None) -> dict:
        """Combine desktop + mobile + flow_data into one dict.

        Precedence (highest to lowest):
          1. flow_data (addresses, business coords)
          2. mobile    (shared fields like geo_area, strategy, password)
          3. desktop   (platform fields like proxy, profile_id)
        """
        merged: dict = {}

        # 1. Start with desktop fields
        if desktop:
            for k, v in desktop.items():
                if _has_value(v):
                    merged[k] = v

        # 2. Overlay mobile fields (shared + mobile-only)
        if mobile:
            for k, v in mobile.items():
                if k in _MOBILE_ONLY_FIELDS or k in _SHARED_FIELDS:
                    if _has_value(v):
                        merged[k] = v
                elif k not in merged:
                    # Catch-all: any field not already set
                    if _has_value(v):
                        merged[k] = v

        # 3. Overlay flow_data fields (addresses + business)
        if flow:
            flow_shared = {
                "business_id":        flow.get("business_id"),
                "business_name":      flow.get("business_name"),
                "business_lat":       flow.get("business_lat"),
                "business_lng":       flow.get("business_lng"),
                "home_address":       flow.get("home_address"),
                "home_lat":           flow.get("home_lat"),
                "home_lng":           flow.get("home_lng"),
                "work_address":       flow.get("work_address"),
                "work_lat":           flow.get("work_lat"),
                "work_lng":           flow.get("work_lng"),
            }
            for k, v in flow_shared.items():
                if _has_value(v):
                    merged[k] = v

            # target_businesses: if flow_data has business_id, use it
            biz_id = flow.get("business_id")
            if biz_id and not merged.get("target_businesses"):
                merged["target_businesses"] = [biz_id]

        return merged

    # ── Write: desktop ──────────────────────────────────────────────────────

    def save_desktop_accounts(self, accounts: list[dict]) -> None:
        """Persist the full account list to accounts.yaml."""
        with self._lock:
            _save_yaml(ACCOUNTS_FILE, {"accounts": accounts})

    # ── Write: mobile ───────────────────────────────────────────────────────

    def save_mobile_accounts(self, accounts: list[dict]) -> None:
        """Persist the full account list to geelark_accounts.yaml."""
        with self._lock:
            _save_yaml(GEELARK_ACCOUNTS_FILE, {"accounts": accounts})

    # ── Cross-reference ─────────────────────────────────────────────────────

    def find_desktop_by_email(self, email: str) -> dict | None:
        """Return the desktop account with the given email, or None."""
        email_lower = email.lower()
        for a in self.get_desktop_accounts():
            if (a.get("email") or "").lower() == email_lower:
                return a
        return None

    def find_mobile_by_email(self, email: str) -> dict | None:
        """Return the mobile account with the given email, or None."""
        email_lower = email.lower()
        for a in self.get_mobile_accounts():
            if (a.get("email") or "").lower() == email_lower:
                return a
        return None

    # ── Deletion cleanup ────────────────────────────────────────────────────

    def remove_flow_data(self, ids) -> int:
        """
        Remove geelark_flow_data.yaml entries whose account_id is in `ids`.
        Returns the number of flow entries removed. No-op if the file is missing.
        """
        id_set = {i for i in ids if i}
        if not id_set:
            return 0
        with self._lock:
            data  = _load_yaml(GEELARK_FLOW_DATA_FILE)
            flows = data.get("flows", [])
            if not flows:
                return 0
            kept    = [f for f in flows if f.get("account_id") not in id_set]
            removed = len(flows) - len(kept)
            if removed:
                data["flows"] = kept
                _save_yaml(GEELARK_FLOW_DATA_FILE, data)
            return removed

    def remove_mobile_sessions(self, ids) -> int:
        """
        Remove mobile_sessions.json records whose account_id is in `ids`.
        Returns the number of session records removed. No-op if missing/corrupt.
        """
        id_set = {i for i in ids if i}
        if not id_set:
            return 0
        sessions_file = LOGS_DIR / "mobile_sessions.json"
        if not sessions_file.exists():
            return 0
        with self._lock:
            try:
                records = json.loads(sessions_file.read_text(encoding="utf-8"))
            except Exception:
                return 0
            if not isinstance(records, list):
                return 0
            kept    = [r for r in records if r.get("account_id") not in id_set]
            removed = len(records) - len(kept)
            if removed:
                sessions_file.write_text(json.dumps(kept, indent=2), encoding="utf-8")
            return removed

    # ── Mobile coordination ─────────────────────────────────────────────────

    # Activity name mapping: mobile step → desktop activity name
    _MOBILE_TO_DESKTOP = {
        "maps":             "maps_browse",
        "maps_directions":  "maps_browse",
        "google_search":    "search",
        "gmail":            "email_read",
        "youtube":          "browse",
    }

    def get_mobile_activities_done_today(self, email: str) -> set[str]:
        """
        Return desktop activity names already performed today on the paired
        GeelarK phone for the account with the given email.

        Encapsulates all the file I/O that was previously in
        orchestrator._get_mobile_done_today().
        """
        if not email:
            return set()

        # Find the matching GeelarK account
        mobile = self.find_mobile_by_email(email)
        if not mobile:
            return set()

        gl_id = mobile.get("id", "")

        # Read mobile_sessions.json
        mobile_log = LOGS_DIR / "mobile_sessions.json"
        if not mobile_log.exists():
            return set()

        try:
            with open(mobile_log, encoding="utf-8") as f:
                sessions: list[dict] = json.load(f)
        except Exception:
            return set()

        today_str = str(date.today())
        done: set[str] = set()
        for session in sessions:
            if session.get("account_id") != gl_id:
                continue
            ts = session.get("timestamp", "")
            if not ts.startswith(today_str):
                continue
            for step in session.get("steps_done", []):
                desktop_name = self._MOBILE_TO_DESKTOP.get(step)
                if desktop_name:
                    done.add(desktop_name)

        return done


# ── Singleton ───────────────────────────────────────────────────────────────────

_store: AccountStore | None = None


def get_account_store() -> AccountStore:
    """Return the process-wide singleton AccountStore."""
    global _store
    if _store is None:
        _store = AccountStore()
    return _store
