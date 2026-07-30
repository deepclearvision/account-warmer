#!/usr/bin/env python3
"""
Full end-to-end run: close old phone, start new phone, open live view,
run steps 1-6c, send deep links, verify mock, retry if needed.
Usage: python _full_run.py <phone_id> <lat> <lng> <account_label> [old_phone_id]
"""
import sys, time, re, subprocess, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
LAT = sys.argv[2]
LNG = sys.argv[3]
LABEL = sys.argv[4]
OLD_PHONE = sys.argv[5] if len(sys.argv) > 5 else None
GPS = "com.theappninjas.fakegpsjoystick"
MAPS = "com.google.android.apps.maps"
WAIT = 8

ORCH = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient, _post

client = GeelarKClient()

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
    texts = []
    xml_start = xml_str.find("<?xml") if xml_str else -1
    if xml_start < 0: return texts
    try:
        root = ET.fromstring(xml_str[xml_start:])
        for node in root.iter("node"):
            t = (node.get("text", "") or "").strip()
            cd = (node.get("content-desc", "") or "").strip()
            if t: texts.append(t)
            if cd and cd != t: texts.append(cd)
    except: pass
    return texts

def find_and_tap(xml_str, queries, step_label):
    if isinstance(queries, str): queries = [queries]
    xml_start = xml_str.find("<?xml")
    if xml_start < 0: return False
    try:
        root = ET.fromstring(xml_str[xml_start:])
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
                    x1, y1, x2, y2 = map(int, bounds.replace("[", "").replace("]", ",").rstrip(",").split(","))
                    best = ((x1 + x2) // 2, (y1 + y2) // 2)
                    best_query = q
                except: pass
    if best:
        print("  [%s] Tapped \"%s\" at %s" % (step_label, best_query, best))
        tap(best[0], best[1])
        return True
    else:
        print("  [%s] NOT FOUND: %s" % (step_label, queries[:3]))
        texts = get_visible_texts(xml_str)
        safe = ", ".join(texts[:20]).encode("ascii", errors="replace").decode("ascii")
        print("    Visible: %s" % safe)
        return False

def find_clickable_in(xml_str, text_queries, step_label):
    xml_start = xml_str.find("<?xml")
    if xml_start < 0: return False
    try: root = ET.fromstring(xml_str[xml_start:])
    except: return False
    nodes = list(root.iter("node"))
    for i, node in enumerate(nodes):
        t = (node.get("text", "") or "").lower()
        cd = (node.get("content-desc", "") or "").lower()
        matched = any(q.lower() in t or q.lower() in cd for q in text_queries)
        if not matched: continue
        for j in range(max(0,i-8), min(len(nodes), i+8)):
            if nodes[j].get("clickable") == "true":
                bounds = nodes[j].get("bounds", "")
                try:
                    x1, y1, x2, y2 = map(int, bounds.replace("[", "").replace("]", ",").rstrip(",").split(","))
                    pos = ((x1 + x2) // 2, (y1 + y2) // 2)
                    print("  [%s] Found '%s', tapping clickable at %s" % (step_label, q, pos))
                    tap(pos[0], pos[1])
                    return True
                except: pass
    return False

def scroll_down(w, h):
    sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.78), w//2, int(h*0.25)))
    time.sleep(2)

# ================================================================
print("=" * 70)
print("FULL RUN: %s" % LABEL)
print("Phone: %s | Target: %s,%s" % (PHONE, LAT, LNG))
print("=" * 70)

# ── STEP 0: Rotate proxy IP (non-blocking) ──────────────
print()
print("=== STEP 0: Rotate proxy IP ===")
try:
    import requests
    resp = requests.post(
        "https://mobile-proxy-140-166.streamvia.io/index.php",
        auth=("19866_1", "Wealther3211!!"),
        data={"action": "changeip"},
        timeout=10
    )
    print("  IP rotation triggered (takes ~2 min, non-blocking) — response: %s" % resp.status_code)
    print("[OK] Step 0")
except Exception as e:
    print("  WARNING: Could not trigger IP rotation: %s" % e)

# ── CLOSE OLD PHONE ─────────────────────────────────────
print()
print("--- Stopping old phone ---")
if OLD_PHONE:
    try:
        client.stop_phone(OLD_PHONE)
        print("  Stopped %s" % OLD_PHONE)
    except Exception as e:
        print("  Already stopped (%s)" % e)

# ── START NEW PHONE ─────────────────────────────────────
print()
print("--- Starting %s ---" % LABEL)
live_url = client.start_phone(PHONE)
print("Live URL: %s" % (live_url or "none"))

if live_url:
    redirect = ORCH / "scripts" / "_live_view_redirect.html"
    redirect.write_text('<meta http-equiv="refresh" content="0; url=%s">' % live_url, encoding="utf-8")
    subprocess.Popen(["cmd", "/c", "start", "", "chrome", "--new-window", str(redirect)], shell=False)
    print("Live view opened in new window.")
    time.sleep(3)

deadline = time.time() + 120
while time.time() < deadline:
    time.sleep(5)
    try:
        s = client.get_phone_status([PHONE])[0].get("status", -1)
        print("  status=%s (%ss left)" % (s, int(deadline - time.time())))
        if s == 0: break
    except Exception as e:
        print("  poll error: %s" % e)

# ── GET SCREEN ──────────────────────────────────────────
w, h = get_screen()
print("\nScreen: %dx%d" % (w, h))

# ── STEP 1: Close all apps ──────────────────────────────
print("\n=== STEP 1: Close all apps ===")
for p in [MAPS, GPS, "com.android.vending", "com.google.android.gms", "com.android.chrome"]:
    sh("am force-stop %s" % p); time.sleep(0.3)
time.sleep(1)
sh("input keyevent KEYCODE_HOME"); time.sleep(0.5)
sh("input keyevent KEYCODE_APP_SWITCH"); time.sleep(0.5)
sh("input swipe 360 600 360 1200 300"); time.sleep(0.5)
sh("input keyevent KEYCODE_HOME"); time.sleep(WAIT)
print("[OK] Step 1")

# ── STEP 2: Clear GPS app storage ───────────────────────
print("\n=== STEP 2: Clear GPS app storage ===")
print("  pm clear: %s" % sh("pm clear %s" % GPS).strip())
time.sleep(1)
sh("am force-stop %s" % GPS); time.sleep(WAIT)
print("[OK] Step 2")

# ── STEP 3: Grant permissions ───────────────────────────
print("\n=== STEP 3: Grant permissions ===")
# GPS — location + background location (Android 11+)
sh("pm grant %s android.permission.ACCESS_FINE_LOCATION" % GPS)
sh("pm grant %s android.permission.ACCESS_COARSE_LOCATION" % GPS)
sh("pm grant %s android.permission.ACCESS_BACKGROUND_LOCATION" % GPS)
# GPS — other
sh("pm grant %s android.permission.POST_NOTIFICATIONS" % GPS)
sh("appops set %s MOCK_LOCATION allow" % GPS)
sh("appops set %s SYSTEM_ALERT_WINDOW allow" % GPS)
# System mock-location switches
sh("settings put secure mock_location_app %s" % GPS)
sh("settings put secure mock_location 1")
sh("settings put secure location_mode 3")
# Maps — location + background
sh("pm grant %s android.permission.ACCESS_FINE_LOCATION" % MAPS)
sh("pm grant %s android.permission.ACCESS_COARSE_LOCATION" % MAPS)
sh("pm grant %s android.permission.ACCESS_BACKGROUND_LOCATION" % MAPS)
sh("pm grant %s android.permission.POST_NOTIFICATIONS" % MAPS)
time.sleep(WAIT)
print("[OK] Step 3")

# ── STEP 4: Open app ────────────────────────────────────
print("\n=== STEP 4: Open App ===")
sh("am force-stop %s" % GPS); time.sleep(2)
sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % GPS)
time.sleep(5)
focus = get_focus()
print("  Focus: %s" % focus[:200])
print("[OK] Step 4")

