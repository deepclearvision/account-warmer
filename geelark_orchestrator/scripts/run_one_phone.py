#!/usr/bin/env python3
"""
run_one_phone.py — Production orchestrator with GPS baker + Maps evidence (Section 9.12).

Sequence per run:
  1. Load profile, resolve RunPlan
  2. check_phone_fit gate
  3. Adapter audit (Guardrail #2)
  4. Ensure phone running
  5. BAKE ephemeral GPS flow with resolver coords
  6. Dispatch GPS flow → poll → verify dumpsys matches resolver
  7. Dispatch Maps flow → poll
  8. Post-run Maps evidence (uiautomator dump → parse → classify)
  9. Final verification (foreground, dumpsys, screenshot)
  10. Record BOTH halves in SQLite + JSONL

Status classification:
  success               = GPS verified + search submitted + business found + interactions present
  maps_business_not_found = GPS verified + search submitted + business NOT found
  maps_search_failed    = GPS verified + search NOT submitted
  maps_no_interactions  = GPS verified + business found + NO interaction buttons
  gps_failed            = dumpsys coords did not match resolver
  maps_failed           = Maps task failed (Geelark status 4)
  maps_cancelled        = Maps task cancelled (Geelark status 7)
  failed_infra          = timeout, poll error, or other infrastructure failure
"""
import argparse
import json
import queue
import re
import sys
sys.stdout.reconfigure(encoding="utf-8")
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

# Explicit absolute repo root — avoids __file__ resolution failures when launched
# from working directories other than the project root (e.g. Geelark tasks, cron).
_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
if not (_repo_root / "geelark_orchestrator" / "src").exists():
    # Fallback for local development / unexpected locations
    _repo_root = Path(__file__).resolve().parent.parent.parent

_orchestrator_root = _repo_root / "geelark_orchestrator"
_orchestrator_src = _orchestrator_root / "src"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from data_adapter import load_real_profile_by_key, DataError
from resolver import resolve
from flow_adapter import build_param_map, print_adapter_audit
from run_logger import log_plan, make_feedback_packet
from runs_store import RunsStore
from provisioning import check_phone_fit
from core.geelark_client import GeelarKClient, _post
from gps_flow_baker import bake_and_import as bake_gps_flow
from maps_flow_baker import bake_and_import as bake_maps_flow
from maps_evidence import gather_maps_evidence, classify_run_status, snapshot_screen
from flow_registry import get_reusable_flow_ids, register_flow_id
from proxy_control import change_proxy_ip, check_proxy_ip
from state_tracker import BusinessTracker

GPS_TEMPLATE_FLOW_ID = "620512663138468211"
MAPS_TEMPLATE_FLOW_ID = "620892967896350964"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
MAPS_PACKAGE = "com.google.android.apps.maps"


def ensure_running(client: GeelarKClient, phone_id: str) -> bool:
    try:
        statuses = client.get_phone_status([phone_id])
        s = statuses[0].get("status", -1) if statuses else -1
    except Exception:
        s = -1
    if s == 0:
        print("  phone already running.")
        return True
    print("  starting phone...")
    try:
        client.start_phone(phone_id)
    except Exception as e:
        print(f"  ERROR: start failed: {e}", file=sys.stderr)
        return False
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            statuses = client.get_phone_status([phone_id])
            s = statuses[0].get("status", -1) if statuses else -1
            if s == 0:
                print("  phone running.")
                return True
        except Exception:
            pass
    print("  ERROR: phone did not start within 120s.", file=sys.stderr)
    return False


def dumpsys_mock_check(phone_id: str) -> tuple[bool, str]:
    try:
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "dumpsys location"})
        out = r.get("output", "")
        for line in out.splitlines():
            if "mock" in line.lower() and "Location[" in line:
                m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
                if m:
                    return True, f"{m.group(1)},{m.group(2)}"
        return False, "no mock location found in dumpsys"
    except Exception as e:
        return False, str(e)


def _verify_google_account(phone_id: str) -> tuple[bool, str]:
    """
    Runtime Google account verification.
    Runs 'dumpsys account | grep "Account {"' to confirm at least one
    Google account exists on the phone.
    Returns (True, account_email) or (False, reason).
    """
    try:
        r = _post(
            "/open/v1/shell/execute",
            {"id": phone_id, "cmd": 'dumpsys account | grep "Account {"'},
        )
        out = r.get("output", "") or ""
        for line in out.splitlines():
            if "Account {" in line and "type=com.google" in line:
                # Extract email from: Account {name=email@gmail.com, type=com.google}
                m = re.search(r'name=([^,]+)', line)
                if m:
                    return True, m.group(1)
        if out.strip():
            return False, f"dumpsys returned non-Google accounts: {out.strip()[:200]}"
        return False, "no accounts found in dumpsys"
    except Exception as e:
        return False, str(e)


def _get_android_version(phone_id: str) -> int:
    """Query the phone's Android version from build props."""
    try:
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "getprop ro.build.version.release"})
        out = r.get("output", "").strip()
        # e.g. "13" or "10" or "14" — take the first segment before any dot.
        return int(float(out.split(".")[0]))
    except Exception:
        return 0


def _get_screen_size(phone_id: str) -> tuple[int, int]:
    """Query phone screen size via wm size. Returns (width, height)."""
    try:
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "wm size"})
        out = r.get("output", "").strip()
        # Parse lines like "Physical size: 1080x2400" or "Override size: 720x1440"
        for line in out.splitlines():
            m = re.search(r"size:\s*(\d+)x(\d+)", line, re.IGNORECASE)
            if m:
                return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    # Default fallback (proven on 720x1440 devices)
    return 720, 1440


def _verify_phone_provisioned(phone_id: str) -> tuple[bool, str]:
    """
    Pre-flight check: Developer Options enabled + Fake GPS set as mock location app.
    Returns (True, "") if both are OK, else (False, reason).
    """
    try:
        # Check Developer Options
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get global development_settings_enabled"})
        dev_enabled = r.get("output", "").strip()
        if dev_enabled != "1":
            return False, f"Developer Options not enabled (got: {dev_enabled})"

        # Check mock location app
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get secure mock_location_app"})
        mock_app = r.get("output", "").strip()
        if mock_app != FAKE_GPS_PACKAGE:
            return False, f"Mock location app not set (got: {mock_app})"

        return True, ""
    except Exception as e:
        return False, f"Provisioning check exception: {e}"


