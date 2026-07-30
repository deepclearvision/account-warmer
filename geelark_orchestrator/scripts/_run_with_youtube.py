#!/usr/bin/env python3
"""
Unified warmup runner with optional YouTube warmup on the same phone session.

Usage:
  python _run_with_youtube.py brand-1km acc_004
  python _run_with_youtube.py money-kw acc_004
  python _run_with_youtube.py local-discovery acc_004
  python _run_with_youtube.py brand-1km acc_004 leave
  python _run_with_youtube.py money-kw acc_004 "custom keyword"
  python _run_with_youtube.py brand-1km acc_004 --youtube before
  python _run_with_youtube.py local-discovery acc_004 --youtube random

YouTube timing:
  --youtube before    YouTube before Maps
  --youtube after     YouTube after Maps
  --youtube both      YouTube before AND after Maps
  --youtube none      no YouTube
  --youtube random    randomly choose (default if --youtube not provided)
"""
import sys, time, re, subprocess, json, random, math, csv, os as _os, ctypes
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# ── Parse --youtube <timing> from sys.argv ────────────────────────────────────
YOUTUBE_TIMING = None  # None = use random weights
_i = 1
while _i < len(sys.argv):
    if sys.argv[_i] == "--youtube" and _i + 1 < len(sys.argv):
        YOUTUBE_TIMING = sys.argv[_i + 1]
        if YOUTUBE_TIMING not in ("before", "after", "both", "none", "random"):
            print("Invalid --youtube value: %s. Use before|after|both|none|random." % YOUTUBE_TIMING)
            sys.exit(1)
        # Remove --youtube and its value from argv
        sys.argv = sys.argv[:_i] + sys.argv[_i + 2:]
    else:
        _i += 1

# Resolve random timing if not explicitly provided
if YOUTUBE_TIMING is None:
    YOUTUBE_TIMING = random.choices(
        ["before", "after", "both", "none"],
        weights=[0.25, 0.35, 0.10, 0.30],
        k=1,
    )[0]

# ── YouTube keyword pool ──────────────────────────────────────────────────────
_YOUTUBE_KEYWORDS = [
    "london street food", "premier league highlights", "uk news today",
    "how to make sourdough", "best restaurants london", "morning workout routine",
    "travel uk vlog", "cooking at home recipes", "funny moments football",
    "manchester city highlights", "recipe pasta easy", "london vlog walk",
    "best walking routes uk", "how to fix a leaking tap uk",
    "chelsea fc highlights", "arsenal highlights today", "tech reviews 2024",
    "british sitcom clips", "garden makeover ideas uk", "true crime documentary uk",
    "best electric cars 2024 uk", "music festival highlights", "home organization ideas",
    "uk weather forecast", "diy home improvement",
]

# ── YouTube RPA flow helper ───────────────────────────────────────────────────

def _run_youtube_flow(phone_id, acc_id, video_count, keyword="", client=None):
    """
    Run the YouTube warmup RPA flow on an already-running phone.

    Uses GeelarK custom flow ID 629648416401522754.
    Does NOT rotate proxy, start, or stop the phone.

    Args:
        phone_id:    GeelarK phone ID.
        acc_id:      Account ID for logging.
        video_count: Number of videos to watch (typically 3-8).
        keyword:     Search keyword (empty = browse Shorts feed).
        client:      Existing GeelarKClient instance (created if None).

    Returns:
        True if flow completed (status 3), False otherwise.
    """
    if client is None:
        from core.geelark_client import GeelarKClient
        client = GeelarKClient()

    flow_id = "629648416401522754"
    param_map = {"ExpectedNumberOfVideosViewed": str(video_count)}
    if keyword:
        param_map["SearchKeyword"] = keyword

    print("  [YouTube] Starting flow - videos=%d keyword=%s" % (video_count, keyword or "(browse)"))
    try:
        tid = client.run_custom_flow(
            flow_id=flow_id, phone_id=phone_id, param_map=param_map,
            task_name="YouTube warmup - %s" % acc_id,
        )
        print("  [YouTube] Task: %s" % tid)
    except Exception as e:
        print("  [YouTube] Failed to start flow: %s" % e)
        return False

    # Poll for up to 900s
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(8)
        try:
            tasks = client.query_tasks([tid])
            s = tasks[0].get("status", -1) if tasks else -1
            sm = {1: "Waiting", 2: "InProgress", 3: "Completed", 4: "Failed"}
            st = sm.get(s, str(s))
            elapsed = int(time.time() - (deadline - 900))
            print("  [YouTube] t+%ds: %s" % (elapsed, st))
            if st in ("Completed", "Failed"):
                break
        except Exception:
            pass

    if st == "Completed":
        print("  [YouTube] Flow completed successfully.")
        return True
    print("  [YouTube] Flow ended: %s" % st)
    return False


