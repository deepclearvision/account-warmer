#!/usr/bin/env python3
"""
Autonomous social account creator for GeelarK cloud phones.
Platform: pinterest
Agent folder: agents/pinterest/
"""
import sys, time, re, json, random
from pathlib import Path
from datetime import datetime, timezone

PLATFORM = "pinterest"
AGENT_DIR = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents") / PLATFORM
LOG_DIR = AGENT_DIR / "logs"
DUMP_DIR = AGENT_DIR / "dumps"
SHOT_DIR = AGENT_DIR / "screenshots"
STATE_DIR = AGENT_DIR / "state"
RECOVERY_DIR = AGENT_DIR / "recovery_codes"

for d in [LOG_DIR, DUMP_DIR, SHOT_DIR, STATE_DIR, RECOVERY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

PHONE_ID = "629191237835948360"

PROJECT_ROOT = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
sys.path.insert(0, str(PROJECT_ROOT))
from core.geelark_client import GeelarKClient, _post
from core.sms_pool_client import order_sms, wait_for_sms, check_sms

client = GeelarKClient()

# ── SHELL HELPERS ──────────────────────────────────────────────────────────
def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})
    return (r.get("output", "") or "")

def tap(x, y):
    return sh("input tap %d %d" % (x, y))

def swipe(x1, y1, x2, y2, duration=300):
    return sh("input swipe %d %d %d %d %d" % (x1, y1, x2, y2, duration))

def back():
    return sh("input keyevent KEYCODE_BACK")

def home():
    return sh("input keyevent KEYCODE_HOME")

