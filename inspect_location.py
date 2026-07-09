"""
inspect_location.py — Inspect location history page in detail.
"""
import asyncio, sys, yaml
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
_af = DATA_DIR / "accounts.yaml"
if not _af.exists():
    _af = APP_DIR / "config" / "accounts.yaml"
data = yaml.safe_load(_af.read_text(encoding="utf-8")) or {}
acc = next(a for a in data.get("accounts", []) if a["id"] == "acc_004")

async def run():
    async with ProfileSession(profile_id=acc["multilogin_profile_id"],
                              account_id=acc["id"], account=acc) as page:
        await page.goto("https://myaccount.google.com/activitycontrols/location",
                        wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(4)
        # Get the full page body text to understand what's there
        body = await page.inner_text("body")
        print("=== PAGE TEXT (first 3000 chars) ===")
        print(body[:3000])
        # Get all links
        links = page.locator("a[href]")
        lc = await links.count()
        print(f"\n=== LINKS ({lc}) ===")
        for i in range(min(lc, 20)):
            try:
                href = await links.nth(i).get_attribute("href")
                txt = await links.nth(i).inner_text()
                print(f"  [{i}] {txt[:60]} -> {href}")
            except: pass
        # Full page source (limited)
        html = await page.content()
        print("\n=== CHECKBOXES HTML ===")
        import re
        for m in re.finditer(r'<input[^>]*type="checkbox"[^>]*>', html):
            print(m.group()[:400])

asyncio.run(run())
