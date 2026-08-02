#!/usr/bin/env python3
"""
Unified warmup runner with optional YouTube warmup on the same phone session.
"""
import sys, time, re, subprocess, json, random, math, csv, os as _os, ctypes
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# Parse --youtube from sys.argv
YOUTUBE_TIMING = None
_i = 1
while _i < len(sys.argv):
    if sys.argv[_i] == "--youtube" and _i + 1 < len(sys.argv):
        YOUTUBE_TIMING = sys.argv[_i + 1]
        if YOUTUBE_TIMING not in ("before", "after", "both", "none", "random"):
            print("Invalid --youtube value: %s. Use before|after|both|none|random." % YOUTUBE_TIMING)
            sys.exit(1)
        sys.argv = sys.argv[:_i] + sys.argv[_i + 2:]
    else:
        _i += 1

if YOUTUBE_TIMING is None:
    YOUTUBE_TIMING = random.choices(["before", "after", "both", "none"], weights=[0.25, 0.35, 0.10, 0.30], k=1)[0]

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


def _run_youtube_flow(phone_id, acc_id, video_count, keyword="", client=None):
    if client is None:
        from core.geelark_client import GeelarKClient
        client = GeelarKClient()
    flow_id = "629648416401522754"
    param_map = {"ExpectedNumberOfVideosViewed": str(video_count)}
    if keyword:
        param_map["SearchKeyword"] = keyword
    print("  [YouTube] Starting flow - videos=%d keyword=%s" % (video_count, keyword or "(browse)"))
    time.sleep(5)
    xml = dump_ui("yt_notif")
    vis = get_visible_texts(xml)
    if any("notifications" in v.lower() or "allow" in v.lower() for v in vis):
        print("  [YouTube] notification dialog detected, tapping Allow")
        if not find_and_tap(xml, ["Allow", "ALLOW", "Got it", "OK"], "yt_notif"):
            tap(int(w * 0.50), int(h * 0.60))
        time.sleep(2)
    try:
        tid = client.run_custom_flow(flow_id=flow_id, phone_id=phone_id, param_map=param_map, task_name="YouTube warmup - %s" % acc_id)
        print("  [YouTube] Task: %s" % tid)
    except Exception as e:
        print("  [YouTube] Failed to start flow: %s" % e)
        return False
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


if len(sys.argv) < 3:
    print("Usage: python _run_with_youtube.py <mode> <acc_id> [keyword|leave] [leave] [--youtube <timing>]")
    print("Modes: brand-1km, money-kw, local-discovery")
    sys.exit(1)

MODE = sys.argv[1]
ACC_ID = sys.argv[2]

YAML_PATH = Path(r"C:\WarmingData\geelark_accounts.yaml")
try:
    import yaml
    yaml_data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}
except Exception as e:
    print("Failed to load geelark_accounts.yaml: %s" % e)
    sys.exit(1)

account = next((a for a in yaml_data.get("accounts", []) if a.get("id") == ACC_ID), None)
if not account:
    print("Account %s not found in geelark_accounts.yaml" % ACC_ID)
    sys.exit(1)

PHONE = account.get("geelark_phone_id")
if not PHONE:
    print("Account %s has no geelark_phone_id" % ACC_ID)
    sys.exit(1)

CSV_PATH = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\data\accounts_business_mapping.csv"
with open(CSV_PATH) as f:
    for row in csv.DictReader(f):
        if row["account_id"] == ACC_ID:
            LAT = float(row["business_lat"])
            LNG = float(row["business_lng"])
            BUSINESS = row["business_name"]
            LABEL = ACC_ID
            break
    else:
        print("Account %s not found in business CSV" % ACC_ID)
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

MAPS_PKG = "com.google.android.apps.maps"
WAIT = 8
LOCK_FILE = Path(r"C:\WarmingData\logs\state\phone_lock.json")

