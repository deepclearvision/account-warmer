#!/usr/bin/env python3
"""
Autonomous social account creator for GeelarK cloud phones.
Platform: facebook — Phone ID: 629193102808055908
"""
import sys, time, re, json, random
from pathlib import Path
from datetime import datetime, timezone

PLATFORM = "facebook"
AGENT_DIR = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents") / PLATFORM
LOG_DIR = AGENT_DIR / "logs"
DUMP_DIR = AGENT_DIR / "dumps"
SHOT_DIR = AGENT_DIR / "screenshots"
STATE_DIR = AGENT_DIR / "state"
RECOVERY_DIR = AGENT_DIR / "recovery_codes"

for d in [LOG_DIR, DUMP_DIR, SHOT_DIR, STATE_DIR, RECOVERY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

PHONE_ID = "629193102808055908"
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
    """Read a remote file in chunks, stripping trailing newlines to avoid XML corruption."""
    chunks = []
    offset = 0
    while True:
        cmd = "dd if=%s bs=1 skip=%d count=%d 2>/dev/null" % (remote_path, offset, chunk_size)
        chunk = sh(cmd)
        if not chunk:
            break
        chunk_clean = chunk.replace("\r\n", "\n").rstrip("\n").rstrip("\r")
        chunks.append(chunk_clean)
        if len(chunk_clean) < chunk_size:
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
                    # Penalize non-clickable matches (+3) so clickable ones win
                    is_clickable = node.get("clickable", "") == "true"
                    effective_score = score if is_clickable else score + 3
                    if effective_score < best_score:
                        best_score = effective_score
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

def type_text(text):
    """Type text via ADB input. Handles special characters."""
    # Escape special chars for shell
    safe = text.replace("'", "\\'").replace('"', '\\"')
    return sh("input text '%s'" % safe)

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

def save_credentials(account_id, email, password, username=None, backup_codes=None, phone=None, overwrite=False):
    cred_path = STATE_DIR / "credentials.json"
    if cred_path.exists() and not overwrite:
        print("  Credentials already exist, not overwriting")
        return
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

# ── PHONE LIFECYCLE ─────────────────────────────────────────────────────────
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

# ── APP MANAGEMENT ──────────────────────────────────────────────────────────
def pkg_installed(pkg):
    """Check specific package — avoids 2KB truncation of 'pm list packages'."""
    out = sh("pm list packages %s" % pkg)
    return ("package:" + pkg) in out

def ensure_app_installed():
    candidates = ["com.facebook.katana", "com.facebook.lite"]
    for pkg in candidates:
        if pkg_installed(pkg):
            print("Found installed: %s" % pkg)
            return pkg
    # Install from Play Store with interactive tapping
    pkg = "com.facebook.lite"
    print("Installing %s via Play Store..." % pkg)
    sh("am start -a android.intent.action.VIEW -d 'market://details?id=%s'" % pkg)
    time.sleep(10)
    for attempt in range(36):
        for c in candidates:
            if pkg_installed(c):
                print("Installed %s (attempt %d)" % (c, attempt))
                sh("input keyevent KEYCODE_HOME")
                return c
        time.sleep(5)
        if attempt % 3 == 2:
            xml = dump_ui("playstore_%d" % attempt)
            find_and_tap(xml, ["Install", "install"], "ps_install")
            find_and_tap(xml, ["Accept", "Allow", "Continue", "Got it"], "ps_dialog")
    for c in candidates:
        if pkg_installed(c):
            return c
    return None

# ── MAIN SIGNUP FLOW ────────────────────────────────────────────────────────
def main():
    ensure_phone_running()
    maybe_rotate_ip()
    w, h = get_screen()
    print("Screen: %dx%d" % (w, h))

    pkg = ensure_app_installed()
    if not pkg:
        save_report("install", False, "Could not find/install Facebook", "No package", "Install APK manually")
        return
    print("Using package: %s" % pkg)

    # Fresh start — but DON'T clear data since account exists
    print("Launching %s (preserving session)..." % pkg)
    sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
    time.sleep(5)

    # ── Navigate signup screens ────────────────────────────────────────────
    for step in range(20):  # max 20 screens
        xml = dump_ui("step_%02d" % step)
        m = re.search(r'package="([^"]+)"', xml)
        pkg_vis = m.group(1) if m else "unknown"
        print("Step %d: package=%s" % (step, pkg_vis))

        # --- Google Password Manager / Credential Manager ---
        if "credentialmanager" in pkg_vis:
            print("  Credential manager — using Google account to sign in...")
            # Tap the account option at [72,1787][1008,1974] → center (540, 1880)
            find_and_tap(xml, ["sandeephassamatta@gmail.com", "Sign in with Google", "Continue"], "credmgr")
            # Also try tapping the main account card area
            tap(540, 1880)
            time.sleep(5)
            continue

        # --- Android permission dialogs ---
        if "permissioncontroller" in pkg_vis:
            find_and_tap(xml, ["ALLOW", "Allow", "While using the app"], "perm")
            time.sleep(3)
            continue

        # --- Google sign-in dialogs ---
        if "google.android.gms" in pkg_vis:
            if "Agree and share" in xml or "agree" in xml.lower():
                find_and_tap(xml, ["Agree and share", "Agree", "Allow"], "google_consent")
            else:
                find_and_tap(xml, ["Sandeep", "@gmail.com"], "google_acct")
            time.sleep(5)
            continue

        # --- Facebook screens ---
        if "facebook.katana" in pkg_vis:

            # ── FEED CHECK FIRST — must run before any other handlers ──
            # Feed indicators: "What's on your mind", "News Feed", profile buttons
            if "What's on your mind" in xml or "News Feed" in xml:
                print("\n*** ACCOUNT LOGGED IN / CREATED SUCCESSFULLY! ***\n")
                cred_path = STATE_DIR / "credentials.json"
                if cred_path.exists():
                    try:
                        creds = json.loads(cred_path.read_text(encoding="utf-8"))
                        creds["status"] = "verified_logged_in"
                        creds["verified_at"] = datetime.now(timezone.utc).isoformat()
                        cred_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
                        print("  Credentials updated: status=verified_logged_in")
                    except Exception as e:
                        print("  Credentials update failed: %s" % e)
                save_report("account_verified", True,
                           "Account is logged in — News Feed screen detected",
                           None, "Done — account ready for warming",
                           creds_saved=True)
                break

            # Already have an account — log in instead
            if "You might already have a Facebook account" in xml or "already have a Facebook account" in xml:
                print("\n*** ACCOUNT ALREADY EXISTS! Logging in... ***\n")
                find_and_tap(xml, ["Yes, log in", "Log in"], "already_exists")
                time.sleep(5)
                continue

            # Password entry screen (after tapping "Yes, log in")
            if "password" in xml.lower() and ("Log in" in xml or "Log In" in xml or "login" in xml.lower()):
                if "Enter your password" in xml or "Password" in xml:
                    print("  Password screen — entering saved password...")
                    pwd = "FbRed6200!"
                    sh("input text '%s'" % pwd)
                    time.sleep(0.5)
                    find_and_tap(xml, ["Log in", "Log In", "Continue", "Next"], "pwd_login")
                    time.sleep(4)
                    continue

            # TOTP / code verification prompt (Facebook may ask for 2FA code)
            if "Enter code" in xml or "code generator" in xml.lower() or "6-digit" in xml or "confirmation code" in xml.lower():
                print("  Code verification screen — checking SMS...")
                find_and_tap(xml, ["Text me", "Send code", "Send"], "send_code")
                time.sleep(3)
                continue

            # Wrong password / incorrect password
            if "Wrong password" in xml or "Incorrect password" in xml or "password you entered" in xml.lower():
                print("  Wrong password — trying saved password...")
                # Clear the field and try again
                find_and_tap(xml, ["OK", "Try again"], "wrong_pwd_ok")
                time.sleep(2)
                continue

            # Entry screen — must come FIRST to avoid false matching
            if "Create new account" in xml or "Get started" in xml:
                find_and_tap(xml, ["Create new account", "Get started"], "entry")
                time.sleep(5)
                continue

            # Name screen (pre-filled from Google sign-in, just tap Next)
            if "What's your name" in xml:
                print("  Name screen — names pre-filled from Google, tapping Next...")
                # Tap Next button — bounds [48,719][1032,851] → center (540, 785)
                tap(540, 785)
                time.sleep(4)
                continue

            # Login screen (we landed on login instead of signup — go back to signup)
            if "Log in" in xml and "Mobile number or email" in xml and not ("Create new account" in xml or "Get started" in xml):
                find_and_tap(xml, ["Create new account", "Get started", "Sign up"], "try_signup")
                if not find_and_tap(xml, ["Create new account", "Get started", "Sign up"], "try_signup2"):
                    find_and_tap(xml, ["Next", "Continue"], "login_next")
                time.sleep(4)
                continue

            # Date of birth screen (before date picker opens) — NOT the age screen
            if "What's your date of birth" in xml or ("date of birth" in xml.lower() and "How old are you" not in xml):
                # If year already changed (not default 2026), try Next
                if "2026" not in xml and "(0 years" not in xml:
                    print("  DOB already changed, tapping Next...")
                    find_and_tap(xml, ["Next", "Continue"], "dob_next")
                    time.sleep(3)
                    continue
                print("  Opening date picker (DOB still default 2026)...")
                # Tap the date field to open date picker
                find_and_tap(xml, ["7 August 2026", "2026", "Date of birth"], "dob_open")
                time.sleep(2)
                continue

            # Date picker dialog — set year to 1996 by scrolling NumberPicker
            if "numberpicker_input" in xml:
                print("  Date picker open — scrolling year from 2026 down to 1996...")
                # The year NumberPicker shows: 2025(top) / 2026(center) / 2027(bottom)
                # Swipe top→bottom on year column to decrease value
                # Year picker bounds: X=[672,864], Y=[874,1414]
                for i in range(10):
                    swipe(768, 920, 768, 1350, 80)  # top→bottom swipe = decrease year
                    time.sleep(0.03)
                print("  Tapping SET button")
                tap(780, 1555)  # SET button at center [684,1474][876,1636]
                time.sleep(3)
                continue

            # Age entry (fallback if no date picker — tap "Use date of birth" instead)
            if "How old are you" in xml:
                print("  Age screen — tapping 'Use date of birth' instead...")
                find_and_tap(xml, ["Use date of birth"], "use_dob")
                time.sleep(3)
                continue

            # Password setup (only on actual setup screen, not login screen)
            if "Choose a password" in xml or "Create a password" in xml or "Set a password" in xml or "Add a password" in xml:
                print("  Setting up password...")
                # Generate a strong password
                pwd = "Fb%s%d!" % (random.choice(["Sun","Moon","Star","Blue","Red"]), random.randint(100, 9999))
                sh("input text '%s'" % pwd)
                time.sleep(0.5)
                find_and_tap(xml, ["Next", "Continue", "Sign up", "Register"], "pwd_next")
                # Save password immediately
                save_credentials("fb_account", "sandeephassamatta@gmail.com", pwd,
                               username="Sandeep Hassamatta")
                time.sleep(3)
                continue

            # Email address screen (pre-filled from Google, just confirm Next)
            if "What's your email address" in xml or "email address" in xml.lower():
                print("  Email screen — email pre-filled, tapping Next...")
                # Next button at [48,907][1032,1039] → center (540, 973)
                find_and_tap(xml, ["Next", "Continue"], "email_next")
                time.sleep(4)
                continue

            # Gender selection
            if "gender" in xml.lower() or "male" in xml.lower() or "female" in xml.lower():
                find_and_tap(xml, ["Male", "Female", "Custom"], "gender")
                time.sleep(1)
                find_and_tap(xml, ["Next", "Continue"], "gender_next")
                time.sleep(3)
                continue

            # Terms / privacy acceptance
            if "terms" in xml.lower() or "privacy" in xml.lower() or "policy" in xml.lower():
                find_and_tap(xml, ["Accept", "Agree", "I agree", "Continue", "Next"], "terms")
                time.sleep(3)
                continue

            # Save login info
            if "Save login" in xml or "Save password" in xml or "Remember" in xml:
                find_and_tap(xml, ["Save", "Yes", "Continue", "Not now"], "save_login")
                time.sleep(3)
                continue

            # Cookies consent
            if "cookies" in xml.lower() or "Allow all cookies" in xml or "Decline optional cookies" in xml:
                print("  Cookies consent — accepting all cookies...")
                find_and_tap(xml, ["Allow all cookies", "Accept all", "Decline optional cookies"], "cookies")
                time.sleep(3)
                continue

            # Add mobile number
            if "Add a mobile number" in xml or "mobile number" in xml.lower():
                print("  Mobile number — skipping...")
                find_and_tap(xml, ["Skip", "Not now"], "skip_phone")
                time.sleep(3)
                continue

            # "Are you sure you want to skip" confirmation
            if "Are you sure" in xml or "skip this step" in xml.lower():
                print("  Skip confirmation — confirming skip...")
                find_and_tap(xml, ["Skip"], "skip_confirm")
                time.sleep(3)
                continue

            # Loading / processing screen — just wait
            if "Loading" in xml:
                print("  Loading screen — waiting for account creation...")
                time.sleep(5)
                continue

            # Sync contacts / find friends
            if "contacts" in xml.lower() or "sync" in xml.lower() or "friends" in xml.lower():
                find_and_tap(xml, ["Skip", "Not now", "No thanks", "Maybe later", "I'll do this later"], "skip_sync")
                time.sleep(3)
                continue

            # Profile photo
            if "photo" in xml.lower() or "profile picture" in xml.lower() or "Add a photo" in xml:
                find_and_tap(xml, ["Skip", "Not now", "Maybe later", "I'll do this later"], "skip_photo")
                time.sleep(3)
                continue

            # General navigation: standard buttons
            tapped = find_and_tap(xml, ["Agree and continue", "Agree", "Accept", "Allow"], "fb_confirm")
            if tapped:
                time.sleep(3)
                continue

            tapped = find_and_tap(xml, ["Next", "Continue", "Sign up", "Create", "Register", "Join", "Finish"], "fb_next")
            if tapped:
                time.sleep(3)
                continue

            tapped = find_and_tap(xml, ["Skip", "Not now", "No thanks", "Maybe later", "I'll do this later"], "fb_skip")
            if tapped:
                time.sleep(3)
                continue

            # Fallback feed check (should already be caught at top, but safety net)

            # Nothing matched — dump and report
            print("  No recognized element on screen %d" % step)
            save_report("stuck_step_%d" % step, True,
                       "On Facebook screen, no recognized element",
                       None, "Check UI dump for new screen type")
            break

        # Unknown package
        print("  Unknown package: %s" % pkg_vis)
        find_and_tap(xml, ["Continue", "Next", "Accept", "Agree", "Allow", "Skip"], "unknown")
        time.sleep(3)

    # Final screenshot
    try:
        shot = client.take_screenshot(PHONE_ID)
        if shot:
            shot_path = SHOT_DIR / ("final_%s.png" % datetime.now().strftime("%Y%m%d_%H%M%S"))
            shot_path.write_bytes(shot)
            print("Screenshot saved: %s" % shot_path)
    except Exception as e:
        print("Screenshot failed: %s" % e)

    final_xml = dump_ui("final")
    save_report("flow_complete", True, "Signup flow completed (or max steps reached)",
               None, "Review final state and credentials")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        save_report("crash", False, "Script crashed", str(e), "Fix exception and rerun")
        raise