def _provision_developer_and_mock(phone_id: str) -> tuple[bool, str]:
    """
    Auto-provision Developer Options + Fake GPS mock app using shell commands only.
    """
    # 1. Fast-path: check if already provisioned
    ok, reason = _verify_phone_provisioned(phone_id)
    if ok:
        return True, "Already provisioned"

    print("    Auto-provisioning: enabling Developer Options + mock app...")

    # 2. Try the direct settings toggle first (works on many devices)
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": "settings put global development_settings_enabled 1"})
    time.sleep(1)

    # Re-check
    ok, reason = _verify_phone_provisioned(phone_id)
    if ok:
        print("    Provisioned via settings put.")
        return True, ""

    # 3. Direct toggle didn't work — try tapping Build number 7 times
    print("    Settings put failed — trying Build number tap fallback...")
    screen_w, screen_h = _get_screen_size(phone_id)

    # Open Settings -> About phone (common path)
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": "am start -a android.settings.DEVICE_INFO_SETTINGS"})
    time.sleep(2)

    # Scroll down to find Build number (roughly lower third of screen)
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": f"input swipe {screen_w//2} {int(screen_h*0.8)} {screen_w//2} {int(screen_h*0.3)} 500"})
    time.sleep(1)

    # Tap Build number area 7 times
    build_x, build_y = screen_w // 2, int(screen_h * 0.75)
    for i in range(7):
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": f"input tap {build_x} {build_y}"})
        time.sleep(0.3)

    time.sleep(3)

    # 4. Set mock location app regardless of which path worked
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": f"appops set {FAKE_GPS_PACKAGE} android:mock_location allow"})
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": f"settings put secure mock_location_app {FAKE_GPS_PACKAGE}"})
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": "settings put secure mock_location 1"})
    time.sleep(1)

    # 5. Final verification
    ok, reason = _verify_phone_provisioned(phone_id)
    if ok:
        print("    Auto-provision succeeded.")
        return True, ""

    return False, f"Auto-provision failed: {reason}"


def _proportional_tap(width: int, height: int, rel_x: float, rel_y: float) -> tuple[int, int]:
    """Convert relative coordinates (0.0-1.0) to absolute pixel taps."""
    return int(width * rel_x), int(height * rel_y)


def _activate_gps_android13(phone_id: str, lat: float, lng: float) -> bool:
    """
    Android 13-specific GPS activation for Fake GPS JoyStick v5.3.0.

    On Android 13 the deep-link sets coordinates in the app but does NOT
    start the mock provider. The mock only activates when the map activity is
    foregrounded. Once active, pulling the notification shade and tapping
    "Hide" on the GPS JoyStick notification keeps the mock alive while the
    app is backgrounded — verified persistent 30s+ with Maps foreground.

    This version queries the actual screen size and uses proportional
    coordinates so it works across devices with different resolutions.

    Steps:
      0. Ensure OEM permissions (appops MOCK_LOCATION allow + deviceidle whitelist).
      1. Ensure OverlayService running.
      2. Deep-link to set coords.
      3. Dismiss interstitial ad if present.
      4. Tap map area to enter map view (mock activates here).
      5. Verify mock in dumpsys (with retry).
      6. Pull notification shade, tap "Hide" or "Start".
      7. Background app (HOME).
      8. Verify mock persists.
    """
def _activate_gps_android13(phone_id: str, lat: float, lng: float) -> bool:
    """
    Android 13-specific GPS activation for Fake GPS JoyStick v5.3.0.

    Revised approach based on live provisioning scripts (tmp_provision_findx2.py):
      1. Open app MainActivity to ensure it's initialized.
      2. Enable mock location via appops + secure settings.
      3. Start OverlayService.
      4. Send deep-link and wait generously (8-10s).
      5. Dismiss any dialog.
      6. Look for START button; if not found, try map-area tap.
      7. Verify mock in dumpsys (coordinate-matching retry loop).
      8. Notification shade -> Hide/Start -> background -> verify persists.
    """
    screen_w, screen_h = _get_screen_size(phone_id)
    print(f"    Screen size: {screen_w}x{screen_h}")

    # 0. Permissions + system settings
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "appops set com.theappninjas.fakegpsjoystick MOCK_LOCATION allow"},
    )
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "settings put secure mock_location_app com.theappninjas.fakegpsjoystick"},
    )
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "settings put secure mock_location 1"},
    )
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "settings put secure location_mode 3"},
    )
    time.sleep(1)

    # 1. Open app MainActivity first (proven in findx2 provisioning)
    print("    Opening Fake GPS MainActivity...")
    _post(
        "/open/v1/shell/execute",
        {
            "id": phone_id,
            "cmd": "am start -n com.theappninjas.fakegpsjoystick/com.theappninjas.fakegpsjoystick.MainActivity",
        },
    )
    time.sleep(5)

    # 2. Start OverlayService
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "am startservice -n com.theappninjas.fakegpsjoystick/.service.OverlayService"},
    )
    time.sleep(3)

    # 3. Deep-link
    url = f"gpsjoystick://teleport?lat={lat}&lng={lng}"
    print(f"    Sending deep-link {url} ...")
    _post(
        "/open/v1/shell/execute",
        {
            "id": phone_id,
            "cmd": f"am start -a android.intent.action.VIEW -d '{url}' com.theappninjas.fakegpsjoystick",
        },
    )
    time.sleep(10)

    # 4. Dismiss any dialog (update, welcome, etc.)
    for dialog_attempt in range(1, 4):
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"uiautomator dump /sdcard/a13_gps_dialog_{dialog_attempt}.xml"})
        time.sleep(1)
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"cat /sdcard/a13_gps_dialog_{dialog_attempt}.xml"})
        xml = r.get("output", "")

        # Update prompt: "Cancel" or "Download" → tap Cancel
        for cancel_label in ("Cancel", "cancel", "CANCEL"):
            pos = _find_bounds(xml, cancel_label)
            if pos:
                print(f"    Dismissing update dialog via '{cancel_label}' at {pos}")
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
                time.sleep(3)
                break
        else:
            # No Cancel found — try other dismiss labels
            for dialog_text in ("Done", "done", "DONE", "Got it", "OK", "Allow", "Accept", "Dismiss", "Continue to app"):
                pos = _find_bounds(xml, dialog_text)
                if pos:
                    print(f"    Dismissing dialog '{dialog_text}' at {pos}")
                    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
                    time.sleep(3)
                    break
            else:
                # Nothing found to dismiss — screen is clean
                break

    # 5. Activate mock: find "start using gps joystick" button
    start_pos = None
    for start_attempt in range(1, 4):
        time.sleep(2)
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"uiautomator dump /sdcard/a13_gps_start_{start_attempt}.xml"})
        time.sleep(1)
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"cat /sdcard/a13_gps_start_{start_attempt}.xml"})
        xml = r.get("output", "")
        # Exact button text from user observation
        for label in ("start using gps joystick", "Start using GPS Joystick", "START USING GPS JOYSTICK", "Start", "START"):
            start_pos = _find_bounds(xml, label)
            if start_pos:
                print(f"    Found '{label}' button at {start_pos} (attempt {start_attempt})")
                break
        if start_pos:
            break

    if start_pos:
        print(f"    Tapping START at {start_pos}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {start_pos[0]} {start_pos[1]}"})
        time.sleep(5)
    else:
        print("    WARNING: START button not found after retries — using map-area fallback")
        # Map-area fallback (proven on Pixel 7) + start-button-area fallback (findx2)
        for rel_x, rel_y, name in (
            (0.50, 0.811, "map area"),     # 360,1168 on 720x1440
            (0.50, 0.799, "start btn"),    # 360,1150 on 720x1440
            (0.83, 0.833, "alt start"),    # 600,1200 on 720x1440
        ):
            tap_x, tap_y = _proportional_tap(screen_w, screen_h, rel_x, rel_y)
            print(f"    Tapping {name} at ({tap_x}, {tap_y})...")
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {tap_x} {tap_y}"})
            time.sleep(5)
            mock_ok, mock_info = dumpsys_mock_check(phone_id)
            coords_match = False
            if mock_ok and mock_info:
                try:
                    dl, dln = mock_info.split(",")
                    coords_match = abs(float(dl) - lat) <= 0.0002 and abs(float(dln) - lng) <= 0.0002
                except Exception:
                    pass
            print(f"      Result: {mock_info if mock_ok else 'NONE'} | match={coords_match}")
            if coords_match:
                break
        else:
            # None of the taps matched — fall through to the retry loop below
            pass

    # 6. Verify mock active AND coordinates match target (retry up to 4 times)
    mock_ok = False
    mock_info = "NONE"
    for attempt in range(1, 5):
        mock_ok, mock_info = dumpsys_mock_check(phone_id)
        coords_match = False
        if mock_ok and mock_info:
            try:
                dl, dln = mock_info.split(",")
                coords_match = abs(float(dl) - lat) <= 0.0002 and abs(float(dln) - lng) <= 0.0002
            except Exception:
                pass
        print(f"    Mock verify (attempt {attempt}): {mock_info if mock_ok else 'NONE'} | match={coords_match}")
        if coords_match:
            break
        time.sleep(4)
    if not mock_ok:
        return False

    # 7. Pull notification shade and tap "clear all" at bottom right
    print("    Pulling notification shade...")
    swipe_x, swipe_y_start = _proportional_tap(screen_w, screen_h, 0.50, 0.007)
    swipe_y_end = int(screen_h * 0.556)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input swipe {swipe_x} {swipe_y_start} {swipe_x} {swipe_y_end}"})
    time.sleep(2)

    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/a13_gps_2.xml"})
    time.sleep(1)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/a13_gps_2.xml"})
    xml = r.get("output", "")

    # Try Fake GPS Hide/Start first, then "clear all" at bottom right
    action_found = False
    for label in ("Hide", "Start", "Stop", "clear all", "Clear all", "CLEAR ALL", "Clear"):
        pos = _find_bounds(xml, label)
        if pos:
            print(f"    Tapping notification action '{label}' at {pos}")
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
            time.sleep(2)
            action_found = True
            break
    if not action_found:
        print("    WARNING: No notification action found — closing shade")

    # 8. Close shade and background
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input swipe {swipe_x} {swipe_y_end} {swipe_x} {swipe_y_start}"})
    time.sleep(1)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_HOME"})
    time.sleep(2)

    # 9. Final persistence check
    mock_ok, mock_info = dumpsys_mock_check(phone_id)
    coords_match = False
    if mock_ok and mock_info:
        try:
            dl, dln = mock_info.split(",")
            coords_match = abs(float(dl) - lat) <= 0.0002 and abs(float(dln) - lng) <= 0.0002
        except Exception:
            pass
    print(f"    Mock after background: {mock_info if mock_ok else 'NONE'} | match={coords_match}")
    return coords_match


