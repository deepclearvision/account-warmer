#!/usr/bin/env python3
"""
warming_runner.py — Daily Google Review warming session runner.

Runs one ~15-minute warming session per account on GeelarK cloud Android phones.
Mixes YouTube watching, Maps knowledge panel browsing, and (in the final week)
branded search + driving directions.

Architecture:
  1. Load account + state
  2. Start phone
  3. Set GPS to a nearby point
  4. Run YouTube Shorts flow (3-5 min)
  5. Run Maps browsing flow (5-8 min)
  6. Optionally: branded search + animated GPS drive (5-10 min)
  7. Stop phone
  8. Record session in state

Usage:
  python warming_runner.py --account acc_004                  # single account
  python warming_runner.py --account acc_004 --dry-run        # preview plan only
  python warming_runner.py --all                              # all eligible accounts
  python warming_runner.py --test                             # test on phone 629021249506377828
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
import re
import sys
import time
import os
from datetime import datetime
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))

from core.geelark_client import GeelarKClient
from warming.state_tracker import (
    load_state, save_state, init_account, can_run_today,
    should_do_drive, record_session, record_drive_completed,
    get_todays_activity_plan, get_eligible_accounts, COHORTS,
)

# ── Logging ─────────────────────────────────────────────────────────────────────
_LOG_DIR = _REPO_ROOT / "logs" / "warming"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_DIR / f"warming_{datetime.now():%Y%m%d}.log"),
    ],
)
log = logging.getLogger("warming_runner")

# ── Constants ───────────────────────────────────────────────────────────────────
YOUTUBE_TEMPLATE_ID = "629648416401522754"
TEST_PHONE_ID = "629021249506377828"
CSV_PATH = _REPO_ROOT / "data" / "accounts_business_mapping.csv"


# ── Variance helpers ────────────────────────────────────────────────────────────

def parse_csv_list(raw: str) -> list[str]:
    """Parse a pipe-separated CSV field into a deduplicated list."""
    if not raw:
        return []
    return list(dict.fromkeys(
        item.strip() for item in raw.split("|") if item.strip()
    ))


def jitter_gps(lat: float, lng: float, metres: float = 50.0) -> tuple[float, float]:
    """Add small random offset to GPS coords so the exact same point never repeats.
    ±50m by default (~0.00045 degrees latitude)."""
    lat_offset = (random.random() * 2 - 1) * (metres / 111320.0)
    lng_offset = (random.random() * 2 - 1) * (metres / (111320.0 * abs(__import__('math').cos(__import__('math').radians(lat)))))
    return round(lat + lat_offset, 6), round(lng + lng_offset, 6)


def pick_varied(choices: list, day: int, min_count: int = 3) -> list:
    """Pick min_count items from choices, seeded by the session day so the
    same account doesn't repeat the same terms on consecutive days."""
    if len(choices) <= min_count:
        return list(choices)
    rng = random.Random(hash(str(choices[:3])) + day)
    return rng.sample(choices, min(min_count, len(choices)))


def expand_keywords(base_keywords: list[str], business_area: str,
                    service_types: list[str] = None) -> list[str]:
    """
    Cross-combine base keywords with area names and service variants to produce
    80+ unique search terms from a pool of 20. Not every combination makes sense,
    so we filter for ones that read naturally.

    Returns deduplicated list of expanded keywords.
    """
    if service_types is None:
        service_types = ["plumber", "plumbing", "heating engineer", "boiler repair",
                         "drain unblocking", "gas engineer", "emergency plumber",
                         "local plumber", "reliable plumber"]

    # Parse area into components: "Crystal Palace, London" -> ["Crystal Palace", "Crystal Palace London"]
    area_parts = [business_area]
    if "," in business_area:
        borough = business_area.split(",")[0].strip()
        area_parts.append(borough)
        city = business_area.split(",")[-1].strip()
        area_parts.append(f"{borough} {city}")

    expanded = list(base_keywords)

    # 1. Add area-qualified variants for each keyword
    for kw in base_keywords[:10]:  # use first 10 as seeds
        for area in area_parts:
            if area.lower() not in kw.lower():
                expanded.append(f"{kw} {area}")

    # 2. Add service-type cross-combinations
    for svc in service_types:
        for area in area_parts:
            expanded.append(f"{svc} {area}")
            expanded.append(f"{svc} near {area}")
        expanded.append(f"{svc} near me")
        expanded.append(f"best {svc} near me")
        expanded.append(f"local {svc}")
        expanded.append(f"emergency {svc}")
        expanded.append(f"24 hour {svc}")

    # 3. Add local-service combos
    locals_ = ["local", "best", "emergency", "reliable", "affordable", "same day",
               "top rated", "recommended", "professional"]
    for prefix in locals_:
        for svc in service_types[:5]:
            expanded.append(f"{prefix} {svc}")
            for area in area_parts[:2]:
                expanded.append(f"{prefix} {svc} {area}")

    # 4. Add "near me" variants
    for svc in service_types[:6]:
        for area in area_parts[:2]:
            expanded.append(f"{svc} in {area}")
            expanded.append(f"find {svc} {area}")
        expanded.append(f"find {svc} near me")

    # Deduplicate (case-insensitive) and return
    seen = set()
    result = []
    for kw in expanded:
        key = kw.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(kw.strip())
    return result


def scatter_gps_points(business_lat: float, business_lng: float,
                       existing_points: list[tuple[float, float]] = None,
                       count: int = 50, radius_km: float = 2.0) -> list[tuple[float, float]]:
    """
    Generate additional GPS scatter points in a realistic ring around the business.
    Points are placed at varying distances (200m to radius_km) at random bearings,
    avoiding the business center itself by at least 150m.

    Merges existing points if provided so the pool grows rather than replaces.
    """
    import math as _m
    pts = list(existing_points) if existing_points else []

    # Existing points count toward the total
    needed = max(0, count - len(pts))
    rng = random.Random(hash(str(business_lat) + str(business_lng)))

    for _ in range(needed):
        # Random bearing
        bearing = rng.uniform(0, 360)
        # Distance: mix of close (200-600m), medium (600-1200m), far (1200m+)
        band = rng.random()
        if band < 0.4:
            dist_km = rng.uniform(0.2, 0.6)
        elif band < 0.8:
            dist_km = rng.uniform(0.6, 1.2)
        else:
            dist_km = rng.uniform(1.2, radius_km)

        # Convert to lat/lng offset
        lat_offset = (dist_km / 111.32) * _m.cos(_m.radians(bearing))
        lng_offset = (dist_km / (111.32 * _m.cos(_m.radians(business_lat)))) * _m.sin(_m.radians(bearing))

        new_lat = round(business_lat + lat_offset, 6)
        new_lng = round(business_lng + lng_offset, 6)
        pts.append((new_lat, new_lng))

    # Shuffle so existing and generated points interleave
    rng.shuffle(pts)
    return pts


# ── Pre-compute expanded resources per account (lazy cached) ────────────────────
_expanded_cache = {}  # account_id -> {"keywords": [...], "gps_points": [...]}


