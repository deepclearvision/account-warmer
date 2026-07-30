#!/usr/bin/env python3
"""
GPS + Maps v3 — STRIPPED.  No fallbacks.  No GAL bakers.  One code path per step.

Flow:
  1. SHELL: Close all apps
  2. SHELL: pm clear Fake GPS storage + force-stop (clean slate, no relaunch)
  3. SHELL: Permissions + OverlayService + deep-link coords → activate mock
  4. SHELL: Verify dumpsys → notification shade Hide → HOME → verify persists
  5. SHELL: Open Maps via geo: intent with keyword
  6. RPA:  Run "Test A" flow to search → scroll → find business → click
  7. SHELL: Verify card opened → tap interactions → screenshots

Key rule: NEVER open the GPS app after mock is active — it resets coords.

Usage:
  python gps_maps_v3_stripped.py --account acc_005 --keyword-type branded --watch
"""
import argparse
import csv
import json
import math
import random
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ── Paths ---------------------------------------------------------─
_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_orchestrator_root = _repo_root / "geelark_orchestrator"
_orchestrator_src = _orchestrator_root / "src"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient, _post

# ── Constants ------------------------------------------------------─
CSV_PATH = _repo_root / "data" / "accounts_business_mapping.csv"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
MAPS_PACKAGE = "com.google.android.apps.maps"
TEST_A_FLOW_ID = "620084214733209921"   # RPA flow: openApp → inputText → scroll → find → click
START_TIMEOUT = 120
GPS_ACTIVATE_TIMEOUT = 120

# ── Logging ---------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat()


def _log(msg: str, log_file: Path) -> None:
    line = f"[{_now()}] {msg}"
    print(line)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── CSV helpers ---------------------------------------------------──