# ── STEP 5: Privacy screen ──────────────────────────────
# WebView content unreliable via uiautomator — proportional tap first
print("\n=== STEP 5: Privacy screen ===")
if "PrivacyActivity" in focus:
    # Try proportional tap first (WebView-safe)
    px, py = int(w * 0.887), int(h * 0.910)
    print("  [Step5] Proportional tap at (%d, %d)" % (px, py))
    tap(px, py)
    time.sleep(WAIT)
    focus = get_focus()
    print("  New focus: %s" % focus[:200])
    # If proportional didn't work, try uiautomator text search as fallback
    if "PrivacyActivity" in focus:
        print("  [Step5] Proportional missed, trying text search...")
        xml = dump_ui("privacy")
        find_and_tap(xml, ["ACCEPT", "Accept", "Agree", "OK", "I AGREE"], "Step5")
        time.sleep(WAIT)
        focus = get_focus()
        print("  New focus: %s" % focus[:200])
    print("[OK] Step 5")
else:
    print("[OK] Step 5 - skip")

# ── STEP 6: Setup wizard ────────────────────────────────
print("\n=== STEP 6: Setup wizard (scroll + find button) ===")
setup_done = False
for sp in range(1, 4):
    print("  Scroll pass %d..." % sp)
    scroll_down(w, h)
    xml = dump_ui("step6_s%d" % sp)
    texts = get_visible_texts(xml)
    safe = ", ".join(texts[:15]).encode("ascii", errors="replace").decode("ascii")
    print("  Texts: %s" % safe)

    if find_and_tap(xml, [
        "Start Using GPS JoyStick", "Start Using GPS", "START USING GPS",
        "Start", "Get Started", "START", "Begin", "Continue", "Next",
    ], "Step6"):
        time.sleep(WAIT); setup_done = True; break

    if find_clickable_in(xml, ["You're All Set", "Everything is set up", "all set"], "Step6-finish"):
        time.sleep(WAIT); setup_done = True; break

    if find_and_tap(xml, ["Done", "FINISH", "Got it", "OK", "Let's Go"], "Step6-done"):
        time.sleep(WAIT); setup_done = True; break
