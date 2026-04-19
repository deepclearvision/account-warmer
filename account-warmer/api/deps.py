"""
Shared dependencies and helpers for the dashboard API.
Path constants, YAML read/write with async locks, token status.
"""

import asyncio
import json
import time
from pathlib import Path

import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent))

from core.paths import (
    DATA_DIR, CONFIG_DIR, LOGS_DIR, STATE_DIR,
    ACCOUNTS_FILE, BUSINESSES_FILE, PROXIES_FILE,
    STRATEGIES_FILE, TOKEN_FILE, SYNC_CACHE_FILE,
)

BASE_DIR = DATA_DIR   # kept for any internal references

# ── Locks — prevent concurrent YAML corruption ────────────────────────────────

_accounts_lock   = asyncio.Lock()
_businesses_lock = asyncio.Lock()
_proxies_lock    = asyncio.Lock()

# ── YAML helpers ──────────────────────────────────────────────────────────────

def load_yaml_sync(path: Path) -> dict:
    """Synchronous YAML load. Safe to call from non-async helpers."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


async def load_accounts() -> list[dict]:
    async with _accounts_lock:
        data = load_yaml_sync(ACCOUNTS_FILE)
        return data.get("accounts", [])


async def save_accounts(accounts: list[dict]) -> None:
    async with _accounts_lock:
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            yaml.dump(
                {"accounts": accounts},
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )


async def load_proxies() -> list[dict]:
    async with _proxies_lock:
        data = load_yaml_sync(PROXIES_FILE)
        return data.get("proxies", [])


async def save_proxies(proxies: list[dict]) -> None:
    async with _proxies_lock:
        PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROXIES_FILE, "w", encoding="utf-8") as f:
            yaml.dump(
                {"proxies": proxies},
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )


async def load_businesses() -> list[dict]:
    async with _businesses_lock:
        data = load_yaml_sync(BUSINESSES_FILE)
        return data.get("businesses", [])


async def save_businesses(businesses: list[dict]) -> None:
    async with _businesses_lock:
        BUSINESSES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BUSINESSES_FILE, "w", encoding="utf-8") as f:
            yaml.dump(
                {"businesses": businesses},
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )


# ── Sync cache ────────────────────────────────────────────────────────────────

def load_sync_cache() -> dict:
    """
    Return the last ML sync result cache.
    Keys: synced_at, matched (list of profile IDs), new_in_ml (list of profile IDs),
          orphaned (list of account IDs).
    Returns empty dict if no sync has been run yet.
    """
    if not SYNC_CACHE_FILE.exists():
        return {}
    try:
        with open(SYNC_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_sync_cache(matched: list, new_in_ml: list, orphaned: list) -> None:
    from datetime import datetime
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(SYNC_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "synced_at":  datetime.now().isoformat(),
            "matched":    matched,
            "new_in_ml":  new_in_ml,
            "orphaned":   orphaned,
        }, f, indent=2)


# ── Token status ──────────────────────────────────────────────────────────────

def get_token_status() -> dict:
    """Read the cached ML token file and return validity info."""
    if not TOKEN_FILE.exists():
        return {"valid": False, "reason": "No token file found"}
    try:
        import base64 as _b64
        with open(TOKEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        fetched_at  = data.get("fetched_at", 0)
        age_seconds = time.time() - fetched_at
        token       = data.get("token", "")

        # Decode JWT exp field so we track the real expiry
        exp = 0.0
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            exp = float(json.loads(_b64.b64decode(payload_b64)).get("exp", 0))
        except Exception:
            pass

        if exp:
            remaining   = max(0.0, exp - time.time())
            valid       = remaining > 60  # at least 60 s left
            expires_in_hours = round(remaining / 3600, 2)
        else:
            # Fallback: 55-minute conservative window
            valid       = age_seconds < 55 * 60
            expires_in_hours = round(max(0.0, 55 * 60 - age_seconds) / 3600, 2)

        return {
            "valid":            valid,
            "fetched_at":       fetched_at,
            "age_hours":        round(age_seconds / 3600, 1),
            "expires_in_hours": expires_in_hours,
        }
    except Exception as e:
        return {"valid": False, "reason": str(e)}


def load_session_history(account_id: str) -> list:
    """Return the recorded session list for this account, newest first."""
    f = STATE_DIR / f"{account_id}_sessions.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text())
        return list(reversed(data))  # newest first
    except Exception:
        return []


def load_trust_checks() -> dict:
    """Return the trust check results dict keyed by account_id."""
    f = STATE_DIR / "trust_checks.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def save_trust_checks(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "trust_checks.json").write_text(json.dumps(data, indent=2))
