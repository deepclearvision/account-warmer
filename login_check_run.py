#!/usr/bin/env python3
"""
Check whether a Google account is currently logged in.

Usage:
    python login_check_run.py --account acc_001

Result is saved to STATE_DIR/login_status.json:
    { "acc_001": { "status": "logged_in"|"not_logged_in"|"error", "checked_at": "...", "email": "..." } }

Detection method:
    Uses Google's lightweight ListAccounts endpoint which returns the list of
    signed-in accounts without requiring a full page navigation.  Falls back to
    navigating to myaccount.google.com if the fetch fails.
"""

import argparse
import asyncio
import json
import sys
import yaml
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

STATE_DIR  = ROOT / "logs" / "state"
LOGIN_FILE = STATE_DIR / "login_status.json"

# Google lightweight endpoint — returns JSON list of signed-in accounts.
# No page render required; uses existing browser cookies.
LIST_ACCOUNTS_URL = (
    "https://accounts.google.com/ListAccounts"
    "?gpsia=1&source=ogb&json=standard"
)


async def run_login_check(account: dict) -> None:
    from core.logger import get_logger
    from core.profile_manager import ProfileSession

    log = get_logger(account["id"])
    log.info(f"Login check starting for {account['id']} ({account.get('email', '?')})")

    with open(ROOT / "config" / "schedule.yaml", encoding="utf-8") as f:
        schedule = yaml.safe_load(f)
    timeout = schedule.get("multilogin", {}).get("start_timeout_seconds", 90)

    status        = "error"
    detected_email = None

    try:
        async with ProfileSession(
            profile_id  = account["multilogin_profile_id"],
            folder_id   = account.get("multilogin_folder_id"),
            account_id  = account["id"],
            timeout     = timeout,
            account     = account,
        ) as page:

            # ── Method 1: ListAccounts fetch (fast, no redirect wait) ──────────
            try:
                result = await page.evaluate(
                    """async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            return {ok: r.ok, status: r.status, text: await r.text()};
                        } catch(e) {
                            return {ok: false, status: 0, text: String(e)};
                        }
                    }""",
                    LIST_ACCOUNTS_URL,
                )
                log.info(f"ListAccounts response status={result.get('status')} text={result.get('text','')[:120]}")

                if result.get("ok"):
                    raw = result.get("text", "")
                    # Response is like: ["gaia.l.a.r",[[...account rows...],[],...]]
                    # account row[2] is the email address when signed in.
                    # If no accounts: ["gaia.l.a.r",[]]
                    parsed = json.loads(raw)
                    # parsed[1] is the accounts list
                    accounts_list = parsed[1] if len(parsed) > 1 else []
                    if accounts_list:
                        status = "logged_in"
                        # Try to extract the email from the first account entry
                        try:
                            detected_email = accounts_list[0][2]
                        except Exception:
                            pass
                    else:
                        status = "not_logged_in"
                    log.info(
                        f"ListAccounts: {status}"
                        + (f" ({detected_email})" if detected_email else "")
                    )
                else:
                    raise ValueError(f"ListAccounts fetch failed: status={result.get('status')}")

            except Exception as e1:
                log.warning(f"ListAccounts method failed ({e1}), falling back to page navigation")

                # ── Method 2: Navigate and check URL (fallback) ────────────────
                await page.goto(
                    "https://myaccount.google.com",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                await asyncio.sleep(2)
                url       = page.url
                url_lower = url.lower()
                if (
                    "myaccount.google.com" in url_lower
                    and "signin"       not in url_lower
                    and "servicelogin" not in url_lower
                    and "login"        not in url_lower
                ):
                    status = "logged_in"
                else:
                    status = "not_logged_in"
                log.info(f"Fallback nav result: {status} (landed at {url[:80]})")

    except Exception as e:
        log.error(f"Login check error: {e}")
        status = "error"

    # ── Persist result ─────────────────────────────────────────────────────────
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(LOGIN_FILE.read_text(encoding="utf-8")) if LOGIN_FILE.exists() else {}
    except Exception:
        data = {}

    entry: dict = {
        "status":     status,
        "checked_at": datetime.now().isoformat(),
    }
    if detected_email:
        entry["email"] = detected_email

    data[account["id"]] = entry
    LOGIN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info(f"Login status saved: {account['id']} → {status}")


def main():
    parser = argparse.ArgumentParser(description="Check Google login status for an account")
    parser.add_argument("--account", required=True, help="Account ID, e.g. acc_001")
    args = parser.parse_args()

    with open(ROOT / "config" / "accounts.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    accounts = raw.get("accounts", raw) if isinstance(raw, dict) else raw

    account = next((a for a in accounts if a["id"] == args.account), None)
    if not account:
        print(f"ERROR: Account {args.account!r} not found in accounts.yaml")
        sys.exit(1)

    asyncio.run(run_login_check(account))


if __name__ == "__main__":
    main()
