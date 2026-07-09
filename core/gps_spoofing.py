"""
core/gps_spoofing.py - Reliable GPS spoofing for GeelarK cloud phones.

Method: Install a real mock-GPS app (e.g. Lexa's Fake GPS Location or
GPS Emulator by Digitools UY) and drive it via ADB broadcasts or UI automation.
The app registers as an Android mock location provider, so every app on the
phone (Maps, Gmail, Timeline) sees the spoofed coordinates.

CRITICAL: The app process MUST be running before broadcasts work.
MockLocationReceiver is dynamically registered when the app UI opens.

Usage:
    from core.gps_spoofing import set_gps, ensure_installed, verify_in_maps

    ensure_installed(phone_id)
    launch_app(phone_id)
    set_gps(phone_id, 51.5137, -0.1337)
    verify_in_maps(phone_id)
"""

import logging
import random
import time
from pathlib import Path

import yaml

log = logging.getLogger("gps_spoof")

# -- Configuration ----------------------------------------------------------------
# Load GPS app settings from config/settings.yaml if present; otherwise use
# sensible defaults for Lexa Fake GPS.


def _load_gps_settings() -> dict:
    settings_file = Path(__file__).parent.parent / "config" / "settings.yaml"
    defaults = {
        "apk_path": "data/fake-gps.apk",
        "package": "com.lexa.fakegps",
        "broadcast_action": "com.lexa.fakegps.SET_LOCATION",
        "broadcast_receiver": "com.lexa.fakegps/.MockLocationReceiver",
    }
    if not settings_file.exists():
        return defaults
    try:
        data = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
        gps = data.get("gps_spoofing", {})
        for key in defaults:
            if gps.get(key):
                defaults[key] = gps[key]
    except Exception:
        pass
    return defaults


_GPS_CFG = _load_gps_settings()

_apk_raw = Path(_GPS_CFG["apk_path"])
if _apk_raw.is_absolute():
    _DEFAULT_APK_PATH = _apk_raw
else:
    _DEFAULT_APK_PATH = Path(__file__).parent.parent / _apk_raw
_GPS_APP_PACKAGE = _GPS_CFG["package"]
_GPS_BROADCAST_ACTION = _GPS_CFG["broadcast_action"]
_GPS_BROADCAST_RECEIVER = _GPS_CFG["broadcast_receiver"]

# Alternative known configurations (kept for reference):
# "ru.gavrikov.mocklocations"  -> action unknown; may require opening the app UI
# "com.incorporateapps.fakegps.fre" -> action unknown
# "com.rosteam.gpsemulator"    -> UI automation required (search -> result -> start)


# -- Helpers ----------------------------------------------------------------------

def _shell(phone_id: str, cmd: str) -> tuple[bool, str]:
    """Execute shell command via GeelarK API."""
    from core.geelark_client import _post
    try:
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
        return r.get("status", False), r.get("output", "")
    except Exception as e:
        log.warning("Shell failed (%s): %s", cmd[:60], e)
        return False, ""


def is_installed(phone_id: str, package: str = _GPS_APP_PACKAGE) -> bool:
    """Check whether the mock GPS app is installed."""
    ok, out = _shell(phone_id, f"pm list packages | grep {package}")
    return ok and package in out


