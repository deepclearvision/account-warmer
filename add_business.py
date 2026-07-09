"""
Business Lookup & Add Tool

Finds a business on Google Maps, extracts its details automatically,
and saves it to config/businesses.yaml ready for the warming script.

Usage:
  python add_business.py "Joe's Barbers London"
  python add_business.py "The Crown" --location "Manchester"
  python add_business.py --file businesses.txt          (bulk add, one per line)
  python add_business.py --list                         (show saved businesses)
  python add_business.py --remove biz_001

businesses.txt format (one per line):
  Joe's Barbers, London
  The Crown Pub, Manchester
  Mario's Italian Restaurant
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
import yaml
from playwright.async_api import async_playwright, Page

from core.paths import BUSINESSES_FILE
MAPS_URL        = "https://www.google.com/maps"

# Map Google category keywords → our internal type
CATEGORY_MAP = {
    "barber":      "barber",
    "hair salon":  "salon",
    "hair":        "salon",
    "nail":        "salon",
    "beauty":      "salon",
    "cafe":        "cafe",
    "coffee":      "cafe",
    "tea room":    "cafe",
    "bakery":      "cafe",
    "restaurant":  "restaurant",
    "takeaway":    "restaurant",
    "pizza":       "restaurant",
    "indian":      "restaurant",
    "chinese":     "restaurant",
    "italian":     "restaurant",
    "sushi":       "restaurant",
    "burger":      "restaurant",
    "pub":         "pub",
    "bar ":        "pub",
    "tavern":      "pub",
    "club":        "pub",
    "gym":         "gym",
    "fitness":     "gym",
    "leisure":     "gym",
    "swimming":    "gym",
    "sport":       "gym",
    "dentist":     "dentist",
    "dental":      "dentist",
    "orthodon":    "dentist",
    "garage":      "garage",
    "mot":         "garage",
    "car repair":  "garage",
    "auto":        "garage",
    "mechanic":    "garage",
    "tyre":        "garage",
}


def _detect_type(category_text: str) -> str:
    lower = category_text.lower()
    for keyword, biz_type in CATEGORY_MAP.items():
        if keyword in lower:
            return biz_type
    return "other"


def _next_biz_id(existing: list) -> str:
    existing_ids = {b["id"] for b in existing}
    n = 1
    while True:
        candidate = f"biz_{n:03d}"
        if candidate not in existing_ids:
            return candidate
        n += 1


def _load_businesses() -> list:
    if BUSINESSES_FILE.exists():
        with open(BUSINESSES_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("businesses", [])
    return []


def _save_businesses(businesses: list) -> None:
    BUSINESSES_FILE.parent.mkdir(exist_ok=True)
    with open(BUSINESSES_FILE, "w", encoding="utf-8") as f:
        yaml.dump(
            {"businesses": businesses},
            f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )


# ------------------------------------------------------------------
# Maps scraper
# ------------------------------------------------------------------

async def lookup_business(query: str, headless: bool = False) -> dict | None:
    """
    Search Google Maps for `query`, return extracted business details
    or None if not found.
    """
    print(f"  Searching Maps for: {query!r} …")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
        )
        page = await context.new_page()

        try:
            await page.goto(MAPS_URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # Dismiss consent if present
            for sel in ['button:has-text("Accept all")', 'button:has-text("I agree")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        await asyncio.sleep(1)
                        break
                except Exception:
                    pass

            # Type search query
            search = page.locator("input#searchboxinput").first
            await search.click()
            await asyncio.sleep(0.5)
            await search.fill(query)
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)

            # Click first result if results panel appeared
            result = page.locator("div[role='feed'] div.Nv2PK").first
            if await result.is_visible(timeout=5000):
                await result.click()
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(3)

            return await _extract_listing(page)

        except Exception as e:
            print(f"  Error during lookup: {e}")
            return None
        finally:
            await browser.close()


async def _extract_listing(page: Page) -> dict | None:
    """Extract business details from the currently open Maps listing."""
    details = {}

    # Name
    try:
        name_el = page.locator("h1.DUwDvf, h1.fontHeadlineLarge").first
        details["name"] = (await name_el.inner_text(timeout=3000)).strip()
    except Exception:
        return None  # Can't get name = wrong page

    if not details.get("name"):
        return None

    # Category / type
    try:
        cat_el = page.locator("button.DkEaL, span.DkEaL").first
        cat    = (await cat_el.inner_text(timeout=2000)).strip()
        details["category"] = cat
        details["type"]     = _detect_type(cat)
    except Exception:
        details["category"] = ""
        details["type"]     = "other"

    # Address
    try:
        addr_el = page.locator(
            'button[data-item-id="address"], [data-tooltip="Copy address"] div.Io6YTe'
        ).first
        details["address"] = (await addr_el.inner_text(timeout=2000)).strip()
    except Exception:
        details["address"] = ""

    # Phone
    try:
        phone_el = page.locator(
            'button[data-tooltip="Copy phone number"] div.Io6YTe,'
            '[data-item-id*="phone"] div.Io6YTe'
        ).first
        details["phone"] = (await phone_el.inner_text(timeout=2000)).strip()
    except Exception:
        details["phone"] = ""

    # Website
    try:
        web_el = page.locator(
            'a[data-item-id="authority"], a[aria-label*="website"]'
        ).first
        href = await web_el.get_attribute("href", timeout=2000)
        if href and href.startswith("http"):
            details["website"] = href
        else:
            details["website"] = ""
    except Exception:
        details["website"] = ""

    # Location (city from address)
    details["location"] = _extract_city(details.get("address", ""))

    # Maps URL — also contains lat/lng we can extract
    details["maps_url"] = page.url

    # Coordinates — extracted from the Maps URL  e.g. /@51.5074,-0.1278,17z
    lat, lng = _extract_coords(page.url)
    details["lat"] = lat
    details["lng"] = lng

    return details


def _extract_coords(url: str) -> tuple[float | None, float | None]:
    """
    Extract latitude and longitude from a Google Maps URL.
    Maps URLs contain  /@lat,lng,zoom  e.g. /@51.5074,-0.1278,17z
    """
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


def _extract_city(address: str) -> str:
    """Best-effort city extraction from an address string."""
    if not address:
        return ""
    # UK postcode pattern — city is usually before the postcode
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        # Return the second-to-last part (usually city), skip postcode
        for part in reversed(parts[:-1]):
            if part and not re.match(r"^[A-Z]{1,2}\d", part):
                return part
    return parts[0] if parts else address


# ------------------------------------------------------------------
# Add business
# ------------------------------------------------------------------

async def add_business(raw_query: str, yes: bool = False) -> bool:
    """Look up a business and add it to businesses.yaml."""
    query   = raw_query.strip()
    details = await lookup_business(query)

    if not details:
        print(f"  Could not find: {query!r}")
        print("  Try adding the city name e.g. \"Joe's Barbers London\"")
        return False

    # Show what was found
    print()
    lat, lng = details.get("lat"), details.get("lng")
    coords   = f"{lat}, {lng}" if lat and lng else "(not found — geolocation spoofing unavailable)"
    print(f"  Found:")
    print(f"    Name:     {details['name']}")
    print(f"    Type:     {details['type']}  ({details['category']})")
    print(f"    Address:  {details['address'] or '(not found)'}")
    print(f"    Phone:    {details['phone'] or '(not found)'}")
    print(f"    Website:  {details['website'] or '(none)'}")
    print(f"    Location: {details['location'] or '(unknown)'}")
    print(f"    GPS:      {coords}")
    print()

    # Confirm
    if not yes:
        answer = input("  Add this business? [Y/n]: ").strip().lower()
        if answer == "n":
            print("  Skipped.")
            return False

    # Check for duplicate name
    businesses = _load_businesses()
    existing_names = {b["name"].lower() for b in businesses}
    if details["name"].lower() in existing_names:
        print(f"  Already saved: {details['name']!r} — skipping.")
        return False

    # Build entry
    biz_id = _next_biz_id(businesses)
    entry  = {
        "id":       biz_id,
        "name":     details["name"],
        "type":     details["type"],
        "address":  details["address"],
        "phone":    details["phone"],
        "website":  details["website"],
        "location": details["location"],
        "lat":      details.get("lat"),
        "lng":      details.get("lng"),
    }

    businesses.append(entry)
    _save_businesses(businesses)
    print(f"  Saved as {biz_id}: {details['name']!r}")
    return True


# ------------------------------------------------------------------
# Bulk add from file
# ------------------------------------------------------------------

async def add_from_file(file_path: Path, yes: bool = False) -> None:
    if not file_path.exists():
        print(f"File not found: {file_path}")
        sys.exit(1)

    with open(file_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    print(f"  {len(lines)} businesses to process\n")
    added = 0
    for line in lines:
        print(f"─── {line}")
        success = await add_business(line, yes=yes)
        if success:
            added += 1
        print()

    print(f"  Done — {added}/{len(lines)} added.")


# ------------------------------------------------------------------
# List / Remove
# ------------------------------------------------------------------

def list_businesses() -> None:
    businesses = _load_businesses()
    if not businesses:
        print("\n  No businesses saved yet.\n")
        return

    print()
    print(f"  {'ID':<10} {'Name':<35} {'Type':<12} {'Location'}")
    print(f"  {'─'*10} {'─'*35} {'─'*12} {'─'*20}")
    for b in businesses:
        print(f"  {b['id']:<10} {b['name']:<35} {b.get('type',''):<12} {b.get('location','')}")
    print()


def remove_business(biz_id: str) -> None:
    businesses = _load_businesses()
    before     = len(businesses)
    businesses = [b for b in businesses if b["id"] != biz_id]
    if len(businesses) == before:
        print(f"\n  Business '{biz_id}' not found.\n")
        return
    _save_businesses(businesses)
    print(f"\n  Removed: {biz_id}\n")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Business Lookup & Add Tool")
    parser.add_argument("query",    nargs="?",       help="Business name and optional location")
    parser.add_argument("--location", type=str,      help="Location hint (e.g. 'London')")
    parser.add_argument("--file",   type=str,        help="Text file with one business per line")
    parser.add_argument("--list",   action="store_true", help="List all saved businesses")
    parser.add_argument("--remove", type=str,        help="Remove a business by ID")
    parser.add_argument("--yes",    action="store_true", help="Skip confirmation prompts")
    args = parser.parse_args()

    if args.list:
        list_businesses()

    elif args.remove:
        remove_business(args.remove)

    elif args.file:
        asyncio.run(add_from_file(Path(args.file), yes=args.yes))

    elif args.query:
        query = args.query
        if args.location and args.location.lower() not in query.lower():
            query = f"{query} {args.location}"
        asyncio.run(add_business(query, yes=args.yes))

    else:
        print(__doc__)
        sys.exit(0)


if __name__ == "__main__":
    main()
