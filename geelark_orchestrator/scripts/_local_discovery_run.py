#!/usr/bin/env python3
"""
[Local Discovery Run]

GPS within 1km of business (random point). Searches everyday local terms
(supermarkets, cafes, barbers, etc), clicks random listings, browses briefly.

Usage: python _local_discovery_run.py <phone_id> <lat> <lng> <account_label> <business_name>
"""
import sys, time, re, subprocess, json, random, math, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
LAT = float(sys.argv[2])
LNG = float(sys.argv[3])
LABEL = sys.argv[4]
BUSINESS = sys.argv[5]

GPS = "com.theappninjas.fakegpsjoystick"
MAPS = "com.google.android.apps.maps"
WAIT = 8

# ── Everyday local search terms ──
EVERYDAY_TERMS = [
    # Food & Drink
    "supermarket near me",
    "grocery store near me",
    "cafe near me",
    "coffee shop near me",
    "bakery near me",
    "restaurant near me",
    "takeaway near me",
    "fish and chips near me",
    "pizza near me",
    "breakfast near me",
    "sandwich shop near me",
    "corner shop near me",
    "convenience store near me",
    "butcher near me",
    "greengrocer near me",
    "dessert near me",
    "ice cream near me",
    "pub near me",
    "wine shop near me",
    "off licence near me",
    # Services
    "barbers near me",
    "hair salon near me",
    "post office near me",
    "pharmacy near me",
    "dentist near me",
    "optometrist near me",
    "bank near me",
    "petrol station near me",
    "car wash near me",
    "laundrette near me",
    "dry cleaners near me",
    "tailor near me",
    "cobbler near me",
    "locksmith near me",
    "electrician near me",
    # Shopping
    "shopping centre near me",
    "DIY store near me",
    "hardware store near me",
    "pound shop near me",
    "charity shop near me",
    "bookshop near me",
    "newsagent near me",
    "flower shop near me",
    "pet shop near me",
    "bicycle shop near me",
    # Leisure & Community
    "park near me",
    "gym near me",
    "swimming pool near me",
    "library near me",
    "cinema near me",
    "community centre near me",
    "church near me",
    "mosque near me",
    "playground near me",
    "sports centre near me",
    # Transport
    "train station near me",
    "bus stop near me",
    "taxi rank near me",
    "car park near me",
    # Everyday
    "atm near me",
    "vets near me",
    "recycling centre near me",
    "tip near me",
    "council office near me",
    "police station near me",
]

# How many searches to do per run (1 per day)
NUM_SEARCHES = 1

ORCH = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient, _post

client = GeelarKClient()

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return (r.get("output", "") or "")

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
    path = "/sdcard/discovery_%s.xml" % tag
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
            if t: texts.append(t)
    except: pass
    return texts

def find_text(xml_str, text_query):
    xml_start = xml_str.find("<?xml") if xml_str else -1
    if xml_start < 0: return None
    try:
        root = ET.fromstring(xml_str[xml_start:])
    except: return None
    ql = text_query.lower()
    best = None; best_score = 999
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
                except: pass
    return best

def find_and_tap(xml_str, queries, step_label):
    if isinstance(queries, str): queries = [queries]
    xml_start = xml_str.find("<?xml")
    if xml_start < 0: return False
    try: root = ET.fromstring(xml_str[xml_start:])
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
    return False

