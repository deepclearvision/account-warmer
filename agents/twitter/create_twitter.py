#!/usr/bin/env python3
"""
Autonomous social account creator for GeelarK cloud phones.
Platform: twitter
Agent folder: agents/twitter/
"""
import sys, time, re, json, random
from pathlib import Path
from datetime import datetime

PLATFORM = "twitter"
AGENT_DIR = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents") / PLATFORM
LOG_DIR = AGENT_DIR / "logs"
DUMP_DIR = AGENT_DIR / "dumps"
SHOT_DIR = AGENT_DIR / "screenshots"
STATE_DIR = AGENT_DIR / "state"
RECOVERY_DIR = AGENT_DIR / "recovery_codes"

for d in [LOG_DIR, DUMP_DIR, SHOT_DIR, STATE_DIR, RECOVERY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

PHONE_ID = "629204580722278728"

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
        # Strip trailing newline added by shell, then normalize line endings
        chunk = chunk.rstrip("\r\n").replace("\r\n", "\n")
        if chunk:
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
        # Sanitize malformed XML: fix unescaped & not part of valid entities
        sanitized = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', xml[x:])
        root = ET.fromstring(sanitized)
    except Exception as e:
        print("  [%s] XML parse error: %s" % (label, str(e)[:100]))
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
        "timestamp": datetime.utcnow().isoformat(),
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
        "created_at": datetime.utcnow().isoformat(),
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

# ── MAIN FLOW (stub — expand per platform) ──────────────────────────────────
def main():
    maybe_rotate_ip()
    w, h = get_screen()
    print("Screen: %dx%d" % (w, h))

    pkg = "com.twitter.android"
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

    # ── STEP 0: Clear stale dialogs and launch cleanly ────────────────────
    sh("am force-stop %s" % pkg)
    time.sleep(1)
    # Dismiss any stale Google/perm dialogs from previous runs
    for _ in range(3):
        back()
        time.sleep(0.5)
    time.sleep(1)

    sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
    time.sleep(8)

    # Handle any system dialogs that might appear on top
    xml = dump_ui("app_opened")
    for attempt in range(5):
        if "permissioncontroller" in xml:
            print("Permission dialog attempt %d - pressing ENTER" % (attempt+1))
            sh("input keyevent KEYCODE_ENTER")
            time.sleep(4)
            xml = dump_ui("perm_%d" % (attempt+1))
        elif "com.twitter.android" in xml:
            print("Twitter app visible after attempt %d" % (attempt+1))
            break
        elif "com.google.android.gms" in xml:
            print("Stale Google dialog on attempt %d - pressing BACK" % (attempt+1))
            back()
            time.sleep(2)
            xml = dump_ui("stale_google_%d" % (attempt+1))
        else:
            print("Unknown screen attempt %d - pressing BACK and relaunching" % (attempt+1))
            back()
            time.sleep(2)
            sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
            time.sleep(8)
            xml = dump_ui("retry_%d" % (attempt+1))

    save_report("open_app", True, "App opened", None, "Begin Google sign-in")

    # ── STEP 1: Tap "Continue with Google" ───────────────────────────────
    if "com.twitter.android" not in xml:
        print("ERROR: Twitter not visible, cannot proceed")
        save_report("fatal", False, "Twitter app not visible after 5 attempts", xml[:300], "Manual intervention needed")
        return

    print("Tapping Continue with Google (left icon ~585, 1884)...")
    tap(585, 1884)
    time.sleep(8)
    xml = dump_ui("after_google_tap")

    # ── STEP 2: Select Google account ─────────────────────────────────────
    if "Choose an account" in xml or "crystalwiggins9533" in xml:
        print("Google account chooser visible - selecting Crystal Wiggins")
        tap(720, 1675)  # Account container center
        time.sleep(10)
        xml = dump_ui("after_account_select")
    else:
        print("Unexpected screen after Google tap")
        save_report("google_fail", False, "Google account chooser not found", xml[:500], "Check dump")
        return

    # ── STEP 3: Handle Google consent / terms screen ─────────────────────
    google_consent_keywords = [
        "Agree and share", "AGREE", "ALLOW",
        "NEXT", "CONTINUE", "ACCEPT", "I AGREE", "YES", "OK"
    ]
    for consent_attempt in range(5):
        if "com.twitter.android" in xml:
            print("Back in Twitter app - consent done!")
            break
        if "com.google.android.gms" in xml:
            print("Google consent screen attempt %d" % (consent_attempt+1))
            if find_and_tap(xml, google_consent_keywords, "consent"):
                time.sleep(6)
                xml = dump_ui("consent_%d" % (consent_attempt+1))
            else:
                print("  No consent button found, trying fallback taps")
                # Try known button positions
                for tap_x, tap_y, tap_label in [
                    (1013, 2139, "Agree_share"),  # "Agree and share" button
                    (1200, 2805, "NEXT_full"),    # NEXT in full-screen consent
                    (720, 2229, "bottom_center"), # generic bottom center
                ]:
                    print("  Tapping fallback: %s at (%d, %d)" % (tap_label, tap_x, tap_y))
                    tap(tap_x, tap_y)
                    time.sleep(5)
                xml = dump_ui("consent_fallback_%d" % (consent_attempt+1))
        else:
            print("Unknown screen after account select - dumping")
            xml = dump_ui("unknown_post_consent_%d" % (consent_attempt+1))
            break

    save_report("google_signin", True, "Google sign-in flow completed", None, "Handle account verification")

    # ── STEP 4: Handle post-signup screens ───────────────────────────────
    locked_keywords = ["Verify your email", "Verify email", "locked", "Confirm"]

    for post_attempt in range(8):
        if "Your account has been locked" in xml:
            print("Account locked screen - need to verify email")
            if find_and_tap(xml, ["Verify your email address", "Verify your email", "Verify email"], "verify_email"):
                time.sleep(8)
                xml = dump_ui("after_verify_tap_%d" % (post_attempt+1))
                save_report("verify_email", True, "Tapped email verification", None, "Check verification result")
            else:
                save_report("verify_email", False, "Could not find verify button", xml[:500], "Manual check")
                break
        elif "Enter your password" in xml or "password" in xml.lower():
            print("Password setup screen detected")
            save_report("password_screen", True, "Password setup screen", None, "Need to set password")
            break
        elif "Set up your profile" in xml or "Add a photo" in xml or "Pick a profile" in xml:
            print("Profile setup screen")
            # Skip for now, tap NEXT or SKIP
            if find_and_tap(xml, ["SKIP", "NEXT", "Continue"], "profile_skip"):
                time.sleep(5)
                xml = dump_ui("after_profile_%d" % (post_attempt+1))
            else:
                tap(1200, 2805)  # generic next
                time.sleep(5)
                xml = dump_ui("profile_fallback_%d" % (post_attempt+1))
        elif "What are you interested in" in xml or "Follow" in xml or "Interests" in xml:
            print("Interests/topics screen")
            if find_and_tap(xml, ["SKIP", "NEXT", "Continue", "Follow"], "interests_skip"):
                time.sleep(5)
                xml = dump_ui("after_interests_%d" % (post_attempt+1))
            else:
                tap(1200, 2805)
                time.sleep(5)
                xml = dump_ui("interests_fallback_%d" % (post_attempt+1))
        elif "See what's happening" in xml:
            print("Back at initial screen - Google sign-in may have failed")
            save_report("signin_fail", False, "Returned to initial screen after Google sign-in", xml[:500], "Try phone signup instead")
            break
        elif "com.twitter.android" in xml:
            # Unknown Twitter screen - take screenshot and move on
            print("Unknown Twitter screen - dumping for analysis")
            save_report("unknown_twitter", True, "Unknown Twitter screen", xml[:500], "Analyze screenshot")
            # Try tapping common positions for NEXT/SKIP
            tap(1200, 2805)
            time.sleep(5)
            xml = dump_ui("unknown_twitter_%d" % (post_attempt+1))
        else:
            print("Non-Twitter screen - might be in external flow")
            save_report("external_screen", True, "External screen", xml[:300], "Handle external flow")
            break

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        save_report("crash", False, "Script crashed", str(e), "Fix exception and rerun")
        raise
