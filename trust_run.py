#!/usr/bin/env python3
"""
Run a trust check for a single account.

Usage:
    python trust_run.py --account acc_001 --check-type both
    python trust_run.py --account acc_001 --check-type v2
    python trust_run.py --account acc_001 --check-type v3
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

from core.paths import STATE_DIR
TRUST_FILE = STATE_DIR / "trust_checks.json"


async def _quick_warmup(page, account: dict, behaviour: dict, log) -> None:
    """
    Minimal ~45-second warmup before running the trust check.
    Visits Google and Maps, scrolls and moves the mouse — enough to
    establish real session activity without clicking into any external sites.
    """
    import asyncio, random

    async def _sleep(ms_min: int, ms_max: int):
        await asyncio.sleep(random.randint(ms_min, ms_max) / 1000)

    async def _scroll(steps: int = 4):
        for _ in range(steps):
            await page.mouse.wheel(0, random.randint(200, 500))
            await _sleep(400, 900)

    async def _dismiss_google_consent():
        for text in ["Accept all", "I agree", "Reject all"]:
            try:
                loc = page.locator(f'button:has-text("{text}")').first
                if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                    await loc.click()
                    await _sleep(300, 600)
                    return
            except Exception:
                pass

    urls = [
        ("Google", "https://www.google.com"),
        ("Google Maps", "https://www.google.com/maps"),
    ]

    for label, url in urls:
        try:
            log.info(f"Warmup: visiting {label}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await _sleep(1500, 2500)
            await _dismiss_google_consent()
            await _sleep(800, 1500)
            await _scroll(steps=3)
            await _sleep(2000, 4000)
        except Exception as e:
            log.debug(f"Warmup {label} error (non-fatal): {e}")

    log.info("Warmup complete (~45s)")


async def run_trust_check(account: dict, check_type: str) -> None:
    from core.logger import get_logger
    from core.profile_manager import ProfileSession
    log = get_logger(account["id"])

    log.info(f"=== Trust check ({check_type}) starting for {account['id']} ===")

    with open(ROOT / "config" / "behaviour.yaml", encoding="utf-8") as f:
        behaviour = yaml.safe_load(f)

    with open(ROOT / "config" / "schedule.yaml", encoding="utf-8") as f:
        schedule = yaml.safe_load(f)

    timeout = schedule.get("multilogin", {}).get("start_timeout_seconds", 90)

    async with ProfileSession(
        profile_id  = account["multilogin_profile_id"],
        folder_id   = account.get("multilogin_folder_id"),
        account_id  = account["id"],
        timeout     = timeout,
        account     = account,
    ) as page:

        # ── Quick warm-up (~60s) ──────────────────────────────────────────
        # Light version: 2 Google searches, scroll results, no clicking into pages.
        # This establishes real browser activity without the full SearchActivity
        # delays (which can run 5+ mins with result reading + internal links).
        log.info("Warming up: quick Google searches (~60s)")
        await _quick_warmup(page, account, behaviour, log)

        # ── Trust check ───────────────────────────────────────────────────
        log.info(f"Running trust check (type={check_type})")
        from activities.trust_check import TrustCheckActivity
        result = await TrustCheckActivity(page, account, behaviour).run(check_type=check_type)

        # ── Save result ───────────────────────────────────────────────────
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(TRUST_FILE.read_text()) if TRUST_FILE.exists() else {}
        except Exception:
            data = {}

        now = datetime.now().isoformat()
        entry = data.get(account["id"], {})

        if "v2" in result:
            entry["v2"]            = result["v2"]
            entry["v2_checked_at"] = now
        if "v3" in result:
            entry["v3"]            = result["v3"]
            entry["v3_checked_at"] = now

        entry["checked_at"] = now
        data[account["id"]] = entry

        TRUST_FILE.write_text(json.dumps(data, indent=2))
        log.info(f"Trust check result saved: {result}")


def main():
    parser = argparse.ArgumentParser(description="Run trust check for a Google account")
    parser.add_argument("--account",    required=True, help="Account ID, e.g. acc_001")
    parser.add_argument("--check-type", default="both", choices=["v2", "v3", "both"])
    args = parser.parse_args()

    with open(ROOT / "config" / "accounts.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    accounts = raw.get("accounts", raw) if isinstance(raw, dict) else raw

    account = next((a for a in accounts if a["id"] == args.account), None)
    if not account:
        print(f"ERROR: Account {args.account!r} not found in accounts.yaml")
        sys.exit(1)

    asyncio.run(run_trust_check(account, args.check_type))


if __name__ == "__main__":
    main()