def _tap_my_location_button(phone_id: str) -> bool:
    """
    Use uiautomator to find and tap the Maps 'My Location' / re-center button.
    Falls back to a known-good coordinate if the dump fails.
    """
    import xml.etree.ElementTree as ET

    # Dump UI hierarchy
    ok, _ = _shell(phone_id, "uiautomator dump /sdcard/window_dump.xml")
    if not ok:
        log.warning("[%s] uiautomator dump failed, falling back to hard tap", phone_id)
        _shell(phone_id, "input tap 655 757")
        return True

    ok, xml_data = _shell(phone_id, "cat /sdcard/window_dump.xml")
    if not ok or not xml_data.strip():
        _shell(phone_id, "input tap 655 757")
        return True

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        _shell(phone_id, "input tap 655 757")
        return True

    # Look for the re-center / My Location button by content description
    candidates = []
    for node in root.iter("node"):
        desc = (node.get("content-desc") or "").lower()
        text = (node.get("text") or "").lower()
        bounds = node.get("bounds", "")
        if not bounds:
            continue
        score = 0
        if "re-center map to your location" in desc:
            score = 100
        elif "my location" in desc or "my location" in text:
            score = 90
        elif "location" in desc and "re-center" in desc:
            score = 80
        elif "location" in desc:
            score = 50
        if score > 0:
            candidates.append((score, bounds, desc, text))

    if not candidates:
        log.warning("[%s] My Location button not found in UI dump, falling back", phone_id)
        _shell(phone_id, "input tap 655 757")
        return True

    # Pick highest score
    candidates.sort(reverse=True)
    score, bounds, desc, text = candidates[0]
    log.info("[%s] My Location button found: %s (score=%d)", phone_id, bounds, score)

    # Parse bounds like [629,731][681,783]
    try:
        parts = bounds.replace("][", ",").replace("[", "").replace("]", "").split(",")
        x1, y1, x2, y2 = map(int, parts)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
    except Exception:
        _shell(phone_id, "input tap 655 757")
        return True

    _shell(phone_id, f"input tap {cx} {cy}")
    log.info("[%s] Tapped My Location at %d,%d", phone_id, cx, cy)
    return True


