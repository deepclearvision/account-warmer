#!/usr/bin/env python3
"""Run Steps 1-6c with scroll-for-button handling. Usage: python _run_steps_1_to_6c.py <phone_id> <lat> <lng>"""
import sys, time, re, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
LAT = sys.argv[2]
LNG = sys.argv[3]
GPS = "com.theappninjas.fakegpsjoystick"
MAPS = "com.google.android.apps.maps"
WAIT = 8

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return r.get("output", "") or ""

def tap(x, y):
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "input tap %d %d" % (x, y)})

def get_focus():
    return sh("dumpsys window | grep mCurrentFocus").strip()

def get_screen():
    out = sh("wm size")
    m = re.search(r"(\d+)x(\d+)", out)
    if m: return int(m.group(1)), int(m.group(2))
    return 720, 1440

def dump_ui(tag):
    path = "/sdcard/%s_dump.xml" % tag
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump %s" % path})
    time.sleep(1.5)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat %s" % path})
    return r.get("output", "")

def get_visible_texts(xml_str):
    """Return list of all visible text/content-desc from UI dump."""
    texts = []
    if not xml_str or not xml_str.strip().startswith("<"): return texts
    try:
        root = ET.fromstring(xml_str[xml_str.find("<?"):])
        for node in root.iter("node"):
            t = (node.get("text", "") or "").strip()
            cd = (node.get("content-desc", "") or "").strip()
            if t: texts.append(t)
            if cd and cd != t: texts.append(cd)
    except: pass
    return texts