# ── Parse args ──────────────────────────────────────────────────────────────
if len(sys.argv) < 3:
    print("Usage: python _run_with_youtube.py <mode> <acc_id> [keyword|leave] [leave] [--youtube <timing>]")
    print("Modes: brand-1km, money-kw, local-discovery")
    print("YouTube timing: before|after|both|none|random")
    sys.exit(1)

MODE = sys.argv[1]
ACC_ID = sys.argv[2]

# Auto-lookup from CSV
CSV_PATH = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\data\accounts_business_mapping.csv"
with open(CSV_PATH) as f:
    for row in csv.DictReader(f):
        if row["account_id"] == ACC_ID:
            PHONE = row["geelark_phone_id"]
            LAT = float(row["business_lat"])
            LNG = float(row["business_lng"])
            LABEL = ACC_ID
            BUSINESS = row["business_name"]
            break
    else:
        print("Account %s not found" % ACC_ID)
        sys.exit(1)

KEYWORD_ARG = None
LEAVE_OPEN = False
FORCE_RUN = False
for a in sys.argv[3:]:
    if a == "leave":
        LEAVE_OPEN = True
    elif a == "force":
        FORCE_RUN = True
    elif not a.startswith("acc_"):
        KEYWORD_ARG = a

# ── Settings ────────────────────────────────────────────────────────────────
GPS_PKG = "com.theappninjas.fakegpsjoystick"
MAPS_PKG = "com.google.android.apps.maps"
WAIT = 8

# Shared phone lock — prevents ANY two phones from running simultaneously
LOCK_FILE = Path(r"C:\WarmingData\logs\state\phone_lock.json")

# ── Keyword selection by mode ───────────────────────────────────────────────
MONEY_KEYWORDS = [
    "emergency plumber near me","leak repair near me","blocked drain near me",
    "burst pipe repair near me","water leak repair near me",
    "emergency plumbing repair near me","toilet repair near me",
    "emergency drain unblocking near me","flood repair near me",
    "urgent plumber near me","emergency pipe repair near me",
    "plumbing leak repair near me","sink repair near me",
    "emergency toilet repair near me","drain unblocking near me",
]

BRAND_KEYWORDS = [
    "{biz} phone number","{biz} address","{biz} opening times",
    "{biz} reviews","{biz} contact number","{biz} services",
    "{biz} opening hours","{biz} customer reviews",
    "{biz} emergency","{biz} location",
]

LOCAL_TERMS = [
    "supermarket near me","cafe near me","coffee shop near me",
    "bakery near me","restaurant near me","takeaway near me",
    "barbers near me","post office near me","pharmacy near me",
    "park near me","gym near me","library near me",
    "pub near me","hardware store near me","charity shop near me",
    "train station near me","petrol station near me","atm near me",
    "vets near me","cinema near me","swimming pool near me",
    "fish and chips near me","pizza near me","bookshop near me",
    "DIY store near me","car wash near me","police station near me",
    "church near me","playground near me","laundrette near me",
    "dry cleaners near me","florist near me","newsagent near me",
    "corner shop near me","butcher near me","greengrocer near me",
    "off licence near me","dentist near me","bank near me",
    "taxi rank near me","car park near me","bus stop near me",
    "recycling centre near me","grocery store near me",
    "dessert near me","breakfast near me","pound shop near me",
    "tailor near me","bicycle shop near me","cobbler near me",
    "locksmith near me","community centre near me",
]

if KEYWORD_ARG:
    KEYWORD = KEYWORD_ARG
elif MODE == "brand-1km":
    kw = random.choice(BRAND_KEYWORDS)
    KEYWORD = kw.replace("{biz}", BUSINESS)
elif MODE == "local-discovery":
    KEYWORD = random.choice(LOCAL_TERMS)
else:  # money-kw
    KEYWORD = random.choice(MONEY_KEYWORDS)

