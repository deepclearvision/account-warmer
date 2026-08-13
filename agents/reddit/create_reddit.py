#!/usr/bin/env python3
"""
Autonomous Reddit account creator for GeelarK cloud phones.

Signup flow: Welcome → Email choice → Email form → Verification → Username → Password → Done

Key challenges solved:
- Android Credential Manager (com.android.credentialmanager) interception → dismiss at content-desc="Dismiss" or tap (540,500)
- Google Play Services (com.google.android.gms) "Sign in with ease" → tap SKIP
- React Native custom views not in uiautomator XML → input swipe with hold duration for gesture recognition
- Verification auto-submit via TextWatcher → hidden advance area at (540,1527)
- dd file reading on Android → no 2>/dev/null, filter dd status lines from stdout

Phone ID: 629194645154299961
Package: com.reddit.frontpage
Launcher activity: com.reddit.frontpage/launcher.default
"""
import sys, time, re, json, random, string, requests, os
from pathlib import Path
from datetime import datetime, timezone

PLATFORM = "reddit"
AGENT_DIR = Path(__file__).resolve().parent
LOG_DIR = AGENT_DIR / "logs"
DUMP_DIR = AGENT_DIR / "dumps"
STATE_DIR = AGENT_DIR / "state"
RECOVERY_DIR = AGENT_DIR / "recovery_codes"

for d in [LOG_DIR, DUMP_DIR, STATE_DIR, RECOVERY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

PHONE_ID = "629194645154299961"
PACKAGE = "com.reddit.frontpage"
LAUNCHER_ACTIVITY = "com.reddit.frontpage/launcher.default"

PROJECT_ROOT = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
sys.path.insert(0, str(PROJECT_ROOT))

# Direct _post import — bypasses GeelarKClient which has its own error handling
from core.geelark_client import _post

# ═══════════════════════════════════════════════════════════════════════════════
# SHELL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def sh(cmd, retries=1):
    """Execute a shell command on the phone.
    retries=1 by default because many ADB commands (tap, keyevent, swipe)
    return empty output on success, and retrying would double the action."""
    for attempt in range(retries):
        try:
            r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})
            out = r.get("output", "") or ""
            if out.strip():
                return out
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1)
    return ""

def tap(x, y):
    """Tap at coordinates."""
    sh("input tap %d %d" % (x, y))

def swipe(x1, y1, x2, y2, duration=200):
    """Swipe gesture. Use zero-distance with duration for React Native gesture recognition."""
    sh("input swipe %d %d %d %d %d" % (x1, y1, x2, y2, duration))

def long_press(x, y, ms=400):
    """Long press / hold at coordinates — triggers React Native press handlers."""
    swipe(x, y, x, y, ms)

def back():
    sh("input keyevent KEYCODE_BACK")

def home():
    sh("input keyevent KEYCODE_HOME")

def enter_key():
    sh("input keyevent KEYCODE_ENTER")

def clear_field(count=30):
    """Clear a text field by sending DEL keyevents."""
    for _ in range(count):
        sh("input keyevent KEYCODE_DEL")

def tap_remove_button(xml, field_resource_id):
    """Find and tap the Remove (X) button inside a text field.
    Reddit's text fields have a content-desc="Remove" button to clear text."""
    # Find the field element and look for a child with content-desc="Remove"
    # The Remove button is typically at the right edge of the field
    remove = find_bounds(xml, content_desc="Remove")
    if remove:
        tap(*remove)
        time.sleep(0.3)
        return True
    return False

def fill_field(x, y, text, xml=None):
    """Tap field, clear if needed, then input text.
    Uses field's Remove button if available, otherwise assumes empty field."""
    tap(x, y)
    time.sleep(0.4)
    # Try to clear via Remove button if XML provided
    if xml:
        tap_remove_button(xml, None)
    # Input text replaces at cursor; field should be empty now
    sh("input text %s" % text)
    time.sleep(0.5)

def type_text(text):
    """Type text reliably using keyevents — one per character.
    Avoids input text doubling bug on some Android versions.
    KEYCODE_R types 'r', KEYCODE_1 types '1', etc."""
    for ch in text:
        if ch == ' ':
            sh("input keyevent KEYCODE_SPACE")
        elif ch == '@':
            sh("input keyevent KEYCODE_AT")
        elif ch == '.':
            sh("input keyevent KEYCODE_PERIOD")
        elif ch == '_':
            sh("input keyevent KEYCODE_SHIFT_LEFT")
            time.sleep(0.02)
            sh("input keyevent KEYCODE_MINUS")
            time.sleep(0.02)
        elif ch == '-':
            sh("input keyevent KEYCODE_MINUS")
        elif ch.isupper():
            sh("input keyevent KEYCODE_SHIFT_LEFT")
            time.sleep(0.02)
            sh("input keyevent KEYCODE_%s" % ch.upper())
            time.sleep(0.02)
        elif ch.isdigit():
            sh("input keyevent KEYCODE_%s" % ch)
        elif ch in ('!',):
            sh("input keyevent KEYCODE_SHIFT_LEFT")
            time.sleep(0.02)
            sh("input keyevent KEYCODE_1")
            time.sleep(0.02)
        else:
            # Lowercase letters
            sh("input keyevent KEYCODE_%s" % ch.upper())
        time.sleep(0.04)
    time.sleep(0.3)

def type_digits_keyevent(digits):
    """Type digits one at a time via keyevent to trigger TextWatcher callbacks.
    Critical for Reddit's OTP field which auto-submits on TextWatcher fire."""
    for ch in digits:
        if ch.isdigit():
            sh("input keyevent KEYCODE_%s" % ch)
            time.sleep(0.10)
    print("  Typed %d digits via keyevents: %s" % (len(digits), digits))