def scroll_slow(w, h, distance=None):
    """Scroll with human-like speed."""
    if distance is None:
        distance = random.randint(300, 600)
    start_y = int(h * random.uniform(0.55, 0.70))
    end_y = start_y - distance
    duration = random.randint(400, 800)
    sh("input swipe %d %d %d %d %d" % (w//2, start_y, w//2, end_y, duration))
    time.sleep(random.uniform(1.5, 3))

# ── Calculate GPS offset ──
angle = random.uniform(0, 2 * math.pi)
km_offset = random.uniform(0.3, 1.0)
d_lat = (km_offset / 111.32) * math.cos(angle)
d_lng = (km_offset / (111.32 * math.cos(math.radians(LAT)))) * math.sin(angle)
GPS_LAT = LAT + d_lat
GPS_LNG = LNG + d_lng
distance = math.sqrt(d_lat**2 * 111.32**2 + (d_lng * 111.32 * math.cos(math.radians(LAT)))**2)

# ── Pick search terms ──
search_terms = random.sample(EVERYDAY_TERMS, min(NUM_SEARCHES, len(EVERYDAY_TERMS)))

run_start = time.time()
phase_start = run_start

def log_phase(name):
    global phase_start
    now = time.time()
    elapsed = now - phase_start
    total = now - run_start
    print("  [TIMER] %s: %.0fs (total: %.0fs)" % (name, elapsed, total))
    phase_start = now

print("=" * 70)
print("LOCAL DISCOVERY RUN: %s" % LABEL)
print("Phone: %s | GPS offset: %.2f km" % (PHONE, distance))
print("Search: %s" % search_terms[0])
print("Started: %s" % time.strftime("%H:%M:%S"))
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# PHASE 1: GPS SETUP
# ═══════════════════════════════════════════════════════════════
# ── Wrap entire run in try/finally so phone always stops ──
run_error = None
try:
    # ROTATE PROXY
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
        print("  Response: %s" % resp.status_code)
    except Exception as e:
        print("  WARNING: %s" % e)
    print("  Waiting 60s for IP propagation...")
    time.sleep(60)

    # START PHONE
    print()
    print("--- Starting %s ---" % LABEL)
    live_url = client.start_phone(PHONE)
    print("Live URL: %s" % (live_url or "none"))
    if live_url:
        redirect = ORCH / "scripts" / "_live_view_redirect.html"
        redirect.write_text('<meta http-equiv="refresh" content="0; url=%s">' % live_url, encoding="utf-8")
        subprocess.Popen(["cmd", "/c", "start", "", "chrome", "--new-window", str(redirect)], shell=False)
        time.sleep(3)

    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            s = client.get_phone_status([PHONE])[0].get("status", -1)
            if s == 0: break
        except: pass

    w, h = get_screen()
    print("Screen: %dx%d" % (w, h))

    # IP check immediately after boot
    print("--- Checking IP ---")
    try:
        ip_addr = sh("curl -s --max-time 10 https://ifconfig.me").strip()
        if not ip_addr or "not found" in ip_addr.lower():
            ip_addr = sh("curl -s --max-time 10 https://api.ipify.org").strip()
        print("  Phone IP: %s" % (ip_addr or "?"))
    except:
        print("  IP check failed")

# STEPS 1-6c (GPS setup — condensed from _full_run.py)
print("\n=== GPS Setup ===")
for p in [MAPS, GPS, "com.android.vending", "com.google.android.gms", "com.android.chrome"]:
    sh("am force-stop %s" % p); time.sleep(0.2)
time.sleep(1)
sh("input keyevent KEYCODE_HOME"); time.sleep(0.5)
sh("input keyevent KEYCODE_APP_SWITCH"); time.sleep(0.5)
sh("input swipe 360 600 360 1200 300"); time.sleep(0.5)
sh("input keyevent KEYCODE_HOME"); time.sleep(WAIT)

sh("pm clear %s" % GPS); time.sleep(1)
sh("am force-stop %s" % GPS); time.sleep(1)

for perm in ["ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION", "POST_NOTIFICATIONS"]:
    sh("pm grant %s android.permission.%s" % (GPS, perm))
    sh("pm grant %s android.permission.%s" % (MAPS, perm))
sh("appops set %s MOCK_LOCATION allow" % GPS)
sh("appops set %s SYSTEM_ALERT_WINDOW allow" % GPS)
sh("settings put secure mock_location_app %s" % GPS)
sh("settings put secure mock_location 1")
sh("settings put secure location_mode 3")
time.sleep(WAIT)

sh("am force-stop %s" % GPS); time.sleep(2)
sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % GPS)
time.sleep(5)
focus = get_focus()

# Privacy
if "PrivacyActivity" in focus:
    tap(int(w * 0.887), int(h * 0.910)); time.sleep(WAIT)
    focus = get_focus()
    if "PrivacyActivity" in focus:
        xml = dump_ui("gps_privacy")
        find_and_tap(xml, ["ACCEPT", "Accept", "Agree", "OK", "I AGREE"], "privacy")
        time.sleep(WAIT)

# Permission dialogs
xml = dump_ui("gps_perm")
perm_texts = get_visible_texts(xml)
if any(w in " ".join(perm_texts).lower() for w in ["allow gps", "location permission", "access this device"]):
    find_and_tap(xml, ["Allow all the time", "ALLOW ALL THE TIME", "While using the app", "WHILE USING THE APP", "Allow", "ALLOW"], "perm")

# Setup wizard
for sp in range(1, 4):
    def scroll_gps(w, h):
        sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.78), w//2, int(h*0.25)))
        time.sleep(2)
    scroll_gps(w, h)
    xml = dump_ui("gps_wiz_%d" % sp)
    if find_and_tap(xml, ["Start Using GPS JoyStick", "Start Using GPS", "START USING GPS", "Start", "Get Started", "START", "Begin", "Continue", "Next"], "wiz"): break
    if find_and_tap(xml, ["Done", "FINISH", "Got it", "OK", "Let's Go"], "wiz"): break