# ── GPS offset for brand-1km and local-discovery ───────────────────────────
if MODE in ("brand-1km", "local-discovery"):
    angle = random.uniform(0, 2 * math.pi)
    km = random.uniform(0.8, 1.2) if MODE == "brand-1km" else random.uniform(0.3, 1.0)
    d_lat = (km / 111.32) * math.cos(angle)
    d_lng = (km / (111.32 * math.cos(math.radians(LAT)))) * math.sin(angle)
    GPS_LAT = LAT + d_lat
    GPS_LNG = LNG + d_lng
    DISTANCE_KM = math.sqrt(d_lat**2 * 111.32**2 + (d_lng * 111.32 * math.cos(math.radians(LAT)))**2)
else:
    GPS_LAT = LAT
    GPS_LNG = LNG
    DISTANCE_KM = 0.0

# ── Imports that need PHONE defined ────────────────────────────────────────
ORCH = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient, _post

client = GeelarKClient()

# ── Shell helpers ──────────────────────────────────────────────────────────
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
    path = "/sdcard/_run_%s.xml" % tag
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump %s" % path})
    time.sleep(1.5)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat %s" % path})
    return r.get("output", "")

def get_visible_texts(xml_str):
    texts = []
    x = xml_str.find("<?xml") if xml_str else -1
    if x < 0: return texts
    try:
        root = ET.fromstring(xml_str[x:])
        for node in root.iter("node"):
            t = (node.get("text", "") or "").strip()
            cd = (node.get("content-desc", "") or "").strip()
            if t: texts.append(t)
            if cd and cd != t: texts.append(cd)
    except: pass
    return texts

def find_and_tap(xml_str, queries, step_label):
    if isinstance(queries, str): queries = [queries]
    x = xml_str.find("<?xml") if xml_str else -1
    if x < 0: return False
    try: root = ET.fromstring(xml_str[x:])
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
        return False

# ========================================================================
# MAIN
# ========================================================================
run_start = time.time()
_run_error = None
phone_started = False

mode_names = {"brand-1km": "BRAND-1KM", "money-kw": "MONEY KW", "local-discovery": "LOCAL DISCOVERY"}
RUN_NAME = mode_names.get(MODE, MODE.upper())