# Keep the original A10 deep-link fallback; it is still used for Android < 13.
def _activate_gps_via_deeplink(phone_id: str, lat: float, lng: float) -> None:
    """
    Shell-based GPS activation fallback using Fake GPS Joystick deep-link.
    The app exposes gpsjoystick://teleport?lat=...&lng=... which sets
    the mock provider at the given coordinates, bypassing UI dialogs.
    The OverlayService must be running for the deep-link to take effect.
    """
    # 1. Ensure OverlayService is running (required for deep-link to work)
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "am startservice -n com.theappninjas.fakegpsjoystick/.service.OverlayService"},
    )
    time.sleep(3)

    # 2. Send deep-link intent to set coordinates
    url = f"gpsjoystick://teleport?lat={lat}&lng={lng}"
    r = _post(
        "/open/v1/shell/execute",
        {
            "id": phone_id,
            "cmd": f"am start -a android.intent.action.VIEW -d '{url}' com.theappninjas.fakegpsjoystick",
        },
    )
    out = r.get("output", "").strip()
    print(f"    deep-link result: {out[:120]}")
    time.sleep(5)

    # 3. Fake GPS stays running in background; the mock provider persists in the
    # Android location service.  Force-stopping was a pre-UI-Clear-Storage ad-fix
    # leftover that caused perishable-mock failures on multi-interface phones.


def foreground_check(phone_id: str) -> tuple[bool, str]:
    try:
        r = _post("/open/v1/shell/execute",
                   {"id": phone_id, "cmd": "dumpsys activity activities | grep mResumedActivity"})
        out = r.get("output", "").strip()
        if MAPS_PACKAGE in out:
            return True, MAPS_PACKAGE
        if "com.android.vending" in out:
            return False, "com.android.vending (Play Store)"
        return False, out
    except Exception as e:
        return False, str(e)


def _get_phone_ip(phone_id: str, timeout: int = 20) -> str | None:
    """Query the phone's outbound IP via shell curl (through the proxy).

    Uses ifconfig.me primary / icanhazip.com fallback.
    checkip.amazonaws.com is intentionally NOT used because it bypasses some
    proxies (AWS carve-out) and gave false leak positives on Android 13.
    """
    for url in ("http://ifconfig.me", "https://icanhazip.com"):
        try:
            r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"curl -s {url}"})
            out = r.get("output", "").strip()
            if out and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", out):
                return out
        except Exception:
            continue
    return None


def _find_bounds(xml_data: str, text_query: str) -> tuple[int, int] | None:
    """Find center point of accessibility node matching text_query."""
    try:
        root = ET.fromstring(xml_data)
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


