#!/usr/bin/env python3
"""
Maps search using shell commands — no GeelarK variables, no ifElse.
uiautomator dump + input tap, same pattern proven in _full_run.py.

Usage: python _maps_shell.py <phone_id> <business_name> <keyword> <label>
"""
import sys, time, re, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
BUSINESS = sys.argv[2]
KEYWORD = sys.argv[3] if len(sys.argv) > 3 else "Emergency Plumber Near Me"
LABEL = sys.argv[4] if len(sys.argv) > 4 else "unknown"

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
    path = "/sdcard/%s_%s.xml" % (tag, LABEL)
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump %s" % path})
    time.sleep(1.5)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat %s" % path})
    return (r.get("output", "") or "")

def find_text_coords(xml_str, text_query):
    """Find coordinates of element whose text contains text_query. Returns (cx, cy) or None."""
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
        score = None
        if ql in t or ql in cd:
            # Prefer shorter matches (closer to exact)
            score = min(len(t), len(cd)) if t or cd else 999
            if score < best_score:
                best_score = score
                bounds = node.get("bounds", "")
                try:
                    x1, y1, x2, y2 = map(int, bounds.replace("[", "").replace("]", ",").rstrip(",").split(","))
                    best = ((x1 + x2) // 2, (y1 + y2) // 2)
                except:
                    pass
    return best

print("=" * 60)
print("MAPS SHELL: %s" % LABEL)
print("Phone: %s | Business: %s" % (PHONE, BUSINESS))
print("Keyword: %s" % KEYWORD)
print("=" * 60)

w, h = get_screen()
print("Screen: %dx%d" % (w, h))

# Step 1: Open Maps
print("\n--- Open Maps ---")
sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1")
time.sleep(12)

# Step 2: Dismiss any cookie/GDPR dialogs
print("--- Dismiss dialogs ---")
xml = dump_ui("maps_init")
for dismiss_text in ["No thanks", "SKIP", "GOT IT", "OK", "CLOSE", "NOT NOW", "CANCEL"]:
    coords = find_text_coords(xml, dismiss_text)
    if coords:
        print("  Tapping '%s' at %s" % (dismiss_text, coords))
        tap(coords[0], coords[1])
        time.sleep(2)
        break
time.sleep(3)

# Step 3: Tap search bar
print("--- Tap search bar ---")
xml = dump_ui("pre_search")
search_coords = find_text_coords(xml, "Search here")
if search_coords:
    tap(search_coords[0], search_coords[1])
    print("  Tapped search at %s" % (search_coords,))
else:
    # Proportional fallback - search bar usually near top
    tap(int(w * 0.5), int(h * 0.09))
    print("  Proportional tap at (%d, %d)" % (int(w * 0.5), int(h * 0.09)))
time.sleep(3)

# Step 4: Type keyword
print("--- Type keyword: %s ---" % KEYWORD)
# Replace spaces with %s for input text
sh("input text '%s'" % KEYWORD.replace("'", "\\'"))
time.sleep(3)

# Step 5: Press Enter (search)
print("--- Press Enter ---")
sh("input keyevent 66")  # KEYCODE_ENTER
time.sleep(6)

# Step 6: Scroll and search loop
print("--- Search loop for '%s' ---" % BUSINESS)
found = False
for i in range(15):
    xml = dump_ui("search_%d" % i)
    coords = find_text_coords(xml, BUSINESS)

    if coords:
        print("  [PASS %d] FOUND '%s' at %s!" % (i, BUSINESS, coords))
        tap(coords[0], coords[1])
        found = True
        break
    else:
        print("  [PASS %d] Not found, scrolling..." % i)
        # Safe scroll: start middle-upper, end lower-middle, stay away from bottom
        sh("input swipe %d %d %d %d 500" % (w // 2, int(h * 0.65), w // 2, int(h * 0.35)))
        time.sleep(3)

if found:
    print("\n[OK] Business found and clicked!")
else:
    print("\n[WARN] Business not found after 15 scrolls")

print("=" * 60)
print("DONE — phone still running")