time.sleep(WAIT)

# Update dialog
xml = dump_ui("gps_update")
vis = get_visible_texts(xml)
if any("CANCEL" in v or "DOWNLOAD" in v for v in vis):
    find_and_tap(xml, ["CANCEL", "Cancel"], "cancel")
    time.sleep(WAIT)

# What's New
xml = dump_ui("gps_whatsnew")
vis = get_visible_texts(xml)
if any("Done" in v or "DONE" in v for v in vis):
    find_and_tap(xml, ["Done", "DONE"], "done")
    time.sleep(WAIT)

# Deep links
url = "gpsjoystick://teleport?lat=%.6f&lng=%.6f" % (GPS_LAT, GPS_LNG)
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS)); time.sleep(5)
sh("input keyevent KEYCODE_HOME"); time.sleep(3)
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS)); time.sleep(WAIT)
sh("input keyevent KEYCODE_HOME"); time.sleep(3)

# Verify mock
out = sh("dumpsys location")
mock_active = "[mock]" in out.lower()
if not mock_active:
    sh("am force-stop %s" % GPS); time.sleep(2)
    sh("settings put secure mock_location 0"); time.sleep(1)
    sh("settings put secure mock_location 1"); time.sleep(2)
    sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS)); time.sleep(5)
    sh("input keyevent KEYCODE_HOME"); time.sleep(3)
    sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS)); time.sleep(WAIT)
    sh("input keyevent KEYCODE_HOME"); time.sleep(3)
    out = sh("dumpsys location")
    mock_active = "[mock]" in out.lower()
    if not mock_active:
        sh("am force-stop %s" % GPS); time.sleep(3)
        sh("settings put secure mock_location 0"); time.sleep(1)
        sh("settings put secure mock_location 1"); time.sleep(2)
        sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS)); time.sleep(5)
        sh("input keyevent KEYCODE_HOME"); time.sleep(3)
        sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS)); time.sleep(WAIT)
        sh("input keyevent KEYCODE_HOME"); time.sleep(3)
        out = sh("dumpsys location")
        mock_active = "[mock]" in out.lower()

print("GPS: %s" % ("OK" if mock_active else "FAIL"))
log_phase("GPS Setup")

# ═══════════════════════════════════════════════════════════════
# PHASE 2: LOCAL DISCOVERY SEARCHES
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("LOCAL DISCOVERY: %d searches" % len(search_terms))
print("=" * 70)