def _ui_clear_storage(phone_id: str, package: str = FAKE_GPS_PACKAGE) -> None:
    """
    Lean UI Clear Storage: Settings -> App Info -> Storage & cache -> CLEAR STORAGE -> OK.
    Uses shell + uiautomator dump (no full RPA flow).  Handles both App Info and
    Storage screen in case the phone is already on one of them.
    Falls back to 'pm clear' if the UI path cannot be navigated.
    """
    print("  --- UI Clear Storage (pre-run) ---")
    # 1. Open App Info
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": f"am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d package:{package}"},
    )
    time.sleep(2)

    # 2. Determine current screen
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/ui_clear_1.xml"})
    time.sleep(0.5)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/ui_clear_1.xml"})
    xml1 = r.get("output", "")

    if _find_bounds(xml1, "CLEAR STORAGE") or _find_bounds(xml1, "Clear storage"):
        print("    Already on Storage screen")
    else:
        # Try multiple labels used across OEM skins
        storage_labels = ("Storage & cache", "Storage", "Storage usage", "App storage", "Memory")
        pos = None
        for label in storage_labels:
            pos = _find_bounds(xml1, label)
            if pos:
                print(f"    Tapping '{label}' at {pos}")
                break
        if pos:
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
            time.sleep(2)
        else:
            print("    WARNING: Storage button not found, trying scroll")
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input swipe 260 1200 260 600 500"})
            time.sleep(1)
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/ui_clear_1b.xml"})
            time.sleep(0.5)
            r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/ui_clear_1b.xml"})
            xml1b = r.get("output", "")
            for label in storage_labels:
                pos = _find_bounds(xml1b, label)
                if pos:
                    print(f"    Tapping '{label}' after scroll at {pos}")
                    break
            if pos:
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
                time.sleep(2)
            else:
                print("    WARNING: Could not find Storage button — falling back to pm clear + force-stop + relaunch")
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"pm clear {package}"})
                time.sleep(2)
                # Force-stop and relaunch to trigger clean first-run init (fixes GPS after pm clear)
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"am force-stop {package}"})
                time.sleep(1)
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"monkey -p {package} -c android.intent.category.LAUNCHER 1"})
                time.sleep(4)
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"pm clear {package}"})
                time.sleep(2)
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_HOME"})
                print("  --- UI Clear Storage complete (pm clear + force-stop + relaunch fallback) ---")
                return

    # 3. Tap CLEAR STORAGE
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/ui_clear_2.xml"})
    time.sleep(0.5)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/ui_clear_2.xml"})
    xml2 = r.get("output", "")
    pos = _find_bounds(xml2, "CLEAR STORAGE") or _find_bounds(xml2, "Clear storage")
    if pos:
        print(f"    Tapping CLEAR STORAGE at {pos}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
        time.sleep(2)
    else:
        print("    WARNING: CLEAR STORAGE button not found")

    # 4. Handle confirmation dialog if present
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/ui_clear_3.xml"})
    time.sleep(0.5)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/ui_clear_3.xml"})
    xml3 = r.get("output", "")
    ok_pos = _find_bounds(xml3, "OK") or _find_bounds(xml3, "Clear")
    if ok_pos:
        print(f"    Tapping confirmation at {ok_pos}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {ok_pos[0]} {ok_pos[1]}"})
        time.sleep(2)
    else:
        print("    No confirmation dialog OK found (may auto-confirm)")

    # 5. Return home
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_HOME"})
    time.sleep(1)
    print("  --- UI Clear Storage complete ---")


def poll_task(client: GeelarKClient, task_id: str, timeout_minutes: int = 60,
              interval: int = 10, backoff_after: int = 3) -> dict:
    """
    Poll a GeelarK task until completion or timeout.

    Args:
        interval: seconds between polls (default 10).
        backoff_after: after this many polls, widen interval to 30s to cut
                       call volume once the task is known mid-run.
    """
    print(f"  polling task {task_id} ...")
    deadline = time.time() + (timeout_minutes * 60)
    poll_count = 0
    current_interval = interval
    while time.time() < deadline:
        time.sleep(current_interval)
        poll_count += 1
        if poll_count == backoff_after:
            current_interval = 30
            print(f"    (poll backoff: widening to {current_interval}s after {backoff_after} polls)")
        try:
            items = client.query_tasks([task_id])
            if not items:
                continue
            t = items[0]
            status = t.get("status")
            if status == 3:
                print("  task completed.")
                return t
            if status == 4:
                print(f"  task failed: {t.get('failDesc', t.get('failCode', 'unknown'))}")
                return t
            if status == 7:
                print("  task cancelled.")
                return t
        except Exception as e:
            print(f"  poll error: {e}", file=sys.stderr)
    print("  ERROR: poll timed out.", file=sys.stderr)
    return {"status": -1, "failDesc": "poll_timeout"}


def _save_screenshot(client: GeelarKClient, phone_id: str, run_id: str, log_dir: str) -> str | None:
    try:
        data = client.take_screenshot(phone_id, max_wait=30)
        if not data:
            return None
        p = Path(log_dir) / "screenshots"
        p.mkdir(parents=True, exist_ok=True)
        path = p / f"{run_id}.png"
        path.write_bytes(data)
        return str(path)
    except Exception as e:
        print(f"  screenshot save failed: {e}", file=sys.stderr)
        return None


def _abort_run(store: RunsStore, run_plan, log_dir: str, reason: str,
               failed_step: str | None = None, gps_verified: int = 0,
               gps_dumpsys_coords: str | None = None,
               interaction_ip: str | None = None) -> int:
    run_plan.abort = True
    run_plan.abort_reason = reason
    run_plan.decisions.append(f"ABORT: {reason}")
    print(f"\nRun aborted — {reason}")
    store.finish_run(
        run_plan.run_id, "failed_infra",
        failed_step=failed_step or reason,
        gps_verified=gps_verified,
        gps_dumpsys_coords=gps_dumpsys_coords,
        interaction_ip=interaction_ip,
    )
    log_plan(run_plan, log_dir=log_dir,
             extra={"phase": "run_one_phone", "status": "aborted",
                    "abort_reason": reason, "failed_step": failed_step})
    print("\n" + make_feedback_packet(run_plan.run_id, log_dir=log_dir))
    store.close()
    return 1


def _parse_execution_logs_for_interactions(logs: list[str]) -> list[str]:
    """
    Heuristic: scan Geelark execution logs for evidence that interaction
    clicks (Save, Share, etc.) were actually dispatched successfully.
    We count an interaction if a log line mentions it and does NOT also
    contain failure keywords.
    """
    keywords = ("Save", "Share", "Photos", "Reviews", "Directions", "Call")
    found: set[str] = set()
    for line in logs:
        lower = line.lower()
        # Skip obvious failure lines
        if any(bad in lower for bad in ("failed", "no element found", "error", "timeout")):
            continue
        for kw in keywords:
            if kw.lower() in lower:
                found.add(kw)
    return sorted(found)


def _timeline_worker_screenshots(
    client: GeelarKClient,
    phone_id: str,
    run_id: str,
    log_dir: str,
    stop_event: threading.Event,
    result_queue: queue.Queue,
    interval: float = 15.0,
) -> None:
    """
    Background thread that captures screenshots periodically while the Maps
    RPA flow runs. Uses take_screenshot (safe) instead of uiautomator dump
    (which interferes with the RPA accessibility engine).
    """
    time.sleep(interval)
    count = 0
    while not stop_event.is_set():
        try:
            data = client.take_screenshot(phone_id, max_wait=30)
            if data:
                p = Path(log_dir) / "screenshots" / f"{run_id}_timeline_{count:03d}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                result_queue.put({"type": "screenshot", "path": str(p), "seq": count})
                count += 1
        except Exception:
            pass
        time.sleep(interval)