def get_screen():
    out = sh("wm size")
    m = re.search(r"(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (1080, 2412)

def read_remote_file(remote_path, chunk_size=1800):
    chunks = []
    offset = 0
    while True:
        cmd = "dd if=%s bs=1 skip=%d count=%d 2>/dev/null" % (remote_path, offset, chunk_size)
        chunk = sh(cmd)
        if not chunk:
            break
        # GeelarK shell word-wraps at ~120 chars — strip all newlines.
        # XML doesn't need them; they break parsing when mid-tag.
        chunk = chunk.replace("\r\n", "").replace("\n", "").replace("\r", "")
        chunks.append(chunk)
        if len(chunk) < chunk_size:
            break
        offset += chunk_size
    return "".join(chunks)

def dump_ui(step):
    tag = "%s_%s_%s" % (PLATFORM, step, datetime.now().strftime("%H%M%S"))
    path = "/sdcard/%s.xml" % tag
    sh("uiautomator dump %s" % path)
    time.sleep(0.8)
    xml = read_remote_file(path)
    local_path = DUMP_DIR / ("%s.xml" % tag)
    local_path.write_text(xml, encoding="utf-8")
    return xml

def find_and_tap(xml, queries, label):
    if isinstance(queries, str):
        queries = [queries]
    x = xml.find("<?xml") if xml else -1
    if x < 0:
        return False
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml[x:])
    except Exception:
        return False
    best = None
    best_score = 999
    best_q = None
    for q in queries:
        ql = q.lower()
        for node in root.iter("node"):
            for attr in ["text", "content-desc", "resource-id"]:
                v = (node.get(attr, "") or "").strip().lower()
                if not v:
                    continue
                score = None
                if v == ql:
                    score = 0
                elif v.startswith(ql) or ql in v:
                    score = 1 if v.startswith(ql) else 2
                if score is not None and score < best_score:
                    best_score = score
                    bounds = node.get("bounds", "")
                    try:
                        x1, y1, x2, y2 = map(int, bounds.replace("[","").replace("]",",").rstrip(",").split(","))
                        best = ((x1 + x2) // 2, (y1 + y2) // 2)
                        best_q = q
                    except Exception:
                        pass
    if best:
        print("  [%s] Tapped %r at %s" % (label, best_q, best))
        tap(best[0], best[1])
        return True
    print("  [%s] NOT FOUND: %s" % (label, queries[:3]))
    return False

# ── IP ROTATION ─────────────────────────────────────────────────────────────
def maybe_rotate_ip():
    state_file = STATE_DIR / "state.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    last_rot = state.get("last_rotation", 0)
    should_rotate = False
    if (time.time() - last_rot) > 1200:
        should_rotate = True
    elif random.random() < 0.30:
        should_rotate = True
    if not should_rotate:
        print("IP rotation skipped")
        return
    print("Rotating proxy IP...")
    try:
        import requests
        resp = requests.post(
            "https://mobile-proxy-140-166.streamvia.io/index.php",
            auth=("19866_1", "Wealther3211!!"),
            data={"action": "changeip"}, timeout=15
        )
        print("  Rotation: %s %s" % (resp.status_code, resp.text.strip()))
    except Exception as e:
        print("  Rotation failed: %s" % e)
    time.sleep(30)
    ip = sh("curl -s --max-time 10 https://ifconfig.me").strip()
    print("  New IP: %s" % ip)
    state["last_ip"] = ip
    state["last_rotation"] = time.time()
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

# ── REPORTING ───────────────────────────────────────────────────────────────
def save_report(step, success, what_happened, what_failed, next_action, creds_saved=False):
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "phone_id": PHONE_ID,
        "step": step,
        "success": success,
        "what_happened": what_happened,
        "what_failed": what_failed,
        "next_action": next_action,
        "credentials_saved": creds_saved,
    }
    path = LOG_DIR / ("report_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

def save_credentials(account_id, email, password, username=None, backup_codes=None, phone=None):
    creds = {
        "platform": PLATFORM,
        "account_id": account_id,
        "email": email,
        "username": username,
        "password": password,
        "phone": phone,
        "backup_codes": backup_codes or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    cred_path = STATE_DIR / "credentials.json"
    cred_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    if backup_codes:
        recovery_path = RECOVERY_DIR / ("%s_%s_%s.txt" % (PLATFORM, account_id, datetime.now().strftime("%Y%m%d_%H%M%S")))
        recovery_path.write_text("\n".join(backup_codes), encoding="utf-8")

# ── SMS VERIFICATION ────────────────────────────────────────────────────────
def do_sms_verification():
    print("Ordering SMS number for %s..." % PLATFORM)
    try:
        order = order_sms(PLATFORM)
        print("  Order: %s" % order)
        order_id = order.get("order_id") or order.get("id")
        phone_number = order.get("phonenumber") or order.get("number")
        if not order_id:
            raise RuntimeError("No order_id in SMS Pool response: %s" % order)
        print("  Waiting for SMS on %s ..." % phone_number)
        sms_code = wait_for_sms(order_id, timeout=180, interval=5)
        print("  SMS code received: %s" % sms_code)
        return phone_number, sms_code
    except Exception as e:
        print("  SMS verification failed: %s" % e)
        return None, None

# ── PHONE LIFECYCLE ───────────────────────────────────────────────────────────
def ensure_phone_running():
    print("Checking phone status for %s..." % PHONE_ID)
    health = client.check_phone_health(PHONE_ID)
    print("  Health: %s" % health)
    status = health.get("status", -1)
    if status == 0:
        print("  Phone is already running")
        return True
    if status == 1:
        print("  Phone is starting, waiting...")
        for _ in range(30):
            time.sleep(5)
            h2 = client.check_phone_health(PHONE_ID)
            if h2.get("status") == 0:
                print("  Phone is now running")
                return True
        raise RuntimeError("Phone stuck in starting state")
    print("  Phone is stopped (status=%d), starting..." % status)
    try:
        url = client.start_phone(PHONE_ID)
        print("  Start initiated, viewer URL: %s" % url)
    except Exception as e:
        raise RuntimeError("Phone start command failed: %s" % e)
    for _ in range(36):
        time.sleep(5)
        h2 = client.check_phone_health(PHONE_ID)
        if h2.get("status") == 0:
            print("  Phone is now running")
            return True
    raise RuntimeError("Phone failed to start within 3 minutes")

# ── SIGN IN FLOW ──────────────────────────────────────────────────────────────
def sign_in_flow():
    pkg = "com.pinterest"
    sh("am force-stop %s" % pkg)
    time.sleep(1)
    sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
    time.sleep(8)
    xml = dump_ui("app_opened")

    if "home_feed_container" in xml or "bottom_nav_bar" in xml:
        print("  Already logged in, on home feed — skipping sign-in")
        return xml

    if "jacobbareethe" in xml or "Sign in with Google" in xml:
        print("  [Sign-In] Credential Manager — tapping Google account...")
        tap(540, 1880)
        time.sleep(5)
        xml = dump_ui("after_credential_tap")

    if "Continue as Jacob" in xml or "Sign in to Pinterest" in xml:
        print("  [Sign-In] Google consent — tapping 'Continue as Jacob'...")
        tap(540, 2136)
        time.sleep(6)
        sh("am force-stop %s" % pkg)
        time.sleep(1)
        sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
        time.sleep(8)
        xml = dump_ui("after_google_consent")

    return xml

def handle_birthday(xml):
    if "datePicker" not in xml:
        return xml
    print("  [Birthday] Date picker visible — setting year to 1998...")
    tap(768, 1077)
    time.sleep(0.5)
    sh("input keyevent KEYCODE_MOVE_END")
    for _ in range(4):
        sh("input keyevent KEYCODE_DEL")
        time.sleep(0.1)
    sh("input text 1998")
    time.sleep(0.5)
    xml2 = dump_ui("birthday_set")
    if not find_and_tap(xml2, "OK", "birthday_ok"):
        tap(780, 1171)
    time.sleep(5)
    xml = dump_ui("after_birthday")
    save_report("birthday", True, "Set birthday to 1998 and tapped OK", None, "Check next onboarding screen")
    return xml

# ── LOGIN CHECK ───────────────────────────────────────────────────────────────
def check_login():
    """Verify Pinterest account is logged in and healthy. Returns (status, details_dict)."""
    pkg = "com.pinterest"
    sh("am force-stop %s" % pkg)
    time.sleep(1)
    sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
    time.sleep(5)

    # Check for permission dialogs that block app launch
    xml = dump_ui("login_check_pre")
    if "com.android.permissioncontroller" in xml or "com.android.packageinstaller" in xml:
        print("  Permission dialog detected — dismissing...")
        # Try ALLOW first, then back
        if "ALLOW" in xml:
            find_and_tap(xml, "ALLOW", "perm_allow") or tap(810, 1450)
        elif "Allow" in xml:
            find_and_tap(xml, "Allow", "perm_allow") or tap(810, 1450)
        time.sleep(2)
        # If still stuck, press back
        sh("input keyevent KEYCODE_BACK")
        time.sleep(1)
        # Relaunch
        sh("am force-stop %s" % pkg)
        time.sleep(1)
        sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
        time.sleep(6)

    xml = dump_ui("login_check")

    has_home = "home_feed_container" in xml
    has_nav = "bottom_nav_bar" in xml
    has_pins = "lego_pin_grid_cell_id" in xml
    has_signup = "Sign up" in xml or "sign_up" in xml.lower()
    has_login = "Log in" in xml or "log_in" in xml.lower()
    # uiautomator XML is sometimes truncated from the bottom — bottom_nav_bar
    # may be cut off. home_feed_container + pins is definitive enough.
    is_truncated = not xml.endswith("</hierarchy>")

    if has_home and has_pins:
        status = "healthy"
        detail = "Logged in, home feed with pins visible"
        if not has_nav and is_truncated:
            detail += " (XML truncated — bottom_nav_bar cut off)"
    elif has_home and has_nav:
        status = "healthy_empty_feed"
        detail = "Logged in, home feed but no pins (new account or slow load)"
    elif has_signup or has_login:
        status = "logged_out"
        detail = "Not logged in — sign-up/login gate visible"
    elif has_home:
        status = "healthy_no_pins"
        detail = "Logged in, home feed visible but no pins loaded yet"
    elif "com.pinterest" in xml:
        status = "possible_flag"
        detail = "Inside Pinterest but no home feed or sign-up gate — account may be flagged/disabled"
    else:
        status = "not_in_app"
        detail = "Not inside Pinterest app — may have crashed or been redirected"

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "phone_id": PHONE_ID,
        "status": status,
        "detail": detail,
        "markers": {
            "home_feed_container": has_home,
            "bottom_nav_bar": has_nav,
            "pins_visible": has_pins,
            "signup_gate": has_signup,
            "login_gate": has_login,
        }
    }
    print(json.dumps(result, indent=2))
    save_report("login_check", status in ("healthy", "healthy_empty_feed"),
                 detail, None,
                 "OK" if status.startswith("healthy") else "Needs attention: " + status)
    return status, result


# ── MAIN FLOW ──────────────────────────────────────────────────────────────────
def main():
    ensure_phone_running()
    maybe_rotate_ip()
    w, h = get_screen()
    print("Screen: %dx%d" % (w, h))

    pkg = "com.pinterest"
    installed = pkg in sh("pm list packages")
    if not installed:
        print("Installing %s via Play Store..." % pkg)
        sh("am start -a android.intent.action.VIEW -d 'market://details?id=%s'" % pkg)
        for _ in range(30):
            time.sleep(10)
            if pkg in sh("pm list packages"):
                print("Installed")
                break
        else:
            save_report("install", False, "App did not install", "Package not found after 5 min", "Check Play Store availability or use APK")
            return

    xml = sign_in_flow()
    xml = handle_birthday(xml)

    # ── Onboarding loop ──────────────────────────────────────────────────
    last_hash = None
    stuck = 0
    for i in range(12):
        if "com.pinterest" not in xml:
            save_report("left_pinterest", False, "No longer in Pinterest", xml[:800], "Investigate")
            break

        # Stuck detection
        h = hash(xml[:200])
        if h == last_hash:
            stuck += 1
            if stuck >= 3:
                print("  STUCK — same screen 3x, breaking")
                save_report("stuck", False, "Stuck on same screen", "Button likely disabled or wrong tap", "Manual intervention needed")
                break
        else:
            stuck = 0
            last_hash = h

        # Screen detection (topics first — has disabled Next button)
        if "home_feed_container" in xml and ("bottom_nav_bar" in xml or "lego_pin_grid_cell_id" in xml):
            print("  ACCOUNT CREATED — on Pinterest home feed!")
            # Navigate to profile tab to capture real username
            print("  Navigating to profile tab...")
            find_and_tap(xml, "Saved, tab", "profile_tab") or tap(924, 2196)
            time.sleep(4)
            # Scroll up to try to expand the collapsing toolbar header
            swipe(540, 500, 540, 1400, 300)
            time.sleep(1)
            profile_xml = dump_ui("profile")
            # Extract display name and @username from profile using regex
            import re
            actual_username = "Jacob Bareethe"
            pinterest_handle = ""
            # Find @username in text or content-desc attributes
            handle_matches = re.findall(r'(?:text|content-desc)="(@[^"]+)"', profile_xml)
            for h in handle_matches:
                if h.startswith("@") and len(h) > 1:
                    pinterest_handle = h
                    print("  Found handle: %s" % pinterest_handle)
                    break
            # Find display name in profile_name or name_text resource-id
            name_matches = re.findall(r'resource-id="com\.pinterest:id/(?:profile_name|name_text)"[^>]*text="([^"]+)"', profile_xml)
            if name_matches:
                actual_username = name_matches[0]
                print("  Found display name: %s" % actual_username)
            # Also try content-desc for avatar
            avatar_match = re.search(r'content-desc="Avatar:\s*([^"]+)"', profile_xml)
            if avatar_match and not pinterest_handle:
                print("  Avatar name: %s" % avatar_match.group(1))
            save_report("account_created", True,
                "Account created. Handle: %s, Display: %s" % (pinterest_handle, actual_username),
                None, "Done", creds_saved=True)
            save_credentials(
                account_id='pinterest_jacobbareethe',
                email='jacobbareethe@gmail.com',
                password='',
                username=pinterest_handle or actual_username,
                phone=None,
                backup_codes=None
            )
            break
        elif "What are you interested in" in xml or "nux_interest_next_button" in xml:
            screen = "interests"
        elif "Enter your date of birth" in xml:
            screen = "birthday_confirm"
        elif "What is your gender" in xml or "gender_female_button" in xml:
            screen = "gender"
        elif "fragment_signup_step_button" in xml or "Next" in xml:
            screen = "generic_next"
        elif "Skip" in xml or "Done" in xml or "Get started" in xml:
            screen = "final"
        else:
            screen = "unknown"

        print("  [Onboarding %d] %s" % (i + 1, screen))

        if screen == "birthday_confirm":
            find_and_tap(xml, "Next", "birthday_next") or tap(540, 2130)
            time.sleep(4)
            xml = dump_ui("on_%d" % (i + 1))
            continue

        if screen == "gender":
            print("  Selecting Female...")
            find_and_tap(xml, "Female", "gender_female") or tap(540, 617)
            time.sleep(4)
            xml = dump_ui("on_%d" % (i + 1))
            continue

        if screen == "interests":
            print("  Selecting 5 interests...")
            import xml.etree.ElementTree as ET
            xp = xml.find("<?xml")
            if xp >= 0:
                root = ET.fromstring(xml[xp:])
                interests = []
                for node in root.iter("node"):
                    cd = (node.get("content-desc", "") or "").strip()
                    cl = (node.get("clickable", "") or "").strip()
                    bs = (node.get("bounds", "") or "").strip()
                    if cd and cl == "true" and cd != "Back" and cd != "X" and len(cd) > 2:
                        try:
                            parts = bs.replace("[","").replace("]",",").rstrip(",").split(",")
                            x1, y1, x2, y2 = map(int, parts)
                            cy = (y1 + y2) // 2
                            if cy < 2100:
                                interests.append((cd, (x1 + x2) // 2, cy))
                        except Exception:
                            pass
                for name, cx, cy in interests[:5]:
                    print("    %s" % name)
                    tap(cx, cy)
                    time.sleep(0.7)
                if not interests:
                    for px, py in [(196, 630), (540, 630), (196, 1437), (540, 1437), (540, 1851)]:
                        tap(px, py)
                        time.sleep(0.7)
            time.sleep(1)
            xml = dump_ui("interests_done")
            find_and_tap(xml, "Next", "interests_next") or tap(540, 2154)
            time.sleep(5)
            xml = dump_ui("on_%d" % (i + 1))
            continue

        if screen == "generic_next":
            find_and_tap(xml, "Next", "generic_next") or tap(540, 2130)
            time.sleep(4)
            xml = dump_ui("on_%d" % (i + 1))
            continue

        if screen == "final":
            find_and_tap(xml, ["Done", "Get started", "Skip", "Next"], "finish")
            time.sleep(4)
            xml = dump_ui("after_onboarding")
            save_report("onboarding_complete", True, "Onboarding finished", None, "Verify account")
            break

        # Unknown screen
        save_report("unknown_screen", True, "Unknown: " + screen, None, "Analyze dump")
        break

    # ── Final ─────────────────────────────────────────────────────────────
    if "com.pinterest" in xml:
        save_report("flow_done", True, "Pinterest flow complete", None, "Verify and save credentials")
    else:
        save_report("flow_done", False, "Not in Pinterest at end", xml[:800], "Investigate")

if __name__ == "__main__":
    try:
        if "--check" in sys.argv:
            ensure_phone_running()
            check_login()
        else:
            main()
    except Exception as e:
        save_report("crash", False, "Script crashed", str(e), "Fix exception and rerun")
        raise
