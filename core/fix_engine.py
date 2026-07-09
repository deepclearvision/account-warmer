"""
Fix Engine

Applies approved fixes to accounts.yaml, state files, and the Multilogin API.
Each fix_action type maps to a specific handler below.

Called by POST /api/fixes/{id}/apply
"""

import json
import yaml
from pathlib import Path

from core.paths import ACCOUNTS_FILE, STATE_DIR, TOKEN_FILE


def apply_fix(fix: dict) -> tuple[bool, str]:
    """
    Apply a fix and return (success, message).
    fix["fix_action"] must have a "type" key.
    """
    action = fix.get("fix_action") or {}
    atype  = action.get("type")

    handlers = {
        "clear_proxy":         _clear_proxy,
        "stop_profile":        _stop_profile,
        "force_token_refresh": _force_token_refresh,
        "flag_relogin":        _flag_relogin,
        "pause_account":       _pause_account,
    }

    handler = handlers.get(atype)
    if not handler:
        return False, f"Unknown fix action type: {atype!r}"

    try:
        return handler(action)
    except Exception as e:
        return False, f"Fix failed with exception: {e}"


# ── Handlers ──────────────────────────────────────────────────────────────────

def _load_accounts() -> list:
    if not ACCOUNTS_FILE.exists():
        return []
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("accounts", [])


def _save_accounts(accounts: list) -> None:
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(
            {"accounts": accounts},
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def _clear_proxy(action: dict) -> tuple[bool, str]:
    """Remove the proxy from an account so it's flagged for reassignment."""
    acc_id   = action["account_id"]
    accounts = _load_accounts()
    target   = next((a for a in accounts if a["id"] == acc_id), None)
    if not target:
        return False, f"Account {acc_id!r} not found"
    old_proxy = target.get("proxy", "")
    target["proxy"] = ""
    _save_accounts(accounts)
    return True, f"Proxy cleared for {acc_id} (was: {old_proxy[:40] if old_proxy else 'empty'})"


def _stop_profile(action: dict) -> tuple[bool, str]:
    """Force-stop the Multilogin profile via the local API."""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    profile_id = action.get("profile_id", "")
    if not profile_id:
        return False, "No profile_id in fix action"

    from core.multilogin_auth import LOCAL_API, auth_headers
    url = f"{LOCAL_API}/api/v1/profile/stop/p/{profile_id}"
    try:
        resp = requests.get(url, headers=auth_headers(), timeout=15, verify=False)
        if resp.status_code in (200, 404):
            return True, f"Profile {profile_id[:8]}… stopped (or was already stopped)"
        return False, f"Multilogin API returned HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        return False, f"Could not reach Multilogin API: {e}"


def _force_token_refresh(action: dict) -> tuple[bool, str]:
    """Delete the cached ML token so it is fully re-fetched on the next run."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        return True, "Cached Multilogin token deleted — will refresh on next run"
    return True, "Token file did not exist (already clean)"


def _flag_relogin(action: dict) -> tuple[bool, str]:
    """Pause warming and flag the account as requiring manual re-login."""
    acc_id   = action["account_id"]
    accounts = _load_accounts()
    target   = next((a for a in accounts if a["id"] == acc_id), None)
    if not target:
        return False, f"Account {acc_id!r} not found"
    target["needs_relogin"] = True
    target["paused"]        = True
    _save_accounts(accounts)
    return True, f"{acc_id} flagged as needs_relogin and paused"


def _pause_account(action: dict) -> tuple[bool, str]:
    """Set paused: true on an account so the scheduler skips it."""
    acc_id   = action["account_id"]
    accounts = _load_accounts()
    target   = next((a for a in accounts if a["id"] == acc_id), None)
    if not target:
        return False, f"Account {acc_id!r} not found"
    target["paused"] = True
    _save_accounts(accounts)
    return True, f"{acc_id} paused"
