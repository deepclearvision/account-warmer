#!/usr/bin/env python3
"""
[Brand-1km-mobile]

Full run: GPS setup → Maps RPA workflow with emergency plumber money keywords → business interactions.
Money keywords are emergency-related, non-gas. Injected into the Maps search input.

Usage: python _money_kw_run.py <phone_id> <lat> <lng> <account_label> <business_name>
"""
import sys, time, re, subprocess, json, random, xml.etree.ElementTree as ET
from pathlib import Path

# Support CSV auto-lookup by account ID:
#   python _money_kw_run.py acc_039 [keyword] [leave]
# Or manual:
#   python _money_kw_run.py <phone_id> <lat> <lng> <label> <business> [keyword] [leave]
if sys.argv[1].startswith("acc_"):
    import csv
    acc_id = sys.argv[1]
    csv_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\data\accounts_business_mapping.csv"
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if row["account_id"] == acc_id:
                PHONE = row["geelark_phone_id"]
                LAT = row["business_lat"]
                LNG = row["business_lng"]
                LABEL = acc_id
                BUSINESS = row["business_name"]
                break
        else:
            print("Account %s not found in CSV" % acc_id)
            sys.exit(1)
    KEYWORD_OVERRIDE = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("leave") else None
    LEAVE_OPEN = "leave" in sys.argv
else:
    PHONE = sys.argv[1]
    LAT = sys.argv[2]
    LNG = sys.argv[3]
    LABEL = sys.argv[4]
    BUSINESS = sys.argv[5]
    KEYWORD_OVERRIDE = sys.argv[6] if len(sys.argv) > 6 and not sys.argv[6].startswith("leave") else None
    LEAVE_OPEN = len(sys.argv) > 6 and sys.argv[6] == "leave" or len(sys.argv) > 7 and sys.argv[7] == "leave"

GPS = "com.theappninjas.fakegpsjoystick"
MAPS = "com.google.android.apps.maps"
WAIT = 8

# ── Money keywords (emergency plumber, non-gas) ──
MONEY_KEYWORDS = [
    "emergency plumber near me",
    "leak repair near me",
    "blocked drain near me",
    "burst pipe repair near me",
    "water leak repair near me",
    "emergency plumbing repair near me",
    "toilet repair near me",
    "emergency drain unblocking near me",
    "flood repair near me",
    "urgent plumber near me",
    "emergency pipe repair near me",
    "plumbing leak repair near me",
    "sink repair near me",
    "emergency toilet repair near me",
    "drain unblocking near me",
]

# Pick a random money keyword
if KEYWORD_OVERRIDE:
    KEYWORD = KEYWORD_OVERRIDE
else:
    KEYWORD = random.choice(MONEY_KEYWORDS)

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
        return False

def scroll_down(w, h):
    sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.78), w//2, int(h*0.25)))
    time.sleep(2)