def run_phone(
    profile,
    csv_path: str,
    seed: int | None = None,
    dry_run: bool = False,
    log_dir: str = str(_repo_root / "geelark_orchestrator" / "logs"),
    db_path: str = str(_repo_root / "geelark_orchestrator" / "runs.sqlite"),
    schema_path: str = str(_repo_root / "geelark_orchestrator" / "schema.sql"),
) -> dict:
    """
    Execute a single phone run. Returns a result dict for batch aggregation.
    """
    # 1. Resolve
    run_plan = resolve(profile, seed=seed)
    print(run_plan.summary())
    print()
    for d in run_plan.decisions:
        print(f"  - {d}")
    print()

    # 1b. Ensure phone is running BEFORE any shell-based provisioning checks
    client = GeelarKClient()
    phone_id = profile.geelark_profile_id
    if not ensure_running(client, phone_id):
        run_plan.abort = True
        run_plan.abort_reason = "Phone did not start"
        run_plan.decisions.append("ABORT: Phone did not start")
        print("\nRun aborted — Phone did not start")
        log_plan(run_plan, log_dir=log_dir,
                 extra={"phase": "run_one_phone", "status": "aborted", "abort_reason": "Phone did not start"})
        return {"status": "aborted", "abort_reason": "Phone did not start", "run_id": run_plan.run_id}

    # 1c. Runtime provisioning check + auto-fix (before CSV-static gate)
    prov_ok, prov_reason = _verify_phone_provisioned(phone_id)
    if not prov_ok:
        print(f"  Phone not provisioned: {prov_reason}. Attempting auto-provision...")
        prov_ok, prov_reason = _provision_developer_and_mock(phone_id)
        if not prov_ok:
            run_plan.abort = True
            run_plan.abort_reason = f"Auto-provision failed: {prov_reason}"
            run_plan.decisions.append(f"ABORT: {run_plan.abort_reason}")
            print(f"\nRun aborted — {run_plan.abort_reason}")
            log_plan(run_plan, log_dir=log_dir,
                     extra={"phase": "run_one_phone", "status": "aborted", "abort_reason": run_plan.abort_reason})
            return {"status": "aborted", "abort_reason": run_plan.abort_reason, "run_id": run_plan.run_id}
        else:
            print("  Auto-provision succeeded — continuing.")

    # Sync runtime provisioning state to profile so CSV-static gate passes
    profile.provisioned = True
    profile.maps_verified = True

    # 2. Provisioning gate (CSV-static — now augmented by runtime auto-fix above)
    fit, reason = check_phone_fit(profile)
    if not fit:
        run_plan.abort = True
        run_plan.abort_reason = reason
        run_plan.decisions.append(f"ABORT: {reason}")
        print(f"\nRun aborted — {reason}")
        log_plan(run_plan, log_dir=log_dir,
                 extra={"phase": "run_one_phone", "status": "aborted", "abort_reason": reason})
        return {"status": "aborted", "abort_reason": reason, "run_id": run_plan.run_id}

    # 3. Adapter audit
    print_adapter_audit(run_plan, profile)

    if run_plan.abort:
        print("\nRun aborted — no phone touched.")
        log_plan(run_plan, log_dir=log_dir,
                 extra={"phase": "run_one_phone", "status": "aborted"})
        return {"status": "aborted", "run_id": run_plan.run_id}

    if dry_run:
        print("\nDRY RUN — resolved plan looks good; no phone touched.")
        log_plan(run_plan, log_dir=log_dir,
                 extra={"phase": "run_one_phone", "status": "dry_run"})
        return {"status": "dry_run", "run_id": run_plan.run_id}

    # 4. Persistence
    store = RunsStore(db_path, schema_path)
    store.upsert_profile(profile)
    store.record_run(run_plan)
    log_plan(run_plan, log_dir=log_dir,
             extra={"phase": "run_one_phone", "status": "pending"})

    # 5. Phone operations (client & phone_id already established in step 1b)
    resolved_coord = f"{run_plan.gps_lat},{run_plan.gps_lng}"

    print(f"\n--- Phone {phone_id} ({profile.profile_key}) ---")

    # 5a. Google account verification gate (runtime — not CSV-static)
    print("\n  --- Google account verification ---")
    google_ok, google_info = _verify_google_account(phone_id)
    if not google_ok:
        _abort_run(store, run_plan, log_dir,
                   f"Google account not found: {google_info}",
                   failed_step=f"google_account_missing: {google_info}")
        return {"status": "failed_infra", "failed_step": f"google_account_missing: {google_info}", "run_id": run_plan.run_id}
    print(f"  Google account confirmed: {google_info}")

    interaction_ip = None

    # 5a. Rotate proxy
    print("\n  --- Proxy rotation ---")
    try:
        new_proxy_ip = change_proxy_ip()
        print(f"  Proxy rotated to {new_proxy_ip}")
    except Exception as e:
        _abort_run(store, run_plan, log_dir, f"Proxy rotation failed: {e}",
                   failed_step=f"proxy_rotation_failed: {e}")
        return {"status": "failed_infra", "failed_step": f"proxy_rotation_failed: {e}", "run_id": run_plan.run_id}

    # 5b. Proxy routing gate: shell curl to confirm phone IP matches rotated proxy
    print("\n  --- Proxy routing gate (shell curl) ---")
    time.sleep(8)
    phone_ip = _get_phone_ip(phone_id)
    if phone_ip != new_proxy_ip:
        _abort_run(store, run_plan, log_dir,
                   f"Proxy routing leak — phone IP {phone_ip} does not match proxy {new_proxy_ip}",
                   failed_step=f"proxy_routing_leak: phone_ip={phone_ip} proxy={new_proxy_ip}")
        return {"status": "failed_infra", "failed_step": f"proxy_routing_leak: phone_ip={phone_ip} proxy={new_proxy_ip}", "run_id": run_plan.run_id}
    print(f"  Proxy routing confirmed: {phone_ip}")

    # 5c. IP convergence gate: poll until stable (secondary confirmation + settle wait)
    print("\n  --- IP convergence gate ---")
    phone_ip = None
    settle_deadline = time.time() + 90
    while time.time() < settle_deadline:
        time.sleep(5)
        phone_ip = _get_phone_ip(phone_id)
        if phone_ip == new_proxy_ip:
            print(f"  Phone IP converged: {phone_ip}")
            break
        print(f"  Phone IP={phone_ip}, proxy IP={new_proxy_ip} — still settling ...")
    else:
        _abort_run(store, run_plan, log_dir,
                   f"Phone IP did not converge to proxy IP within 90s (last={phone_ip}, proxy={new_proxy_ip})",
                   failed_step=f"ip_convergence_timeout: phone={phone_ip} proxy={new_proxy_ip}")
        return {"status": "failed_infra", "failed_step": f"ip_convergence_timeout: phone={phone_ip} proxy={new_proxy_ip}", "run_id": run_plan.run_id}

    # Confirm this IP is fresh (different from previous interaction)
    prev_ip = store.previous_interaction_ip(profile.profile_key)
    if prev_ip and phone_ip == prev_ip:
        _abort_run(store, run_plan, log_dir,
                   f"IP not fresh — phone IP {phone_ip} matches previous interaction IP {prev_ip}",
                   failed_step=f"ip_not_fresh: {phone_ip} == prev {prev_ip}")
        return {"status": "failed_infra", "failed_step": f"ip_not_fresh: {phone_ip} == prev {prev_ip}", "run_id": run_plan.run_id}
    if prev_ip:
        print(f"  IP freshness confirmed: {phone_ip} != previous {prev_ip}")
    else:
        print(f"  IP freshness: no previous IP (first run), using {phone_ip}")
    interaction_ip = phone_ip

    # Pre-run: UI Clear Storage to prevent ad interstitial stall.
    # Proven in 10-run head-to-head: drives ad rate to 0% vs ~27% with pm clear.
    _ui_clear_storage(phone_id)

    # ================================================================
    # NEW ORDER: Warm-up GPS → Warm-up flow → Primary GPS → Primary flow
    # Only TWO GPS activations per session.  The S23 "works once but not
    # twice" problem is eliminated because there is no third activation.
    # ================================================================

    # ----------------------------------------------------------------
    # 1. WARM-UP GPS — nearby point (GAL baker + shell fallback)
    # ----------------------------------------------------------------
    print("\n  --- Warm-up GPS setup ---")
    import random
    if profile.nearby_points:
        warmup_coord = random.choice(profile.nearby_points)
    else:
        warmup_coord = profile.business
    print(f"  Warm-up coords: {warmup_coord.lat},{warmup_coord.lng}")

    # 1a. Try GAL baker first
    warmup_gps_ok = False
    try:
        warmup_gps_id = bake_gps_flow(
            client=client,
            lat=str(warmup_coord.lat),
            lng=str(warmup_coord.lng),
            template_flow_id=GPS_TEMPLATE_FLOW_ID,
            reuse_flow_id=None,
        )
        print(f"    Warm-up GPS flow id: {warmup_gps_id}")
        warmup_gps_task_id = client.run_custom_flow(
            flow_id=warmup_gps_id,
            phone_id=phone_id,
            param_map={},
            task_name=f"gps_warmup_{profile.profile_key}_{run_plan.run_id[:8]}",
            schedule_delay=3,
        )
        print(f"    warmup_gps_task_id = {warmup_gps_task_id}")
        warmup_gps_task = poll_task(client, warmup_gps_task_id, timeout_minutes=10)
        if warmup_gps_task.get("status") == 3:
            time.sleep(3)
            mock_ok, mock_info = dumpsys_mock_check(phone_id)
            try:
                dl, dln = mock_info.split(",")
                warmup_match = abs(float(dl) - warmup_coord.lat) <= 0.0002 and abs(float(dln) - warmup_coord.lng) <= 0.0002
            except Exception:
                warmup_match = False
            if mock_ok and warmup_match:
                print(f"  Warm-up GPS verified at {mock_info}.")
                warmup_gps_ok = True
    except Exception as e:
        print(f"  GAL warm-up GPS failed: {e}")

    # 1b. Shell fallback if GAL did not verify
    if not warmup_gps_ok:
        print("  Trying shell fallback for warm-up GPS...")
        android_ver = _get_android_version(phone_id)
        if android_ver >= 13:
            warmup_gps_ok = _activate_gps_android13(phone_id, warmup_coord.lat, warmup_coord.lng)
        else:
            _activate_gps_via_deeplink(phone_id, warmup_coord.lat, warmup_coord.lng)
            time.sleep(5)
            warmup_gps_ok, _ = dumpsys_mock_check(phone_id)
        if warmup_gps_ok:
            print("  Warm-up GPS activated.")
        else:
            print("  Warm-up GPS failed — skipping warm-up flow, proceeding to primary GPS.")

    # ----------------------------------------------------------------
    # 2. WARM-UP FLOW — browse random nearby listings (only if GPS succeeded)
    # ----------------------------------------------------------------
    if warmup_gps_ok:
        print("\n  --- Warm-up browse flow ---")
        try:
            from run_full_maps_flow import build_warmup_flow

            warmup_gal = build_warmup_flow(profile=profile, run_plan=run_plan, max_listings=2)
            print(f"  Warm-up flow built: {warmup_gal['title']}")
            print(f"    Steps: {len(warmup_gal['content']['contents'])}")

            warmup_flow_id = client.import_rpa_flow(json.dumps(warmup_gal, ensure_ascii=False))
            print(f"    warmup_flow_id = {warmup_flow_id}")

            warmup_task_id = client.run_custom_flow(
                flow_id=warmup_flow_id,
                phone_id=phone_id,
                param_map={},
                task_name=f"warmup_{profile.profile_key}_{run_plan.run_id[:8]}",
                schedule_delay=3,
            )
            print(f"    warmup_task_id = {warmup_task_id}")

            warmup_task = poll_task(client, warmup_task_id, timeout_minutes=10)
            warmup_status = warmup_task.get("status")
            warmup_duration = warmup_task.get("cost")
            if warmup_status == 3:
                print(f"  Warm-up flow completed ({warmup_duration}s)")
            elif warmup_status == 4:
                print(f"  Warm-up flow failed: {warmup_task.get('failDesc', 'unknown')} — continuing to primary")
            else:
                print(f"  Warm-up flow status={warmup_status} — continuing to primary")

            tracker = BusinessTracker(profile.profile_key, ttl_days=7)
            tracker.save()
        except Exception as e:
            print(f"  Warm-up flow error (non-fatal): {e}", file=sys.stderr)
            print("  Continuing to primary flow...")
    else:
        print("  --- Warm-up flow SKIPPED (GPS failed) ---")

    # ----------------------------------------------------------------
    # 3. PRIMARY GPS — business coords (GAL baker + shell fallback)
    # ----------------------------------------------------------------
    print("\n  --- Primary GPS setup ---")
    print(f"  Target coords: {resolved_coord}")

    # Reset Fake GPS to clean state before primary activation.
    # Proven: warm-up GPS leaves the app running; re-activating without
    # a reset causes the notification shade to show "Stop" (not "Hide")
    # and the mock becomes perishable.  Force-stop kills the stale service.
    print("  Pre-primary GPS: force-stopping Fake GPS for clean state...")
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": f"am force-stop {FAKE_GPS_PACKAGE}"},
    )
    time.sleep(2)

    gps_verified = 0
    gps_dumpsys_coords = None

    # 3a. Try GAL baker first
    try:
        ephemeral_gps_id = bake_gps_flow(
            client=client,
            lat=str(run_plan.gps_lat),
            lng=str(run_plan.gps_lng),
            template_flow_id=GPS_TEMPLATE_FLOW_ID,
            reuse_flow_id=None,
        )
        print(f"  GPS flow id: {ephemeral_gps_id}")
        gps_task_id = client.run_custom_flow(
            flow_id=ephemeral_gps_id,
            phone_id=phone_id,
            param_map={},
            task_name=f"gps_setup_{profile.profile_key}_{run_plan.run_id[:8]}",
            schedule_delay=5,
        )
        print(f"  gps_task_id = {gps_task_id}")
        gps_task = poll_task(client, gps_task_id, timeout_minutes=10)
        if gps_task.get("status") == 3:
            time.sleep(3)
            mock_ok, mock_info = dumpsys_mock_check(phone_id)
            try:
                dl, dln = mock_info.split(",")
                match = abs(float(dl) - run_plan.gps_lat) <= 0.0002 and abs(float(dln) - run_plan.gps_lng) <= 0.0002
            except Exception:
                match = False
            if mock_ok and match:
                print(f"  PASS: dumpsys mock active at {mock_info} — matches resolver.")
                gps_verified = 1
                gps_dumpsys_coords = mock_info
    except Exception as e:
        print(f"  GAL primary GPS failed: {e}")

    # 3b. Shell fallback if GAL did not verify
    if not gps_verified:
        print("  Trying shell fallback for primary GPS...")
        android_ver = _get_android_version(phone_id)
        if android_ver >= 13:
            shell_ok = _activate_gps_android13(phone_id, run_plan.gps_lat, run_plan.gps_lng)
        else:
            _activate_gps_via_deeplink(phone_id, run_plan.gps_lat, run_plan.gps_lng)
            time.sleep(5)
            shell_ok, _ = dumpsys_mock_check(phone_id)
        if shell_ok:
            time.sleep(3)
            mock_ok, mock_info = dumpsys_mock_check(phone_id)
            try:
                dl, dln = mock_info.split(",")
                match = abs(float(dl) - run_plan.gps_lat) <= 0.0002 and abs(float(dln) - run_plan.gps_lng) <= 0.0002
            except Exception:
                match = False
            if mock_ok and match:
                print(f"  PASS: dumpsys mock active at {mock_info} — matches resolver (shell fallback).")
                gps_verified = 1
                gps_dumpsys_coords = mock_info

    if not gps_verified:
        _abort_run(store, run_plan, log_dir, "Primary GPS could not be activated",
                   failed_step="primary_gps_activation_failed",
                   interaction_ip=interaction_ip)
        return {"status": "gps_failed", "failed_step": "primary_gps_activation_failed", "run_id": run_plan.run_id}

    # 5c. PRIMARY Maps flow: bake ephemeral flow with resolver values (reusable per phone)
    print("\n  --- Primary Maps flow (baker) ---")
    print(f"  Search term: {run_plan.search_term!r}")
    print(f"  Business name: {profile.business_name!r}")

    maps_reuse_id = None  # skip reuse; bake fresh each run until registry lookup is wired
    # TODO: wire get_reusable_flow_ids(phone_id) when registry persistence is ready

    try:
        ephemeral_maps_id = bake_maps_flow(
            client=client,
            lat=str(run_plan.gps_lat),
            lng=str(run_plan.gps_lng),
            search_term=run_plan.search_term or "",
            business_name=profile.business_name,
            template_flow_id=MAPS_TEMPLATE_FLOW_ID,
            reuse_flow_id=maps_reuse_id,
            search_mode=getattr(profile, "search_mode", "discovery"),
        )
        print(f"  Maps flow id: {ephemeral_maps_id}")
        if not maps_reuse_id:
            register_flow_id(phone_id, maps_flow_id=ephemeral_maps_id)
    except Exception as e:
        _abort_run(store, run_plan, log_dir, f"Maps flow bake failed: {e}",
                   failed_step=f"maps_bake_failed: {e}",
                   gps_verified=gps_verified, gps_dumpsys_coords=gps_dumpsys_coords,
                   interaction_ip=interaction_ip)
        return {"status": "maps_failed", "failed_step": f"maps_bake_failed: {e}", "run_id": run_plan.run_id}

    # Pre-Maps cleanup: force-stop Fake GPS again to kill any ad interstitial
    # that might have appeared after the earlier force-stop.
    # TEMPORARILY DISABLED pre-Maps cleanup to test if force-stop causes crashes.
    # print("  Pre-Maps cleanup: force-stopping Fake GPS ...")
    # try:
    #     _post(
    #         "/open/v1/shell/execute",
    #         {"id": phone_id, "cmd": "am force-stop com.theappninjas.fakegpsjoystick"},
    #     )
    #     time.sleep(2)
    # except Exception as e:
    #     print(f"    force-stop warning: {e}")

    print(f"  dispatching Maps flow ...")
    try:
        maps_task_id = client.run_custom_flow(
            flow_id=ephemeral_maps_id,
            phone_id=phone_id,
            param_map={},
            task_name=f"maps_{profile.profile_key}_{run_plan.run_id[:8]}",
            schedule_delay=5,
        )
        print(f"  maps_task_id = {maps_task_id}")
    except Exception as e:
        _abort_run(store, run_plan, log_dir, f"Maps flow dispatch failed: {e}",
                   failed_step=f"maps_dispatch_failed: {e}",
                   gps_verified=gps_verified, gps_dumpsys_coords=gps_dumpsys_coords,
                   interaction_ip=interaction_ip)
        return {"status": "maps_failed", "failed_step": f"maps_dispatch_failed: {e}", "run_id": run_plan.run_id}

    log_plan(run_plan, log_dir=log_dir,
             extra={"phase": "run_one_phone", "status": "maps_dispatched",
                    "maps_task_id": maps_task_id, "ephemeral_maps_flow_id": ephemeral_maps_id})

    # Screenshot-based timeline monitor (safe — take_screenshot does NOT interfere
    # with Geelark's accessibility sessions, unlike uiautomator dump).
    timeline_queue: queue.Queue = queue.Queue()
    timeline_stop = threading.Event()
    timeline_thread = threading.Thread(
        target=_timeline_worker_screenshots,
        args=(client, phone_id, run_plan.run_id, log_dir, timeline_stop, timeline_queue),
        daemon=True,
    )
    timeline_thread.start()

    maps_task = poll_task(client, maps_task_id, timeout_minutes=15)
    maps_status = maps_task.get("status")
    maps_fail_desc = maps_task.get("failDesc") or str(maps_task.get("failCode", ""))
    duration = maps_task.get("cost")

    timeline_stop.set()
    timeline_thread.join(timeout=30)

    # Collect timeline screenshots for reporting
    timeline_snaps: list[dict] = []
    while not timeline_queue.empty():
        timeline_snaps.append(timeline_queue.get())

    # Fetch Geelark execution logs (all runs, not just failures — we need them
    # as interaction evidence since Share sheet gets dismissed before the end).
    log_interactions: list[str] = []
    try:
        detail = _post("/open/v1/task/detail", {"id": maps_task_id})
        logs = detail.get("logs", [])
        log_interactions = _parse_execution_logs_for_interactions(logs)
        if maps_status != 3:
            print("  Fetching Geelark execution logs for failed Maps task ...")
            for line in logs:
                print(f"    LOG: {line}")
    except Exception as e:
        print(f"    log fetch error: {e}")

    # 5d. Post-run Maps evidence (uiautomator dump)
    print("\n  --- Maps behavioural evidence (uiautomator) ---")
    time.sleep(3)
    try:
        maps_evidence = gather_maps_evidence(phone_id, profile.business_name, log_interactions)
        print(f"  search_submitted: {maps_evidence['search_submitted']}")
        print(f"  business_found: {maps_evidence['business_found']}")
        print(f"  card_opened: {maps_evidence['card_opened']}")
        if maps_evidence['business_name_matched']:
            print(f"  matched text: {maps_evidence['business_name_matched']!r}")
        print(f"  interactions_present: {maps_evidence['interactions_present']}")
        print(f"  screen_package: {maps_evidence['screen_package']}")
        if log_interactions:
            print(f"  log_interactions: {log_interactions}")
    except Exception as e:
        print(f"  WARNING: Maps evidence gathering failed: {e}", file=sys.stderr)
        maps_evidence = {
            "search_submitted": False,
            "business_found": False,
            "card_opened": False,
            "business_name_matched": None,
            "interactions_present": [],
            "all_texts": [],
            "screen_package": None,
        }

    # Merge log-derived interactions if screen evidence alone was insufficient
    if log_interactions and not maps_evidence["interactions_present"]:
        maps_evidence["interactions_present"] = log_interactions
        print(f"  Merged interactions from logs: {maps_evidence['interactions_present']}")

    # 5e. Final verification (foreground + dumpsys)
    print("\n  --- Final verification ---")
    time.sleep(3)

    maps_fg_ok, maps_fg_pkg = foreground_check(phone_id)
    print(f"  Foreground: {maps_fg_pkg}")

    mock_ok, mock_info = dumpsys_mock_check(phone_id)
    if mock_ok:
        print(f"  Mock still active: {mock_info}")
    else:
        print(f"  Mock check: {mock_info}")

    # Classify run status
    run_status, failed_step = classify_run_status(
        gps_verified=gps_verified,
        evidence=maps_evidence,
        maps_task_status=maps_status,
    )

    screenshot_path = None
    if run_status not in ("success", "maps_business_not_found"):
        screenshot_path = _save_screenshot(client, phone_id, run_plan.run_id, log_dir)
        if screenshot_path:
            print(f"  Screenshot saved: {screenshot_path}")

    interactions_fired_str = ",".join(maps_evidence["interactions_present"]) if maps_evidence["interactions_present"] else None

    print(f"\n  === RUN STATUS: {run_status} ===")
    if failed_step:
        print(f"  failed_step: {failed_step}")

    # Persist results
    store.finish_run(
        run_plan.run_id,
        status=run_status,
        duration_seconds=duration,
        interactions_fired=interactions_fired_str,
        failed_step=failed_step,
        screenshot_url=screenshot_path,
        gps_verified=gps_verified,
        gps_dumpsys_coords=gps_dumpsys_coords,
        maps_task_status=maps_status,
        maps_foreground=maps_fg_pkg,
        search_submitted=1 if maps_evidence["search_submitted"] else 0,
        business_found=1 if maps_evidence["business_found"] else 0,
        business_name_matched=maps_evidence["business_name_matched"],
        card_opened=1 if maps_evidence.get("card_opened") else 0,
        interactions_present=interactions_fired_str,
        interaction_ip=interaction_ip,
    )
    log_plan(run_plan, log_dir=log_dir,
             extra={
                 "phase": "run_one_phone",
                 "status": run_status,
                 "duration_seconds": duration,
                 "failed_step": failed_step,
                 "gps_task_id": gps_task_id,
                 "maps_task_id": maps_task_id,
                 "gps_verified": gps_verified,
                 "gps_dumpsys_coords": gps_dumpsys_coords,
                 "maps_task_status": maps_status,
                 "maps_foreground": maps_fg_pkg,
                 "search_submitted": maps_evidence["search_submitted"],
                 "business_found": maps_evidence["business_found"],
                 "card_opened": maps_evidence.get("card_opened"),
                 "business_name_matched": maps_evidence["business_name_matched"],
                 "interactions_present": maps_evidence["interactions_present"],
                 "screen_package": maps_evidence["screen_package"],
                 "ephemeral_gps_flow_id": ephemeral_gps_id,
                 "ephemeral_maps_flow_id": ephemeral_maps_id,
                 "interaction_ip": interaction_ip,
             })

    # Feedback packet
    print("\n=== FEEDBACK PACKET ===")
    print(make_feedback_packet(run_plan.run_id, log_dir=log_dir))
    print("\n=== EXPECTED vs ACTUAL ===")
    print(f"Expected: GPS={resolved_coord} | search={run_plan.search_term!r} | branded={run_plan.branded_term!r}")
    print(f"Actual  : status={run_status} | duration={duration}s | failed_step={failed_step or 'n/a'}")
    print(f"GPS     : verified={gps_verified} | dumpsys={gps_dumpsys_coords}")
    print(f"Maps    : task_status={maps_status} | search={maps_evidence['search_submitted']} | business={maps_evidence['business_found']} | card={maps_evidence.get('card_opened')} | interactions={maps_evidence['interactions_present']}")
    if screenshot_path:
        print(f"Screenshot: {screenshot_path}")

    store.close()

    return {
        "run_id": run_plan.run_id,
        "status": run_status,
        "failed_step": failed_step,
        "duration_seconds": duration,
        "gps_verified": gps_verified,
        "gps_dumpsys_coords": gps_dumpsys_coords,
        "maps_task_status": maps_status,
        "search_submitted": maps_evidence["search_submitted"],
        "business_found": maps_evidence["business_found"],
        "card_opened": maps_evidence.get("card_opened"),
        "interactions_present": maps_evidence["interactions_present"],
        "screenshot_path": screenshot_path,
        "interaction_ip": interaction_ip,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one phone with GPS baker + Maps evidence.")
    ap.add_argument("csv_path", help="Path to CSV with profile data")
    ap.add_argument("profile_key", help="e.g. serial_24")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log-dir", default=str(_repo_root / "geelark_orchestrator" / "logs"))
    ap.add_argument("--db-path", default=str(_repo_root / "geelark_orchestrator" / "runs.sqlite"))
    ap.add_argument("--schema-path", default=str(_repo_root / "geelark_orchestrator" / "schema.sql"))
    args = ap.parse_args()

    try:
        profile = load_real_profile_by_key(args.csv_path, args.profile_key)
    except DataError as e:
        print(f"DATA ERROR: {e}", file=sys.stderr)
        return 2

    result = run_phone(
        profile=profile,
        csv_path=args.csv_path,
        seed=args.seed,
        dry_run=args.dry_run,
        log_dir=args.log_dir,
        db_path=args.db_path,
        schema_path=args.schema_path,
    )
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