# ═══════════════════════════════════════════════════════════════════════════════
# XML / UI DUMP HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def read_remote_file(path, max_chunks=30, chunk_size=1500):
    """Read a remote file in chunks using dd.

    CRITICAL: Do NOT use 2>/dev/null — it breaks dd on some Android shells.
    dd status lines appear in stdout; we filter them out.

    Chunk size of 1500 keeps us under the ~2000 char API response limit
    after accounting for dd status lines (~80 chars).
    """
    chunks = []
    for i in range(max_chunks):
        offset = i * chunk_size
        raw = sh("dd if=%s bs=1 skip=%d count=%d" % (path, offset, chunk_size))
        if not raw:
            break
        # Filter out dd status lines
        clean_lines = []
        for line in raw.split("\n"):
            s = line.strip()
            if not s:
                continue
            # Skip dd status lines
            if re.match(r'^\d+\+\d+\s+records\s+(in|out)$', s):
                continue
            if "bytes" in s and ("transferred" in s or "copied" in s):
                continue
            clean_lines.append(line)
        chunk = "".join(clean_lines)
        chunks.append(chunk)
        # Stop when we got less than a full chunk (end of file)
        if len(chunk) < chunk_size - 50 and i > 1:
            break
    return "".join(chunks)

def dump_ui(tag=""):
    """Dump the current UI hierarchy and return the XML string."""
    ts = datetime.now().strftime("%H%M%S")
    name = "rd_%s_%s" % (tag, ts) if tag else "rd_%s" % ts
    path = "/sdcard/%s.xml" % name
    sh("uiautomator dump %s" % path)
    time.sleep(2.0)  # uiautomator needs time to write the file
    xml = read_remote_file(path)
    # Save locally for debugging
    local = DUMP_DIR / ("%s.xml" % name)
    try:
        local.write_text(xml, encoding="utf-8")
    except Exception:
        pass
    return xml

def parse_ui(xml):
    """Extract package name and visible text from a UI dump."""
    if not xml:
        return "unknown", []
    pkg_m = re.search(r'package="([^"]+)"', xml)
    pkg = pkg_m.group(1) if pkg_m else "unknown"
    texts = [t.strip() for t in re.findall(r'text="([^"]*)"', xml)
             if t.strip() and len(t.strip()) > 1]
    return pkg, texts