def get_expanded_resources(account_data: dict) -> dict:
    """Get or compute the expanded keyword + GPS pool for an account."""
    aid = account_data["account_id"]
    if aid in _expanded_cache:
        return _expanded_cache[aid]

    area = account_data.get("business_area", "London")
    lat = float(account_data.get("business_lat", "51.5"))
    lng = float(account_data.get("business_lng", "-0.1"))

    # Discovery keywords: base CSV + expanded combos
    base_disc = parse_csv_list(account_data.get("discovery_keywords", ""))
    disc_kws = expand_keywords(base_disc, area) if base_disc else []

    # Standalone + money combined for KP browsing
    base_standalone = parse_csv_list(account_data.get("standalone_keywords", ""))
    base_money = parse_csv_list(account_data.get("money_keywords", ""))
    kp_kws = expand_keywords(base_standalone + base_money, area) if (base_standalone or base_money) else []

    # GPS points: merge all phase pools + scatter
    all_raw = (parse_csv_list(account_data.get("nearby_points_discovery", "")) +
               parse_csv_list(account_data.get("nearby_points_standalone", "")) +
               parse_csv_list(account_data.get("nearby_points_branded", "")) +
               parse_csv_list(account_data.get("nearby_points", "")))
    existing = []
    for pt in all_raw:
        if "," in pt:
            parts = pt.split(",")
            try:
                existing.append((float(parts[0].strip()), float(parts[1].strip())))
            except ValueError:
                pass

    gps_pts = scatter_gps_points(lat, lng, existing, count=150)

    result = {
        "discovery_keywords": disc_kws,
        "kp_keywords": kp_kws,
        "gps_points": gps_pts,
    }
    _expanded_cache[aid] = result
    return result


# ══════════════════════════════════════════════════════════════════════════════════
# Maps Knowledge Panel Shell-Based Interaction (Autosuggest-Driven)
# ══════════════════════════════════════════════════════════════════════════════════
#
# On Android 14 Maps, using `geo:` intent shows SEARCH RESULTS (including
# sponsored listings), NOT the knowledge panel. To open the knowledge panel
# for a SPECIFIC business, you MUST:
#   1. Tap the search box and type the business name
#   2. Wait for the autosuggest dropdown
#   3. Read the autosuggest items from uiautomator XML (chunked, due to API limits)
#   4. Match the correct business by name (exact match, rejecting LTD variants)
#   5. Tap the matching item at its specific y-coordinate
#   6. Verify via search bar content-desc that our business + address is showing
#   7. Only then interact: Directions -> Call -> scroll -> Photos -> Reviews
#
# Key coordinates (from XML probe on Android 14, 1080x2340):
#   Search bar tap:      (580, 168)  — EditText, bounds [36,96][900,240]
#   Directions button:   (237, 1880) — content-desc="Directions"
#   Call button:         (555, 1880) — content-desc="Call"
#   Autosuggest items:   ~183px apart starting at y≈276


def _read_xml_chunks(phone_id: str) -> str:
    """Read the full uiautomator XML in 1600-char chunks (fallback, slow — use grep when possible)."""
    from geelark_orchestrator.scripts.moving_gps import shell
    all_text = ""
    for i in range(35):
        offset = i * 1600
        ok, chunk = shell(phone_id,
            f"head -c $(({offset} + 1600)) /sdcard/ui.xml | tail -c 1600")
        if not ok or not chunk:
            break
        all_text += chunk
        if len(chunk) < 1600:
            break
    return all_text


def _shell_grep_count(phone_id: str, pattern: str) -> int:
    """Count pattern occurrences in uiautomator XML via grep. Returns -1 on error."""
    from geelark_orchestrator.scripts.moving_gps import shell
    ok, out = shell(phone_id, f"grep -c '{pattern}' /sdcard/ui.xml 2>&1")
    if ok and out.strip().isdigit():
        return int(out.strip())
    return -1


def _shell_grep_lines(phone_id: str, pattern: str) -> str:
    """Grep matching lines from uiautomator XML. Returns raw output string."""
    from geelark_orchestrator.scripts.moving_gps import shell
    ok, out = shell(phone_id, f"grep '{pattern}' /sdcard/ui.xml 2>&1")
    return out if ok else ""


def _xml_dump(phone_id: str) -> None:
    """Dump current UI hierarchy to /sdcard/ui.xml."""
    from geelark_orchestrator.scripts.moving_gps import shell
    shell(phone_id, "uiautomator dump /sdcard/ui.xml 2>&1 >/dev/null")


def _get_autosuggest_items(phone_id: str) -> list[dict]:
    """Find autosuggest dropdown items from XML.

    Uses fast grep -o for names only (~2s). Does NOT read XML chunks for
    bounds because we navigate via D-pad keyevents, not tap coordinates.
    The item order from grep matches the autosuggest display order.

    Returns list of {name, index}
    """
    from geelark_orchestrator.scripts.moving_gps import shell

    # Fast grep: extract just the suggestion names (no bounds, no escaping issues)
    ok, out = shell(phone_id,
        "grep -o 'Activate to enter suggestion[^\"]*'"
        " /sdcard/ui.xml 2>&1")
    if not ok or not out:
        return []

    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line or 'Activate to enter suggestion' not in line:
            continue
        # Extract name: "Activate to enter suggestion NAME into search bar"
        raw = line.replace("Activate to enter suggestion ", "")
        name = raw.replace(" into search bar", "")
        name = name.replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
        if name:
            items.append({"name": name, "index": len(items)})

    return items


