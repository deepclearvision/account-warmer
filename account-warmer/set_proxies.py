"""
set_proxies.py
==============
Reads proxy settings from config/accounts.yaml and pushes them to the
matching Multilogin profile via the Multilogin cloud API.

Useful when you:
  - Switch proxy providers and want to update all profiles at once
  - Add new accounts and want to apply proxies without touching the ML UI
  - Want to verify what proxy each profile currently has set

Commands:
    python set_proxies.py --all                  Push proxy to all profiles
    python set_proxies.py --account acc_001      Push proxy to one profile
    python set_proxies.py --check                Show current proxy config (no changes)
    python set_proxies.py --all --dry-run        Preview what would be sent

Proxy format in accounts.yaml (standard URL format):
    socks5://username:password@host:port
    http://username:password@host:port

Requires config/multilogin.yaml to be set up with your ML credentials.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

from core.multilogin_auth import auth_headers, CLOUD_API


# ── Paths ──────────────────────────────────────────────────────────────────────
from core.paths import ACCOUNTS_FILE


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_accounts() -> list:
    if not ACCOUNTS_FILE.exists():
        print(f"  [ERROR] accounts.yaml not found at {ACCOUNTS_FILE}")
        sys.exit(1)
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("accounts", [])


def parse_proxy(proxy_url: str) -> dict | None:
    """
    Parse a proxy URL into the dict Multilogin's API expects.

    Input:  "socks5://user:pass@host:1080"
    Output: {"type": "SOCKS5", "host": "host", "port": 1080,
             "username": "user", "password": "pass"}

    Returns None if the URL can't be parsed.
    """
    if not proxy_url:
        return None
    try:
        p = urlparse(proxy_url)

        scheme = p.scheme.lower()
        type_map = {
            "socks5": "SOCKS5",
            "socks4": "SOCKS4",
            "http":   "HTTP",
            "https":  "HTTP",   # Multilogin treats https proxy as HTTP type
        }
        proxy_type = type_map.get(scheme)
        if not proxy_type:
            print(f"  [WARN] Unrecognised proxy scheme: {scheme!r} — skipping")
            return None

        host = p.hostname
        port = p.port
        username = p.username or ""
        password = p.password or ""

        if not host or not port:
            print(f"  [WARN] Could not parse host/port from proxy URL — skipping")
            return None

        proxy = {
            "type": proxy_type,
            "host": host,
            "port": port,
        }
        if username:
            proxy["username"] = username
        if password:
            proxy["password"] = password

        return proxy

    except Exception as e:
        print(f"  [WARN] Proxy parse error: {e}")
        return None


def get_current_proxy(profile_id: str, headers: dict) -> dict | None:
    """Fetch the current proxy setting for a profile from the ML API."""
    try:
        resp = requests.post(
            f"{CLOUD_API}/profile/search",
            headers=headers,
            json={
                "is_removed":  False,
                "limit":       100,
                "offset":      0,
                "search_text": "",
                "storage_type": "all",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        profiles = resp.json().get("data", {}).get("profiles", [])
        for p in profiles:
            if p.get("id") == profile_id:
                return p.get("proxy")
        return None
    except Exception:
        return None


def push_proxy(account: dict, headers: dict, dry_run: bool = False) -> bool:
    """
    Push the proxy from accounts.yaml to the Multilogin cloud profile.

    Uses POST /profile/update with proxy nested under parameters.proxy —
    the same endpoint and format used by ProfileSession._push_proxy_to_ml().
    Returns True on success.
    """
    from core.profile_manager import _proxy_url_to_ml_dict, _build_profile_update

    acc_id     = account["id"]
    profile_id = account.get("multilogin_profile_id", "")
    proxy_url  = account.get("proxy", "")

    if not profile_id:
        print(f"  [{acc_id}]  No multilogin_profile_id set — skipping")
        return False

    if not proxy_url:
        print(f"  [{acc_id}]  No proxy set in accounts.yaml — skipping")
        return False

    proxy = _proxy_url_to_ml_dict(proxy_url)
    if not proxy:
        print(f"  [{acc_id}]  Could not parse proxy URL — skipping")
        return False

    print(f"  [{acc_id}]  Profile: {profile_id}")
    print(f"             Proxy:   {proxy['type']}  {proxy['host']}:{proxy['port']}")
    if proxy.get("username"):
        print(f"             User:    {proxy['username'][:30]}...")

    if dry_run:
        print(f"             [DRY RUN] Would POST /profile/update for {profile_id}")
        return True

    profile_name = account.get("email", profile_id)
    try:
        resp = requests.post(
            f"{CLOUD_API}/profile/update",
            headers={**headers, "Content-Type": "application/json"},
            json=_build_profile_update(profile_id, profile_name, proxy),
            timeout=20,
        )

        if resp.status_code == 200:
            print(f"             OK  (HTTP {resp.status_code})")
            return True
        else:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:200]
            print(f"             FAILED  (HTTP {resp.status_code})")
            print(f"             Response: {detail}")
            return False

    except requests.RequestException as e:
        print(f"             FAILED — network error: {e}")
        return False


def check_proxies(accounts: list, headers: dict) -> None:
    """Print the current proxy config for each account as seen by Multilogin."""
    print()
    print("  Fetching current proxy settings from Multilogin...")
    print()

    try:
        resp = requests.post(
            f"{CLOUD_API}/profile/search",
            headers=headers,
            json={
                "is_removed":  False,
                "limit":       100,
                "offset":      0,
                "search_text": "",
                "storage_type": "all",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            print(f"  [ERROR] Could not fetch profiles ({resp.status_code}): {resp.text[:200]}")
            return
        ml_profiles = {p["id"]: p for p in resp.json().get("data", {}).get("profiles", [])}
    except Exception as e:
        print(f"  [ERROR] API request failed: {e}")
        return

    for acc in accounts:
        acc_id     = acc["id"]
        profile_id = acc.get("multilogin_profile_id", "")
        yaml_proxy = acc.get("proxy", "(none)")

        print(f"  {acc_id}  ({acc.get('email', '')})")
        print(f"    accounts.yaml:  {yaml_proxy[:80]}{'...' if len(yaml_proxy) > 80 else ''}")

        ml_profile = ml_profiles.get(profile_id)
        if not ml_profile:
            print(f"    Multilogin:     Profile not found  ({profile_id})")
        else:
            ml_proxy = ml_profile.get("proxy") or {}
            if ml_proxy:
                ml_host = ml_proxy.get("host", "?")
                ml_port = ml_proxy.get("port", "?")
                ml_type = ml_proxy.get("type", "?")
                ml_user = ml_proxy.get("username", "")
                print(f"    Multilogin:     {ml_type}  {ml_host}:{ml_port}" +
                      (f"  (user: {ml_user[:30]}...)" if ml_user else ""))
            else:
                print(f"    Multilogin:     No proxy set on this profile")

        # Check if yaml and ML match
        parsed = parse_proxy(yaml_proxy)
        if parsed and ml_proxy:
            match = (
                parsed.get("host") == ml_proxy.get("host") and
                str(parsed.get("port")) == str(ml_proxy.get("port"))
            )
            print(f"    Match:          {'YES' if match else 'NO — run --all to sync'}")
        print()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Push proxy settings from accounts.yaml to Multilogin profiles."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all",     action="store_true", help="Update all accounts")
    group.add_argument("--account", type=str,            help="Update a single account")
    group.add_argument("--check",   action="store_true", help="Show current proxy config, no changes")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making API calls")
    args = parser.parse_args()

    if not any([args.all, args.account, args.check]):
        parser.print_help()
        sys.exit(0)

    accounts = _load_accounts()
    if not accounts:
        print("  No accounts found in accounts.yaml.")
        sys.exit(0)

    print()
    print("  Authenticating with Multilogin...")
    try:
        headers = auth_headers()
    except Exception as e:
        print(f"  [ERROR] Could not authenticate: {e}")
        sys.exit(1)
    print("  Authenticated OK")
    print()

    # ── Check mode ────────────────────────────────────────────────────────────
    if args.check:
        check_proxies(accounts, headers)
        return

    # ── Select accounts to update ─────────────────────────────────────────────
    if args.account:
        targets = [a for a in accounts if a["id"] == args.account]
        if not targets:
            print(f"  [ERROR] Account not found: {args.account}")
            sys.exit(1)
    else:
        targets = accounts

    if args.dry_run:
        print("  [DRY RUN] No changes will be made.\n")

    # ── Push proxies ──────────────────────────────────────────────────────────
    success = 0
    failed  = 0

    for acc in targets:
        ok = push_proxy(acc, headers, dry_run=args.dry_run)
        if ok:
            success += 1
        else:
            failed += 1
        print()

    print(f"  Done — {success} updated" + (f", {failed} failed" if failed else "") + ".")
    print()


if __name__ == "__main__":
    main()