print("[OK] Step 6" if setup_done else "[WARN] Step 6 - no button found")

# ── STEP 6b: Update dialog ──────────────────────────────
print("\n=== STEP 6b: Update dialog ===")
xml = dump_ui("update")
vis = get_visible_texts(xml)
safe = [s.encode("ascii",errors="replace").decode() for s in vis]
print("  Visible: %s" % (safe[:15],))
if any("CANCEL" in v or "DOWNLOAD" in v for v in vis):
    print("  Update detected - tapping CANCEL")
    found = find_and_tap(xml, ["CANCEL", "Cancel"], "Cancel")
    if not found:
        # Proportional fallback: CANCEL ~lower-left of dialog
        cx, cy = int(w * 0.42), int(h * 0.54)
        print("  [Cancel] Proportional fallback at (%d, %d)" % (cx, cy))
        tap(cx, cy)
    time.sleep(WAIT)
else:
    print("  No update dialog")
print("[OK] Step 6b")

# ── STEP 6c: What's New + blocker dialogs ───────────────
print("\n=== STEP 6c: What's New / blockers ===")
xml = dump_ui("whatsnew")
vis = get_visible_texts(xml)
safe = [s.encode("ascii",errors="replace").decode() for s in vis]
print("  Visible: %s" % (safe[:15],))
tapped = False
if any("Done" in v or "DONE" in v for v in vis):
    print("  What's New - tapping Done")
    found = find_and_tap(xml, ["Done", "DONE"], "Done")
    if not found:
        cx, cy = int(w * 0.5), int(h * 0.90)
        print("  [Done] Proportional fallback at (%d, %d)" % (cx, cy))
        tap(cx, cy)
    time.sleep(WAIT); tapped = True