def _match_business(items: list[dict], business_name: str) -> dict | None:
    """Find the autosuggest item that best matches our target business.

    Priority:
    1. Exact name match (case-insensitive, &/and normalized) — strongest
    2. Target name fully contained in item name with short extra text — close variant
    3. Item name contained in target — partial match

    The first item (index 0) is usually the closest business to our GPS
    location, which is the one we want.
    Returns None if no reasonable match found.
    """
    target = business_name.lower().replace("&", "and").strip()

    # Score each item
    scored = []
    for item in items:
        item_name = item["name"].lower().replace("&", "and").strip()
        score = 0

        # Exact match
        if item_name == target:
            score += 100
        # Target fully contained in item name (e.g. "X Plumbing" in "X Plumbing LTD")
        elif target in item_name:
            extra = item_name.replace(target, "").strip(".,;: ")
            if len(extra) <= 10:
                score += 80
            else:
                score += 50
        # Item name contained in target
        elif item_name in target:
            score += 30
        else:
            continue  # no name match, skip

        # Bonus for being earlier in the list (closest to our GPS location)
        score += max(0, 5 - item["index"])

        scored.append((score, item))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _verify_business_panel(phone_id: str, business_name: str,
                           xml_text: str = "") -> tuple:
    """Verify knowledge panel shows OUR business (not sponsored result).

    Reads XML via chunked approach (grep is broken on single-line XML).
    Counts Directions/Call buttons and checks EditText content-desc.

    If xml_text is provided (pre-read from _get_autosuggest_items), reuses it.
    Returns (is_our_business: bool, markers: dict)
    """
    from geelark_orchestrator.scripts.moving_gps import shell

    _xml_dump(phone_id)
    time.sleep(0.3)

    biz_short = business_name.split("&")[0].strip().lower()

    # Read XML if not provided
    if not xml_text:
        xml_text = ""
        for i in range(30):
            offset = i * 2000
            ok, chunk = shell(phone_id,
                f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
            if not ok or not chunk:
                break
            xml_text += chunk
            if len(chunk) < 2000:
                break

    # Count Directions and Call buttons (real count, not line-based)
    directions = xml_text.count('"Directions"') + xml_text.count('Directions</')
    call = xml_text.count('"Call"') + xml_text.count('Call</')
    has_actions = directions > 0 and call > 0

    # Find search bar text: check both content-desc AND text on EditText elements
    # After autosuggest submit, the EditText may have empty content-desc but
    # text="Sidcup Plumbing" in the text attribute
    search_bar_text = ""
    edittext_pattern_full = re.compile(
        r'<node[^>]*class="android\.widget\.EditText"[^>]*'
        r'(?:content-desc="([^"]*)"[^>]*text="([^"]*)"'
        r'|text="([^"]*)"[^>]*content-desc="([^"]*)")'
        r'[^>]*/?>'
    )
    ed_m = edittext_pattern_full.search(xml_text)
    if ed_m:
        # Either order: (desc, text) or (text2, desc2)
        desc1, text1, text2, desc2 = ed_m.groups()
        search_desc = (desc1 or desc2 or '').lower().replace('&amp;', '&')
        search_text = (text1 or text2 or '').lower().replace('&amp;', '&')
        search_bar_text = search_desc or search_text

    # Simpler fallback: find EditText and check both text and content-desc
    if not search_bar_text:
        simple_ed = re.compile(
            r'<node[^>]*class="android\.widget\.EditText"[^>]*/?>'
        )
        for m in simple_ed.finditer(xml_text):
            node = m.group()
            desc_m = re.search(r'content-desc="([^"]*)"', node)
            text_m = re.search(r'text="([^"]*)"', node)
            if desc_m and desc_m.group(1):
                search_bar_text = desc_m.group(1).lower().replace('&amp;', '&')
                break
            if text_m and text_m.group(1):
                search_bar_text = text_m.group(1).lower().replace('&amp;', '&')
                break

    bar_has_biz = biz_short in search_bar_text

    # Fallback: if bar doesn't show biz, check if biz name appears in ANY
    # content-desc on the page (maps often puts biz name in panel content-desc)
    if not bar_has_biz:
        all_desc = re.findall(r'content-desc="([^"]*)"', xml_text)
        for d in all_desc:
            d_clean = d.lower().replace('&amp;', '&')
            if biz_short in d_clean:
                bar_has_biz = True
                search_bar_text = d_clean[:80]
                break

    markers = {
        "Directions": directions,
        "Call": call,
        "biz_in_bar": bar_has_biz,
        "search_bar": search_bar_text[:80],
    }

    is_our_business = has_actions and bar_has_biz
    return is_our_business, markers


def _take_screenshot(client: GeelarKClient, phone_id: str,
                     log_dir: Path, name: str) -> bool:
    """Take a screenshot and save to log_dir. Returns True on success."""
    try:
        img = client.take_screenshot(phone_id, max_wait=20)
        if img:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{name}.png").write_bytes(img)
            return True
    except Exception:
        pass
    return False


def run_maps_kp_shell(client: GeelarKClient, phone_id: str,
                       business_name: str, business_area: str = "",
                       log_dir: Path = None,
                       task_timeout: int = 360) -> dict:
    """
    Shell-based Maps knowledge panel interaction — autosuggest driven.

    Reads autosuggest items from XML, matches the correct business by name,
    taps the matching item, verifies via search bar content-desc that OUR
    business panel opened, then runs Direction/Call/Scroll/Photos/Reviews.

    Returns {"success": bool, "duration_s": float, "error": str, "markers": dict}
    """
    from geelark_orchestrator.scripts.moving_gps import shell

    t0 = time.time()
    log.info("[Maps-Shell] '%s'", business_name)

    if log_dir is None:
        log_dir = Path("logs") / "warming" / f"maps_{phone_id}_{datetime.now():%Y%m%d_%H%M%S}"

    def ss(name: str) -> bool:
        return _take_screenshot(client, phone_id, log_dir, name)

    # ── Phase 1: Open knowledge panel via autosuggest ──
    is_our_biz = False
    markers = {}
    attempt = 0

    # Strategy 1 (PRIMARY): Autosuggest — type name, match item, D-pad navigate + ENTER
    attempt += 1
    log.info("  [Attempt %d] Autosuggest match", attempt)

    shell(phone_id, "am force-stop com.google.android.apps.maps")
    time.sleep(2)
    shell(phone_id, "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity")
    time.sleep(5)
    ss("s01_fresh_maps")

    shell(phone_id, "input tap 580 168")
    time.sleep(2)

    safe_name = business_name.replace("&", "and").replace("'", "")
    shell(phone_id, f'input text "{safe_name}"')
    time.sleep(3)
    ss("s02_autosuggest_visible")

    _xml_dump(phone_id)
    items = _get_autosuggest_items(phone_id)
    log.info("    Autosuggest: %s",
             [f"[{i['index']}] '{i['name']}' (y1={i.get('fill_y1','?')})" for i in items])

    match = _match_business(items, business_name)
    if match:
        match_idx = match["index"]
        log.info("    Matched: [%d] '%s' -> navigating via D-pad (%dx DOWN + ENTER)",
                 match_idx, match["name"], match_idx)

        # Navigate autosuggest via D-pad (input tap doesn't work on overlay)
        # First item should be auto-focused after typing
        # Press DPAD_DOWN N times to reach our matched item
        for n in range(match_idx):
            shell(phone_id, "input keyevent 20")  # DPAD_DOWN
            time.sleep(0.3)

        shell(phone_id, "input keyevent 66")  # ENTER
        time.sleep(6)
        ss("s03_after_autosuggest_enter")

        is_our_biz, markers = _verify_business_panel(phone_id, business_name)
        log.info("    Verify: D=%d C=%d bar_biz=%s -> %s",
                 markers.get("Directions", -1), markers.get("Call", -1),
                 markers.get("biz_in_bar", False),
                 "OURS" if is_our_biz else "WRONG")
        if markers.get("search_bar"):
            log.info("    Search bar: '%s'", markers["search_bar"])

        # Retry: if first match opens wrong panel, try next best matches
        # (e.g. index 0 is search history, index 1 is the real business listing)
        if not is_our_biz and len(items) > 1:
            # Build list of alternative matches sorted by name similarity
            alt_items = [it for it in items if it["index"] != match_idx]
            alt_match = _match_business(alt_items, business_name)
            if alt_match:
                log.info("    Retry: matched [%d] '%s' was wrong, trying [%d] '%s'",
                         match_idx, match["name"], alt_match["index"], alt_match["name"])

                # Go back, re-open search, type again
                shell(phone_id, "input keyevent 4")
                time.sleep(2)
                shell(phone_id, "input tap 580 168")
                time.sleep(1)
                shell(phone_id, f'input text "{safe_name}"')
                time.sleep(3)

                for n in range(alt_match["index"]):
                    shell(phone_id, "input keyevent 20")
                    time.sleep(0.3)
                shell(phone_id, "input keyevent 66")
                time.sleep(6)

                is_our_biz, markers = _verify_business_panel(phone_id, business_name)
                log.info("    Retry verify: D=%d C=%d bar_biz=%s -> %s",
                         markers.get("Directions", -1), markers.get("Call", -1),
                         markers.get("biz_in_bar", False),
                         "OURS" if is_our_biz else "WRONG")
                if markers.get("search_bar"):
                    log.info("    Search bar: '%s'", markers["search_bar"])
    else:
        log.info("    No autosuggest match for '%s'", business_name)

    # Strategy 2 (FALLBACK): Name + address/area -> autosuggest
    if not is_our_biz:
        attempt += 1
        log.info("  [Attempt %d] Name+address autosuggest", attempt)

        shell(phone_id, "input keyevent 4")
        time.sleep(2)
        shell(phone_id, "input tap 580 168")
        time.sleep(1)
        shell(phone_id, "input tap 972 168")  # clear button
        time.sleep(0.5)

        area = business_area or ""
        search_term = f"{business_name.split('&')[0].strip()} {area}" if area else business_name
        safe_term = search_term.replace("&", "and").replace("'", "")
        shell(phone_id, f'input text "{safe_term}"')
        time.sleep(3)
        ss("s04_name_area_typed")

        _xml_dump(phone_id)
        items2 = _get_autosuggest_items(phone_id)
        log.info("    Autosuggest: %s",
                 [f"[{i['index']}] '{i['name']}' (y1={i.get('fill_y1','?')})" for i in items2])

        match2 = _match_business(items2, business_name)
        if match2:
            match2_idx = match2["index"]
            log.info("    Matched: [%d] '%s' -> navigating via D-pad (%dx DOWN + ENTER)",
                     match2_idx, match2["name"], match2_idx)

            for n in range(match2_idx):
                shell(phone_id, "input keyevent 20")
                time.sleep(0.3)
            shell(phone_id, "input keyevent 66")
            time.sleep(6)
            ss("s05_nameaddr_enter")

            is_our_biz, markers = _verify_business_panel(phone_id, business_name)
            log.info("    Verify: D=%d C=%d bar_biz=%s -> %s",
                     markers.get("Directions", -1), markers.get("Call", -1),
                     markers.get("biz_in_bar", False),
                     "OURS" if is_our_biz else "WRONG")
        else:
            log.info("    Still no match for '%s'", business_name)

    # Strategy 3 (LAST RESORT): Geo intent (shows search results, not full KP)
    if not is_our_biz:
        attempt += 1
        log.info("  [Attempt %d] Geo intent (search results, last resort)", attempt)
        q = business_name.replace(" ", "+").replace("&", "%26")
        shell(phone_id,
              f'am start -a android.intent.action.VIEW '
              f'-d "geo:0,0?q={q}" '
              f'com.google.android.apps.maps')
        time.sleep(8)
        ss("s06_geo_intent")

        # Use chunked verification (grep is broken on single-line XML)
        is_our_biz, markers = _verify_business_panel(phone_id, business_name)
        log.info("    Intent: D=%d C=%d bar_biz=%s -> %s",
                 markers.get("Directions", -1), markers.get("Call", -1),
                 markers.get("biz_in_bar", False),
                 "FOUND" if is_our_biz else "NOT FOUND")

    # ── Phase 2: Not reached -> fail ──
    if not is_our_biz:
        elapsed = time.time() - t0
        log.error("  Business panel NOT reached after %d attempts", attempt)
        log.error("  Final markers: %s", markers)
        return {"success": False, "duration_s": elapsed,
                "error": f"Business panel not reached after {attempt} attempts",
                "markers": markers}

    log.info("  >>> OUR BUSINESS PANEL CONFIRMED (strategy %d)", attempt)
    ss("s10_OUR_BUSINESS_CONFIRMED")

    # ── Phase 3: Interact with panel (only after XML-confirmed) ──

    # Helper: read XML and find a clickable button's center by content-desc keyword
    def _find_button_center(keyword: str) -> tuple:
        """Return (x, y) center of the first clickable node whose content-desc
        contains `keyword`. Returns (0, 0) if not found."""
        _xml_dump(phone_id)
        time.sleep(0.3)
        xml_text = ""
        for i in range(30):
            offset = i * 2000
            ok, chunk = shell(phone_id,
                f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
            if ok and chunk:
                xml_text += chunk
            if not ok or len(chunk) < 2000:
                break
        # Find clickable node with keyword in content-desc
        pattern = re.compile(
            r'<node[^>]*clickable="true"[^>]*content-desc="([^"]*' + re.escape(keyword) + r'[^"]*)"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*/?>',
            re.IGNORECASE)
        m = pattern.search(xml_text)
        if m:
            x1, y1, x2, y2 = int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            return ((x1 + x2) // 2, (y1 + y2) // 2)
        return (0, 0)

    # Helper: count pattern occurrences in XML via chunked read (grep broken on single-line)
    def _xml_count(pattern: str) -> int:
        _xml_dump(phone_id)
        time.sleep(0.3)
        count = 0
        for i in range(25):
            offset = i * 2000
            ok, chunk = shell(phone_id,
                f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
            if ok and chunk:
                count += chunk.count(pattern)
            if not ok or len(chunk) < 2000:
                break
        return count

    # 3a. Directions — find button dynamically from XML
    log.info("  [Interaction] Directions")
    dx, dy = _find_button_center("Directions")
    if dx == 0:
        # Fallback: search for any node (not just clickable) with Directions
        _xml_dump(phone_id); time.sleep(0.3)
        xml_text2 = ""
        for i in range(30):
            offset = i * 2000
            ok, chunk = shell(phone_id,
                f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
            if ok and chunk: xml_text2 += chunk
            if not ok or len(chunk) < 2000: break
        fb = re.search(
            r'content-desc="[^"]*Directions[^"]*"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_text2)
        if fb:
            dx = (int(fb.group(1)) + int(fb.group(3))) // 2
            dy = (int(fb.group(2)) + int(fb.group(4))) // 2
    log.info("    Directions button at (%d, %d)", dx, dy)
    shell(phone_id, f"input tap {dx} {dy}")
    time.sleep(8)  # wait for route to calculate + load
    ss("s11_directions_screen")

    # ── 3a-continued: Start Navigation ──────────────────────────────────
    # Find the Start button — full-width clickable bar at bottom of directions
    # screen. On Android Maps this has empty content-desc (icon-only).
    log.info("  [Interaction] Start Navigation")
    _xml_dump(phone_id); time.sleep(0.3)
    xml_nav = ""
    for i in range(35):
        offset = i * 2000
        ok, chunk = shell(phone_id,
            f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
        if ok and chunk: xml_nav += chunk
        if not ok or len(chunk) < 2000: break

    # Find the bottom-most wide clickable element (the Start button)
    start_x, start_y = 540, 1856  # fallback from probe
    best_y = 0
    for m in re.finditer(
        r'<node[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*/?>',
        xml_nav):
        x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        w = x2 - x1
        if w > 800 and y1 > best_y:  # wide bar at bottom
            best_y = y1
            start_x = (x1 + x2) // 2
            start_y = (y1 + y2) // 2
    log.info("    Start button at (%d, %d)", start_x, start_y)
    shell(phone_id, f"input tap {start_x} {start_y}")
    time.sleep(4)
    ss("s11b_navigation_started")

    # Handle first-time popups — Maps shows dialogs the first time you navigate.
    # Use a single XML read + fast keyword scan (avoid ~60s per _find_button_center).
    time.sleep(2)
    _xml_dump(phone_id); time.sleep(0.3)
    xml_popup = ""
    for i in range(25):
        offset = i * 2000
        ok, chunk = shell(phone_id,
            f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
        if ok and chunk: xml_popup += chunk
        if not ok or len(chunk) < 2000: break

    popup_labels = ["GOT IT", "OK", "Dismiss", "No thanks", "Continue", "Accept",
                     "SKIP", "Close", "Not now", "Later"]
    for label in popup_labels:
        fb_popup = re.search(
            rf'content-desc="[^"]*{re.escape(label)}[^"]*"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_popup, re.I)
        if fb_popup:
            pp_x = (int(fb_popup.group(1)) + int(fb_popup.group(3))) // 2
            pp_y = (int(fb_popup.group(2)) + int(fb_popup.group(4))) // 2
            log.info("    Dismissing popup: '%s' at (%d, %d)", label, pp_x, pp_y)
            shell(phone_id, f"input tap {pp_x} {pp_y}")
            time.sleep(1.5)
            break  # one popup at a time — return loop if more appear

    # Generic dismiss taps at common dialog positions (fallback for non-standard popups)
    for tap_y in [1600, 1400, 1800, 1200]:
        shell(phone_id, f"input tap 540 {tap_y}")
        time.sleep(0.8)
    ss("s11c_popups_cleared")

    # Let navigation run briefly — since GPS is at the business location,
    # Maps will quickly register "You have arrived" or similar
    log.info("    Navigation running (GPS at business — quick arrival)...")
    time.sleep(10)
    ss("s11d_navigating")

    # End navigation — use back button, then confirm exit if prompted
    shell(phone_id, "input keyevent 4")
    time.sleep(2)
    # If "Exit navigation" dialog appears, tap to confirm
    ex, ey = _find_button_center("Exit")
    if ex > 0:
        shell(phone_id, f"input tap {ex} {ey}")
        time.sleep(1.5)
    ss("s11e_navigation_ended")

    # Handle "How was your journey?" feedback card
    # Google Maps shows a post-navigation feedback prompt with thumbs up/down
    log.info("    Looking for post-navigation feedback...")
    time.sleep(3)
    _xml_dump(phone_id); time.sleep(0.3)
    xml_fb = ""
    for i in range(30):
        offset = i * 2000
        ok, chunk = shell(phone_id,
            f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
        if ok and chunk: xml_fb += chunk
        if not ok or len(chunk) < 2000: break

    # Find thumbs-up / good-feedback button
    for fb_kw in ["Thumbs up", "Good", "Like", "Satisfied", "Great", "Yes",
                   "Happy", "Positive"]:
        fx, fy = 0, 0
        fb_match = re.search(
            rf'content-desc="[^"]*{fb_kw}[^"]*"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_fb, re.I)
        if fb_match:
            fx = (int(fb_match.group(1)) + int(fb_match.group(3))) // 2
            fy = (int(fb_match.group(2)) + int(fb_match.group(4))) // 2
        if fx > 0:
            log.info("    Providing positive feedback via '%s' at (%d, %d)",
                     fb_kw, fx, fy)
            shell(phone_id, f"input tap {fx} {fy}")
            time.sleep(2)
            break
    else:
        # No explicit feedback button found — tap center for any implicit dismiss
        log.info("    No feedback widget found, dismissing any overlay")
        shell(phone_id, "input tap 540 1200")
        time.sleep(1)

    # Dismiss feedback card / return to Maps
    shell(phone_id, "input keyevent 4")
    time.sleep(3)
    ss("s12_back_to_panel")

    d2 = _xml_count("Directions")
    c2 = _xml_count("Call")
    log.info("    After navigation: Directions=%d, Call=%d", d2, c2)

    # 3b. Call — find button dynamically from XML
    log.info("  [Interaction] Call")
    cx, cy = _find_button_center("Call")
    if cx == 0:
        # Fallback: search for any node with Call (even if not clickable)
        _xml_dump(phone_id); time.sleep(0.3)
        xml_text3 = ""
        for i in range(30):
            offset = i * 2000
            ok, chunk = shell(phone_id,
                f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
            if ok and chunk: xml_text3 += chunk
            if not ok or len(chunk) < 2000: break
        fb2 = re.search(
            r'content-desc="[^"]*Call[^"]*"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_text3)
        if fb2:
            cx = (int(fb2.group(1)) + int(fb2.group(3))) // 2
            cy = (int(fb2.group(2)) + int(fb2.group(4))) // 2
    log.info("    Call button at (%d, %d)", cx, cy)
    shell(phone_id, f"input tap {cx} {cy}")
    time.sleep(3)
    ss("s13_call_dialog")

    shell(phone_id, "input tap 540 1000")  # tap center to dismiss
    time.sleep(1)
    shell(phone_id, "input keyevent 4")    # back as fallback
    time.sleep(3)

    still_open, check = _verify_business_panel(phone_id, business_name)
    if not still_open:
        log.warning("    Panel lost after Call — re-opening Maps")
        q = business_name.replace(" ", "+").replace("&", "%26")
        shell(phone_id,
              f'am start -a android.intent.action.VIEW '
              f'-d "geo:0,0?q={q}" '
              f'com.google.android.apps.maps')
        time.sleep(8)
        still_open, check = _verify_business_panel(phone_id, business_name)
        log.info("    Re-opened: %s", "OPEN" if still_open else "STILL LOST")

    # 3c. Scroll panel content
    log.info("  [Interaction] Scroll panel")
    shell(phone_id, "input swipe 540 1500 540 900 600")
    time.sleep(3)
    ss("s14_scrolled_panel")

    # 3d. Content engagement
    photos_count = _xml_count("Photos")
    reviews_count = _xml_count("Reviews")
    overview_count = _xml_count("Overview")
    log.info("    Content: Photos=%d Reviews=%d Overview=%d",
             photos_count, reviews_count, overview_count)

    if photos_count > 0:
        log.info("  [Interaction] Photos")
        shell(phone_id, "input tap 540 1400")
        time.sleep(4)
        ss("s15_photos")
        shell(phone_id, "input keyevent 4")
        time.sleep(2)

    if reviews_count > 0 or overview_count > 0:
        target = "Reviews" if reviews_count > 0 else "Overview"
        log.info("  [Interaction] %s", target)
        shell(phone_id, "input tap 540 1500")
        time.sleep(4)
        ss("s16_content")
        shell(phone_id, "input keyevent 4")
        time.sleep(2)

    # ── Phase 4: YouTube section ─────────────────────────────────────────
    log.info("  [Interaction] YouTube")
    try:
        # Use business name + area as search term for YouTube
        yt_search = business_name.split("&")[0].strip()
        if business_area:
            yt_search = f"{yt_search} {business_area}"
        yt_search_safe = yt_search.replace("&", "and").replace("'", "")[:60]

        # Open YouTube
        shell(phone_id, "am force-stop com.google.android.youtube")
        time.sleep(1)
        shell(phone_id, "am start -n com.google.android.youtube/.HomeActivity")
        time.sleep(5)
        ss("s20_youtube_home")

        # Tap search icon — magnifying glass, usually top-right
        shell(phone_id, "input tap 980 160")
        time.sleep(2)

        # Type search term
        shell(phone_id, f'input text "{yt_search_safe}"')
        time.sleep(3)
        ss("s21_youtube_search")

        # Submit search (ENTER)
        shell(phone_id, "input keyevent 66")
        time.sleep(5)
        ss("s22_youtube_results")

        # Tap first video result — usually near top of screen
        shell(phone_id, "input tap 540 600")
        time.sleep(15)  # watch video for ~15s
        ss("s23_youtube_watching")

        # Like the video if possible (optional engagement signal)
        # Like button is usually on the right side of video controls
        shell(phone_id, "input tap 540 1900")  # tap video area to show controls
        time.sleep(1)
        shell(phone_id, "input tap 540 1000")  # tap center to dismiss
        time.sleep(3)
        ss("s24_youtube_watched")

        # Back to YouTube home
        shell(phone_id, "input keyevent 4")
        time.sleep(2)
        shell(phone_id, "input keyevent 4")
        time.sleep(1)

        # Close YouTube
        shell(phone_id, "am force-stop com.google.android.youtube")
        log.info("    YouTube: watched video for '%s'", yt_search_safe)
    except Exception as e:
        log.warning("    YouTube section error (non-fatal): %s", e)

    # Final screenshot
    ss("s17_final_state")

    # Stop Maps cleanly
    shell(phone_id, "am force-stop com.google.android.apps.maps")

    elapsed = time.time() - t0
    log.info("  Maps KP complete in %.0fs", elapsed)
    return {"success": True, "duration_s": elapsed, "markers": markers}


# ── Legacy RPA-flow functions removed. Use run_maps_kp_shell() above. ──────────
# ══════════════════════════════════════════════════════════════════════════════════
# Activity runners
# ══════════════════════════════════════════════════════════════════════════════════

def run_youtube(client: GeelarKClient, phone_id: str,
                num_videos: int = 5, search_keyword: str = None,
                task_timeout: int = 300) -> dict:
    """
    Run the YouTube warmup flow on a phone.

    Returns {"success": bool, "task_id": str, "duration_s": float, "error": str}
    """
    log.info("[YouTube] %d videos, keyword=%s", num_videos, search_keyword)

    param_map = {
        "ExpectedNumberOfVideosViewed": num_videos,
    }
    if search_keyword:
        param_map["SearchKeyword"] = search_keyword

    try:
        task_id = client.run_custom_flow(
            YOUTUBE_TEMPLATE_ID, phone_id, param_map,
            task_name=f"YouTube warmup — {num_videos} videos",
        )
        log.info("  YouTube task submitted: %s", task_id)

        # Poll for completion
        deadline = time.time() + task_timeout
        while time.time() < deadline:
            time.sleep(10)
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                status = t.get("status", 0)
                if status == 3:  # Completed
                    cost = t.get("cost", 0)
                    log.info("  YouTube completed in ~%ds", cost)
                    return {"success": True, "task_id": task_id, "duration_s": cost}
                elif status == 4:  # Failed
                    fail_desc = t.get("failDesc", "Unknown error")
                    # "Not logged in" is expected sometimes
                    if "Not logged in" in str(fail_desc):
                        log.warning("  YouTube: account not logged in")
                        return {"success": False, "task_id": task_id,
                                "duration_s": 0, "error": "not_logged_in"}
                    log.error("  YouTube failed: %s", fail_desc)
                    return {"success": False, "task_id": task_id,
                            "duration_s": 0, "error": str(fail_desc)[:200]}
                elif status in (1, 2):  # Waiting or InProgress
                    pass
        log.warning("  YouTube timed out after %ds", task_timeout)
        return {"success": False, "task_id": task_id, "duration_s": task_timeout,
                "error": "timeout"}

    except Exception as e:
        log.error("  YouTube error: %s", e)
        return {"success": False, "task_id": "", "duration_s": 0, "error": str(e)[:200]}


def run_maps_browse(client: GeelarKClient, phone_id: str,
                    search_term: str, business_area: str = "",
                    log_dir: Path = None, task_timeout: int = 360) -> dict:
    """
    Run a Maps knowledge-panel interaction using shell/ADB commands.

    Uses run_maps_kp_shell() which:
    - Opens Maps, types business name, reads autosuggest from XML
    - Matches the correct business by name, taps its specific y-position
    - Verifies OUR business panel via search bar content-desc + action buttons
    - Only after XML-confirmed: Directions -> Call -> scroll -> Photos -> Reviews
    - Screenshots at every step for audit/debugging

    GPS must already be set on the phone before calling this.

    Returns {"success": bool, "duration_s": float, "error": str}
    """
    return run_maps_kp_shell(
        client, phone_id,
        business_name=search_term,
        business_area=business_area,
        log_dir=log_dir,
        task_timeout=task_timeout,
    )


def run_branded_drive(client: GeelarKClient, phone_id: str,
                      account_data: dict, task_timeout: int = 600) -> dict:
    """
    Run the branded search → navigation start → GPS drive → thumbs up sequence.

    Uses moving_gps.py for the animated GPS drive.

    Returns {"success": bool, "duration_s": float, "error": str}
    """
    from geelark_orchestrator.scripts.moving_gps import (
        set_gps, get_gps, ensure_phone_running, prep_maps,
        fetch_route_waypoints, animate_gps_route, shell,
    )

    business_name = account_data["business_name"]
    lat = float(account_data["business_lat"])
    lng = float(account_data["business_lng"])

    # Pick a branded keyword — day-seeded so we cycle through all 20
    session_day = account_data.get("state", {}).get("current_day", 1)
    branded_raw = account_data.get("branded_keywords", "")
    keywords = parse_csv_list(branded_raw)
    if not keywords:
        keywords = [business_name]

    # Rotate through keywords by day rather than pure random
    keyword = keywords[(session_day - 1) % len(keywords)]
    log.info("[Drive] '%s' -> %s (%s, %s)",
             keyword, business_name, lat, lng)

    # Pick a start point using branded-specific GPS points when available
    branded_pts_raw = account_data.get("nearby_points_branded", "")
    branded_pts = parse_csv_list(branded_pts_raw)
    nearby_raw = account_data.get("nearby_points", "")

    # Parse helper
    def _parse_pts(raw_list):
        pts = []
        for pt in raw_list:
            if "," in pt:
                parts = pt.split(",")
                try:
                    pts.append((float(parts[0].strip()), float(parts[1].strip())))
                except ValueError:
                    pass
        return pts

    nearby = _parse_pts(branded_pts) or _parse_pts(parse_csv_list(nearby_raw))

    if not nearby:
        start_lat = lat + 0.01
        start_lng = lng
    else:
        start_lat, start_lng = random.choice(nearby)

    # Jitter GPS so no two drives start from exactly the same spot
    start_lat, start_lng = jitter_gps(start_lat, start_lng)

    try:
        # 1. Set GPS to start point
        log.info("  Setting GPS to start: %s, %s", start_lat, start_lng)
        set_gps(phone_id, start_lat, start_lng)
        time.sleep(2)

        # 2. Open Maps with branded search via intent (bypasses RPA entirely)
        #    Brand-name search on Android 14 Maps auto-opens the knowledge panel
        log.info("  Opening Maps with branded search: %s", keyword)
        search_q = keyword.replace(" ", "+").replace("&", "%26")
        shell(phone_id,
              f'am start -a android.intent.action.VIEW '
              f'-d "geo:0,0?q={search_q}" '
              f'com.google.android.apps.maps')
        log.info("  Maps intent fired, waiting for knowledge panel...")
        time.sleep(10)  # Wait for Maps + knowledge panel to load

        # Verify knowledge panel via chunked XML (grep broken on single-line)
        is_open, kp_markers = _verify_business_panel(phone_id, business_name)
        log.info("  KP verification: D=%d C=%d biz_in_bar=%s -> %s",
                 kp_markers.get("Directions", -1), kp_markers.get("Call", -1),
                 kp_markers.get("biz_in_bar", False),
                 "OPEN" if is_open else "NOT OPEN")

        if not is_open:
            log.warning("  Knowledge panel not confirmed for drive, continuing anyway")

        # Tap Directions — find button dynamically from XML
        _xml_dump(phone_id); time.sleep(0.3)
        xml_drive = ""
        for i in range(30):
            offset = i * 2000
            ok, chunk = shell(phone_id,
                f"head -c $(({offset} + 2000)) /sdcard/ui.xml | tail -c 2000")
            if ok and chunk: xml_drive += chunk
            if not ok or len(chunk) < 2000: break
        # Find Directions button by content-desc (prefer clickable, fallback to any)
        dir_btn = re.search(
            r'<node[^>]*clickable="true"[^>]*content-desc="[^"]*Directions[^"]*"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_drive)
        if not dir_btn:
            dir_btn = re.search(
                r'content-desc="[^"]*Directions[^"]*"[^>]*'
                r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_drive)
        if dir_btn:
            dx2 = (int(dir_btn.group(1)) + int(dir_btn.group(3))) // 2
            dy2 = (int(dir_btn.group(2)) + int(dir_btn.group(4))) // 2
        else:
            dx2, dy2 = 200, 1642  # fallback from probe
        log.info("  Tapping Directions at (%d, %d)...", dx2, dy2)
        shell(phone_id, f"input tap {dx2} {dy2}")
        time.sleep(5)
        log.info("  On directions screen, proceeding to pop-up dismissal")

        # 3. Navigate "Dismiss" / "Okay" pop-ups
        # Tap any dialog buttons that appear when starting navigation
        time.sleep(3)
        for dismiss_text in ["Dismiss", "Okay", "GOT IT", "OK", "Close", "No thanks"]:
            try:
                ok, _ = shell(phone_id,
                    f"input tap 540 1200")  # bottom center (common dialog position)
                time.sleep(1)
            except Exception:
                pass

        # 4. Fetch route and animate GPS drive
        log.info("  Fetching route: %s,%s → %s,%s",
                 start_lat, start_lng, lat, lng)
        route = fetch_route_waypoints(start_lat, start_lng, lat, lng)
        distance = route.get("distance_km", 0)
        log.info("  Route: %.1f km, %d intersections",
                 distance, len(route.get("intersections", [])))

        log.info("  Animating GPS drive...")
        from geelark_orchestrator.scripts.moving_gps import (
            resample_waypoints, compute_speed_profile,
            MAX_WAYPOINTS, MIN_WAYPOINT_INTERVAL_M, MAX_WAYPOINT_INTERVAL_M,
        )
        # Resample waypoints and build speed profile
        route_dist_m = distance * 1000.0
        wp_interval = route_dist_m / MAX_WAYPOINTS
        wp_interval = max(MIN_WAYPOINT_INTERVAL_M,
                          min(MAX_WAYPOINT_INTERVAL_M, wp_interval))
        waypoints = resample_waypoints(route["raw_coords"], interval_m=wp_interval)
        profile = compute_speed_profile(waypoints, route["intersections"])
        log.info("  %d waypoints, interval=%.0fm", len(waypoints), wp_interval)

        # Log dir for screenshots
        drive_log_dir = Path("logs") / "warming" / f"drive_{account_data['account_id']}_{datetime.now():%Y%m%d_%H%M%S}"
        drive_log_dir.mkdir(parents=True, exist_ok=True)

        anim_result = animate_gps_route(
            client=client,
            phone_id=phone_id,
            waypoint_profile=profile,
            log_dir=drive_log_dir,
            start_lat=start_lat,
            start_lng=start_lng,
            end_lat=lat,
            end_lng=lng,
            business_name=business_name,
            waypoint_interval_m=wp_interval,
            fast=False,
        )

        # 5. Arrival
        arrived = anim_result.get("waypoints_animated", 0) > 0
        log.info("  Drive done: %d micro-steps, %.0fs, arrived=%s",
                 anim_result.get("micro_steps", 0),
                 anim_result.get("total_time_s", 0), arrived)

        duration = anim_result.get("total_time_s", 0)
        return {"success": True, "duration_s": duration,
                "distance_km": distance, "keyword": keyword}

    except Exception as e:
        log.error("  Branded drive error: %s", e)
        return {"success": False, "duration_s": 0, "error": str(e)[:200]}


# ══════════════════════════════════════════════════════════════════════════════════
# Session orchestrator
# ══════════════════════════════════════════════════════════════════════════════════

def run_session(account_data: dict, dry_run: bool = False) -> dict:
    """
    Run a complete warming session for one account.

    Returns session result dict with all activity outcomes.
    """
    account_id = account_data["account_id"]
    phone_id = account_data["geelark_phone_id"]
    business_name = account_data["business_name"]
    plan = account_data.get("plan") or get_todays_activity_plan(account_data["state"])
    state = account_data.get("state") or load_state(account_id)

    log.info("=" * 60)
    log.info("SESSION: %s — Day %d/%d (%s cohort)",
             account_id, plan["day"], plan["total_days"], plan["cohort"])
    log.info("  Account:  %s", account_data["account_email"])
    log.info("  Business: %s", business_name)
    activities_list = [a["type"] for a in plan["activities"]]
    if plan["drive"]:
        activities_list.append("branded_drive")
    log.info("  Plan:     %s", ", ".join(activities_list))
    log.info("=" * 60)

    if dry_run:
        log.info("DRY RUN — no activities will be executed")
        return {"success": True, "dry_run": True, "plan": plan}

    client = GeelarKClient()
    session_start = time.time()
    results = []
    errors = []

    # ── 1. Start phone ──────────────────────────────────────────────────────────
    log.info("Starting phone %s...", phone_id)
    try:
        from geelark_orchestrator.scripts.moving_gps import ensure_phone_running
        ok, viewer_url = ensure_phone_running(client, phone_id)
        if not ok:
            raise RuntimeError(f"Failed to start phone {phone_id}")
        log.info("  Phone running: %s", viewer_url[:80])
    except Exception as e:
        log.error("Phone start failed: %s", e)
        record_session(state, time.time() - session_start,
                       [], False, error=str(e))
        return {"success": False, "error": str(e), "phone_id": phone_id}

    # Also rotate the StreamVia proxy IP for a fresh mobile IP
    try:
        import requests as req
        control_url = os.environ.get("GEELARK_PROXY_CONTROL_URL", "")
        if control_url:
            resp = req.get(f"{control_url}?changeipunique=true", timeout=30)
            log.info("  Proxy IP rotated: %s", resp.text.strip()[:100])
    except Exception as e:
        log.warning("  Proxy rotation failed (non-fatal): %s", e)

    # ── 2. Set GPS + prep Maps ─────────────────────────────────────────────────
    log.info("Setting GPS and prepping Maps...")
    try:
        from geelark_orchestrator.scripts.moving_gps import set_gps, prep_maps, shell

        # Use the expanded GPS pool (80 scattered + existing points)
        resources = get_expanded_resources(account_data)
        gps_pool = resources["gps_points"]

        if gps_pool:
            gps_lat, gps_lng = random.choice(gps_pool)
            gps_lat, gps_lng = jitter_gps(gps_lat, gps_lng)
        else:
            gps_lat = float(account_data.get("business_lat", "51.5"))
            gps_lng = float(account_data.get("business_lng", "-0.1"))
            gps_lat, gps_lng = jitter_gps(gps_lat, gps_lng)

        log.info("  GPS -> %s, %s (pool: %d pts, jitter applied)",
                 gps_lat, gps_lng, len(gps_pool))
        set_gps(phone_id, gps_lat, gps_lng)
        time.sleep(2)

        # Prep Maps permissions
        prep_maps(phone_id)
        log.info("  Maps permissions OK")

    except Exception as e:
        log.error("GPS/prep error: %s", e)
        # Non-fatal — continue with session

    # ── 3. Run YouTube ──────────────────────────────────────────────────────────
    yt = plan["activities"][0]  # YouTube is always first
    # Occasionally inject a branded keyword for variety (30% chance)
    yt_keyword = None
    branded_raw = account_data.get("branded_keywords", "")
    branded_kws = parse_csv_list(branded_raw)
    if branded_kws and random.random() < 0.3:
        yt_keyword = random.choice(branded_kws)
        log.info("[YouTube] Keyword: %s", yt_keyword)
    yt_result = run_youtube(client, phone_id, num_videos=yt.get("videos", 5),
                            search_keyword=yt_keyword)
    results.append(("youtube", yt_result))
    if not yt_result.get("success"):
        errors.append(f"youtube: {yt_result.get('error', 'unknown')}")

    # ── 4. Run Maps browse ─────────────────────────────────────────────────────
    # Shell-based approach: searches the brand name via geo intent, verifies
    # knowledge panel via uiautomator XML grep (Directions + Call + business name),
    # then shell-taps interactions only after XML-confirmed panel is open.
    log.info("[Maps] Brand-name KP: '%s'", business_name)
    maps_log_dir = Path("logs") / "warming" / f"maps_{account_id}_{datetime.now():%Y%m%d_%H%M%S}"
    maps_result = run_maps_browse(
        client, phone_id,
        search_term=business_name,
        business_area=account_data.get("business_area", ""),
        log_dir=maps_log_dir,
    )
    results.append(("maps_browse", maps_result))
    if not maps_result.get("success"):
        errors.append(f"maps_browse: {maps_result.get('error', 'unknown')}")
        log.warning("Maps KP for %s: %s — continuing session",
                    business_name, maps_result.get("error", "unknown")[:100])

    # ── 5. Optional: Knowledge panels (generic local business browsing) ─────────
    kp_activities = [a for a in plan["activities"] if a["type"] == "knowledge_panels"]
    if kp_activities:
        kp = kp_activities[0]
        num_kp = kp.get("num_businesses", 1)
        log.info("[KP] Generic local browsing: %d search(es)", num_kp)

        # Use expanded KP keyword pool for generic local discovery
        kp_pool = resources.get("kp_keywords", [])
        if kp_pool:
            picks = pick_varied(kp_pool, session_day, min_count=num_kp + 2)
            log.info("  KP keywords: %d expanded available", len(kp_pool))
        else:
            picks = ["coffee near me", "restaurant near me"]

        # Only run 1 KP search per session (keeps it simple, realistic)
        kp_search = picks[0]
        log.info("  [KP] '%s'", kp_search)
        # Jitter GPS so the local search looks like a slightly different location
        kp_lat, kp_lng = jitter_gps(float(account_data.get("business_lat", "51.5")),
                                     float(account_data.get("business_lng", "-0.1")),
                                     metres=random.randint(200, 800))
        # Re-set GPS for the generic search so it looks like we're exploring
        try:
            from geelark_orchestrator.scripts.moving_gps import set_gps as _set_gps
            _set_gps(phone_id, kp_lat, kp_lng)
            time.sleep(2)
        except Exception:
            pass
        kp_log_dir = Path("logs") / "warming" / f"kp_{account_id}_{datetime.now():%Y%m%d_%H%M%S}"
        kp_result = run_maps_browse(
            client, phone_id,
            search_term=kp_search,
            business_area=account_data.get("business_area", ""),
            log_dir=kp_log_dir,
        )
        results.append(("knowledge_panels", kp_result))

    # ── 6. Optional: Branded drive ─────────────────────────────────────────────
    if plan.get("drive"):
        drive_result = run_branded_drive(client, phone_id, account_data)
        results.append(("branded_drive", drive_result))
        if drive_result.get("success"):
            record_drive_completed(state)
        else:
            errors.append(f"drive: {drive_result.get('error', 'unknown')}")

    # ── 7. Stop phone ───────────────────────────────────────────────────────────
    log.info("Stopping phone %s...", phone_id)
    try:
        client.stop_phone(phone_id)
        log.info("  Phone stopped")
    except Exception as e:
        log.warning("  Phone stop failed (non-fatal): %s", e)

    # ── 8. Record session ───────────────────────────────────────────────────────
    total_duration = time.time() - session_start
    success = len(errors) == 0
    activities_done = [r[0] for r in results if r[1].get("success")]

    record_session(state, total_duration, activities_done, success,
                   error="; ".join(errors) if errors else None)

    log.info("SESSION COMPLETE: %s in %.0fs — %s",
             account_id, total_duration, "OK" if success else f"{len(errors)} errors")
    if errors:
        for e in errors:
            log.warning("  Error: %s", e)

    return {
        "success": success,
        "account_id": account_id,
        "duration_s": total_duration,
        "activities": activities_done,
        "errors": errors,
        "results": results,
    }


# ══════════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════════

def load_account_data(account_id: str) -> dict | None:
    """Load account data from CSV."""
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["account_id"] == account_id:
                state = load_state(account_id)
                if state.get("current_day", 0) == 0:
                    state = init_account(account_id, cohort="standard")
                plan = get_todays_activity_plan(state)
                return {
                    "account_id": account_id,
                    "account_email": row["account_email"],
                    "geelark_phone_id": row["geelark_phone_id"],
                    "business_name": row["business_name"],
                    "business_lat": row.get("business_lat", ""),
                    "business_lng": row.get("business_lng", ""),
                    "business_area": row.get("business_area", ""),
                    "nearby_points": row.get("nearby_points", ""),
                    "onsite_points": row.get("onsite_points", ""),
                    "branded_keywords": row.get("branded_keywords", ""),
                    "home_lat": row.get("home_lat", ""),
                    "home_lng": row.get("home_lng", ""),
                    "state": state,
                    "plan": plan,
                }
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Google Review Warming — Daily Session Runner"
    )
    parser.add_argument("--account", type=str, default=None,
                        help="Run session for a single account (e.g. acc_004)")
    parser.add_argument("--all", action="store_true",
                        help="Run sessions for ALL eligible accounts")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview plan without executing")
    parser.add_argument("--test", action="store_true",
                        help="Test on phone 629021249506377828")
    parser.add_argument("--cohort", type=str, default="standard",
                        choices=["fast", "standard"],
                        help="Default cohort for new accounts")
    parser.add_argument("--list", action="store_true",
                        help="List all eligible accounts and their plans")
    parser.add_argument("--init", type=str, default=None,
                        help="Initialize warming state for an account (--init acc_004)")
    args = parser.parse_args()

    # ── Init mode ────────────────────────────────────────────────────────────────
    if args.init:
        account_data = load_account_data(args.init)
        if not account_data:
            print(f"Account {args.init} not found in CSV")
            return
        state = init_account(args.init, cohort=args.cohort)
        plan = get_todays_activity_plan(state)
        print(f"Initialized {args.init}:")
        print(f"  Cohort:     {args.cohort} ({COHORTS[args.cohort]['total_days']} days)")
        print(f"  Start date: {state['warmup_start_date']}")
        print(f"  Day 1 plan: {plan['activities']}")
        return

    # ── List mode ────────────────────────────────────────────────────────────────
    if args.list:
        eligible = get_eligible_accounts(str(CSV_PATH))
        print(f"Eligible for warming today: {len(eligible)} accounts\n")
        for a in eligible:
            plan = a["plan"]
            acts = [ac["type"] for ac in plan["activities"]]
            if plan["drive"]:
                acts.append("DRIVE")
            print(f"  {a['account_id']:8s}  day {plan['day']:2d}/{plan['total_days']:2d}  "
                  f"{plan['cohort']:8s}  {a['business_name'][:40]:40s}  "
                  f"{', '.join(acts)}")
        return

    # ── Test mode ────────────────────────────────────────────────────────────────
    if args.test:
        print(f"Testing on phone {TEST_PHONE_ID}...")
        test_data = {
            "account_id": "test",
            "account_email": "vincehawthorne92@gmail.com",
            "geelark_phone_id": TEST_PHONE_ID,
            "business_name": "Sidcup Plumbing & Heating",
            "business_lat": "51.427684",
            "business_lng": "0.101638",
            "business_area": "sidcup",
            "nearby_points": "51.43319,0.110021|51.437879,0.098076|51.424351,0.098199",
            "onsite_points": "51.427618,0.101708|51.427695,0.101605",
            "branded_keywords": "Sidcup Plumbing & Heating|plumber Sidcup",
            "home_lat": "51.421604",
            "home_lng": "0.060148",
            "state": init_account("test", cohort="standard"),
        }
        test_data["plan"] = get_todays_activity_plan(test_data["state"])
        if args.dry_run:
            test_data["plan"]["drive"] = {"type": "branded_drive", "duration_min": 7}
        result = run_session(test_data, dry_run=args.dry_run)
        print(f"\nTest result: {'OK' if result.get('success') else 'FAILED'}")
        if result.get("errors"):
            for e in result["errors"]:
                print(f"  Error: {e}")
        return

    # ── Single account mode ──────────────────────────────────────────────────────
    if args.account:
        account_data = load_account_data(args.account)
        if not account_data:
            print(f"Account {args.account} not found in CSV")
            return

        if args.dry_run:
            plan = account_data["plan"]
            print(f"DRY RUN — {args.account}: Day {plan['day']}/{plan['total_days']}")
            for act in plan["activities"]:
                print(f"  {act['type']}: ~{act['duration_min']} min")
            if plan["drive"]:
                print(f"  {plan['drive']['type']}: ~{plan['drive']['duration_min']} min")
            return

        result = run_session(account_data, dry_run=False)
        print(f"\nResult: {'OK' if result.get('success') else 'FAILED'} "
              f"in {result.get('duration_s', 0):.0f}s")
        for r in result.get("results", []):
            name, data = r
            status = "OK" if data.get("success") else "FAIL"
            dur = data.get("duration_s", 0)
            print(f"  {name}: {status} ({dur:.0f}s)")
        return

    # ── All eligible mode ────────────────────────────────────────────────────────
    if args.all:
        eligible = get_eligible_accounts(str(CSV_PATH))
        if not eligible:
            print("No accounts eligible for warming today")
            return

        print(f"Running warming sessions for {len(eligible)} eligible accounts...")
        summary = []
        for data in eligible:
            result = run_session(data, dry_run=args.dry_run)
            summary.append(result)
            # Brief pause between accounts
            time.sleep(5)

        print(f"\n{'='*60}")
        print(f"BATCH COMPLETE: {len(summary)} accounts")
        ok = sum(1 for r in summary if r.get("success"))
        fail = len(summary) - ok
        print(f"  OK: {ok}, Failed: {fail}")
        for r in summary:
            status = "OK" if r.get("success") else "FAIL"
            dur = r.get("duration_s", 0)
            print(f"  {r['account_id']}: {status} ({dur:.0f}s)")
        return

    # ── Default: show help ───────────────────────────────────────────────────────
    parser.print_help()


if __name__ == "__main__":
    main()