def launch_app(phone_id: str, package: str = _GPS_APP_PACKAGE) -> bool:
    """
    Launch the mock GPS app so its process is alive and the broadcast receiver
    is registered. This is REQUIRED before sending location broadcasts.
    """
    _shell(phone_id, f"am force-stop {package}")
    time.sleep(0.5)
    # Try explicit activity first, fallback to monkey
    ok, _ = _shell(phone_id, f"am start -n {package}/com.lexa.fakegps.ui.MainActivity")
    if not ok:
        ok, _ = _shell(phone_id, f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    time.sleep(3)
    log.info("[%s] Launched %s (ok=%s)", phone_id, package, ok)
    return ok


def configure_mock_location(phone_id: str, package: str = _GPS_APP_PACKAGE) -> bool:
    """
    Enable mock locations globally and grant the specific app permission.
    This must run AFTER the APK is installed.
    Also enables Developer Options if not already enabled.
    """
    # Ensure Developer Options is enabled (fallback to settings command)
    _shell(phone_id, "settings put global development_settings_enabled 1")

    # Enable mock locations globally
    _shell(phone_id, "settings put secure mock_location 1")

    # Register the mock GPS app as the system's mock location provider
    # (Settings -> Developer Options -> Select mock location app)
    _shell(phone_id, f"settings put secure mock_location_app {package}")

    # Allow GPS and network location providers
    _shell(phone_id, "settings put secure location_providers_allowed gps,network")

    # Ensure location mode is high accuracy (GPS + network)
    _shell(phone_id, "settings put secure location_mode 3")
    _shell(phone_id, "settings put global location_mode 3")

    # Grant the app permission to provide mock locations (Android 6+)
    ok, out = _shell(phone_id, f"appops set {package} android:mock_location allow")
    if not ok:
        log.warning("[%s] appops grant failed: %s", phone_id, out)

    # Google Play Services and Google Services Framework need location permission
    # so that Maps can actually receive fused location updates
    for gms_pkg in ("com.google.android.gms", "com.google.android.gsf"):
        _shell(phone_id, f"pm grant {gms_pkg} android.permission.ACCESS_FINE_LOCATION")
        _shell(phone_id, f"pm grant {gms_pkg} android.permission.ACCESS_COARSE_LOCATION")

    # Maps itself also needs location permission
    _shell(phone_id, "pm grant com.google.android.apps.maps android.permission.ACCESS_FINE_LOCATION")
    _shell(phone_id, "pm grant com.google.android.apps.maps android.permission.ACCESS_COARSE_LOCATION")

    log.info("[%s] Mock location configured for %s", phone_id, package)
    return True


def set_gps(phone_id: str, lat: float, lon: float,
            package: str = _GPS_APP_PACKAGE,
            action: str = _GPS_BROADCAST_ACTION,
            receiver: str = _GPS_BROADCAST_RECEIVER) -> bool:
    """
    Send an ADB broadcast to the mock GPS app to set coordinates.
    Returns True if the broadcast was dispatched successfully.
    """
    # If the app isn't installed, warn but don't block - caller should have
    # called ensure_installed() earlier.
    if not is_installed(phone_id, package):
        log.warning("[%s] Mock GPS app %s not installed - falling back to GeelarK API", phone_id, package)
        return _set_gps_api_fallback(phone_id, lat, lon)

    # GPS Emulator uses UI automation, not broadcasts
    if package == "com.rosteam.gpsemulator":
        return set_gps_emulator(phone_id, lat, lon)

    cmd = (
        f"am broadcast -a {action} "
        f"--ef lat {lat} --ef lng {lon} "
        f"-n {receiver}"
    )
    ok, out = _shell(phone_id, cmd)
    if ok:
        log.info("[%s] GPS broadcast -> %.5f, %.5f", phone_id, lat, lon)
        return True
    log.warning("[%s] GPS broadcast failed: %s", phone_id, out)
    return False


def _set_gps_api_fallback(phone_id: str, lat: float, lon: float) -> bool:
    """Last resort: use the GeelarK native GPS API. Less reliable for Maps."""
    from core.geelark_client import _post
    try:
        r = _post("/open/v1/phone/gps/set", {
            "list": [{"id": phone_id, "lat": lat, "lng": lon}]
        })
        return r.get("successAmount", 0) > 0
    except Exception as e:
        log.warning("[%s] GeelarK GPS API fallback failed: %s", phone_id, e)
        return False


# -- GPS Emulator UI automation (com.rosteam.gpsemulator) -------------------------

def _get_ui(phone_id: str):
    """Dump and parse current UI hierarchy. Returns ElementTree root or None."""
    import xml.etree.ElementTree as ET
    ok, _ = _shell(phone_id, "uiautomator dump /sdcard/ui_temp.xml")
    time.sleep(1)
    ok, output = _shell(phone_id, "cat /sdcard/ui_temp.xml")
    if not ok or not output:
        return None
    try:
        return ET.fromstring(output)
    except Exception:
        return None


def _find_node(root, resource_id=None, text_contains=None, cls_contains=None):
    """Find first matching node in UI tree. Returns dict or None."""
    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        resource = (node.get("resource-id") or "").strip()
        cls = (node.get("class") or "").strip()
        bounds = node.get("bounds", "")
        if resource_id and resource_id in resource:
            return {"text": text, "resource": resource, "class": cls, "bounds": bounds}
        if text_contains and text_contains.lower() in text.lower():
            return {"text": text, "resource": resource, "class": cls, "bounds": bounds}
        if cls_contains and cls_contains in cls:
            return {"text": text, "resource": resource, "class": cls, "bounds": bounds}
    return None


def _tap_bounds(phone_id: str, bounds_str: str):
    """Parse bounds string and tap center."""
    parts = bounds_str.replace("][", ",").replace("[", "").replace("]", "").split(",")
    x1, y1, x2, y2 = map(int, parts)
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    _shell(phone_id, f"input tap {cx} {cy}")
    return cx, cy


def _dismiss_ad(phone_id: str, max_attempts: int = 5) -> bool:
    """
    Dismiss ad overlays in GPS Emulator. Taps close buttons or presses back.
    Returns True when the app UI (search_edit) is visible.
    """
    for _ in range(max_attempts):
        root = _get_ui(phone_id)
        if root is None:
            time.sleep(1)
            continue
        # App UI visible
        if _find_node(root, resource_id="search_edit"):
            return True
        # Ad close button (Liftoff, etc.)
        close = _find_node(root, resource_id="next-button")
        if close:
            log.info("[%s] Dismissing ad: close button at %s", phone_id, close["bounds"])
            _tap_bounds(phone_id, close["bounds"])
            time.sleep(1)
            continue
        # Fullscreen ad overlay
        if any("fullscreen-overlay" in (n.get("resource-id") or "") for n in root.iter("node")):
            log.info("[%s] Dismissing ad: pressing back", phone_id)
            _shell(phone_id, "input keyevent KEYCODE_BACK")
            time.sleep(1)
            continue
    return False


def set_gps_emulator(phone_id: str, lat: float, lon: float,
                     address: str | None = None) -> bool:
    """
    Set mock location via GPS Emulator (com.rosteam.gpsemulator) UI automation.
    Searches by address or falls back to lat/lon coordinates.
    """
    import xml.etree.ElementTree as ET

    pkg = "com.rosteam.gpsemulator"
    if not is_installed(phone_id, pkg):
        log.warning("[%s] %s not installed", phone_id, pkg)
        return False

    # Launch app
    _shell(phone_id, f"am force-stop {pkg}")
    time.sleep(1)
    _shell(phone_id, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    time.sleep(4)

    # Tap search
    root = _get_ui(phone_id)
    search_btn = _find_node(root, resource_id="action_search")
    if search_btn:
        _tap_bounds(phone_id, search_btn["bounds"])
        time.sleep(2)
    else:
        log.warning("[%s] Search button not found", phone_id)

    # Dismiss any ad overlay
    _dismiss_ad(phone_id, max_attempts=5)

    # Type address or coordinates
    root = _get_ui(phone_id)
    edit = _find_node(root, resource_id="search_edit")
    if edit:
        _tap_bounds(phone_id, edit["bounds"])
        time.sleep(0.5)
        _shell(phone_id, "input keyevent KEYCODE_CTRL_A")
        _shell(phone_id, "input keyevent KEYCODE_DEL")
        time.sleep(0.5)

        query = address if address else f"{lat}, {lon}"
        log.info("[%s] Typing search query: %s", phone_id, query)
        _shell(phone_id, f'input text "{query}"')
        time.sleep(1)
        _shell(phone_id, "input keyevent KEYCODE_ENTER")
        time.sleep(3)
    else:
        log.warning("[%s] Search EditText not found", phone_id)
        return False

    # Dismiss ads after typing
    _dismiss_ad(phone_id, max_attempts=3)

    # Tap first result (look for address-like text or pin_name)
    root = _get_ui(phone_id)
    result = _find_node(root, text_contains=address.split(",")[0] if address else str(int(lat)))
    if not result:
        # Fallback: any pin_name node below the search bar
        for node in root.iter("node"):
            resource = (node.get("resource-id") or "").strip()
            bounds = node.get("bounds", "")
            if "pin_name" in resource and bounds:
                # Ensure it's below search bar (y > 250)
                try:
                    y1 = int(bounds.replace("][", ",").replace("[", "").replace("]", "").split(",")[1])
                    if y1 > 250:
                        result = {"bounds": bounds, "text": node.get("text", "")}
                        break
                except Exception:
                    pass
    if result:
        log.info("[%s] Tapping result: %s", phone_id, result.get("text", ""))
        _tap_bounds(phone_id, result["bounds"])
        time.sleep(3)
    else:
        log.warning("[%s] No result found, tapping fallback", phone_id)
        _shell(phone_id, "input tap 360 350")
        time.sleep(2)

    # Dismiss ads after result
    _dismiss_ad(phone_id, max_attempts=3)

    # Tap start_continuous_button
    root = _get_ui(phone_id)
    start_btn = _find_node(root, resource_id="start_continuous_button")
    if start_btn:
        log.info("[%s] Tapping start button", phone_id)
        _tap_bounds(phone_id, start_btn["bounds"])
        time.sleep(2)
    else:
        log.warning("[%s] Start button not found", phone_id)

    # Dismiss any post-start ad
    _dismiss_ad(phone_id, max_attempts=3)

    log.info("[%s] GPS Emulator flow complete for %.5f, %.5f", phone_id, lat, lon)
    return True


# -- Installation -----------------------------------------------------------------

def ensure_installed(phone_id: str, apk_path: Path | None = None) -> bool:
    """
    Install the mock GPS app if it is not already present.
    Also configures mock location settings and launches the app.
    Returns True if the app is installed (or was just installed).
    """
    package = _GPS_APP_PACKAGE
    if is_installed(phone_id, package):
        log.info("[%s] Mock GPS app %s already installed", phone_id, package)
        configure_mock_location(phone_id, package)
        launch_app(phone_id, package)
        return True

    path = apk_path or _DEFAULT_APK_PATH
    if not path.exists():
        log.error(
            "[%s] Mock GPS APK not found at %s. "
            "Download Lexa Fake GPS from APKMirror and place it there.",
            phone_id, path,
        )
        return False

    from core.geelark_client import GeelarKClient
    client = GeelarKClient()
    ok = client.upload_and_install_apk(phone_id, path)
    if ok:
        configure_mock_location(phone_id, package)
        launch_app(phone_id, package)
    return ok


# -- Verification -----------------------------------------------------------------

def verify_in_maps(phone_id: str, acc_id: str = "",
                   client=None, max_wait: int = 15) -> dict:
    """
    Verify that Maps actually sees the spoofed GPS.

    Steps:
      1. Open Google Maps
      2. Tap the My Location button (bottom-right blue dot)
      3. Wait for map to re-center
      4. Take a screenshot

    Returns dict with keys: success, screenshot_path (relative), note.
    The caller can inspect the screenshot to confirm the map centred on the
    expected coordinates.
    """
    from activities.mobile_warmup import _open_app, _press_home

    try:
        _open_app(phone_id, "com.google.android.apps.maps", acc_id)
        time.sleep(5)   # give Maps time to fully render

        # Tap My Location button dynamically via uiautomator
        _tap_my_location_button(phone_id)
        time.sleep(5)   # wait for re-center and blue dot to appear

        # Take screenshot
        if client is None:
            from core.geelark_client import GeelarKClient
            client = GeelarKClient()

        img = client.take_screenshot(phone_id, max_wait=max_wait)
        if not img:
            return {"success": False, "screenshot_path": "", "note": "screenshot failed"}

        from core.paths import LOGS_DIR
        from datetime import datetime
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        name = f"{acc_id or phone_id}_gps_verify_{ts}.png"
        path = LOGS_DIR / "screenshots" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img)

        _press_home(phone_id)
        return {
            "success": True,
            "screenshot_path": f"screenshots/{name}",
            "note": "Maps opened, My Location tapped, screenshot captured",
        }
    except Exception as e:
        log.warning("[%s] GPS verify in Maps failed: %s", acc_id or phone_id, e)
        return {"success": False, "screenshot_path": "", "note": str(e)}


# -- Drive animation ---------------------------------------------------------------

def drive_route(phone_id: str, waypoints: list[tuple[float, float]],
                pause_range: tuple[float, float] = (4.0, 7.0)) -> bool:
    """
    Animate GPS along a list of waypoints by sending a broadcast at each one.
    Used for the OSRM road-following drive animation.
    """
    if not waypoints:
        return False
    for i, (lat, lon) in enumerate(waypoints):
        ok = set_gps(phone_id, lat, lon)
        if not ok:
            log.warning("[%s] GPS drive step %d/%d failed", phone_id, i + 1, len(waypoints))
        if i < len(waypoints) - 1:
            time.sleep(random.uniform(*pause_range))
    log.info("[%s] GPS drive complete: %d waypoints", phone_id, len(waypoints))
    return True
