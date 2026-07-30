#!/usr/bin/env python3
"""
Interact with an already-open Google Maps business listing using shell commands.
Assumes the business detail page is open on screen.

Order: Reviews → Directions → Website → Photos → Call (Photos + Call lose the business page)
Usage: python _business_interact.py <phone_id> <business_name> <label>
"""
import sys, time, re, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
BUSINESS = sys.argv[2]
LABEL = sys.argv[3] if len(sys.argv) > 3 else "unknown"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return (r.get("output", "") or "")

def tap(x, y):
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "input tap %d %d" % (x, y)})

def get_screen():
    out = sh("wm size")
    m = re.search(r"(\d+)x(\d+)", out)
    if m: return int(m.group(1)), int(m.group(2))
    return 720, 1440

def dump_ui(tag):
    path = "/sdcard/interact_%s.xml" % tag
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump %s" % path})
    time.sleep(1.5)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat %s" % path})
    return (r.get("output", "") or "")

def find_text(xml_str, text_query):
    xml_start = xml_str.find("<?xml") if xml_str else -1
    if xml_start < 0: return None
    try:
        root = ET.fromstring(xml_str[xml_start:])
    except:
        return None
    ql = text_query.lower()
    best = None
    best_score = 999
    for node in root.iter("node"):
        t = (node.get("text", "") or "").strip().lower()
        cd = (node.get("content-desc", "") or "").strip().lower()
        for field in [t, cd]:
            if not field: continue
            score = None
            if field == ql: score = 0
            elif field.startswith(ql): score = 1
            elif ql in field: score = 2
            if score is not None and score < best_score:
                best_score = score
                bounds = node.get("bounds", "")
                try:
                    x1, y1, x2, y2 = map(int, bounds.replace("[", "").replace("]", ",").rstrip(",").split(","))
                    best = ((x1 + x2) // 2, (y1 + y2) // 2)
                except:
                    pass
    return best

def get_visible_texts(xml_str):
    texts = []
    xml_start = xml_str.find("<?xml") if xml_str else -1
    if xml_start < 0: return texts
    try:
        root = ET.fromstring(xml_str[xml_start:])
        for node in root.iter("node"):
            t = (node.get("text", "") or "").strip()
            if t: texts.append(t)
    except: pass
    return texts

def is_on_biz_page():
    xml = dump_ui("biz_check")
    texts = get_visible_texts(xml)
    joined = " ".join(texts)
    biz_words = BUSINESS.split()
    partial = " ".join(biz_words[:2]).lower() if len(biz_words) >= 2 else BUSINESS.lower()
    has_biz = BUSINESS.lower()[:20] in joined.lower() or partial in joined.lower()
    markers = ["Directions", "Call", "Save", "Overview", "Reviews", "Photos"]
    found = [m for m in markers if m.lower() in joined.lower()]
    return has_biz or len(found) >= 2, texts, xml

def recover_to_biz_page(w, h, max_attempts=5):
    """Try to get back to Maps business page from wherever we are."""
    for attempt in range(max_attempts):
        on_biz, texts, xml = is_on_biz_page()
        if on_biz:
            return True, xml

        focus = sh("dumpsys window | grep mCurrentFocus").strip()

        if "dialer" in focus.lower() or "contacts" in focus.lower():
            sh("input keyevent KEYCODE_BACK")
            time.sleep(1.5)
            continue

        if "launcher" in focus.lower():
            # Reopen Maps — will go to last state
            sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1")
            time.sleep(3)
            continue

        if "maps" in focus.lower():
            joined = " ".join(texts)
            if "Search here" in joined:
                # Maps home — can't recover without re-running search
                return False, xml
            # Might be on a sub-page, try Back
            sh("input keyevent KEYCODE_BACK")
            time.sleep(1.5)
            continue

        # Unknown — try Back
        sh("input keyevent KEYCODE_BACK")
        time.sleep(1)

    return False, xml


print("=" * 60)
print("BUSINESS INTERACT: %s" % LABEL)
print("Phone: %s | Business: %s" % (PHONE, BUSINESS))
print("=" * 60)

w, h = get_screen()

# ── Verify we're on the business page ──
print("\n--- Verifying business page ---")
on_biz, texts, xml = is_on_biz_page()
joined = " ".join(texts)
markers = ["Directions", "Call", "Save", "Overview", "Reviews", "Photos"]
found_markers = [m for m in markers if m.lower() in joined.lower()]
print("  Business: %s | Markers: %s" % (on_biz, found_markers))

if not on_biz:
    print("[FAIL] Not on business page — aborting")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# INTERACTION 1: REVIEWS (stays on business page)
# ═══════════════════════════════════════════════════════════════
print("\n--- INTERACTION 1: Reviews ---")

review_coords = find_text(xml, "Reviews")
if not review_coords:
    for i in range(4):
        if review_coords: break
        sh("input swipe %d %d %d %d 400" % (w // 2, int(h * 0.58), w // 2, int(h * 0.22)))
        time.sleep(1.5)
        xml = dump_ui("rev_scroll_%d" % i)
        review_coords = find_text(xml, "Reviews")

if review_coords:
    print("  Tapping Reviews at %s" % (review_coords,))
    tap(review_coords[0], review_coords[1])
    time.sleep(3)

    for i in range(3):
        sh("input swipe %d %d %d %d 500" % (w // 2, int(h * 0.70), w // 2, int(h * 0.30)))
        time.sleep(3)
        print("  Review scroll %d/3" % (i + 1))

    xml = dump_ui("rev_expand")
    more_coords = find_text(xml, "More")
    if more_coords:
        print("  Expanding a review...")
        tap(more_coords[0], more_coords[1])
        time.sleep(2)
        sh("input swipe %d %d %d %d 300" % (w // 2, int(h * 0.60), w // 2, int(h * 0.35)))
        time.sleep(1.5)

    sh("input keyevent KEYCODE_BACK")
    time.sleep(2)
    print("[OK] Reviews — browsed")
else:
    print("[SKIP] Reviews — tab not found")

# ═══════════════════════════════════════════════════════════════
# INTERACTION 2: DIRECTIONS (stays on business page)
# ═══════════════════════════════════════════════════════════════
print("\n--- INTERACTION 2: Directions ---")

on_biz, texts, xml = is_on_biz_page()
if not on_biz:
    print("  Lost business page after Reviews, recovering...")
    on_biz, xml = recover_to_biz_page(w, h)

if on_biz:
    dir_coords = find_text(xml, "Directions")
    if dir_coords:
        print("  Tapping Directions at %s" % (dir_coords,))
        tap(dir_coords[0], dir_coords[1])
        time.sleep(3)

        # Select Drive tab (car) — Maps may default to walk/transit
        print("  Selecting Drive...")
        xml = dump_ui("dir_mode")
        drive_coords = find_text(xml, "Drive")
        if drive_coords:
            tap(drive_coords[0], drive_coords[1])
            time.sleep(2)
            print("  Tapped Drive tab")
        else:
            # Proportional fallback: Drive is usually first tab, ~15% from left
            tap(int(w * 0.15), int(h * 0.085))
            time.sleep(2)
            print("  Proportional Drive tap")

        print("  Waiting for route...")
        time.sleep(6)

        xml = dump_ui("dir_route")
        route_texts = get_visible_texts(xml)
        route_info = [t for t in route_texts if any(
            w in t.lower() for w in ["min", "mile", "km", "hr", "walk", "drive", "transit"]
        )]
        if route_info:
            print("  Route: %s" % ", ".join(route_info[:5]))

        sh("input keyevent KEYCODE_BACK")
        time.sleep(2)
        print("[OK] Directions — route viewed")
    else:
        print("[SKIP] Directions — button not found")
else:
    print("[SKIP] Directions — lost business page")

# ═══════════════════════════════════════════════════════════════
# INTERACTION 3: WEBSITE (opens browser — Back returns to Maps)
# ═══════════════════════════════════════════════════════════════
print("\n--- INTERACTION 3: Website ---")

on_biz, texts, xml = is_on_biz_page()
if not on_biz:
    on_biz, xml = recover_to_biz_page(w, h)

website_done = False
if on_biz:
    web_coords = find_text(xml, "Website")
    if not web_coords:
        # Website button might be further down or hidden — try a small scroll
        sh("input swipe %d %d %d %d 200" % (w // 2, int(h * 0.45), w // 2, int(h * 0.30)))
        time.sleep(1.5)
        xml = dump_ui("web_scroll")
        web_coords = find_text(xml, "Website")

    if web_coords:
        print("  Tapping Website at %s" % (web_coords,))
        tap(web_coords[0], web_coords[1])
        time.sleep(5)  # Wait for browser to load the page

        # Back to return to Maps
        sh("input keyevent KEYCODE_BACK")
        time.sleep(2)
        # May need second Back if browser has multiple tabs
        focus = sh("dumpsys window | grep mCurrentFocus").strip()
        if "maps" not in focus.lower():
            sh("input keyevent KEYCODE_BACK")
            time.sleep(1)
        print("[OK] Website — opened")
        website_done = True
    else:
        print("[SKIP] Website — not available for this business")
else:
    print("[SKIP] Website — lost business page")

# ═══════════════════════════════════════════════════════════════
# INTERACTION 4: PHOTOS (tap tab — will lose page after Back)
# ═══════════════════════════════════════════════════════════════
print("\n--- INTERACTION 4: Photos ---")

on_biz, texts, xml = is_on_biz_page()
if not on_biz:
    on_biz, xml = recover_to_biz_page(w, h)

if on_biz:
    photo_coords = find_text(xml, "Photos")
    if not photo_coords:
        sh("input swipe %d %d %d %d 300" % (w // 2, int(h * 0.50), w // 2, int(h * 0.25)))
        time.sleep(1.5)
        xml = dump_ui("photo_scroll")
        photo_coords = find_text(xml, "Photos")

    if photo_coords:
        print("  Tapping Photos at %s" % (photo_coords,))
        tap(photo_coords[0], photo_coords[1])
        time.sleep(3)

        for i in range(3):
            sh("input swipe %d %d %d %d 300" % (int(w * 0.75), h // 2, int(w * 0.25), h // 2))
            time.sleep(2.5)
            print("  Photo swipe %d/3" % (i + 1))

        sh("input keyevent KEYCODE_BACK")
        time.sleep(2)
        print("[OK] Photos — 3 photos browsed")
    else:
        print("[SKIP] Photos — tab not found")
else:
    print("[SKIP] Photos — lost business page")

# ═══════════════════════════════════════════════════════════════
# INTERACTION 5: PHONE CALL (LAST — opens dialer, can't recover)
# ═══════════════════════════════════════════════════════════════
print("\n--- INTERACTION 5: Phone Call ---")

on_biz, texts, xml = is_on_biz_page()
if not on_biz:
    on_biz, xml = recover_to_biz_page(w, h)

if on_biz:
    call_coords = find_text(xml, "Call")
    if call_coords:
        print("  Tapping Call at %s" % (call_coords,))
        tap(call_coords[0], call_coords[1])
        time.sleep(3)

        xml = dump_ui("call_screen")
        call_texts = get_visible_texts(xml)
        phone_numbers = [t for t in call_texts if re.search(r"[\d\s\-\(\)]{7,}", t)]
        if phone_numbers:
            print("  Number visible: %s" % phone_numbers[:3])

        # Back to dismiss dialer — may go to search results, that's OK
        sh("input keyevent KEYCODE_BACK")
        time.sleep(1.5)
        # One more in case dialer has layers
        focus = sh("dumpsys window | grep mCurrentFocus").strip()
        if "dialer" in focus.lower() or "contacts" in focus.lower():
            sh("input keyevent KEYCODE_BACK")
            time.sleep(1)

        print("[OK] Phone Call — number viewed, not dialed")
    else:
        print("[SKIP] Phone Call — button not found")
else:
    print("[SKIP] Phone Call — lost business page")

# ═══════════════════════════════════════════════════════════════
print()
print("=" * 60)
print("INTERACTIONS COMPLETE: %s" % LABEL)
on_biz, texts, xml = is_on_biz_page()
print("Final screen: %s" % ", ".join(texts[:8]).encode("ascii", errors="replace").decode())
print("=" * 60)