# ================================================================
try:
    print("=" * 70)
    print("MONEY KW RUN: %s" % LABEL)
    print("Phone: %s | Target: %s,%s" % (PHONE, LAT, LNG))
    print("Business: %s | Keyword: %s" % (BUSINESS, KEYWORD))
    print("=" * 70)

    # ── STEP 0: Rotate proxy IP (before phone starts) ──
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
        print("  Rotation triggered - response: %s" % resp.status_code)
    except Exception as e:
        print("  WARNING: %s" % e)
    print("  Waiting 60s for IP propagation...")
    time.sleep(60)

    # ── PRE-FLIGHT: verify no other phone is running ──
    ALL_PHONES = [
        "614216763072053315","614216769833271363","614216773910135154",
        "614216777836003395","614216782181302642","614216786023284803",
        "614216790150480242","614216794093125699","614216798086103107",
        "614216802213298243","614216806256607602","614216810182475843",
        "614216814192230770","614216818470420547","614216822245294147",
        "614216826674479171","614216831053332547","614216835079864690",
        "614216838938624067","614216843351031875","614216847293677635",
        "614216851202768963","614216854994420082","614216858970620274",
        "614216863500468594","614216867258564675","614216871117324355",
        "614216875076747634","614216879120056690","614216883146588530",
        "614216886971793475","614216890796998723","614216895242960963",
        "614216899286270322","614216903581237315","614216908010422642",
        "614216911852404803","614216915744719218",
    ]
    others = [p for p in ALL_PHONES if p != PHONE]
    try:
        statuses = client.get_phone_status(others)
        running = [s["id"] for s in statuses if s.get("status") in (1, 2)]
        if running:
            print("[ABORT] %d other phone(s) still running! Stop them before starting a new one." % len(running))
            for r in running:
                print("  Running: %s" % r)
            sys.exit(1)
    except Exception as e:
        print("  Pre-flight check warning: %s" % e)
    print("  Pre-flight: no other phones running")

    # ── START PHONE ─────────────────────────────────────
    print()
    print("--- Starting %s ---" % LABEL)
    live_url = client.start_phone(PHONE)
    print("Live URL: %s" % (live_url or "none"))

    if live_url:
        redirect = ORCH / "scripts" / "_live_view_redirect.html"
        redirect.write_text('<meta http-equiv="refresh" content="0; url=%s">' % live_url, encoding="utf-8")
        subprocess.Popen(["cmd", "/c", "start", "", "chrome", "--new-window", str(redirect)], shell=False)
        print("Live view opened.")
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

    # ── IP check immediately after boot ──
    print("--- Checking IP ---")
    try:
        ip = sh("curl -s --max-time 10 https://ifconfig.me").strip()
        if not ip or "not found" in ip.lower():
            ip = sh("curl -s --max-time 10 https://api.ipify.org").strip()
        if ip:
            print("  Phone IP: %s" % ip)
        else:
            print("  Could not determine phone IP")
    except Exception as ee:
        print("  IP check failed: %s" % ee)

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
    sh("pm grant %s android.permission.ACCESS_FINE_LOCATION" % GPS)
    sh("pm grant %s android.permission.ACCESS_COARSE_LOCATION" % GPS)
    sh("pm grant %s android.permission.ACCESS_BACKGROUND_LOCATION" % GPS)
    sh("pm grant %s android.permission.POST_NOTIFICATIONS" % GPS)
    sh("appops set %s MOCK_LOCATION allow" % GPS)
    sh("appops set %s SYSTEM_ALERT_WINDOW allow" % GPS)
    sh("settings put secure mock_location_app %s" % GPS)
    sh("settings put secure mock_location 1")
    sh("settings put secure location_mode 3")
    sh("pm grant %s android.permission.ACCESS_FINE_LOCATION" % MAPS)
    sh("pm grant %s android.permission.ACCESS_COARSE_LOCATION" % MAPS)
    sh("pm grant %s android.permission.ACCESS_BACKGROUND_LOCATION" % MAPS)
    sh("pm grant %s android.permission.POST_NOTIFICATIONS" % MAPS)
    time.sleep(WAIT)
    print("[OK] Step 3")

    # ── STEP 4: Open GPS app ────────────────────────────────────
    print("\n=== STEP 4: Open App ===")
    sh("am force-stop %s" % GPS); time.sleep(2)
    sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % GPS)
    time.sleep(5)
    focus = get_focus()
    print("  Focus: %s" % focus[:200])
    print("[OK] Step 4")

    # ── STEP 4b: Handle system permission dialogs ──────────
    print("\n=== STEP 4b: Permission dialogs ===")
    perm_xml = dump_ui("perm_dialog")
    perm_texts = get_visible_texts(perm_xml)
    perm_joined = " ".join(perm_texts).lower()
    if any(w in perm_joined for w in ["allow gps", "location permission", "access this device", "location access"]):
        print("  Location permission dialog detected")
        found = find_and_tap(perm_xml, [
            "Allow all the time", "ALLOW ALL THE TIME",
            "While using the app", "WHILE USING THE APP",
            "Allow", "ALLOW", "OK",
        ], "Step4b-perm")
        if found:
            time.sleep(3)
        else:
            tap(int(w * 0.68), int(h * 0.68))
            time.sleep(3)
        focus = get_focus()
        print("  Focus after perm: %s" % focus[:150])
    else:
        print("  No permission dialog detected")
    print("[OK] Step 4b")

    # ── STEP 5: Privacy screen ──────────────────────────────
    print("\n=== STEP 5: Privacy screen ===")
    if "PrivacyActivity" in focus:
        px, py = int(w * 0.887), int(h * 0.910)
        print("  [Step5] Proportional tap at (%d, %d)" % (px, py))
        tap(px, py)
        time.sleep(WAIT)
        focus = get_focus()
        if "PrivacyActivity" in focus:
            xml = dump_ui("privacy")
            find_and_tap(xml, ["ACCEPT", "Accept", "Agree", "OK", "I AGREE"], "Step5")
            time.sleep(WAIT)
        print("[OK] Step 5")
    else:
        print("[OK] Step 5 - skip")

    # ── STEP 6: Setup wizard ────────────────────────────────
    print("\n=== STEP 6: Setup wizard ===")
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
        if find_and_tap(xml, ["Done", "FINISH", "Got it", "OK", "Let's Go"], "Step6-done"):
            time.sleep(WAIT); setup_done = True; break
    print("[OK] Step 6" if setup_done else "[WARN] Step 6")

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
            cx, cy = int(w * 0.42), int(h * 0.54)
            tap(cx, cy)
        time.sleep(WAIT)
    print("[OK] Step 6b")

    # ── STEP 6c: What's New ───────────────────────────────
    print("\n=== STEP 6c: What's New / blockers ===")
    xml = dump_ui("whatsnew")
    vis = get_visible_texts(xml)
    safe = [s.encode("ascii",errors="replace").decode() for s in vis]
    print("  Visible: %s" % (safe[:15],))
    tapped = False
    if any("Done" in v or "DONE" in v for v in vis):
        found = find_and_tap(xml, ["Done", "DONE"], "Done")
        if not found:
            cx, cy = int(w * 0.5), int(h * 0.90)
            tap(cx, cy)
        time.sleep(WAIT); tapped = True
    if not tapped and any("CANCEL" in v or "DOWNLOAD" in v for v in vis):
        found = find_and_tap(xml, ["CANCEL"], "Cancel2")
        if not found:
            cx, cy = int(w * 0.42), int(h * 0.54)
            tap(cx, cy)
        time.sleep(WAIT); tapped = True
    if not tapped and any("CONTINUE" in v or "Consent" in v for v in vis):
        found = find_and_tap(xml, ["CONTINUE WITH ADS", "Continue", "Consent", "OK"], "blocker")
        if not found:
            cx, cy = int(w * 0.5), int(h * 0.85)
            tap(cx, cy)
        time.sleep(WAIT); tapped = True
    print("[OK] Step 6c")

    # ── STEP 7: Deep links ──────────────────────────────────
    print("\n=== STEP 7: Deep links ===")
    def send_deep_links():
        url = "gpsjoystick://teleport?lat=%s&lng=%s" % (LAT, LNG)
        print("  URL: %s" % url)
        sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
        time.sleep(5)
        sh("input keyevent KEYCODE_HOME"); time.sleep(3)
        sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
        time.sleep(WAIT)
        sh("input keyevent KEYCODE_HOME"); time.sleep(3)

    send_deep_links()
    print("[OK] Step 7")

    # ── STEP 8: Verify mock ────────────────────────────────
    print("\n=== STEP 8: Verify mock ===")
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

    mock_active, coords_match = verify_mock()
    if mock_active:
        print("[OK] Step 8 - GPS MOCK ACTIVE (%s)" % ("coords match" if coords_match else "check coords"))
    else:
        print("[WARN] Step 8 - Mock not active")

    # ── STEP 9: Retry if needed ─
    if not mock_active:
        print("\n=== STEP 9: Retry ===")
        sh("am force-stop %s" % GPS)
        time.sleep(2)
        send_deep_links()
        mock_active, coords_match = verify_mock()
        if mock_active:
            print("[OK] Step 9 - GPS MOCK ACTIVE after retry")
        else:
            print("  Attempt 2...")
            sh("am force-stop %s" % GPS)
            time.sleep(3)
            sh("settings put secure mock_location 0")
            time.sleep(1)
            sh("settings put secure mock_location 1")
            time.sleep(2)
            send_deep_links()
            time.sleep(5)
            mock_active, coords_match = verify_mock()
            if mock_active:
                print("[OK] Step 9 - GPS MOCK ACTIVE after retry 2")
            else:
                print("[FAIL] Step 9 - Mock failed after 2 retries")

    print("\n" + "=" * 70)
    print("GPS SETUP COMPLETE: %s" % LABEL)
    print("=" * 70)

    # ═══════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: MAPS WORKFLOW WITH MONEY KEYWORD
    # ═══════════════════════════════════════════════════════════════
    # Same proven structure as _full_run_with_maps.py
    # RPA accessibility reader sees Maps result text (uiautomator cannot)
    print()
    print("=" * 70)
    print("MAPS WORKFLOW: %s" % LABEL)
    print("Keyword: %s | Business: %s" % (KEYWORD, BUSINESS))
    print("=" * 70)

    # Pre-flight: check Maps is installed
    maps_check = sh("pm path com.google.android.apps.maps")
    if "maps" not in maps_check.lower():
        print("[ABORT] Google Maps is not installed on this phone. Provision it first.")
        sys.exit(1)

    gal = client.export_rpa_flow("624431313889263826")
    data = json.loads(gal)

    for step in data["content"]["contents"]:
        # Inject money keyword into inputContent step
        if step["type"] == "inputContent":
            step["config"]["content"] = [KEYWORD]
            print("Injected keyword: %s" % KEYWORD)
        # Inject business name into click step + add waitTime
        if step["type"] == "forTimes":
            new_children = []
            for child in step["config"]["children"]:
                new_children.append(child)
                if child["type"] == "click":
                    for fc in child["config"].get("filterCollection", []):
                        for f in fc:
                            if f.get("type") == "text":
                                f["content"] = BUSINESS
                                print("Injected business: %s" % BUSINESS)
                    new_children.append({
                        "type": "waitTime",
                        "config": {"_a": "8", "_b": "10"}
                    })
                    print("Added waitTime(8-10s) after click")
            step["config"]["children"] = new_children

    new_id = client.import_rpa_flow(json.dumps(data))
    print("Flow ID: %s" % new_id)

    task_id = client.run_custom_flow(
        flow_id=new_id,
        phone_id=PHONE,
        param_map={},
        task_name="Money KW - %s" % LABEL
    )
    print("Task ID: %s" % task_id)

    # Monitor
    print("Waiting for flow...")
    deadline = time.time() + 300
    final_st = "Unknown"
    while time.time() < deadline:
        time.sleep(5)
        tasks = client.query_tasks([task_id])
        status = tasks[0].get("status") if tasks else -1
        sm = {1: "Waiting", 2: "InProgress", 3: "Completed", 4: "Failed", 7: "Cancelled"}
        final_st = sm.get(status, str(status))
        elapsed = int(time.time() - (deadline - 300))
        print("  t+%ds: %s" % (elapsed, final_st))
        if final_st in ("Completed", "Failed", "Cancelled"):
            break

    print("Flow ended: %s" % final_st)

    # Check business page
    time.sleep(2)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/final.xml"})
    time.sleep(1.5)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/final.xml"})
    xml = r.get("output", "") or ""
    texts = re.findall(r'text="([^"]*)"', xml)
    joined = " ".join(texts)
    markers = ["Directions", "Call", "Website", "Reviews", "Share", "Save", "Overview"]
    found_markers = [m for m in markers if m.lower() in joined.lower()]
    has_biz = BUSINESS.lower() in joined.lower()
    print("Business visible: %s | Markers: %s" % (has_biz, found_markers))

    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: BUSINESS INTERACTIONS
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("BUSINESS INTERACTIONS: %s" % LABEL)
    print("=" * 70)

    def interact_find_text(xml_str, text_query):
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

    def interact_is_on_biz():
        r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/interact_check.xml"})
        time.sleep(1.5)
        r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/interact_check.xml"})
        x = r.get("output", "") or ""
        ts = re.findall(r'text="([^"]*)"', x)
        j = " ".join(ts)
        bw = BUSINESS.split()
        pb = " ".join(bw[:2]).lower() if len(bw) >= 2 else BUSINESS.lower()
        hb = BUSINESS.lower()[:20] in j.lower() or pb in j.lower()
        mk = ["Directions", "Call", "Save", "Overview", "Reviews", "Photos"]
        fm = [m for m in mk if m.lower() in j.lower()]
        return hb or len(fm) >= 2, x

    def interact_recover(max_attempts=4):
        for _ in range(max_attempts):
            ok, x = interact_is_on_biz()
            if ok: return True, x
            focus = sh("dumpsys window | grep mCurrentFocus").strip()
            if "dialer" in focus.lower() or "contacts" in focus.lower():
                sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
            elif "launcher" in focus.lower():
                sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1")
                time.sleep(3)
            elif "maps" in focus.lower():
                sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
            else:
                sh("input keyevent KEYCODE_BACK"); time.sleep(1)
        return False, x

    on_biz, xml = interact_is_on_biz()
    if not on_biz:
        print("[WARN] Not on business page, skipping interactions")
    else:
        # Directions FIRST (major interaction — guaranteed while page freshest)
        print("\n--- Directions ---")
        on_biz, xml = interact_is_on_biz()
        if not on_biz: on_biz, xml = interact_recover()
        if on_biz:
            dc = interact_find_text(xml, "Directions")
            if dc:
                tap(dc[0], dc[1]); time.sleep(3)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/dir_mode.xml"})
                time.sleep(1.5); r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/dir_mode.xml"})
                dx = interact_find_text(r.get("output","") or "", "Drive")
                if dx: tap(dx[0], dx[1]); time.sleep(2)
                else: tap(int(w*0.15), int(h*0.085)); time.sleep(2)
                time.sleep(6)
                sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                print("[OK] Directions")
            else:
                print("[SKIP] Directions — button not found")

        # Reviews
        print("--- Reviews ---")
        on_biz, xml = interact_is_on_biz()
        if not on_biz: on_biz, xml = interact_recover()
        if on_biz:
            rc = interact_find_text(xml, "Reviews")
            if not rc:
                for i in range(4):
                    if rc: break
                    sh("input swipe %d %d %d %d 400" % (w//2, int(h*0.58), w//2, int(h*0.22)))
                    time.sleep(1.5)
                    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/rev_s%d.xml" % i})
                    time.sleep(1.5)
                    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/rev_s%d.xml" % i})
                    rc = interact_find_text(r.get("output","") or "", "Reviews")
            if rc:
                tap(rc[0], rc[1]); time.sleep(3)
                for i in range(3):
                    sh("input swipe %d %d %d %d 500" % (w//2, int(h*0.70), w//2, int(h*0.30)))
                    time.sleep(3)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/rev_exp.xml"})
                time.sleep(1.5)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/rev_exp.xml"})
                mc = interact_find_text(r.get("output","") or "", "More")
                if mc: tap(mc[0], mc[1]); time.sleep(2)
                sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                print("[OK] Reviews")

        # Website
        print("--- Website ---")
        on_biz, xml = interact_is_on_biz()
        if not on_biz: on_biz, xml = interact_recover()
        if on_biz:
            wc = interact_find_text(xml, "Website")
            if not wc:
                sh("input swipe %d %d %d %d 200" % (w//2, int(h*0.45), w//2, int(h*0.30)))
                time.sleep(1.5)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/web_s.xml"})
                time.sleep(1.5)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/web_s.xml"})
                wc = interact_find_text(r.get("output","") or "", "Website")
            if wc:
                tap(wc[0], wc[1]); time.sleep(5)
                sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                focus = sh("dumpsys window | grep mCurrentFocus").strip()
                if "maps" not in focus.lower():
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1)
                print("[OK] Website")
            else:
                print("[SKIP] Website not available")

        # Photos
        print("--- Photos ---")
        on_biz, xml = interact_is_on_biz()
        if not on_biz: on_biz, xml = interact_recover()
        if on_biz:
            pc = interact_find_text(xml, "Photos")
            if not pc:
                sh("input swipe %d %d %d %d 300" % (w//2, int(h*0.50), w//2, int(h*0.25)))
                time.sleep(1.5)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/photo_s.xml"})
                time.sleep(1.5)
                r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/photo_s.xml"})
                pc = interact_find_text(r.get("output","") or "", "Photos")
            if pc:
                tap(pc[0], pc[1]); time.sleep(3)
                for i in range(3):
                    sh("input swipe %d %d %d %d 300" % (int(w*0.75), h//2, int(w*0.25), h//2))
                    time.sleep(2.5)
                sh("input keyevent KEYCODE_BACK"); time.sleep(2)
                print("[OK] Photos")

        # Phone Call (last)
        print("--- Phone Call ---")
        on_biz, xml = interact_is_on_biz()
        if not on_biz: on_biz, xml = interact_recover()
        if on_biz:
            cc = interact_find_text(xml, "Call")
            if cc:
                tap(cc[0], cc[1]); time.sleep(3)
                sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
                focus = sh("dumpsys window | grep mCurrentFocus").strip()
                if "dialer" in focus.lower() or "contacts" in focus.lower():
                    sh("input keyevent KEYCODE_BACK"); time.sleep(1)
                print("[OK] Phone Call")

    # ── Stop phone (skip if "leave" passed) ──
    if LEAVE_OPEN:
        print()
        print("--- Phone LEFT RUNNING (leave mode) ---")
    else:
        print()
except Exception as _run_err:
    print("\n[CRASH] %s" % _run_err)
    import traceback; traceback.print_exc()

finally:
    print()
    # Only stop if PHONE was defined (script didn't crash before CSV lookup)
    if 'PHONE' in dir() and PHONE:
        print("--- Stopping phone ---")
        for _retry in range(3):
            try:
                client.stop_phone(PHONE)
                print("Phone stopped.")
                break
            except Exception as _se:
                print("Stop error (attempt %d): %s" % (_retry+1, _se))
                time.sleep(5)
    else:
        print("--- Phone not started, nothing to stop ---")
print("MONEY KW RUN COMPLETE: %s" % LABEL)
print("Keyword: %s" % KEYWORD)
print("=" * 70)