MONEY_KEYWORDS = [
    "emergency plumber near me","leak repair near me","blocked drain near me",
    "burst pipe repair near me","water leak repair near me",
    "emergency plumbing repair near me","toilet repair near me",
    "emergency drain unblocking near me","flood repair near me",
    "urgent plumber near me","emergency pipe repair near me",
    "plumbing leak repair near me","sink repair near me",
    "emergency toilet repair near me","drain unblocking near me",
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
    KEYWORD = BUSINESS
elif MODE == "local-discovery":
    KEYWORD = random.choice(LOCAL_TERMS)
else:
    KEYWORD = random.choice(MONEY_KEYWORDS)

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

ORCH = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")))
from core.geelark_client import GeelarKClient, _post

client = GeelarKClient()

# Optional safety: warn if phone is already running
try:
    st = client.get_phone_status([PHONE])
    if st and st[0].get("status") == 2:
        print("[WARNING] Phone %s is already running. Continuing anyway." % PHONE)
except Exception:
    pass

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

def read_remote_file(remote_path, chunk_size=1500):
    """Read a remote file in chunks via dd to bypass GeelarK 2KB output limit."""
    chunks = []
    offset = 0
    while True:
        cmd = "dd if=%s bs=1 skip=%d count=%d 2>/dev/null" % (remote_path, offset, chunk_size)
        r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
        chunk = (r.get("output", "") or "").replace("\r\n", "\n")
        if not chunk:
            break
        chunks.append(chunk)
        if len(chunk) < chunk_size:
            break
        offset += chunk_size
    return "".join(chunks)

def dump_ui(tag):
    path = "/sdcard/_run_%s.xml" % tag
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump %s" % path})
    time.sleep(1.5)
    return read_remote_file(path)

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

# ── Business-page helper used by both search verify and interactions ──────
def ifind(xml_str, query):
    xs = xml_str.find("<?xml") if xml_str else -1
    if xs < 0: return None
    try: root = ET.fromstring(xml_str[xs:])
    except: return None
    ql = query.lower(); best = None; best_score = 999
    for node in root.iter("node"):
        t = (node.get("text", "") or "").strip().lower()
        cd = (node.get("content-desc", "") or "").strip().lower()
        rid = (node.get("resource-id", "") or "").lower()
        for field in [t, cd, rid]:
            if not field: continue
            if field == ql: score = 0
            elif field.startswith(ql + " ") or field.startswith(ql + "_") or field.startswith(ql): score = 1
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


def list_buttons(xml_str):
    xs = xml_str.find("<?xml") if xml_str else -1
    if xs < 0: return
    try: root = ET.fromstring(xml_str[xs:])
    except: return
    found = set()
    for node in root.iter("node"):
        for attr in ["text", "content-desc", "resource-id"]:
            v = (node.get(attr, "") or "").strip()
            if v and any(k.lower() in v.lower() for k in ["Directions", "Call", "Photos", "Reviews", "Website", "Save", "Overview", "Start", "Message"]):
                found.add("%s=%s" % (attr, v))
    print("  [buttons] %s" % " | ".join(sorted(found)[:12]))


def ibiz():
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/_run_ibiz.xml"})
    time.sleep(1.5)
    x = read_remote_file("/sdcard/_run_ibiz.xml")
    ts = re.findall(r'text="([^"]*)"', x)
    cds = re.findall(r'content-desc="([^"]*)"', x)
    j = " ".join(ts + cds)
    biz_lower = BUSINESS.lower().replace("&", " ")
    words = biz_lower.split()
    biz_match = biz_lower in j
    if not biz_match:
        for n in [min(4, len(words)), min(3, len(words)), 2]:
            if n >= 2 and n <= len(words):
                frag = " ".join(words[:n])
                if frag in j:
                    biz_match = True
                    break
    if not biz_match and words:
        biz_match = words[0] in j
    mk = ["Directions","Call","Save","Overview","Reviews","Photos","Website","About","Address"]
    fm = [m for m in mk if m.lower() in j.lower()]
    print("  [ibiz] business match=%s  markers=%s" % (biz_match, fm))
    return biz_match or len(fm) >= 1, x

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
    # GPS SETUP — native GeelarK API
    # ══════════════════════════════════════════════════════════════════════
    print("\n=== GPS Setup (GeelarK API) ===")
    sh("am force-stop %s" % MAPS_PKG); time.sleep(1)
    sh("am force-stop com.google.android.gms"); time.sleep(0.5)
    sh("settings put secure location_mode 3")
    time.sleep(1)

    # Grant Maps location permissions
    for perm in ["ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION"]:
        sh("pm grant %s android.permission.%s" % (MAPS_PKG, perm))
        sh("pm grant com.google.android.gms android.permission.%s" % perm)

    # Set GPS via GeelarK native API
    try:
        r = _post("/open/v1/phone/gps/set", {
            "list": [{"id": PHONE, "latitude": GPS_LAT, "longitude": GPS_LNG}]
        })
        print("  GPS set: successAmount=%s" % r.get("successAmount", 0))
    except Exception as e:
        print("  GPS set failed: %s" % e)

    # Verify
    try:
        r = _post("/open/v1/phone/gps/get", {"ids": [PHONE]})
        loc = r.get("list", [{}])[0] if r.get("list") else {}
        print("  GPS verify: lat=%s lon=%s" % (loc.get("latitude", "?"), loc.get("longitude", "?")))
    except Exception as e:
        print("  GPS verify failed: %s" % e)

    print("GPS: set via GeelarK API")
    if MODE in ("brand-1km", "local-discovery"):
        print("GPS location: %.6f, %.6f (%.2f km from business)" % (GPS_LAT, GPS_LNG, DISTANCE_KM))

    # ══════════════════════════════════════════════════════════════════════
    # YOUTUBE BEFORE MAPS
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

    # Dismiss Google "Improve location accuracy" dialog if it appears
    sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1")
    time.sleep(5)
    xml = dump_ui("maps_init")
    if any("improve" in v.lower() or "accuracy" in v.lower() for v in get_visible_texts(xml)):
        find_and_tap(xml, ["Yes", "Turn on", "Agree", "OK", "Got it"], "location_accuracy")
        time.sleep(2)

    if MODE in ("brand-1km", "money-kw"):
        # Dismiss notification banners before opening Maps
        sh("input keyevent KEYCODE_HOME")
        time.sleep(1)
        sh("input statusbar collapse")
        time.sleep(1)

        # Open Maps fresh and search via geo intent
        sh("am force-stop com.google.android.apps.maps"); time.sleep(1)
        search_term = KEYWORD.replace(" ", "+").replace("&", "%26")
        sh("am start -a android.intent.action.VIEW -d 'geo:0,0?q=%s' com.google.android.apps.maps" % search_term)
        time.sleep(10)
        print("  [search] dumping UI immediately after geo intent")

        # Build progressively shorter business name queries
        biz_queries = [BUSINESS]
        words = BUSINESS.replace("&", " ").split()
        for n in [min(4, len(words)), min(3, len(words)), 2]:
            if n >= 2 and n <= len(words):
                short = " ".join(words[:n])
                if short not in biz_queries:
                    biz_queries.append(short)
        if words and words[0] not in biz_queries:
            biz_queries.append(words[0])

        def try_text_tap(stage_label):
            xml = dump_ui("search_" + stage_label)
            tapped = find_and_tap(xml, biz_queries, "search_biz")
            if not tapped:
                tapped = find_and_tap(xml, ["Open", "Closed", "Reviews", "Directions", "Website", "Call"], "search_markers")
            return tapped

        tapped = try_text_tap("1")
        time.sleep(4)
        ob, xml = ibiz()

        if not ob and not tapped:
            print("  [search] gently expanding peeking card")
            sh("input swipe %d %d %d %d 200" % (w//2, int(h*0.66), w//2, int(h*0.62)))
            time.sleep(2)
            tapped = try_text_tap("2")
            time.sleep(4)
            ob, xml = ibiz()

        if not ob:
            fallbacks = [
                (int(w * 0.50), int(h * 0.52), "title top"),
                (int(w * 0.50), int(h * 0.56), "title center"),
                (int(w * 0.25), int(h * 0.56), "title left"),
                (int(w * 0.75), int(h * 0.56), "title right"),
                (int(w * 0.50), int(h * 0.60), "card upper"),
                (int(w * 0.50), int(h * 0.64), "card center"),
            ]
            for fx, fy, label in fallbacks:
                print("  [search] not on business page, tapping %s (%d, %d)" % (label, fx, fy))
                tap(fx, fy)
                time.sleep(4)
                ob, xml = ibiz()
                if ob:
                    print("  [search] ibiz() confirmed after %s tap" % label)
                    break

        if not ob:
            print("[WARN] Not on business page after search, trying recovery")
            ob, xml = irecover()
        if ob:
            print("[OK] Business page confirmed via ibiz()")

        # ══════════════════════════════════════════════════════════════════
        # BUSINESS INTERACTIONS
        # ══════════════════════════════════════════════════════════════════
        print()
        print("=" * 70)
        print("BUSINESS INTERACTIONS: %s" % LABEL)
        print("=" * 70)

        def do_scroll(dwell_seconds=10):
            scroll_count = max(1, dwell_seconds // 3)
            for _ in range(scroll_count):
                sh("input swipe %d %d %d %d %d" % (w//2, int(h*0.65), w//2, int(h*0.35), random.randint(400, 700)))
                time.sleep(random.uniform(2.0, 4.0))

        def recover_to_business():
            """HOME → launch Maps → wait → irecover."""
            sh("input keyevent KEYCODE_HOME"); time.sleep(1)
            sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1"); time.sleep(5)
            ok_r, xml_r = irecover()
            if ok_r:
                print("  [recover] back on business page")
            else:
                print("  [recover] WARNING: not on business page")
            return ok_r, xml_r

        if not ob:
            print("[WARN] Not on business page, skipping interactions")
        else:
            # Reset page to top before interactions
            sh("input keyevent KEYCODE_HOME"); time.sleep(1)
            sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1"); time.sleep(4)
            ob, xml = irecover()

            # Initial slow scroll through the business listing
            do_scroll(random.randint(8, 18))

            # ── First interaction: Photos OR Reviews (random) ────────────
            first_choice = random.choice(["photos", "reviews"])
            print("First interaction: %s" % first_choice)

            ok, xml = irecover()
            if not ok:
                print("[SKIP] %s — could not reach business page" % first_choice)
            else:
                btn = ifind(xml, first_choice.capitalize())
                if not btn and first_choice == "photos":
                    # Scroll down to reveal Photos section
                    sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.50), w//2, int(h*0.30)))
                    time.sleep(2)
                    xml = dump_ui("first_photos_2")
                    btn = ifind(xml, "Photos")
                if not btn:
                    print("[SKIP] %s — button not found" % first_choice)
                elif first_choice == "photos":
                    tap(btn[0], btn[1]); time.sleep(3)
                    for i in range(random.randint(4, 7)):
                        sh("input swipe %d %d %d %d 300" % (int(w*0.75), h//2, int(w*0.25), h//2))
                        time.sleep(random.uniform(1.5, 3.0))
                    time.sleep(random.randint(5, 8))
                    sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                    print("[OK] Photos")
                else:  # reviews
                    tap(btn[0], btn[1]); time.sleep(3)
                    for i in range(random.randint(4, 6)):
                        sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.70), w//2, int(h*0.30)))
                        time.sleep(random.uniform(2.0, 4.0))
                    xml = read_remote_file("/sdcard/_run_rev.xml")
                    mc = ifind(xml, "More")
                    if mc: tap(mc[0], mc[1]); time.sleep(2)
                    time.sleep(random.randint(6, 10))
                    sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                    print("[OK] Reviews")

            # Recover after first interaction
            recover_to_business()

            # ── Final interaction: Call OR Directions (50/50) ────────────
            final_choice = random.choice(["call", "directions"])
            print("Final interaction: %s" % final_choice)

            ok, xml = irecover()
            if not ok:
                print("[SKIP] %s — could not reach business page" % final_choice)
            else:
                btn_label = "Call" if final_choice == "call" else "Directions"
                btn = ifind(xml, btn_label)
                if not btn:
                    # Top buttons sometimes hidden by a scroll; reset to top
                    sh("input swipe %d %d %d %d 400" % (w//2, int(h*0.25), w//2, int(h*0.45)))
                    time.sleep(2)
                    xml = dump_ui("final_" + final_choice)
                    btn = ifind(xml, btn_label)
                if not btn:
                    print("[SKIP] %s — button not found" % final_choice)
                elif final_choice == "call":
                    tap(btn[0], btn[1]); time.sleep(random.randint(4, 7))
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
                    focus = sh("dumpsys window | grep mCurrentFocus").strip()
                    if "dialer" in focus.lower() or "contacts" in focus.lower():
                        sh("input keyevent KEYCODE_BACK"); time.sleep(1)
                    print("[OK] Phone Call")
                else:  # directions
                    tap(btn[0], btn[1]); time.sleep(random.randint(6, 10))
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
                    print("[OK] Directions")

            # Final recovery
            recover_to_business()

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
    # YOUTUBE AFTER MAPS
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