def find_and_tap(xml_str, queries, step_label):
    """Try each query in order, tap first match. Returns True if found+tapped."""
    if isinstance(queries, str):
        queries = [queries]
    # Skip non-XML prefix
    xml_start = xml_str.find("<?xml")
    if xml_start < 0: return False
    xml_clean = xml_str[xml_start:]
    try:
        root = ET.fromstring(xml_clean)
    except: return False

    best = None; best_score = 999; best_query = None
    for q in queries:
        ql = q.lower()
        for node in root.iter("node"):
            t = (node.get("text", "") or "").strip().lower()
            cd = (node.get("content-desc", "") or "").strip().lower()
            score = None
            if t == ql or cd == ql: score = 0
            elif t.startswith(ql) or cd.startswith(ql): score = 1
            elif ql in t or ql in cd: score = 2
            if score is not None and score < best_score:
                best_score = score
                bounds = node.get("bounds", "")
                try:
                    parts = bounds.replace("[", "").replace("]", ",").rstrip(",").split(",")
                    x1, y1, x2, y2 = map(int, parts)
                    best = ((x1 + x2) // 2, (y1 + y2) // 2)
                    best_query = q
                except: pass
    if best:
        print("  [%s] Tapped \"%s\" at %s" % (step_label, best_query, best))
        tap(best[0], best[1])
        return True
    else:
        print("  [%s] No match for: %s" % (step_label, queries[:3]))
        texts = get_visible_texts(xml_clean)
        safe = ", ".join(texts[:20])
        try: print("    Visible: %s" % safe)
        except: print("    Visible: (unicode issue, %d texts found)" % len(texts))
        return False

def find_clickable_in(xml_str, text_queries, step_label):
    """Find a clickable node near a matching text. Returns True if tapped."""
    xml_start = xml_str.find("<?xml")
    if xml_start < 0: return False
    xml_clean = xml_str[xml_start:]
    try:
        root = ET.fromstring(xml_clean)
    except: return False
    nodes = list(root.iter("node"))

    # Find text matches
    for i, node in enumerate(nodes):
        t = (node.get("text", "") or "").lower()
        cd = (node.get("content-desc", "") or "").lower()
        matched = False
        for q in text_queries:
            if q.lower() in t or q.lower() in cd:
                matched = True
                break
        if not matched: continue
        # Look for clickable node nearby (within 10 indices, or sibling)
        for j in range(max(0,i-8), min(len(nodes), i+8)):
            if nodes[j].get("clickable") == "true":
                bounds = nodes[j].get("bounds", "")
                try:
                    parts = bounds.replace("[", "").replace("]", ",").rstrip(",").split(",")
                    x1, y1, x2, y2 = map(int, parts)
                    pos = ((x1 + x2) // 2, (y1 + y2) // 2)
                    print("  [%s] Found '%s', tapping clickable at %s" % (step_label, q, pos))
                    tap(pos[0], pos[1])
                    return True
                except: pass
    return False

def scroll_down(w, h):
    """Scroll the setup wizard down to reveal more content."""
    sx = w // 2
    sy_start = int(h * 0.78)
    sy_end = int(h * 0.25)
    sh("input swipe %d %d %d %d 500" % (sx, sy_start, sx, sy_end))
    time.sleep(2)

# ================================================================
print("=" * 60)
print("Steps 1-6c on phone %s" % PHONE)
print("Target: %s, %s" % (LAT, LNG))
w, h = get_screen()
print("Screen: %dx%d" % (w, h))
print("=" * 60)

# ── STEP 1: Close all apps ──────────────────────────────
print()
print("=== STEP 1: Close all apps ===")
for p in [MAPS, GPS, "com.android.vending", "com.google.android.gms", "com.android.chrome"]:
    sh("am force-stop %s" % p)
    time.sleep(0.3)
time.sleep(1)
sh("input keyevent KEYCODE_HOME"); time.sleep(0.5)
sh("input keyevent KEYCODE_APP_SWITCH"); time.sleep(0.5)
sh("input swipe 360 600 360 1200 300"); time.sleep(0.5)
sh("input keyevent KEYCODE_HOME")
time.sleep(WAIT)
print("[OK] Step 1")

# ── STEP 2: Clear GPS app storage ───────────────────────
print()
print("=== STEP 2: Clear GPS app storage ===")
out = sh("pm clear %s" % GPS)
print("  pm clear: %s" % out.strip())
time.sleep(1)
sh("am force-stop %s" % GPS)
time.sleep(WAIT)
print("[OK] Step 2")

# ── STEP 3: Grant permissions ───────────────────────────
print()
print("=== STEP 3: Grant permissions ===")
sh("pm grant %s android.permission.ACCESS_FINE_LOCATION" % GPS)
sh("pm grant %s android.permission.ACCESS_COARSE_LOCATION" % GPS)
sh("pm grant %s android.permission.POST_NOTIFICATIONS" % GPS)
sh("appops set %s MOCK_LOCATION allow" % GPS)
sh("appops set %s SYSTEM_ALERT_WINDOW allow" % GPS)
sh("settings put secure mock_location_app %s" % GPS)
sh("settings put secure mock_location 1")
sh("settings put secure location_mode 3")
time.sleep(WAIT)
print("[OK] Step 3")

# ── STEP 4: Open app (force-stop + monkey) ──────────────
print()
print("=== STEP 4: Open App ===")
sh("am force-stop %s" % GPS)
time.sleep(2)
sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % GPS)
time.sleep(5)
focus = get_focus()
print("  Focus: %s" % focus[:200])
print("[OK] Step 4")

# ── STEP 5: Privacy screen ──────────────────────────────
print()
print("=== STEP 5: Privacy screen ===")
if "PrivacyActivity" in focus:
    xml = dump_ui("privacy")
    found = find_and_tap(xml, ["ACCEPT", "Accept", "Agree", "OK", "I AGREE"], "Step5")
    if not found:
        # Proportional fallback
        px = int(w * 0.887)
        py = int(h * 0.910)
        print("  [Step5] Text not found - proportional tap at (%d, %d)" % (px, py))
        tap(px, py)
    time.sleep(WAIT)
    focus = get_focus()
    print("  New focus: %s" % focus[:200])
    print("[OK] Step 5")
else:
    print("[OK] Step 5 - no privacy screen")

# ── STEP 6: Setup wizard - scroll then find button ──────
print()
print("=== STEP 6: Setup wizard (scroll + find button) ===")
setup_done = False
for scroll_pass in range(1, 4):
    # Scroll down to reveal more content
    print("  Scroll pass %d..." % scroll_pass)
    scroll_down(w, h)

    xml = dump_ui("step6_scroll%d" % scroll_pass)
    texts = get_visible_texts(xml)
    safe_texts = ", ".join([t[:40] for t in texts[:20]])
    try: print("  Texts: %s" % safe_texts)
    except: print("  Texts: (%d found)" % len(texts))

    # Try Start Using GPS JoyStick (various text forms)
    if find_and_tap(xml, [
        "Start Using GPS JoyStick", "Start Using GPS", "START USING GPS",
        "Start", "Get Started", "START", "Begin", "Continue", "Next",
    ], "Step6"):
        time.sleep(WAIT)
        setup_done = True
        break

    # Try "You're All Set" page - find clickable to finish
    if find_clickable_in(xml, ["You're All Set", "Everything is set up", "all set"], "Step6-finish"):
        time.sleep(WAIT)
        setup_done = True
        break

    # Try "Done" or "Finish" as last resort
    if find_and_tap(xml, ["Done", "FINISH", "Got it", "OK", "Let's Go"], "Step6-done"):
        time.sleep(WAIT)
        setup_done = True
        break

    print("  Could not find button on pass %d" % scroll_pass)

if setup_done:
    print("[OK] Step 6 - setup wizard advanced")
else:
    print("[WARN] Step 6 - could not find any setup button after scrolling")

# ── STEP 6b: Cancel update dialog ───────────────────────
print()
print("=== STEP 6b: Update dialog ===")
xml = dump_ui("update")
vis = get_visible_texts(xml)
safe_vis = [s for s in vis if s.isascii() or all(ord(c)<256 for c in s)]
print("  Visible: %s" % (safe_vis[:15],))
if any("CANCEL" in v or "DOWNLOAD" in v for v in vis):
    print("  Update dialog detected - tapping CANCEL")
    find_and_tap(xml, ["CANCEL", "Cancel"], "Cancel")
    time.sleep(WAIT)
else:
    print("  No update dialog")
print("[OK] Step 6b")

# ── STEP 6c: What's New ─────────────────────────────────
print()
print("=== STEP 6c: What's New ===")
xml = dump_ui("whatsnew")
vis = get_visible_texts(xml)
safe_vis = [s for s in vis if s.isascii() or all(ord(c)<256 for c in s)]
print("  Visible: %s" % (safe_vis[:15],))
if any("Done" in v or "DONE" in v for v in vis):
    print("  What's New - tapping Done")
    find_and_tap(xml, ["Done", "DONE"], "Done")
    time.sleep(WAIT)
else:
    # Also check for "CONTINUE WITH ADS" or consent dialogs
    if any("CONTINUE" in v or "Consent" in v or "ADS" in v or "Continue" in v for v in vis):
        print("  Ad/consent dialog - tapping continue")
        find_and_tap(xml, ["CONTINUE WITH ADS", "Continue", "Consent", "OK"], "ad-blocker")
        time.sleep(WAIT)
    else:
        print("  No What's New / blocker dialog")
print("[OK] Step 6c")

print()
print("=" * 60)
focus = get_focus()
print("STEPS 1-6c COMPLETE")
print("Focus: %s" % focus[:200])
print("=" * 60)
