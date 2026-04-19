"""
inspect_toggles.py — Quick DOM inspector for Google activity controls toggles.

Opens a single Multilogin profile, navigates to each activitycontrols page,
and dumps the HTML of any switch/checkbox/toggle elements found.

Usage:
  python inspect_toggles.py --account acc_001
"""
import argparse
import asyncio
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_env = Path(__file__).parent / "warmer.env"
if _env.exists():
    import os
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from core.paths import DATA_DIR, APP_DIR
from core.profile_manager import ProfileSession

_accounts_in_data   = DATA_DIR / "accounts.yaml"
_accounts_in_config = APP_DIR / "config" / "accounts.yaml"
ACCOUNTS_FILE = _accounts_in_data if _accounts_in_data.exists() else _accounts_in_config

PAGES = [
    ("webandapp",  "https://myaccount.google.com/activitycontrols/webandapp"),
    ("youtube",    "https://myaccount.google.com/activitycontrols/youtube"),
    ("location",   "https://myaccount.google.com/activitycontrols/location"),
]

SELECTORS_TO_TRY = [
    '[role="switch"]',
    '[role="checkbox"]',
    'input[type="checkbox"]',
    'button[aria-checked]',
    '[aria-checked]',
    '[data-value]',
    '[jscontroller][aria-checked]',
]


async def inspect(account: dict) -> None:
    profile_id = account.get("multilogin_profile_id", "")
    acc_id = account["id"]
    print(f"\n=== Inspecting {acc_id} (profile {profile_id}) ===")

    async with ProfileSession(
        profile_id=profile_id,
        account_id=acc_id,
        account=account,
    ) as page:
        for page_name, url in PAGES:
            print(f"\n--- {page_name}: {url} ---")
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(3)

            for sel in SELECTORS_TO_TRY:
                elements = page.locator(sel)
                count = await elements.count()
                if count > 0:
                    print(f"  FOUND [{count}x] selector: {sel}")
                    for i in range(min(count, 3)):
                        try:
                            el = elements.nth(i)
                            outer_html = await el.evaluate("el => el.outerHTML")
                            print(f"    [{i}] {outer_html[:300]}")
                        except Exception as e:
                            print(f"    [{i}] error: {e}")
                else:
                    print(f"  not found: {sel}")

            # Also dump the full page title and any h1/h2 to confirm page loaded
            title = await page.title()
            print(f"  Page title: {title}")

            # Look for any button on the page that might be a toggle
            all_buttons = page.locator("button")
            btn_count = await all_buttons.count()
            print(f"  Total buttons on page: {btn_count}")
            for i in range(min(btn_count, 10)):
                try:
                    btn = all_buttons.nth(i)
                    visible = await btn.is_visible(timeout=500)
                    if visible:
                        html = await btn.evaluate("el => el.outerHTML")
                        print(f"  button[{i}]: {html[:200]}")
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, metavar="ID")
    args = parser.parse_args()

    data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    accounts = data.get("accounts", [])
    acc = next((a for a in accounts if a["id"] == args.account), None)
    if not acc:
        print(f"Account {args.account!r} not found")
        sys.exit(1)

    asyncio.run(inspect(acc))


if __name__ == "__main__":
    main()