try:
    print("=" * 70)
    print("%s RUN: %s" % (RUN_NAME, LABEL))
    print("Phone: %s | Mode: %s" % (PHONE, MODE))
    print("YouTube timing: %s" % YOUTUBE_TIMING)
    if MODE in ("brand-1km", "local-discovery"):
        print("GPS offset: %.3f km -> (%.6f, %.6f)" % (DISTANCE_KM, GPS_LAT, GPS_LNG))
    print("Business: %s | Keyword: %s" % (BUSINESS, KEYWORD))
    print("=" * 70)

    # ── PRE-FLIGHT CHECK (pid-based file lock) ─────────────────────────────
    _lock_acquired = False
    if LOCK_FILE.exists():
        try:
            prev = json.loads(LOCK_FILE.read_text())
            if prev.get("running"):
                prev_pid = prev.get("pid")
                alive = False
                if prev_pid:
                    try:
                        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, prev_pid)
                        if handle:
                            ctypes.windll.kernel32.CloseHandle(handle)
                            alive = True
                    except:
                        pass
                if alive:
                    msg = "[ABORT] Batch lock held by %s (pid=%s, mode=%s, started=%s)" % (
                        prev.get("account_id","?"), prev_pid, prev.get("mode","?"), prev.get("started_at","?"))
                    if FORCE_RUN:
                        print("[WARNING] %s" % msg)
                        print("  Continuing with --force override...")
                    else:
                        print(msg)
                        print("  Use 'force' arg to override")
                        sys.exit(1)
                else:
                    print("  Stale lock found (pid %s dead), clearing" % prev_pid)
        except Exception as e:
            print("  Lock file read warning: %s" % e)

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps({
        "running": True, "account_id": ACC_ID, "mode": MODE,
        "pid": _os.getpid(), "started_at": datetime.now().isoformat()
    }))
    _lock_acquired = True
    print("  Batch lock acquired (pid=%s)" % _os.getpid())

    # ── STEP 0: Rotate proxy IP ──────────────────────────────────────────
    print()
    print("=== STEP 0: Rotate proxy IP ===")
    try:
        import requests
        resp = requests.post(
            "https://mobile-proxy-140-166.streamvia.io/index.php",
            auth=("19866_1", "Wealther3211!!"),
            data={"action": "changeip"}, timeout=15
        )
        print("  Rotation triggered - status: %s, body: %s" % (resp.status_code, resp.text.strip()))
    except Exception as e:
        print("  WARNING: %s" % e)
    print("  Waiting 60s for IP propagation...")
    time.sleep(60)

    # ── START PHONE ──────────────────────────────────────────────────────
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
            print("  status=%s (%ss left)" % (s, int(deadline - time.time())))
            if s == 0: break
        except Exception as e:
            print("  poll error: %s" % e)

    w, h = get_screen()
    print("Screen: %dx%d" % (w, h))
    phone_started = True

    # IP check + uniqueness verification
    print("--- Checking IP ---")
    current_ip = None
    try:
        ip = sh("curl -s --max-time 10 https://ifconfig.me").strip()
        if not ip or "not found" in ip.lower():
            ip = sh("curl -s --max-time 10 https://api.ipify.org").strip()
        if ip and re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
            current_ip = ip
            print("  Phone IP: %s" % current_ip)
        else:
            print("  WARNING: unexpected IP response: %s" % (ip or "empty"))
    except:
        print("  IP check failed")

    if current_ip:
        last_ip = None
        if LOCK_FILE.exists():
            try:
                prev = json.loads(LOCK_FILE.read_text())
                last_ip = prev.get("last_ip")
            except: pass
        if last_ip and last_ip == current_ip:
            print("  *** WARNING: Same IP as previous run! (%s) ***" % current_ip)
            print("  *** Proxy rotation may not have worked.            ***")
        elif last_ip:
            print("  IP changed: %s -> %s" % (last_ip, current_ip))
        _current_lock_ip = current_ip
    else:
        _current_lock_ip = None

    # ══════════════════════════════════════════════════════════════════════
    # GPS SETUP
    # ══════════════════════════════════════════════════════════════════════
    print("\n=== GPS Setup ===")
    for p in [MAPS_PKG, GPS_PKG, "com.android.vending", "com.google.android.gms", "com.android.chrome"]:
        sh("am force-stop %s" % p); time.sleep(0.2)
    time.sleep(1)
    sh("input keyevent KEYCODE_HOME"); time.sleep(0.5)
    sh("input keyevent KEYCODE_APP_SWITCH"); time.sleep(0.5)
    sh("input swipe 360 600 360 1200 300"); time.sleep(0.5)
    sh("input keyevent KEYCODE_HOME"); time.sleep(WAIT)

    print("  pm clear: %s" % sh("pm clear %s" % GPS_PKG).strip())
    time.sleep(1)
    sh("am force-stop %s" % GPS_PKG); time.sleep(WAIT)

    for perm in ["ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION", "POST_NOTIFICATIONS"]:
        sh("pm grant %s android.permission.%s" % (GPS_PKG, perm))
        sh("pm grant %s android.permission.%s" % (MAPS_PKG, perm))
    sh("appops set %s MOCK_LOCATION allow" % GPS_PKG)
    sh("appops set %s SYSTEM_ALERT_WINDOW allow" % GPS_PKG)
    sh("settings put secure mock_location_app %s" % GPS_PKG)
    sh("settings put secure mock_location 1")
    sh("settings put secure location_mode 3")
    time.sleep(WAIT)

    sh("am force-stop %s" % GPS_PKG); time.sleep(2)
    sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % GPS_PKG)
    time.sleep(5)
    focus = get_focus()

    if "PrivacyActivity" in focus:
        tap(int(w * 0.887), int(h * 0.910)); time.sleep(WAIT)
        focus = get_focus()
        if "PrivacyActivity" in focus:
            xml = dump_ui("priv")
            find_and_tap(xml, ["ACCEPT", "Accept", "Agree", "OK", "I AGREE"], "priv"); time.sleep(WAIT)

    xml = dump_ui("perm")
    pts = get_visible_texts(xml)
    if any(w in " ".join(pts).lower() for w in ["allow gps", "location permission", "access this device"]):
        find_and_tap(xml, ["Allow all the time", "While using the app", "Allow", "ALLOW"], "perm")

    for sp in range(1, 4):
        sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.78), w//2, int(h*0.25))); time.sleep(2)
        xml = dump_ui("wiz%d" % sp)
        if find_and_tap(xml, ["Start Using GPS JoyStick", "START USING GPS", "Start", "Get Started", "BEGIN", "Continue", "Next"], "wiz"): break
        if find_and_tap(xml, ["Done", "FINISH", "Got it", "OK", "Let's Go"], "wiz"): break
    time.sleep(WAIT)

    xml = dump_ui("update")
    vis = get_visible_texts(xml)
    if any("CANCEL" in v or "DOWNLOAD" in v for v in vis):
        find_and_tap(xml, ["CANCEL"], "cancel"); time.sleep(WAIT)

    xml = dump_ui("whatsnew")
    vis = get_visible_texts(xml)
    if any("Done" in v or "DONE" in v for v in vis):
        find_and_tap(xml, ["Done", "DONE"], "done"); time.sleep(WAIT)

    # Deep links
    def send_links():
        url = "gpsjoystick://teleport?lat=%.6f&lng=%.6f" % (GPS_LAT, GPS_LNG)
        sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS_PKG)); time.sleep(5)
        sh("input keyevent KEYCODE_HOME"); time.sleep(3)
        sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS_PKG)); time.sleep(WAIT)
        sh("input keyevent KEYCODE_HOME"); time.sleep(3)

    send_links()

    # Verify mock
    def verify():
        out = sh("dumpsys location")
        active = "[mock]" in out.lower()
        match = False
        for line in out.splitlines():
            if "last mock location" in line.lower():
                m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
                if m:
                    match = abs(float(m.group(1)) - GPS_LAT) <= 0.001 and abs(float(m.group(2)) - GPS_LNG) <= 0.001
        return active, match

    mock_ok, _ = verify()
    if not mock_ok:
        sh("am force-stop %s" % GPS_PKG); time.sleep(2)
        send_links()
        mock_ok, _ = verify()
        if not mock_ok:
            sh("am force-stop %s" % GPS_PKG); time.sleep(3)
            sh("settings put secure mock_location 0"); time.sleep(1)
            sh("settings put secure mock_location 1"); time.sleep(2)
            send_links()
            time.sleep(5)
            mock_ok, _ = verify()

    print("GPS: %s" % ("OK" if mock_ok else "FAIL"))
    if MODE in ("brand-1km", "local-discovery"):
        print("GPS location: %.6f, %.6f (%.2f km from business)" % (GPS_LAT, GPS_LNG, DISTANCE_KM))

    # ══════════════════════════════════════════════════════════════════════
    # YOUTUBE BEFORE MAPS (if timing is "before" or "both")
    # ══════════════════════════════════════════════════════════════════════
    if YOUTUBE_TIMING in ("before", "both"):
        print()
        print("=" * 70)
        print("[YouTube] running BEFORE Maps for %s" % LABEL)
        print("=" * 70)
        video_count = random.randint(3, 8)
        yt_keyword = random.choice(_YOUTUBE_KEYWORDS) if random.random() < 0.60 else ""
        _run_youtube_flow(PHONE, ACC_ID, video_count, keyword=yt_keyword, client=client)
        sh("input keyevent KEYCODE_HOME"); time.sleep(2)
    else:
        print("\n[YouTube] skipped (timing=%s)" % YOUTUBE_TIMING)

    # ══════════════════════════════════════════════════════════════════════
    # MAPS WORKFLOW
    # ══════════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("MAPS WORKFLOW: %s" % LABEL)
    print("Keyword: %s | Business: %s" % (KEYWORD, BUSINESS))
    print("=" * 70)

    # Pre-flight: check Maps is installed
    mc = sh("pm path com.google.android.apps.maps")
    if "maps" not in mc.lower():
        print("[ABORT] Google Maps is not installed. Provision the phone first.")
        sys.exit(1)

    # For brand-1km and money-kw: use RPA workflow
    # For local-discovery: simplified shell-based browse
    if MODE in ("brand-1km", "money-kw"):
        gal = client.export_rpa_flow("624431313889263826")
        data = json.loads(gal)
        for step in data["content"]["contents"]:
            if step["type"] == "inputContent":
                step["config"]["content"] = [KEYWORD]
                print("Injected keyword: %s" % KEYWORD)
            if step["type"] == "forTimes":
                nc = []
                for child in step["config"]["children"]:
                    nc.append(child)
                    if child["type"] == "click":
                        for fc in child["config"].get("filterCollection", []):
                            for f in fc:
                                if f.get("type") == "text":
                                    f["content"] = BUSINESS
                                    print("Injected business: %s" % BUSINESS)
                        nc.append({"type": "waitTime", "config": {"_a": "8", "_b": "10"}})
                step["config"]["children"] = nc
        fid = client.import_rpa_flow(json.dumps(data))
        tid = client.run_custom_flow(flow_id=fid, phone_id=PHONE, param_map={}, task_name="%s - %s" % (RUN_NAME, LABEL))
        print("Flow: %s / Task: %s" % (fid, tid))

        dl = time.time() + 300
        st = "Unknown"
        while time.time() < dl:
            time.sleep(5)
            ts = client.query_tasks([tid])
            s = ts[0].get("status", -1) if ts else -1
            sm = {1: "Waiting", 2: "InProgress", 3: "Completed", 4: "Failed"}
            st = sm.get(s, str(s))
            print("  t+%ds: %s" % (int(time.time() - (dl - 300)), st))
            if st in ("Completed", "Failed"): break
        print("Flow ended: %s" % st)

        # Verify
        time.sleep(2)
        r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_final.xml"})
        time.sleep(1.5)
        r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/_run_final.xml"})
        x = r.get("output", "") or ""
        texts = re.findall(r'text="([^"]*)"', x)
        joined = " ".join(texts)
        markers = ["Directions", "Call", "Website", "Reviews", "Share", "Save", "Overview"]
        fm = [m for m in markers if m.lower() in joined.lower()]
        import html as _h
        has_biz = BUSINESS.lower()[:20] in _h.unescape(joined).lower()
        print("Business visible: %s | Markers: %s" % (has_biz, fm))

        # ══════════════════════════════════════════════════════════════════
        # INTERACTIONS (brand-1km and money-kw)
        # ══════════════════════════════════════════════════════════════════
        print()
        print("=" * 70)
        print("BUSINESS INTERACTIONS: %s" % LABEL)
        print("=" * 70)

        def ifind(xml_str, query):
            xs = xml_str.find("<?xml") if xml_str else -1
            if xs < 0: return None
            try: root = ET.fromstring(xml_str[xs:])
            except: return None
            ql = query.lower(); best = None; best_score = 999
            for node in root.iter("node"):
                t = (node.get("text", "") or "").strip().lower()
                cd = (node.get("content-desc", "") or "").strip().lower()
                for field in [t, cd]:
                    if not field: continue
                    if field == ql: score = 0
                    elif field.startswith(ql): score = 1
                    elif ql in field: score = 2
                    else: continue
                    if score < best_score:
                        best_score = score
                        bounds = node.get("bounds", "")
                        try:
                            x1,y1,x2,y2 = map(int, bounds.replace("[","").replace("]",",").rstrip(",").split(","))
                            best = ((x1+x2)//2, (y1+y2)//2)
                        except: pass
            return best

        def ibiz():
            r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_ibiz.xml"})
            time.sleep(1.5)
            r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/_run_ibiz.xml"})
            x = r.get("output","") or ""
            ts = re.findall(r'text="([^"]*)"', x)
            j = " ".join(ts)
            bw = BUSINESS.split()
            pb = " ".join(bw[:2]).lower() if len(bw) >= 2 else BUSINESS.lower()
            hb = BUSINESS.lower()[:20] in j.lower() or pb in j.lower()
            mk = ["Directions","Call","Save","Overview","Reviews","Photos"]
            fm = [m for m in mk if m.lower() in j.lower()]
            return hb or len(fm) >= 2, x

        def irecover():
            for _ in range(4):
                ok, x = ibiz()
                if ok: return True, x
                focus = sh("dumpsys window | grep mCurrentFocus").strip()
                if "dialer" in focus.lower() or "contacts" in focus.lower():
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
                elif "launcher" in focus.lower():
                    sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1"); time.sleep(3)
                elif "maps" in focus.lower():
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
                else:
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1)
            return False, x

        ob, xml = ibiz()
        if not ob:
            print("[WARN] Not on business page, skipping interactions")
        else:
            # 1. Directions first (major interaction)
            print("\n--- Directions ---")
            dc = ifind(xml, "Directions")
            if dc:
                tap(dc[0], dc[1]); time.sleep(3)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_dm.xml"})
                time.sleep(1.5); r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/_run_dm.xml"})
                dx = ifind(r.get("output","") or "", "Drive")
                if dx: tap(dx[0], dx[1]); time.sleep(2)
                else: tap(int(w*0.15), int(h*0.085)); time.sleep(2)
                time.sleep(6)
                sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                print("[OK] Directions")

            # 2. Reviews
            print("--- Reviews ---")
            ob, xml = ibiz()
            if not ob: ob, xml = irecover()
            if ob:
                rc = ifind(xml, "Reviews")
                if not rc:
                    for i in range(4):
                        if rc: break
                        sh("input swipe %d %d %d %d 400" % (w//2, int(h*0.58), w//2, int(h*0.22))); time.sleep(1.5)
                        r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_rs%d.xml" % i})
                        time.sleep(1.5); r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/_run_rs%d.xml" % i})
                        rc = ifind(r.get("output","") or "", "Reviews")
                if rc:
                    tap(rc[0], rc[1]); time.sleep(3)
                    for i in range(3):
                        sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.70), w//2, int(h*0.30))); time.sleep(3)
                    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_rev.xml"})
                    time.sleep(1.5); r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/_run_rev.xml"})
                    mc = ifind(r.get("output","") or "", "More")
                    if mc: tap(mc[0], mc[1]); time.sleep(2)
                    sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                    print("[OK] Reviews")

            # 3. Website
            print("--- Website ---")
            ob, xml = ibiz()
            if not ob: ob, xml = irecover()
            if ob:
                wc = ifind(xml, "Website")
                if not wc:
                    sh("input swipe %d %d %d %d 200" % (w//2, int(h*0.45), w//2, int(h*0.30))); time.sleep(1.5)
                    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_ws.xml"})
                    time.sleep(1.5); r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/_run_ws.xml"})
                    wc = ifind(r.get("output","") or "", "Website")
                if wc:
                    tap(wc[0], wc[1]); time.sleep(5)
                    sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                    focus = sh("dumpsys window | grep mCurrentFocus").strip()
                    if "maps" not in focus.lower(): sh("input keyevent KEYCODE_BACK"); time.sleep(1)
                    print("[OK] Website")
                else:
                    print("[SKIP] Website not available")

            # 4. Photos
            print("--- Photos ---")
            ob, xml = ibiz()
            if not ob: ob, xml = irecover()
            if ob:
                pc = ifind(xml, "Photos")
                if not pc:
                    sh("input swipe %d %d %d %d 300" % (w//2, int(h*0.50), w//2, int(h*0.25))); time.sleep(1.5)
                    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_ps.xml"})
                    time.sleep(1.5); r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/_run_ps.xml"})
                    pc = ifind(r.get("output","") or "", "Photos")
                if pc:
                    tap(pc[0], pc[1]); time.sleep(3)
                    for i in range(3):
                        sh("input swipe %d %d %d %d 300" % (int(w*0.75), h//2, int(w*0.25), h//2)); time.sleep(2.5)
                    sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                    print("[OK] Photos")

            # 5. Phone Call (last)
            print("--- Phone Call ---")
            ob, xml = ibiz()
            if not ob: ob, xml = irecover()
            if ob:
                cc = ifind(xml, "Call")
                if cc:
                    tap(cc[0], cc[1]); time.sleep(3)
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
                    focus = sh("dumpsys window | grep mCurrentFocus").strip()
                    if "dialer" in focus.lower() or "contacts" in focus.lower():
                        sh("input keyevent KEYCODE_BACK"); time.sleep(1)
                    print("[OK] Phone Call")

    elif MODE == "local-discovery":
        # Local discovery — geo intent search, open a listing, interact with it
        sh("am force-stop com.google.android.apps.maps"); time.sleep(1)
        sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1"); time.sleep(8)
        # Re-center
        xml = dump_ui("rc")
        try:
            xs = xml.find("<?xml") if xml else -1
            if xs >= 0:
                root = ET.fromstring(xml[xs:])
                for node in root.iter("node"):
                    cd = (node.get("content-desc","") or "").strip().lower()
                    if any(w in cd for w in ["current location","my location","re-center"]):
                        bounds = node.get("bounds","")
                        try:
                            x1,y1,x2,y2 = map(int, bounds.replace("[","").replace("]",",").rstrip(",").split(","))
                            tap((x1+x2)//2,(y1+y2)//2); time.sleep(2); break
                        except: pass
        except: pass
        # Search
        q = KEYWORD.replace(" ","+").replace("&","%26")
        sh("am start -a android.intent.action.VIEW -d 'geo:0,0?q=%s' com.google.android.apps.maps" % q); time.sleep(6)

        # Open a listing
        xml = dump_ui("ld_list")
        opened = find_and_tap(xml, ["Open", "Closed", "km", "m away", "·"], "ld_list")
        if not opened:
            tap(int(w*random.uniform(0.2,0.8)), int(h*random.uniform(0.40,0.60)))
        time.sleep(3)

        # Interact with the listing
        xml = dump_ui("ld_detail")
        detail = find_and_tap(xml, ["Directions", "Call", "Website",
                                     "Photos", "Reviews", "Save", "Share"],
                              "ld_detail")
        if detail:
            dwell = random.randint(20, 40)
            scrolls = dwell // 8
            for _ in range(scrolls):
                sh("input swipe %d %d %d %d %d" % (
                    w//2, int(h*0.65), w//2, int(h*0.35),
                    random.randint(300, 600)))
                time.sleep(random.uniform(2.5, 5.0))
            sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.40), w//2, int(h*0.80)))
            time.sleep(2)
            sh("input keyevent KEYCODE_BACK"); time.sleep(2)
            focus = sh("dumpsys window | grep mCurrentFocus").strip()
            if "maps" not in focus.lower() and "chrome" not in focus.lower():
                sh("input keyevent KEYCODE_BACK"); time.sleep(1)
        else:
            time.sleep(random.randint(15, 25))

        sh("input keyevent KEYCODE_BACK"); time.sleep(2)
        print("[OK] %s browsed" % KEYWORD)

    # ══════════════════════════════════════════════════════════════════════
    # YOUTUBE AFTER MAPS (if timing is "after" or "both")
    # ══════════════════════════════════════════════════════════════════════
    if YOUTUBE_TIMING in ("after", "both"):
        print()
        print("=" * 70)
        print("[YouTube] running AFTER Maps for %s" % LABEL)
        print("=" * 70)
        video_count = random.randint(3, 8)
        yt_keyword = random.choice(_YOUTUBE_KEYWORDS) if random.random() < 0.60 else ""
        _run_youtube_flow(PHONE, ACC_ID, video_count, keyword=yt_keyword, client=client)
        sh("input keyevent KEYCODE_HOME"); time.sleep(2)
    elif YOUTUBE_TIMING not in ("before", "both"):
        # already logged "skipped" above, or was "none"
        pass

except Exception as _run_err:
    print("\n[CRASH] %s" % _run_err)
    import traceback; traceback.print_exc()

finally:
    # Release batch lock
    if '_lock_acquired' in dir() and _lock_acquired:
        try:
            release_data = {
                "running": False, "account_id": ACC_ID, "mode": MODE,
                "pid": _os.getpid(), "stopped_at": datetime.now().isoformat()
            }
            if '_current_lock_ip' in dir() and _current_lock_ip:
                release_data["last_ip"] = _current_lock_ip
            LOCK_FILE.write_text(json.dumps(release_data))
        except:
            pass
    print()
    if 'PHONE' in dir() and PHONE:
        if not phone_started:
            pass
        elif LEAVE_OPEN:
            print("--- Phone LEFT RUNNING (leave mode) ---")
        else:
            print("--- Stopping phone ---")
            for retry in range(3):
                try:
                    client.stop_phone(PHONE)
                    print("Phone stopped.")
                    break
                except Exception as se:
                    print("Stop error (attempt %d): %s" % (retry+1, se))
                    time.sleep(5)
    else:
        print("--- Phone not started, nothing to stop ---")

total = time.time() - run_start
print()
print("=" * 70)
status = "ERROR" if '_run_err' in dir() and _run_err else "COMPLETE"
print("%s RUN %s: %s" % (RUN_NAME, status, LABEL))
print("Keyword: %s | YouTube: %s | Total time: %.0fs (%.1f min)" % (KEYWORD, YOUTUBE_TIMING, total, total/60))
if MODE in ("brand-1km", "local-discovery"):
    print("GPS offset: %.2f km" % DISTANCE_KM)
print("=" * 70)