if not tapped and any("CANCEL" in v or "DOWNLOAD" in v for v in vis):
    print("  Update - tapping CANCEL")
    found = find_and_tap(xml, ["CANCEL"], "Cancel2")
    if not found:
        cx, cy = int(w * 0.42), int(h * 0.54)
        print("  [Cancel2] Proportional fallback at (%d, %d)" % (cx, cy))
        tap(cx, cy)
    time.sleep(WAIT); tapped = True
if not tapped and any("CONTINUE" in v or "Consent" in v or "ADS" in v for v in vis):
    print("  Ad/consent - tapping continue")
    found = find_and_tap(xml, ["CONTINUE WITH ADS", "Continue", "Consent", "OK"], "blocker")
    if not found:
        cx, cy = int(w * 0.5), int(h * 0.85)
        print("  [Consent] Proportional fallback at (%d, %d)" % (cx, cy))
        tap(cx, cy)
    time.sleep(WAIT); tapped = True
if not tapped:
    print("  No dialogs found")
print("[OK] Step 6c")

# ================================================================
# ── STEP 7+8+9: Deep links, verify, retry ──────────────
# ================================================================

def verify_mock():
    out = sh("dumpsys location")
    active = False; match = False
    target_lat, target_lng = float(LAT), float(LNG)
    for line in out.splitlines():
        lower = line.lower()
        if "[mock]" in lower:
            print("    %s" % line.strip())
            active = True
        if "last mock location" in lower:
            m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
            if m:
                mlat, mlng = float(m.group(1)), float(m.group(2))
                match = abs(mlat - target_lat) <= 0.0002 and abs(mlng - target_lng) <= 0.0002
                print("    Coords: %s,%s (match=%s)" % (m.group(1), m.group(2), match))
    return active, match

def send_deep_links():
    url = "gpsjoystick://teleport?lat=%s&lng=%s" % (LAT, LNG)
    print("  URL: %s" % url)
    sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
    time.sleep(5)
    sh("input keyevent KEYCODE_HOME"); time.sleep(3)
    print("  Re-sending from home...")
    sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
    time.sleep(WAIT)
    sh("input keyevent KEYCODE_HOME"); time.sleep(3)

# ── STEP 7: Deep links ──────────────────────────────────
print("\n=== STEP 7: Deep links ===")
send_deep_links()
print("[OK] Step 7 - deep links sent")

# ── STEP 8: Verify mock ────────────────────────────────
print("\n=== STEP 8: Verify mock ===")
mock_active, coords_match = verify_mock()
if mock_active:
    print("[OK] Step 8 - GPS MOCK ACTIVE (%s)" % ("coords match" if coords_match else "check coords",))
else:
    print("[WARN] Step 8 - Mock not active")

# ── STEP 9: Retry (force-stop app + re-send deep links) ─
if not mock_active:
    print("\n=== STEP 9: Retry (force-stop GPS app + re-send deep links) ===")
    print("  Force-stopping GPS app...")
    sh("am force-stop %s" % GPS)
    time.sleep(2)
    send_deep_links()
    print("  Verifying...")
    mock_active, coords_match = verify_mock()
    if mock_active:
        print("[OK] Step 9 - GPS MOCK ACTIVE after retry (%s)" % ("coords match" if coords_match else "check coords",))
    else:
        print("[FAIL] Step 9 - Mock still not active after retry")

# ── STEP 10: Log phone's proxy IP ─────────────────────────
print("\n=== STEP 10: Log phone IP ===")
try:
    out = sh("curl -s --max-time 10 https://ifconfig.me").strip()
    if not out or "not found" in out.lower():
        out = sh("curl -s --max-time 10 https://api.ipify.org").strip()
    if out:
        print("  Phone IP: %s" % out)
    else:
        print("  Could not determine phone IP")
except Exception as e:
    print("  IP check failed: %s" % e)
print("[OK] Step 10")

# ================================================================
print("\n" + "=" * 70)
print("FULL RUN COMPLETE: %s" % LABEL)
print("Focus: %s" % get_focus()[:200])
print("Phone LEFT RUNNING for manual inspection")
print("=" * 70)