def load_all_rows() -> list[dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pick_row(rows: list[dict], account_id: str) -> dict:
    for r in rows:
        if r.get("account_id") == account_id:
            return r
    raise ValueError(f"Account '{account_id}' not found in CSV")


def pick_keyword(row: dict, keyword_type: str) -> str:
    """Pick a random keyword of the given type from the CSV row."""
    if keyword_type == "discovery":
        raw = row.get("discovery_keywords", "")
    elif keyword_type == "standalone":
        raw = row.get("standalone_keywords", "")
    else:
        raw = row.get("branded_keywords", "")
    keywords = [k.strip() for k in raw.split("|") if k.strip()]
    if not keywords:
        return row["business_name"]
    return random.choice(keywords)


def pick_nearby_point(row: dict, business_lat: float, business_lng: float) -> tuple[float, float]:
    """Pick a random nearby point within 2km of the business, or the business itself."""
    nearby_raw = row.get("nearby_points", "")
    points = []
    if nearby_raw:
        for pt in nearby_raw.split("|"):
            pt = pt.strip()
            if not pt:
                continue
            try:
                lat_s, lng_s = pt.split(",")
                n_lat, n_lng = float(lat_s), float(lng_s)
                dist = _haversine_km(business_lat, business_lng, n_lat, n_lng)
                if dist <= 2.0:
                    points.append((n_lat, n_lng))
            except Exception:
                pass
    if points:
        return random.choice(points)
    return (business_lat, business_lng)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Shell helpers ---------------------------------------------------

def _shell(phone_id: str, cmd: str) -> str:
    """Execute a shell command on the phone. Returns output string."""
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
    return r.get("output", "") or ""


def _tap(phone_id: str, x: int, y: int) -> None:
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {x} {y}"})


def _swipe(phone_id: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input swipe {x1} {y1} {x2} {y2} {duration_ms}"})


def _keyevent(phone_id: str, code: str) -> None:
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input keyevent {code}"})


def _get_screen_size(phone_id: str) -> tuple[int, int]:
    """Query phone screen size via wm size. Returns (width, height)."""
    out = _shell(phone_id, "wm size")
    for line in out.splitlines():
        m = re.search(r"size:\s*(\d+)x(\d+)", line, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 720, 1440   # fallback


def _prop_tap(w: int, h: int, rel_x: float, rel_y: float) -> tuple[int, int]:
    """Convert relative (0.0-1.0) coordinates to absolute pixel position."""
    return int(w * rel_x), int(h * rel_y)


def _find_text_bounds(xml: str, text_query: str) -> tuple[int, int] | None:
    """Find the centre point of a UI node whose text or content-desc matches."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    best = None
    best_score = 999
    q = text_query.lower()
    for node in root.iter("node"):
        t = node.get("text", "").strip().lower()
        cd = node.get("content-desc", "").strip().lower()
        if t == q or cd == q:
            score = 0
        elif t.startswith(q) or cd.startswith(q):
            score = 1
        elif q in t or q in cd:
            score = 2
        else:
            continue
        if score < best_score:
            best_score = score
            bounds = node.get("bounds", "")
            try:
                coords = bounds.replace("[", "").replace("]", ",").rstrip(",").split(",")
                x1, y1, x2, y2 = map(int, coords)
                return ((x1 + x2) // 2, (y1 + y2) // 2)
            except Exception:
                pass
    return best


def _dump_ui(phone_id: str, tag: str = "ui") -> tuple[str, list[str]]:
    """Take a uiautomator dump, return (raw_xml, list_of_text_labels)."""
    path = f"/sdcard/{tag}_dump.xml"
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"uiautomator dump {path}"})
    time.sleep(1)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"cat {path}"})
    xml = r.get("output", "")
    if not xml.startswith("<"):
        raise RuntimeError(f"uiautomator dump returned non-XML: {xml[:200]}")
    root = ET.fromstring(xml)
    texts = []
    for node in root.iter("node"):
        t = node.get("text", "").strip()
        if t:
            texts.append(t)
        cd = node.get("content-desc", "").strip()
        if cd and cd != t:
            texts.append(cd)
    return xml, texts


def _dumpsys_mock_check(phone_id: str, target_lat: float, target_lng: float) -> tuple[bool, str, dict]:
    """
    Verify GPS mock is active at target coordinates.

    Checks:
      1. last mock location is present (not null) → mock is active
      2. Coordinates match target within 0.0002 deg (~22m)
      3. All providers (gps, network, fused) show [mock]

    NOTE: et= field is phone uptime, not mock age. ADD/REMOVE events
          only appear on toggle, not during steady-state mock.
    """
    out = _shell(phone_id, "dumpsys location")
    details = {
        "mock_active": False,
        "coords_match": False,
        "coords": "",
        "gps_mock": False,
        "network_mock": False,
        "fused_mock": False,
    }

    in_gps = False
    in_network = False
    for line in out.splitlines():
        # Track which provider section we're in
        if "gps provider" in line.lower():
            in_gps = True
            if "[mock]" in line.lower():
                details["gps_mock"] = True
        elif "network provider" in line.lower():
            in_gps = False
            in_network = True
            if "[mock]" in line.lower():
                details["network_mock"] = True
        elif "fused" in line.lower() and "provider" in line.lower():
            in_network = False

        # Check for mock location
        if "last mock location" in line.lower():
            m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
            if m:
                details["coords"] = f"{m.group(1)},{m.group(2)}"
                details["mock_active"] = True
                dl, dln = float(m.group(1)), float(m.group(2))
                if abs(dl - target_lat) <= 0.0002 and abs(dln - target_lng) <= 0.0002:
                    details["coords_match"] = True

        # fused provider mock check
        if "fused" in line.lower() and "[mock]" in line.lower():
            details["fused_mock"] = True

    if not details["mock_active"]:
        return False, "no last mock location found — mock provider not active", details
    if not details["coords_match"]:
        return False, f"coords mismatch: got {details['coords']} expected {target_lat},{target_lng}", details
    return True, f"mock active at {details['coords']}", details


# ── Phone lifecycle ------------------------------------------------─

def _start_phone(client: GeelarKClient, phone_id: str, log_file: Path, open_browser: bool = False) -> str:
    """Start the phone. Returns live_view_url. Raises on failure."""
    # Check status first
    try:
        statuses = client.get_phone_status([phone_id])
        s = statuses[0].get("status", -1) if statuses else -1
    except Exception:
        s = -1

    _log(f"Initial phone status: {s} (0=running, 1=starting, 2=stopped)", log_file)

    if s == 0:
        _log(f"Phone {phone_id} already running.", log_file)
        try:
            live_url = client.start_phone(phone_id)
            if open_browser and live_url:
                _open_live_view(live_url)
            return live_url
        except Exception:
            return ""

    _log(f"Starting phone {phone_id} ...", log_file)
    live_url = client.start_phone(phone_id)
    _log(f"Live URL: {live_url}", log_file)

    # Open browser IMMEDIATELY so user can watch
    if open_browser and live_url:
        _open_live_view(live_url)

    deadline = time.time() + START_TIMEOUT
    poll_count = 0
    while time.time() < deadline:
        time.sleep(5)
        poll_count += 1
        try:
            statuses = client.get_phone_status([phone_id])
            s = statuses[0].get("status", -1) if statuses else -1
            remaining = int(deadline - time.time())
            _log(f"  Poll {poll_count}: status={s} ({remaining}s remaining)", log_file)
            if s == 0:
                _log("Phone running.", log_file)
                return live_url
        except Exception as e:
            _log(f"  Poll {poll_count} error: {e}", log_file)
    raise RuntimeError(f"Phone did not reach status 0 within {START_TIMEOUT}s (last status={s})")


def _stop_phone(client: GeelarKClient, phone_id: str) -> None:
    """Stop the phone. Idempotent."""
    try:
        client.stop_phone(phone_id)
    except Exception:
        pass


def _open_live_view(url: str) -> None:
    """Open the phone's live view URL in a new Chrome window."""
    print(f"\n  Opening live view: {url}")
    try:
        redirect_path = Path(__file__).parent / "_live_view_redirect.html"
        with open(redirect_path, "w", encoding="utf-8") as f:
            f.write(f'<meta http-equiv="refresh" content="0; url={url}">')
        subprocess.Popen(
            ["cmd", "/c", "start", "", "chrome", "--new-window", str(redirect_path)],
            shell=False,
        )
        print("  Chrome launched in NEW WINDOW.")
    except Exception as e:
        print(f"  WARNING: Could not auto-open Chrome: {e}")
        print(f"  >>> Open this URL manually: {url}")


# ==================================================================═
# STEP 1: Close all apps
# ==================================================================═

def step1_close_all_apps(phone_id: str, log_file: Path) -> None:
    _log("", log_file)
    _log("=== STEP 1: Close all apps ===", log_file)

    packages = [
        MAPS_PACKAGE,
        FAKE_GPS_PACKAGE,
        "com.android.vending",
        "com.google.android.gms",
        "com.android.chrome",
    ]
    for pkg in packages:
        _shell(phone_id, f"am force-stop {pkg}")
        _log(f"  force-stop: {pkg}", log_file)

    time.sleep(1)

    # Swipe away any lingering recent apps
    _keyevent(phone_id, "KEYCODE_HOME")
    time.sleep(0.5)
    _keyevent(phone_id, "KEYCODE_APP_SWITCH")
    time.sleep(0.5)
    _swipe(phone_id, 360, 600, 360, 1200, 300)
    time.sleep(0.5)
    _keyevent(phone_id, "KEYCODE_HOME")
    time.sleep(1)

    _log("  [OK] All apps closed", log_file)


# ==================================================================═
# STEP 2: Clear GPS app storage
# ==================================================================═

def step2_clear_gps_storage(phone_id: str, log_file: Path) -> None:
    _log("", log_file)
    _log("=== STEP 2: Clear GPS app storage ===", log_file)

    # pm clear wipes all app data (settings, saved coords, ad state)
    out = _shell(phone_id, f"pm clear {FAKE_GPS_PACKAGE}")
    _log(f"  pm clear output: {out.strip()}", log_file)
    time.sleep(1)

    # Force-stop to ensure clean state — app will start fresh next launch
    _shell(phone_id, f"am force-stop {FAKE_GPS_PACKAGE}")
    time.sleep(1)

    _log("  [OK] GPS app storage cleared, app force-stopped", log_file)


# ==================================================================═
# STEP 3: Set GPS via deep-link (shell-only)
# ==================================================================═

def step3_set_gps(phone_id: str, lat: float, lng: float, log_file: Path) -> bool:
    """
    Set GPS coordinates via shell commands using Fake GPS JoyStick.

    This is the CORE step.  Every sub-step must succeed, and we verify
    with dumpsys at the end.  No fallbacks — if it fails, it fails loud.
    """
    _log("", log_file)
    _log(f"=== STEP 3: Set GPS to {lat},{lng} ===", log_file)

    screen_w, screen_h = _get_screen_size(phone_id)
    _log(f"  Screen: {screen_w}x{screen_h}", log_file)

    # ── 3a. Permissions ------------------------------------------
    _log("  3a. Setting permissions...", log_file)
    _shell(phone_id, f"appops set {FAKE_GPS_PACKAGE} MOCK_LOCATION allow")
    _shell(phone_id, f"settings put secure mock_location_app {FAKE_GPS_PACKAGE}")
    _shell(phone_id, "settings put secure mock_location 1")
    _shell(phone_id, "settings put secure location_mode 3")
    time.sleep(1)
    _log("    Permissions set.", log_file)

    # ── 3b. Launch MainActivity (app MUST be running for deep-link to work) ──
    _log("  3b. Opening Fake GPS MainActivity...", log_file)
    _shell(phone_id, f"am start -n {FAKE_GPS_PACKAGE}/{FAKE_GPS_PACKAGE}.MainActivity")
    time.sleep(5)
    _log("    MainActivity launched.", log_file)

    # ── 3c. Start OverlayService ---------------------------------─
    _log("  3c. Starting OverlayService...", log_file)
    _shell(phone_id, f"am startservice -n {FAKE_GPS_PACKAGE}/.service.OverlayService")
    time.sleep(3)
    _log("    OverlayService started.", log_file)

    # ── 3d. Send deep-link to set coordinates ---------------------
    url = f"gpsjoystick://teleport?lat={lat}&lng={lng}"
    _log(f"  3d. Sending deep-link: {url}", log_file)
    _shell(phone_id, f"am start -a android.intent.action.VIEW -d '{url}' {FAKE_GPS_PACKAGE}")
    time.sleep(10)
    _log("    Deep-link sent, waited 10s.", log_file)

    # ── 3e. Dismiss any dialogs (update prompts, welcome, etc.) ──
    _log("  3e. Checking for dialogs...", log_file)
    for i in range(1, 4):
        xml, texts = _dump_ui(phone_id, f"gps_dialog_{i}")
        dismissed = False
        for label in ("Cancel", "cancel", "CANCEL", "Done", "Got it", "OK", "Dismiss", "Continue to app", "Agree", "AGREE", "agree", "Accept", "ACCEPT"):
            pos = _find_text_bounds(xml, label)
            if pos:
                _log(f"    Dismissing '{label}' at {pos}", log_file)
                _tap(phone_id, pos[0], pos[1])
                time.sleep(3)
                dismissed = True
                break
        if not dismissed:
            _log("    No dialog found — screen is clean.", log_file)
            break

    # ── 3f. Activate mock: find and tap START button ------------──
    _log("  3f. Looking for START button...", log_file)
    start_found = False
    for attempt in range(1, 4):
        time.sleep(2)
        xml, texts = _dump_ui(phone_id, f"gps_start_{attempt}")
        for label in ("start using gps joystick", "Start using GPS Joystick",
                       "START USING GPS JOYSTICK", "Start", "START"):
            pos = _find_text_bounds(xml, label)
            if pos:
                _log(f"    Found '{label}' at {pos} (attempt {attempt})", log_file)
                _tap(phone_id, pos[0], pos[1])
                time.sleep(5)
                start_found = True
                break
        if start_found:
            break

    if not start_found:
        # Tap map area to activate mock (works when START isn't visible)
        _log("    START button not found — tapping map area...", log_file)
        map_x, map_y = _prop_tap(screen_w, screen_h, 0.50, 0.811)
        _log(f"    Tapping map area at ({map_x}, {map_y})", log_file)
        _tap(phone_id, map_x, map_y)
        time.sleep(5)

    # ── 3g. Verify mock via dumpsys (last mock location + coords match) ──
    _log("  3g. Verifying mock via dumpsys...", log_file)
    for attempt in range(1, 6):
        mock_ok, mock_info, mock_details = _dumpsys_mock_check(phone_id, lat, lng)
        _log(f"    Attempt {attempt}: ok={mock_ok} info={mock_info}", log_file)
        _log(f"      coords={mock_details.get('coords')} match={mock_details.get('coords_match')} "
             f"gps_mock={mock_details.get('gps_mock')} "
             f"network_mock={mock_details.get('network_mock')}", log_file)
        if mock_ok:
            _log("  [OK] GPS mock ACTIVE at correct coordinates", log_file)
            return True
        time.sleep(4)

    _log("  [FAIL] FAILED: GPS mock did not activate at target coordinates", log_file)
    return False


# ==================================================================═
# STEP 4: Hide notification + background + verify persistence
# ==================================================================═

def step4_hide_and_verify(phone_id: str, lat: float, lng: float, log_file: Path) -> bool:
    """
    Pull notification shade → tap Hide on GPS notification → HOME → verify mock persists.

    CRITICAL: After this step, we NEVER open the GPS app again.
    """
    _log("", log_file)
    _log("=== STEP 4: Hide notification + background + verify ===", log_file)

    screen_w, screen_h = _get_screen_size(phone_id)

    # ── 4a. Pull notification shade ------------------------------─
    _log("  4a. Pulling notification shade...", log_file)
    swipe_x = screen_w // 2
    swipe_y_start = int(screen_h * 0.007)
    swipe_y_end = int(screen_h * 0.556)
    _swipe(phone_id, swipe_x, swipe_y_start, swipe_x, swipe_y_end)
    time.sleep(2)

    # ── 4b. Find and tap Hide (or Start / Stop) on GPS notification ──
    _log("  4b. Looking for GPS notification action...", log_file)
    xml, texts = _dump_ui(phone_id, "gps_notification")
    action_tapped = False
    for label in ("Hide", "Start", "Stop", "clear all", "Clear all", "CLEAR ALL"):
        pos = _find_text_bounds(xml, label)
        if pos:
            _log(f"    Tapping '{label}' at {pos}", log_file)
            _tap(phone_id, pos[0], pos[1])
            time.sleep(2)
            action_tapped = True
            break

    if not action_tapped:
        _log("    WARNING: No notification action found — closing shade anyway", log_file)

    # ── 4c. Close notification shade ------------------------------
    _swipe(phone_id, swipe_x, swipe_y_end, swipe_x, swipe_y_start)
    time.sleep(1)

    # ── 4d. Go HOME ---------------------------------------------──
    _keyevent(phone_id, "KEYCODE_HOME")
    time.sleep(2)
    _log("  4d. Home screen — GPS app is now backgrounded.", log_file)

    # ── 4e. Verify mock persists ──
    _log("  4e. Verifying mock persists after background...", log_file)
    mock_ok, mock_info, mock_details = _dumpsys_mock_check(phone_id, lat, lng)
    _log(f"    mock={mock_ok} info={mock_info}", log_file)

    if mock_ok:
        _log("  [OK] Mock persists after background — GPS app will NOT be opened again", log_file)
        return True
    else:
        _log("  [FAIL] FAILED: Mock did not persist after background", log_file)
        return False


# ==================================================================═
# STEP 5: Open Maps via geo: intent
# ==================================================================═

def step5_open_maps(phone_id: str, keyword: str, log_file: Path) -> None:
    """
    Open Google Maps via a geo: intent with the search keyword.
    The RPA flow (Step 6) will handle the actual typing and search.
    This just ensures Maps is open and ready.
    """
    _log("", log_file)
    _log(f"=== STEP 5: Open Maps (keyword: '{keyword}') ===", log_file)

    # Open Maps via the main activity
    _shell(phone_id, f"am start -n {MAPS_PACKAGE}/com.google.android.maps.MapsActivity")
    time.sleep(5)
    _log("  Maps opened.", log_file)


# ==================================================================═
# STEP 6: RPA Maps flow — search → scroll → find business → click
# ==================================================================═

def step6_rpa_maps_flow(
    client: GeelarKClient,
    phone_id: str,
    keyword: str,
    business_name: str,
    log_file: Path,
) -> dict:
    """
    Dispatch the Test A RPA flow that handles:
      openApp(Maps) → inputText(keyword) → scroll → find business → click

    Returns dict with: {task_id, status, fail_desc, duration}
    """
    _log("", log_file)
    _log(f"=== STEP 6: RPA Maps flow ===", log_file)
    _log(f"  Keyword: '{keyword}'", log_file)
    _log(f"  Business: '{business_name}'", log_file)

    task_id = client.run_custom_flow(
        flow_id=TEST_A_FLOW_ID,
        phone_id=phone_id,
        param_map={
            "business_name": business_name,
            "search_term": keyword,
        },
        task_name=f"Find {business_name[:20]}",
        schedule_delay=5,
    )
    _log(f"  RPA task dispatched: {task_id}", log_file)

    # Poll for completion (timeout 120s)
    deadline = time.time() + 120
    task_status = None
    fail_desc = ""
    duration = 0

    while time.time() < deadline:
        time.sleep(5)
        try:
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                task_status = t.get("status")
                fail_desc = t.get("failDesc", "")
                duration = t.get("cost", 0)
                if task_status == 3:
                    _log(f"  [OK] RPA task COMPLETED ({duration}s)", log_file)
                    break
                elif task_status == 4:
                    _log(f"  [FAIL] RPA task FAILED: {fail_desc}", log_file)
                    break
                elif task_status == 7:
                    _log(f"  [FAIL] RPA task CANCELLED", log_file)
                    break
        except Exception as e:
            _log(f"  Poll error: {e}", log_file)

    if task_status is None:
        _log("  [FAIL] RPA task TIMED OUT (120s)", log_file)

    return {
        "task_id": task_id,
        "status": task_status,
        "fail_desc": fail_desc,
        "duration": duration,
    }


# ==================================================================═
# STEP 7: Verify + interact + evidence
# ==================================================================═

def step7_verify_and_interact(
    client: GeelarKClient,
    phone_id: str,
    business_name: str,
    screenshot_dir: Path,
    log_file: Path,
) -> dict:
    """
    After RPA flow completes:
      - Verify business card opened (uiautomator dump)
      - Tap interaction buttons (Save, Directions)
      - Capture screenshots
    """
    _log("", log_file)
    _log("=== STEP 7: Verify + interact ===", log_file)

    result = {
        "card_opened": False,
        "interactions_tapped": [],
        "screenshots": {},
    }

    def _screenshot(tag: str) -> str | None:
        try:
            data = client.take_screenshot(phone_id, max_wait=30)
            if data:
                path = screenshot_dir / f"{phone_id}_{tag}_{datetime.now().strftime('%H%M%S')}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                result["screenshots"][tag] = str(path)
                _log(f"  Screenshot [{tag}]: {path}", log_file)
                return str(path)
        except Exception as e:
            _log(f"  Screenshot error: {e}", log_file)
        return None

    time.sleep(3)
    _screenshot("01_after_rpa")

    # ── 7a. Verify card opened ------------------------------------
    _log("  7a. Checking if business card opened...", log_file)
    try:
        xml, texts = _dump_ui(phone_id, "maps_card")
        all_text = " ".join(texts).lower()
        # Look for card indicators
        indicators = set()
        for label in ("Photos", "Reviews", "Directions", "Save", "Share", "Call", "Website"):
            for t in texts:
                if label.lower() == t.lower():
                    indicators.add(label)
                    break
        for meta in ("about", "updates", "hours", "address", "phone", "rating"):
            if meta in all_text:
                indicators.add(meta)

        card_ok = len(indicators) >= 2
        result["card_opened"] = card_ok
        _log(f"    Card indicators found: {indicators}", log_file)
        _log(f"    Card opened: {card_ok}", log_file)
    except Exception as e:
        _log(f"    Card verify error: {e}", log_file)

    _screenshot("02_card_verify")

    # ── 7b. Tap interactions ------------------------------------──
    _log("  7b. Tapping interaction buttons...", log_file)
    try:
        xml, texts = _dump_ui(phone_id, "maps_interactions")
        # Find all visible interaction buttons
        visible = []
        for label in ("Save", "Directions", "Share", "Photos", "Reviews", "Call", "Website"):
            pos = _find_text_bounds(xml, label)
            if pos:
                visible.append((label, pos))

        if visible:
            _log(f"    Visible buttons: {[v[0] for v in visible]}", log_file)
            # Tap preferred interactions first
            preferred = [v for v in visible if v[0] in ("Save", "Directions", "Share")]
            to_tap = []
            if preferred:
                to_tap.append(random.choice(preferred))
            # Sometimes tap a second
            if len(visible) > 1 and random.random() > 0.3:
                pool = [v for v in visible if v not in to_tap]
                if pool:
                    to_tap.append(random.choice(pool))

            for label, (x, y) in to_tap:
                x += random.randint(-8, 8)
                y += random.randint(-8, 8)
                _log(f"    Tapping '{label}' at ({x},{y})", log_file)
                _tap(phone_id, x, y)
                result["interactions_tapped"].append(label)
                time.sleep(random.uniform(2, 5))
        else:
            _log("    No interaction buttons found.", log_file)
    except Exception as e:
        _log(f"    Interaction tap error: {e}", log_file)

    _screenshot("03_after_interactions")

    # ── 7c. Final state ------------------------------------------─
    _log("  7c. Final state capture...", log_file)
    _screenshot("04_final")

    return result


# ==================================================================═
# MAIN
# ==================================================================═

def main() -> int:
    ap = argparse.ArgumentParser(description="GPS + Maps v3 — Stripped, no fallbacks")
    ap.add_argument("--account", default="acc_005", help="Account ID to run")
    ap.add_argument("--keyword-type", choices=["discovery", "standalone", "branded"], default="branded")
    ap.add_argument("--watch", action="store_true", help="Open live view in browser")
    ap.add_argument("--keep-open", action="store_true", help="Leave phone running at end (don't stop)")
    ap.add_argument("--phone-id", help="Force a specific phone ID")
    args = ap.parse_args()

    # ── Load data ------------------------------------------------─
    client = GeelarKClient()
    rows = load_all_rows()
    row = pick_row(rows, args.account)

    phone_id = args.phone_id or row["geelark_phone_id"]
    email = row["account_email"]
    account_id = row["account_id"]
    business_name = row["business_name"]
    business_lat = float(row["business_lat"])
    business_lng = float(row["business_lng"])

    # Pick keyword
    keyword = pick_keyword(row, args.keyword_type)
    # Pick GPS point (nearby or exact business)
    target_lat, target_lng = pick_nearby_point(row, business_lat, business_lng)

    # ── Setup logging ---------------------------------------------
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = _orchestrator_root / "scripts" / f"v3_run_{account_id}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "run.log"
    screenshot_dir = log_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    # ── Print header ---------------------------------------------─
    _log("=" * 70, log_file)
    _log(f"GPS + MAPS v3 (STRIPPED — no fallbacks) — {timestamp}", log_file)
    _log("=" * 70, log_file)
    _log(f"Account:      {email} ({account_id})", log_file)
    _log(f"Phone ID:     {phone_id}", log_file)
    _log(f"Business:     {business_name}", log_file)
    _log(f"Business GPS: {business_lat},{business_lng}", log_file)
    _log(f"Target GPS:   {target_lat},{target_lng}", log_file)
    _log(f"Keyword type: {args.keyword_type}", log_file)
    _log(f"Keyword:      '{keyword}'", log_file)

    # ============================================================═
    # EXECUTION — each step must succeed before proceeding
    # ============================================================═

    try:
        # ── Start phone ---------------------------------------──
        _log("", log_file)
        _log("--- Starting phone ---", log_file)
        live_url = _start_phone(client, phone_id, log_file, open_browser=args.watch)

        if args.watch and live_url:
            _log("Live view opened -- waiting 5s for browser...", log_file)
            time.sleep(5)

        # ── STEP 1 ---------------------------------------------─
        step1_close_all_apps(phone_id, log_file)

        # ── STEP 2 ---------------------------------------------─
        step2_clear_gps_storage(phone_id, log_file)

        # ── STEP 3 ---------------------------------------------─
        gps_ok = step3_set_gps(phone_id, target_lat, target_lng, log_file)
        if not gps_ok:
            _log("", log_file)
            _log("FAILED: ABORTED: Step 3 (GPS activation) FAILED", log_file)
            _log(f"Log dir: {log_dir}", log_file)
            if not args.keep_open:
                _stop_phone(client, phone_id)
            else:
                _log("--keep-open: phone left running for manual inspection", log_file)
            return 1

        # ── STEP 4 ---------------------------------------------─
        persist_ok = step4_hide_and_verify(phone_id, target_lat, target_lng, log_file)
        if not persist_ok:
            _log("", log_file)
            _log("FAILED: ABORTED: Step 4 (mock persistence) FAILED", log_file)
            _log(f"Log dir: {log_dir}", log_file)
            if not args.keep_open:
                _stop_phone(client, phone_id)
            else:
                _log("--keep-open: phone left running for manual inspection", log_file)
            return 1

        # ── STEP 5 ---------------------------------------------─
        step5_open_maps(phone_id, keyword, log_file)

        # ── STEP 6 ---------------------------------------------─
        rpa_result = step6_rpa_maps_flow(client, phone_id, keyword, business_name, log_file)

        # ── STEP 7 ---------------------------------------------─
        verify_result = step7_verify_and_interact(
            client, phone_id, business_name, screenshot_dir, log_file
        )

        # ── Stop phone ------------------------------------------
        _log("", log_file)
        if not args.keep_open:
            _log("--- Stopping phone ---", log_file)
            _stop_phone(client, phone_id)
        else:
            _log("--- --keep-open: phone left running ---", log_file)

        # =========================================================
        # SUMMARY
        # =========================================================
        _log("", log_file)
        _log("=" * 70, log_file)
        _log("RUN COMPLETE — SUMMARY", log_file)
        _log("=" * 70, log_file)
        _log(f"  GPS set:       {'OK:' if gps_ok else 'FAILED:'}", log_file)
        _log(f"  GPS persist:   {'OK:' if persist_ok else 'FAILED:'}", log_file)
        _log(f"  RPA status:    {rpa_result['status']} "
             f"({'SUCCESS' if rpa_result['status'] == 3 else 'FAILED' if rpa_result['status'] == 4 else 'OTHER'})",
             log_file)
        _log(f"  RPA duration:  {rpa_result['duration']}s", log_file)
        _log(f"  Card opened:   {verify_result['card_opened']}", log_file)
        _log(f"  Interactions:  {verify_result['interactions_tapped'] or 'NONE'}", log_file)
        _log(f"  Screenshots:   {len(verify_result['screenshots'])}", log_file)
        _log(f"", log_file)
        _log(f"  Log dir:       {log_dir}", log_file)

        # ── Write JSON result ------------------------------------─
        result = {
            "timestamp": _now(),
            "account_id": account_id,
            "phone_id": phone_id,
            "business_name": business_name,
            "keyword": keyword,
            "target_lat": target_lat,
            "target_lng": target_lng,
            "gps_ok": gps_ok,
            "gps_persist_ok": persist_ok,
            "rpa_status": rpa_result["status"],
            "rpa_duration": rpa_result["duration"],
            "rpa_fail_desc": rpa_result["fail_desc"],
            "card_opened": verify_result["card_opened"],
            "interactions_tapped": verify_result["interactions_tapped"],
            "screenshots": verify_result["screenshots"],
        }
        json_path = log_dir / "result.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        _log(f"  Result JSON:   {json_path}", log_file)

        # Determine exit code
        overall_ok = gps_ok and persist_ok
        return 0 if overall_ok else 1

    except Exception as e:
        _log("", log_file)
        _log(f"FAILED: UNHANDLED ERROR: {e}", log_file)
        import traceback
        _log(traceback.format_exc(), log_file)
        if not args.keep_open:
            try:
                _stop_phone(client, phone_id)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