for term_idx, term in enumerate(search_terms):
    print("\n--- Search %d/%d: %s ---" % (term_idx + 1, len(search_terms), term))

    # Force-stop Maps to pick up GPS location
    sh("am force-stop com.google.android.apps.maps"); time.sleep(1)
    sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1")
    time.sleep(8)

    # Dismiss dialogs
    xml = dump_ui("disc_%d_init" % term_idx)
    for dismiss in ["No thanks", "SKIP", "GOT IT", "OK", "CLOSE", "NOT NOW", "CANCEL"]:
        if find_and_tap(xml, dismiss, "dismiss"):
            time.sleep(3)
            break
    time.sleep(2)

    # Use geo intent for search (shows list view, no typing needed)
    query_encoded = term.replace(" ", "+").replace("&", "%26")
    sh("am start -a android.intent.action.VIEW -d 'geo:0,0?q=%s' com.google.android.apps.maps" % query_encoded)
    time.sleep(6)

    # Tap proportional positions in results area — try up to 3 positions
    found_listing = False
    for attempt in range(3):
        tap_y = int(h * random.uniform(0.40, 0.65))  # middle of screen
        tap_x = int(w * random.uniform(0.20, 0.80))
        print("  Tapping results at (%d, %d)" % (tap_x, tap_y))
        tap(tap_x, tap_y)
        time.sleep(3)

        # Quick check: did we land on something?
        xml = dump_ui("disc_%d_tap%d" % (term_idx, attempt))
        texts = get_visible_texts(xml)
        joined = " ".join(texts).lower()
        # If we see business-like markers or a name, we're good
        biz_indicators = ["directions", "call", "save", "share", "stars", "reviews", "open", "closed", "website", "overview", "photos"]
        if any(ind in joined for ind in biz_indicators):
            print("  Landed on a listing page")
            found_listing = True
            break
        # If still on search results, scroll and try again
        sh("input swipe %d %d %d %d 400" % (w//2, int(h*0.55), w//2, int(h*0.25)))
        time.sleep(1.5)

    if not found_listing:
        print("  No listing page detected after 3 attempts — continuing anyway")

    # Wait for page to load
    time.sleep(4)

    # Light interaction on the listing
    xml = dump_ui("disc_%d_listing" % term_idx)
    texts = get_visible_texts(xml)
    print("  Page: %s" % ", ".join(texts[:6]).encode("ascii", errors="replace").decode())

    # Browse: scroll the page a bit
    for si in range(random.randint(1, 3)):
        sh("input swipe %d %d %d %d %d" % (
            w//2, int(h*0.65), w//2, int(h*0.35),
            random.randint(300, 600)
        ))
        time.sleep(random.uniform(2, 4))

    # Sometimes tap a photo if available
    if random.random() < 0.4:
        pc = find_text(xml, "Photos")
        if pc:
            tap(pc[0], pc[1]); time.sleep(2)
            for _ in range(random.randint(1, 2)):
                sh("input swipe %d %d %d %d 300" % (int(w*0.75), h//2, int(w*0.25), h//2))
                time.sleep(2)
            sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)

    # Back to results
    sh("input keyevent KEYCODE_BACK"); time.sleep(2)

    print("  [OK] %s browsed" % term)

# ═══════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════
print()
print("--- Cleanup ---")
sh("input keyevent KEYCODE_HOME"); time.sleep(1)

except Exception as e:
    run_error = e
    print("\n[ERROR] Run failed: %s" % e)
    import traceback
    traceback.print_exc()

finally:
    # ── ALWAYS stop phone ──
    print()
    print("--- Stopping phone ---")
    try:
        client.stop_phone(PHONE)
        print("Phone stopped.")
    except Exception as e:
        print("Stop error: %s" % e)

print()
total_time = time.time() - run_start
print("=" * 70)
status = "ERROR" if run_error else "COMPLETE"
print("LOCAL DISCOVERY %s: %s" % (status, LABEL))
print("Searches done: %d" % len(search_terms))
print("Terms: %s" % ", ".join(search_terms))
print("Total time: %.0fs (%.1f min)" % (total_time, total_time/60))
print("=" * 70)
if run_error:
    sys.exit(1)