def find_bounds(xml, text=None, resource_id=None, content_desc=None):
    """Find element center coordinates by text, resource-id, or content-desc.
    Uses regex — NOT ElementTree — because XML may be truncated or malformed.
    Returns (cx, cy) or None."""
    patterns = []
    if text:
        patterns.append(('text="%s"' % re.escape(text),
                         r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'))
    if resource_id:
        patterns.append(('resource-id="%s"' % re.escape(resource_id),
                         r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'))
    if content_desc:
        patterns.append(('content-desc="%s"' % re.escape(content_desc),
                         r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'))

    for attr_pattern, bounds_pattern in patterns:
        # Find the attribute followed by bounds within ~500 chars
        m = re.search(attr_pattern + r'[^>]*' + bounds_pattern, xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups()[-4:])
            return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None

def find_clickable(xml):
    """Return list of (text, resource_id, cx, cy) for all clickable elements."""
    results = []
    for m in re.finditer(
        r'clickable="true"[^>]*text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*'
        r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        t, rid = m.group(1), m.group(2)
        x1, y1, x2, y2 = map(int, m.groups()[2:6])
        results.append((t, rid, (x1+x2)//2, (y1+y2)//2))
    return results

def has_text(xml, *patterns):
    """Check if any pattern appears in visible text nodes only (not XML attributes).
    Extracts text="..." values and checks against patterns case-insensitively."""
    if not xml:
        return False
    # Extract only visible text from text="..." attributes — NOT from XML attributes
    texts = [t.strip().lower() for t in re.findall(r'text="([^"]*)"', xml)
             if t.strip() and len(t.strip()) > 1]
    all_text = " ".join(texts).lower()
    return any(p.lower() in all_text for p in patterns)

# ═══════════════════════════════════════════════════════════════════════════════
# OVERLAY / INTERCEPTION HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def dismiss_credential_manager():
    """Dismiss Android Credential Manager (com.android.credentialmanager) bottom sheet.
    Multiple approaches: content-desc Dismiss, Close sheet, or tap outside the sheet."""
    xml = dump_ui("cred_check")
    pkg, _ = parse_ui(xml)
    if "credentialmanager" not in pkg.lower():
        return False

    print("  Dismissing Credential Manager...")
    # Approach 1: content-desc="Dismiss" — usually at (892, 2118)
    coords = find_bounds(xml, content_desc="Dismiss")
    if coords:
        tap(*coords)
        time.sleep(4)
        return True

    # Approach 2: content-desc="Close sheet"
    coords = find_bounds(xml, content_desc="Close sheet")
    if coords:
        tap(*coords)
        time.sleep(4)
        return True

    # Approach 3: Tap outside the bottom sheet (upper area of screen)
    tap(540, 500)
    time.sleep(4)
    return True

def dismiss_google_play_services():
    """Dismiss Google Play Services sign-in interception.
    Appears after tapping email field with 'Sign in with ease', SKIP, NEXT buttons."""
    xml = dump_ui("gms_check")
    pkg, _ = parse_ui(xml)
    if "google.android.gms" not in pkg.lower() and "gms" not in pkg.lower():
        return False

    print("  Dismissing Google Play Services prompt...")

    # Find SKIP button
    coords = find_bounds(xml, text="SKIP")
    if coords:
        tap(*coords)
        time.sleep(4)
        return True

    # Fallback: tap at common SKIP position
    tap(540, 2100)
    time.sleep(4)

    # If still on GMS, try BACK
    xml2 = dump_ui("gms_back")
    pkg2, _ = parse_ui(xml2)
    if "gms" in pkg2.lower():
        back()
        time.sleep(4)
    return True

def handle_overlays():
    """Handle any system overlays (Credential Manager, Google Play Services).
    Returns True if an overlay was handled."""
    xml = dump_ui("overlay_check")
    pkg, texts = parse_ui(xml)

    # Credential Manager
    if "credentialmanager" in pkg.lower():
        dismiss_credential_manager()
        return True

    # Google Play Services sign-in prompt
    if "gms" in pkg.lower() and ("SKIP" in xml or "Sign in" in xml):
        dismiss_google_play_services()
        return True

    # Permission controller
    if "permissioncontroller" in pkg.lower():
        coords = find_bounds(xml, text="Allow") or find_bounds(xml, text="While using")
        if coords:
            tap(*coords)
            time.sleep(3)
        return True

    return False

# ═══════════════════════════════════════════════════════════════════════════════
# MAIL.GW TEMPORARY EMAIL
# ═══════════════════════════════════════════════════════════════════════════════

def mailgw_create_account():
    """Create a fresh mail.gw account. Returns (address, password, token)."""
    ts = int(time.time())
    local = "rdt%d" % (ts % 100000)
    pw = ''.join(random.choices(string.ascii_letters + string.digits, k=14))

    # Legitimate-looking domains that Reddit accepts (from exploration)
    domains = ["oakon.com", "questtechsystems.com", "pastryofistanbul.com",
               "voncontact.com", "mowline.com", "alapage.com"]

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    for domain in domains:
        address = "%s@%s" % (local, domain)
        print("  Trying: %s" % address)
        try:
            r = session.post("https://api.mail.gw/accounts",
                json={"address": address, "password": pw},
                headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code in (200, 201, 204):
                # Login to get token
                r2 = session.post("https://api.mail.gw/token",
                    json={"address": address, "password": pw},
                    headers={"Content-Type": "application/json"}, timeout=15)
                if r2.status_code == 200:
                    token = r2.json().get("token", "")
                    if token:
                        print("  Created: %s" % address)
                        return address, pw, token
        except Exception as e:
            print("  Error: %s" % e)
            continue

    raise RuntimeError("Could not create mail.gw account on any domain")

def mailgw_refresh_token(address, password):
    """Refresh mail.gw JWT token."""
    try:
        r = requests.post("https://api.mail.gw/token",
            json={"address": address, "password": password},
            headers={"Content-Type": "application/json"}, timeout=15)
        if r.status_code == 200:
            return r.json().get("token")
    except Exception as e:
        print("  Token refresh error: %s" % e)
    return None

def mailgw_poll_for_code(token, timeout=150, interval=5):
    """Poll mail.gw inbox for Reddit verification code. Returns 6-digit code or None."""
    print("  Polling mail.gw for verification code...")
    deadline = time.time() + timeout
    seen = set()

    while time.time() < deadline:
        try:
            r = requests.get("https://api.mail.gw/messages",
                headers={"Authorization": "Bearer %s" % token}, timeout=15)
            if r.status_code == 200:
                msgs = r.json().get("hydra:member", [])
                for msg in msgs:
                    mid = msg.get("id", "")
                    if mid in seen:
                        continue
                    seen.add(mid)

                    subject = msg.get("subject", "")
                    sender = msg.get("from", {}).get("address", "")

                    # Only read messages likely to be from Reddit
                    if not ("reddit" in (subject + sender).lower() or
                            "verify" in subject.lower() or
                            "code" in subject.lower()):
                        continue

                    print("  Checking: '%s' from %s" % (subject[:60], sender))

                    # Check subject first (always a string, fastest path)
                    code_m = re.search(r'\b(\d{6})\b', subject)
                    if code_m:
                        code = code_m.group(1)
                        print("  CODE FOUND in subject: %s" % code)
                        return code

                    # Read full message body as fallback
                    r2 = requests.get("https://api.mail.gw/messages/%s" % mid,
                        headers={"Authorization": "Bearer %s" % token}, timeout=15)
                    if r2.status_code == 200:
                        full = r2.json()
                        body = str(full.get("text", "") or "") + " " + str(full.get("html", "") or "")
                        # Find 6-digit code
                        code_m2 = re.search(r'\b(\d{6})\b', body)
                        if code_m2:
                            code = code_m2.group(1)
                            print("  CODE FOUND in body: %s" % code)
                            return code
        except Exception as e:
            print("  Poll error: %s" % e)

        time.sleep(interval)

    print("  Timeout — no code received")
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# PROXY ROTATION
# ═══════════════════════════════════════════════════════════════════════════════

def maybe_rotate_ip():
    """Rotate mobile proxy IP if needed (every ~20 min or 30% random chance)."""
    sf = STATE_DIR / "state.json"
    state = {}
    if sf.exists():
        try:
            state = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            pass

    last_rot = state.get("last_rotation", 0)
    if (time.time() - last_rot) < 1200 and random.random() > 0.30:
        print("IP rotation skipped")
        return

    print("Rotating proxy IP...")
    try:
        r = requests.post(
            "https://mobile-proxy-140-166.streamvia.io/index.php",
            auth=("19866_1", "Wealther3211!!"),
            data={"action": "changeip"}, timeout=15)
        print("  Rotation HTTP %d: %s" % (r.status_code, r.text.strip()))
    except Exception as e:
        print("  Rotation failed: %s" % e)

    time.sleep(30)

    try:
        ip = sh("curl -s --max-time 10 https://ifconfig.me").strip()
        print("  New IP: %s" % ip)
        state["last_ip"] = ip
    except Exception:
        pass

    state["last_rotation"] = time.time()
    sf.write_text(json.dumps(state, indent=2), encoding="utf-8")

# ═══════════════════════════════════════════════════════════════════════════════
# PHONE LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

def ensure_phone_running():
    """Ensure the GeelarK phone is running. Starts it if stopped.
    Raises RuntimeError if balance is insufficient."""
    print("Checking phone %s..." % PHONE_ID)

    try:
        status = _post("/open/v1/phone/status", {"ids": [PHONE_ID]})
        details = (status.get("successDetails") or [{}])[0]
        st = details.get("status", -1)
        print("  Status: %d" % st)

        if st == 0:  # Running
            print("  Phone is running")
            return True
        elif st == 1:  # Starting
            print("  Phone is starting, waiting...")
            for _ in range(36):
                time.sleep(5)
                s2 = _post("/open/v1/phone/status", {"ids": [PHONE_ID]})
                d2 = (s2.get("successDetails") or [{}])[0]
                if d2.get("status") == 0:
                    print("  Phone is now running")
                    return True
            raise RuntimeError("Phone stuck in starting state")

        # Status 2 = stopped, try to start
        print("  Starting phone...")
        start_r = _post("/open/v1/phone/start", {"ids": [PHONE_ID]})

        # Check for balance error
        fail_details = start_r.get("failDetails") or []
        for fd in fail_details:
            if fd.get("code") == 41001:  # balance not enough
                raise RuntimeError(
                    "GeelarK balance insufficient to start phone. "
                    "Add funds at https://open.geelark.com and retry."
                )

        if start_r.get("successAmount", 0) == 0:
            raise RuntimeError("Phone start failed: %s" % start_r)

        print("  Waiting for phone to boot...")
        for _ in range(48):  # Up to 4 minutes
            time.sleep(5)
            s3 = _post("/open/v1/phone/status", {"ids": [PHONE_ID]})
            d3 = (s3.get("successDetails") or [{}])[0]
            if d3.get("status") == 0:
                print("  Phone is running")
                return True

        raise RuntimeError("Phone failed to start within 4 minutes")

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError("Phone health check failed: %s" % e)

# ═══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════════════════════

def save_report(step, success, what_happened, what_failed=None, next_action=None,
                creds_saved=False):
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
    return report

def save_credentials(account_id, email, password, username=None, phone=None,
                     backup_codes=None):
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
    (STATE_DIR / "credentials.json").write_text(json.dumps(creds, indent=2), encoding="utf-8")
    if backup_codes:
        p = RECOVERY_DIR / ("%s_%s.txt" % (PLATFORM, account_id))
        p.write_text("\n".join(backup_codes), encoding="utf-8")
    print("  Credentials saved: %s" % (STATE_DIR / "credentials.json"))


def check_login_state(xml=None):
    """Detect whether Reddit is logged in, logged out, or in a bad state.

    Call with an existing XML dump or omit to capture a fresh one.
    Returns (state, detail, username_or_None)

    States:
      - LOGGED_IN_FEED        — bottom nav visible, Home feed accessible
      - LOGGED_IN_PROFILE     — profile page accessible, u/username visible
      - LOGGED_OUT            — sign-in/sign-up prompts visible
      - ONBOARDING            — stuck in post-signup onboarding
      - BANNED_SUSPENDED      — suspension/ban message visible
      - ERROR                 — error screen visible
      - NOT_REDDIT            — not inside the Reddit app
      - UNKNOWN               — cannot classify
    """
    if xml is None:
        xml = dump_ui("login_check")

    pkg_match = re.search(r'package="([^"]+)"', xml)
    pkg = pkg_match.group(1) if pkg_match else ""

    if pkg != "com.reddit.frontpage":
        return ("NOT_REDDIT", "Package is %s, not Reddit" % pkg, None)

    texts = [t.strip() for t in re.findall(r'text="([^"]*)"', xml)
             if t.strip() and len(t.strip()) > 1]
    all_text = " ".join(texts).lower()

    # ── Ban/suspension detection (check FIRST — trumps everything) ──
    ban_signals = [
        ("suspended", "Account suspended"),
        ("banned", "Account banned"),
        ("account suspended", "Account suspended message"),
        ("try again later", "Rate-limited or temporarily blocked"),
        ("violation", "Content policy violation"),
        ("something went wrong", "Generic error — possible shadowban"),
    ]
    for signal, label in ban_signals:
        if signal in all_text:
            return ("BANNED_SUSPENDED", label, None)

    # ── Logged-out detection ──
    signout_signals = [
        "sign up", "log in", "sign in", "continue with google",
        "continue with email", "continue with apple", "get started",
    ]
    logout_score = sum(1 for s in signout_signals if s in all_text)
    if logout_score >= 3:
        return ("LOGGED_OUT", "Sign-in/sign-up screen (%d signals)" % logout_score, None)

    # ── Onboarding detection ──
    onboarding_signals = [
        "about you", "choose your interests", "customize your feed",
        "tell us about yourself", "turn on notifications",
    ]
    if any(s in all_text for s in onboarding_signals):
        return ("ONBOARDING", "Post-signup onboarding in progress", None)

    # ── Error detection ──
    error_signals = [
        "error", "oops", "retry", "please try again",
    ]
    if any(s in all_text for s in error_signals):
        return ("ERROR", "Error screen detected", None)

    # ── Login confirmation ──
    # Definitive proof #1: bottom_nav resource-id with all 4 tabs
    has_bottom_nav = "bottom_nav" in xml
    nav_items = re.findall(r'resource-id="bottom_nav_button_label"[^>]*text="(Home|Create|Inbox|You)"', xml)
    if has_bottom_nav and len(nav_items) >= 3:
        # Extract username from avatar
        u_match = re.search(r'content-desc="([^"]+)\s+account"', xml)
        username = u_match.group(1) if u_match else None
        return ("LOGGED_IN_FEED", "Bottom nav confirmed (%d tabs)" % len(nav_items), username)

    # Definitive proof #2: profile page with u/username
    u_url = re.findall(r'u/([\w-]{2,20})', xml)
    if u_url:
        username = u_url[0]
        # Double-check it's not a random text mention
        if "followers" in all_text or "karma" in all_text:
            return ("LOGGED_IN_PROFILE", "Profile page confirmed", username)
        return ("LOGGED_IN_FEED", "u/%s visible" % username, username)

    # Strong signal: avatar content-desc
    avatar_match = re.search(r'content-desc="([^"]+)\s+account"', xml)
    if avatar_match:
        return ("LOGGED_IN_FEED", "Avatar content-desc found", avatar_match.group(1))

    # Weak signal: feed content
    feed_signals = ["r/", "hours ago", "days ago", "minutes ago", "upvotes", "comments"]
    if any(s in all_text for s in feed_signals):
        return ("LOGGED_IN_FEED_LIKELY", "Feed content visible (no nav confirmed)", None)

    return ("UNKNOWN", "Cannot classify — %d unique texts" % len(texts), None)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SIGNUP FLOW
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # ── Phase 1: Setup ──────────────────────────────────────────────────────
    print("=" * 60)
    print("REDDIT ACCOUNT CREATOR — Phone %s" % PHONE_ID)
    print("=" * 60)

    ensure_phone_running()
    maybe_rotate_ip()

    w, h = (1080, 2400)  # Known screen dimensions

    # ── Phase 2: Create or restore email account ────────────────────────────
    state_file = STATE_DIR / "state.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    email = state.get("email")
    mail_pw = state.get("password")
    mail_token = state.get("token")

    if email and mail_pw:
        fresh = mailgw_refresh_token(email, mail_pw)
        if fresh:
            mail_token = fresh
            state["token"] = fresh
            state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            print("Mail.gw token refreshed: %s" % email)
        else:
            print("Token expired, creating new mail.gw account...")
            email, mail_pw, mail_token = mailgw_create_account()
            state = {"email": email, "password": mail_pw, "token": mail_token,
                     "timestamp": int(time.time()), "service": "mail.gw"}
            state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    else:
        print("Creating new mail.gw account...")
        email, mail_pw, mail_token = mailgw_create_account()
        state = {"email": email, "password": mail_pw, "token": mail_token,
                 "timestamp": int(time.time()), "service": "mail.gw"}
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print("Using: %s" % email)

    # ── Phase 3: Launch Reddit app ──────────────────────────────────────────
    print("Clearing Reddit app data...")
    sh("pm clear %s" % PACKAGE)
    time.sleep(2)

    print("Launching Reddit...")
    # Try monkey first, fall back to am start
    sh("monkey -p %s 1" % PACKAGE)
    time.sleep(6)

    # Check if app launched; if not, try explicit activity
    xml_check = dump_ui("launch_check")
    pkg_check, _ = parse_ui(xml_check)
    if PACKAGE not in pkg_check and "credentialmanager" not in pkg_check.lower():
        print("  App didn't launch via monkey, trying am start...")
        sh("am start -n %s" % LAUNCHER_ACTIVITY)
        time.sleep(6)

    # ── Phase 4: Navigate signup flow ──────────────────────────────────────
    verification_code = None
    username = None
    password = None
    screen = "welcome"  # welcome → signup_choice → email_form → verify → username → password → done

    # Known coordinates (from exploration on 1080×2400 screen)
    # These are used as fallbacks when elements can't be found in XML
    COORDS = {
        "get_started": (540, 2004),
        "continue_with_email": (540, 1674),
        "email_field": (540, 1303),
        "email_continue": (540, 2136),
        "code_field": (540, 769),
        "hidden_verify": (540, 1527),  # Hidden advance area for verification
        "username_field": (540, 739),
        "username_continue": (540, 1371),
        "password_field": (540, 601),
        "password_continue": (540, 1395),  # Confirmed in XML: [48,1323][1032,1467]
        "cred_manager_dismiss": (892, 2118),
        "gms_skip": (540, 2100),
    }

    max_steps = 40
    for step_num in range(max_steps):
        # Handle any overlays first (skip during Google auth flow)
        if screen != "google_auth":
            while handle_overlays():
                pass  # Keep dismissing until we're back in the app

        xml = dump_ui("step%02d" % step_num)
        pkg, texts = parse_ui(xml)

        # Display current state
        top_texts = [t[:50] for t in texts[:8]]
        print("\nStep %d: pkg=%s screen=%s texts=%s" % (
            step_num, pkg, screen, top_texts))

        # ── Handle non-Reddit packages ──────────────────────────────────────
        if PACKAGE not in pkg:
            # Google auth, credential manager, and welcome screen are expected
            if screen in ("welcome", "google_auth") or \
               "credentialmanager" in pkg.lower() or \
               "gms" in pkg.lower() or \
               "google" in pkg.lower():
                pass  # Expected — handled by screen-specific logic below
            else:
                print("  Lost Reddit! Trying to recover...")
                sh("monkey -p %s 1" % PACKAGE)
                time.sleep(5)
                continue

        # ── Welcome screen ──────────────────────────────────────────────────
        if screen == "welcome":
            if has_text(xml, "Get Started", "get_started_button"):
                coords = find_bounds(xml, resource_id="get_started_button")
                if coords:
                    tap(*coords)
                else:
                    tap(*COORDS["get_started"])
                print("  Tapped Get Started")
                screen = "signup_choice"
                time.sleep(5)
                continue

            # If it went directly to email form (seen when Get Started not needed)
            if has_text(xml, "Continue with email"):
                screen = "signup_choice"
                continue

        # ── Signup choice screen ────────────────────────────────────────────
        if screen == "signup_choice":
            # Google sign-in often blocked by mobile proxy.
            # Use email/username signup as primary path.
            email_coords = (
                find_bounds(xml, content_desc="Use email or username") or
                find_bounds(xml, text="Continue with email") or
                find_bounds(xml, content_desc="Continue with email")
            )
            if email_coords:
                long_press(*email_coords, 200)
                print("  Tapped email signup at %s" % (email_coords,))
                screen = "email_form"
                time.sleep(5)
                continue

            # Fallback: Google SSO
            google_coords = (
                find_bounds(xml, content_desc="Continue with Google") or
                find_bounds(xml, text="Continue with Google")
            )
            if google_coords:
                long_press(*google_coords, 200)
                print("  Tapped Continue with Google at %s" % (google_coords,))
                screen = "google_auth"
                time.sleep(6)
                continue

            # Fallback: try tapping sign-up if the sheet disappeared
            sheet_visible = (
                "Continue with Google" in xml or
                "Continue with phone" in xml or
                "Use email or username" in xml or
                "Continue with email" in xml
            )
            if not sheet_visible:
                print("  Signup sheet may have closed, retrying...")
                tap(*COORDS["get_started"])
                time.sleep(4)
                continue

        # ── Google Auth screens ────────────────────────────────────────────
        if screen == "google_auth":
            # After tapping "Continue with Google", we may see:
            # 1. Android account picker (com.google.android.gms)
            # 2. Google consent screen ("Allow")
            # 3. Reddit post-auth (username, interests, etc.)
            # 4. Reddit home/feed (success!)

            # Success: we reached the Reddit home feed
            if PACKAGE in pkg and has_text(xml, "Home", "Popular", "feed",
                                           "trending", "communities", "Create a post"):
                print("\n*** ACCOUNT CREATED VIA GOOGLE! ***\n")
                # Save whatever we know
                save_credentials("rdt_%s" % str(int(time.time()))[-6:],
                               email, password or "google_auth", username="google_auth")
                save_report("success", True,
                           "Reddit account created via Google sign-in — Home/Feed detected",
                           None, "Account ready to use", creds_saved=True)
                break

            # Handle Google account chooser / consent
            if "google" in pkg.lower() or "gms" in pkg.lower():
                # Google sign-in flow — several possible screens:
                # 1. "Sign in with ease" — tap NEXT or SKIP to proceed
                # 2. Account list — tap the Google account
                # 3. Consent screen — tap ALLOW

                # Try buttons (case-insensitive search in XML for uppercase variants)
                tapped = False
                for btn_text in ("NEXT", "SKIP", "ALLOW", "Next", "Skip", "Allow",
                                "Continue", "CONTINUE", "I agree", "I AGREE"):
                    coords = find_bounds(xml, text=btn_text)
                    if coords:
                        tap(*coords)
                        print("  Google: tapped %s at %s" % (btn_text, coords))
                        time.sleep(4)
                        tapped = True
                        break

                if not tapped:
                    # Might be account selection — tap an email address
                    accts = re.findall(r'text="([^"]+@[^"]+)"', xml)
                    if accts:
                        coords = find_bounds(xml, text=accts[0])
                        if coords:
                            tap(*coords)
                            print("  Google: tapped account %s" % accts[0])
                            time.sleep(4)
                        else:
                            # Fallback: tap center of screen for account list
                            tap(540, 1200)
                            print("  Google: tapped center for account list")
                            time.sleep(4)
                    else:
                        # No recognizable buttons — tap center and hope
                        tap(540, 1200)
                        print("  Google: no button found, tapped center")
                        time.sleep(4)
                continue

            # If we're back in Reddit but not on home yet
            if PACKAGE in pkg:
                # Skip any onboarding/interests screens
                for skip in ("Skip", "Not now", "Maybe later"):
                    coords = find_bounds(xml, text=skip)
                    if coords:
                        tap(*coords)
                        print("  Skipped: %s" % skip)
                        time.sleep(3)
                        break
                else:
                    for ctn in ("Continue", "Next", "Done", "Finish",
                               "Get started", "Let's go"):
                        coords = find_bounds(xml, text=ctn)
                        if coords:
                            tap(*coords)
                            print("  Tapped: %s" % ctn)
                            time.sleep(3)
                            break
                continue

            # If we left Reddit entirely, try to get back
            print("  Lost Reddit during Google auth, recovering...")
            sh("monkey -p %s 1" % PACKAGE)
            time.sleep(5)
            continue

        # ── Email form ──────────────────────────────────────────────────────
        if screen == "email_form":
            # Check if we're on either:
            # - Signup email form ("Hi new friend", "Enter your email")
            # - Passwordless login form ("What's your email?")
            if has_text(xml, "What's your email", "Enter your email", "Email"):
                # Tap and fill email field
                coords = find_bounds(xml, resource_id="text_auto_fill")
                if not coords:
                    # Find any EditText
                    em = re.search(r'EditText[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
                    if em:
                        x1, y1, x2, y2 = map(int, em.groups())
                        coords = ((x1+x2)//2, (y1+y2)//2)
                    else:
                        coords = COORDS["email_field"]

                # Tap field and fill email (uses Remove button to clear if needed)
                fill_field(*coords, email, xml)
                print("  Entered: %s" % email)

                # Verify email was entered correctly
                check_xml = dump_ui("email_verify")
                typed = re.search(r'text="([^"]+@[^"]+)"', check_xml)
                if typed and email in typed.group(1):
                    print("  Email verified correct")
                else:
                    print("  WARNING: email may be incorrect, typed='%s'" %
                          (typed.group(1) if typed else "not found"))

                # Tap Continue
                coords = find_bounds(xml, resource_id="continue_button")
                if coords:
                    long_press(*coords, 200)
                else:
                    long_press(*COORDS["email_continue"], 200)
                print("  Tapped Continue")
                screen = "verify"
                time.sleep(5)
                continue

            # Check if we moved past email already (verification or magic link)
            if has_text(xml, "6 digit code", "Verification code",
                       "Verify your email", "Check your email", "Check your inbox"):
                screen = "verify"
                continue

        # ── Verification screen ─────────────────────────────────────────────
        if screen == "verify":
            # Check if we're on verification
            on_verify = has_text(xml, "6 digit code", "Verification code",
                                "Verify your email", "Check your email", "Check your inbox")

            if on_verify:
                # Get verification code if we don't have one
                if verification_code is None:
                    print("  Waiting for code...")
                    time.sleep(3)  # Brief wait for email delivery
                    verification_code = mailgw_poll_for_code(mail_token)

                    if not verification_code:
                        # Try tapping Resend
                        print("  No code yet, tapping Resend...")
                        resend = find_bounds(xml, text="Resend")
                        if resend:
                            tap(*resend)
                        else:
                            tap(960, 769)  # Resend button is usually right-aligned
                        time.sleep(2)
                        verification_code = mailgw_poll_for_code(mail_token, timeout=90)

                    if not verification_code:
                        print("  FAILED to get verification code")
                        save_report("no_code", False,
                                   "Could not receive verification code",
                                   "mail.gw did not deliver Reddit verification email",
                                   "Try a different email service")
                        break

                # Enter code all at once via input text (avoids TextWatcher race conditions).
                # Digits-only, so shell escaping is not an issue.
                coords = find_bounds(xml, resource_id="code_input_field")
                if coords:
                    tap(*coords)
                else:
                    tap(*COORDS["code_field"])
                time.sleep(0.3)
                clear_field(10)

                print("  Entering code: %s" % verification_code)
                sh("input text %s" % verification_code)
                time.sleep(1.5)

                # Now tap hidden advance area at (540, 1527) to submit
                print("  Tapping hidden advance area...")
                tap(*COORDS["hidden_verify"])
                time.sleep(1.5)

                # Also try KEYCODE_ENTER as IME submit
                enter_key()
                time.sleep(1.5)

                # Try sweeping long_press over the Continue button area
                # (bounding box is approximately [48,1371][1032,1515] at y offset)
                for y in range(1350, 1600, 50):
                    long_press(540, y, 200)
                    time.sleep(0.15)
                time.sleep(2)

                # Check if we moved past verification
                post_xml = dump_ui("post_verify")
                if has_text(post_xml, "6 digit code", "Verification code",
                           "Verify your email", "Resend"):
                    print("  Still on verification. Trying fallbacks...")
                    # Try KEYCODE_ENTER
                    enter_key()
                    time.sleep(2)
                    # Try various continue button positions
                    for y in (1371, 1450, 1550, 1650):
                        long_press(540, y, 250)
                        time.sleep(0.5)

                    # Check again
                    check_xml = dump_ui("verify_retry")
                    if has_text(check_xml, "6 digit code", "Verification code",
                               "Verify your email"):
                        print("  WARNING: Code may be wrong or timed out. Resetting...")
                        verification_code = None
                        continue

                screen = "username"
                time.sleep(3)
                continue

            # Not on verification — check if we're past it
            if has_text(xml, "Create your username", "username", "Choose"):
                screen = "username"
                continue
            if has_text(xml, "Set a password", "Password"):
                screen = "password"
                continue

        # ── Username screen ─────────────────────────────────────────────────
        if screen == "username":
            if has_text(xml, "Create your username"):
                # Strategy: use suggestion pills. They auto-fill AND auto-validate.
                # NEVER use keyevent-based typing — SHIFT keyevent doesn't work
                # (shift is released before the letter key), producing all-lowercase.

                # Check if a username is already filled and validated by Reddit
                if has_text(xml, "Great name! It's not taken"):
                    username = "VALIDATED"  # Will be updated when we see it
                    print("  Username already validated — tapping Continue")
                    coords = find_bounds(xml, resource_id="continue_button")
                    if coords:
                        long_press(*coords, 400)
                    else:
                        long_press(*COORDS["username_continue"], 400)
                    screen = "password"
                    time.sleep(5)
                    continue

                # No validated username — tap a suggestion pill
                # Pill auto-fills the field AND triggers validation
                # First pill is at ~(283, 1057) on 1080×2400
                print("  Tapping suggestion pill...")
                tap(283, 1057)
                time.sleep(3)

                # Check if validation succeeded
                check = dump_ui("user_sug")
                if has_text(check, "Great name! It's not taken"):
                    # Extract the username from the field
                    uf_m = re.search(r'text="([^"]+)"[^>]*resource-id="username_input_field"', check)
                    if not uf_m:
                        uf_m = re.search(r'username_input_field[^>]*text="([^"]+)"', check)
                    username = uf_m.group(1).strip() if uf_m else "Unknown"
                    print("  Username validated: %s" % username)

                    # Save early
                    save_credentials("rdt_%s" % str(int(time.time()))[-6:],
                                   email, "", username=username)

                    # Tap Continue
                    coords = find_bounds(check, resource_id="continue_button")
                    if coords:
                        long_press(*coords, 400)
                    else:
                        long_press(*COORDS["username_continue"], 400)
                    print("  Tapped Continue after suggestion")
                    screen = "password"
                    time.sleep(5)
                    continue
                else:
                    # Suggestion didn't validate — try another pill
                    print("  First suggestion failed, trying second pill (731, 1057)...")
                    tap(731, 1057)
                    time.sleep(3)
                    check2 = dump_ui("user_sug2")
                    if has_text(check2, "Great name! It's not taken"):
                        uf_m = re.search(r'text="([^"]+)"[^>]*resource-id="username_input_field"', check2)
                        if not uf_m:
                            uf_m = re.search(r'username_input_field[^>]*text="([^"]+)"', check2)
                        username = uf_m.group(1).strip() if uf_m else "Unknown"
                        print("  Username validated: %s" % username)
                        save_credentials("rdt_%s" % str(int(time.time()))[-6:],
                                       email, "", username=username)
                        coords = find_bounds(check2, resource_id="continue_button")
                        if coords:
                            long_press(*coords, 400)
                        else:
                            long_press(*COORDS["username_continue"], 400)
                        screen = "password"
                        time.sleep(5)
                        continue
                    else:
                        print("  WARNING: suggestion pills not validating")
                        continue

        # ── Password screen ─────────────────────────────────────────────────
        if screen == "password":
            if has_text(xml, "Set a password", "password"):
                password = "Rdt%s%d!" % (
                    random.choice(["Sun","Moon","Star","Blue","Red","Green"]),
                    random.randint(100, 9999))
                print("  Password: %s" % password)

                # Find password field (EditText)
                edits = re.findall(r'EditText[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
                if edits:
                    # Password field is usually the first EditText on this screen
                    x1, y1, x2, y2 = map(int, edits[0])
                    coords = ((x1+x2)//2, (y1+y2)//2)
                else:
                    coords = COORDS["password_field"]

                # Tap field and fill password (uses Remove button if field has content)
                fill_field(*coords, password, xml)
                print("  Password entered")
                time.sleep(1)

                # THE CONTINUE BUTTON: Confirmed visible in uiautomator XML on password screen.
                # Bounds: [48,1323][1032,1467], center (540, 1395).
                # This is NOT a React Native custom view — it's found by resource-id="continue_button".
                print("  Tapping password Continue...")

                # Try finding continue_button in XML
                coords = find_bounds(xml, resource_id="continue_button")
                if coords:
                    print("  Found continue_button at %s" % (coords,))
                    # Long press for React Native gesture recognition
                    long_press(*coords, 400)
                else:
                    print("  Continue not found, using known coordinates")
                    long_press(*COORDS["password_continue"], 400)
                time.sleep(1)

                # Also try KEYCODE_ENTER as IME submit fallback
                enter_key()
                time.sleep(2)

                # Save credentials with password
                save_credentials("rdt_%s" % str(int(time.time()))[-6:],
                               email, password, username=username)

                # Check if we advanced
                time.sleep(3)
                check_xml = dump_ui("pass_check")
                if not has_text(check_xml, "Set a password", "Password", "password"):
                    print("  Advanced past password screen!")
                else:
                    print("  WARNING: Still on password screen. Trying sweep...")
                    # Sweep long_press across Continue button area
                    for y in range(1300, 1550, 25):
                        long_press(540, y, 300)
                        time.sleep(0.15)
                    time.sleep(3)
                    # Check again
                    check2 = dump_ui("pass_check2")
                    if not has_text(check2, "Set a password", "Password"):
                        print("  Advanced after sweep!")
                    else:
                        print("  Still stuck. Continue button may need different approach.")

                screen = "done"
                continue

        # ── Post-signup screens (interests, avatar, etc.) ────────────────────
        if screen == "done":
            # ── Success detection using check_login_state ──────────────────
            login_state, login_detail, detected_username = check_login_state(xml)
            if login_state in ("LOGGED_IN_FEED", "LOGGED_IN_FEED_LIKELY", "LOGGED_IN_PROFILE"):
                print("\n*** ACCOUNT CREATED SUCCESSFULLY ***")
                if detected_username:
                    username = detected_username
                    print("  Username: %s" % username)
                    save_credentials("rdt_%s" % str(int(time.time()))[-6:],
                                   email, password, username=username)
                save_report("success", True,
                           "Reddit account created successfully — %s" % login_detail,
                           None, "Account ready to use", creds_saved=True)
                break

            if login_state == "BANNED_SUSPENDED":
                print("\n*** ACCOUNT BANNED/SUSPENDED: %s ***" % login_detail)
                save_report("banned", False,
                           "Account banned/suspended during signup: %s" % login_detail,
                           login_detail, "Account may be lost — start fresh")
                break

            if login_state == "LOGGED_OUT":
                print("\n*** SESSION LOST — LOGGED OUT ***")
                save_report("logged_out", False,
                           "Account logged out during onboarding",
                           None, "May need to re-authenticate")
                break

            # ── Birthday / Age Selection ───────────────────────────────────
            if has_text(xml, "About you", "Birthday"):
                # Check if birthday is already selected (shows as "Month DD, YYYY")
                bd_match = re.search(
                    r'(January|February|March|April|May|June|July|'
                    r'August|September|October|November|December)\s+\d{1,2},\s+\d{4}',
                    xml)
                if bd_match:
                    # Birthday selected — tap Continue
                    print("  Birthday selected: %s" % bd_match.group(0))
                    coords = find_bounds(xml, text="Continue")
                    if coords:
                        print("  Tapping Continue after birthday...")
                        long_press(*coords, 400)
                        time.sleep(5)
                    continue
                else:
                    # Need to select birthday — tap the date picker
                    print("  Opening birthday date picker...")
                    coords = find_bounds(xml, resource_id="picker_text_field_testTag")
                    if not coords:
                        coords = find_bounds(xml, text="Birthday")
                    if coords:
                        long_press(*coords, 400)
                        time.sleep(3)
                        # Now the date picker should be open
                        check = dump_ui("bday_picker")
                        # Pick a date: August 8, 1997 (default month usually)
                        # Tap a visible date cell, then OK
                        ok = find_bounds(check, text="OK")
                        if ok:
                            # Tap the first visible date cell (e.g. Friday 8 August 1997)
                            date_cell = None
                            for m in re.finditer(
                                r'text="[A-Z][a-z]+day, (\d+) ([A-Z][a-z]+) (\d{4})"',
                                check):
                                # Tap the 8th or similar mid-month date
                                day = int(m.group(1))
                                if 5 <= day <= 15:
                                    coords = find_bounds(check, text=m.group(0))
                                    if coords:
                                        date_cell = coords
                                        break
                            if date_cell:
                                long_press(*date_cell, 300)
                                time.sleep(1)
                            long_press(*ok, 300)
                            time.sleep(3)
                    continue

            # ── Birthday Confirmation Dialog ───────────────────────────────
            if has_text(xml, "Confirm your birthday?"):
                print("  Confirming birthday...")
                coords = find_bounds(xml, text="Yes, Confirm")
                if coords:
                    long_press(*coords, 400)
                    time.sleep(5)
                continue

            # ── Ads Personalization ────────────────────────────────────────
            if has_text(xml, "Your ads personalization choices"):
                print("  Accepting ads personalization...")
                coords = find_bounds(xml, text="Accept")
                if coords:
                    long_press(*coords, 400)
                    time.sleep(4)
                continue

            # ── Generic onboarding screens (interests, subreddits, etc.) ───
            # Priority: Skip > Not now > Continue/Next/Done
            skip_priority = ["Skip", "Not now", "Maybe later"]
            continue_texts = ["Continue", "Next", "Done", "Finish", "Get started"]

            tapped = False
            for st in skip_priority:
                coords = find_bounds(xml, text=st)
                if coords:
                    print("  Tapping '%s' at %s" % (st, coords))
                    long_press(*coords, 400)
                    time.sleep(4)
                    tapped = True
                    break

            if not tapped:
                for ct in continue_texts:
                    coords = find_bounds(xml, text=ct)
                    if coords:
                        print("  Tapping '%s' at %s" % (ct, coords))
                        long_press(*coords, 400)
                        time.sleep(4)
                        tapped = True
                        break

            # Fallback: tap common button positions
            if not tapped:
                print("  No button found — trying common positions...")
                for y in [2136, 2200, 2060, 1920]:
                    long_press(540, y, 300)
                    time.sleep(2)

            continue

        # ── Generic fallback ────────────────────────────────────────────────
        # Try any Continue/Next/OK buttons
        for btn_text in ("Continue", "Next", "OK", "Got it", "Accept", "Skip"):
            coords = find_bounds(xml, text=btn_text)
            if coords:
                print("  Falling back: tapped '%s' at %s" % (btn_text, coords))
                long_press(*coords, 200)
                time.sleep(3)
                break

    # ── Final state ──────────────────────────────────────────────────────
    xml = dump_ui("final")
    _, texts = parse_ui(xml)
    print("\n=== FINAL SCREEN ===")
    print("Texts: %s" % [t[:60] for t in texts[:15]])

    save_report("flow_complete", True,
               "Signup flow finished (max steps: %d)" % max_steps,
               None, "Review final state manually")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print("\nFATAL: %s" % e)
        save_report("fatal", False, str(e), str(e),
                   "Fix the issue and re-run")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        save_report("crash", False, "Script crashed", str(e),
                   "Fix exception and re-run")
        sys.exit(1)
