#!/usr/bin/env python3
"""
Facebook Login State Checker — reusable diagnostic for account health.

Usage:
    python check_login.py              # Check current state, launch FB if needed
    python check_login.py --json        # Output JSON only (for automation)

Returns one of: HEALTHY | LOGGED_OUT | FLAGGED | FRESH | UNKNOWN

DEFINITIVE LOGIN-PROOF MARKERS (only visible when logged into a healthy account):
    "What's on your mind?" — the status composer on the News Feed
    This is the STRONGEST signal because:
    - It ONLY appears on the main Feed of a logged-in, healthy account
    - It does NOT appear on login, signup, disabled, suspended, or verification screens
    - It's reliably present as both text and content-desc in the UI dump

BAD ACCOUNT MARKERS (account flagged/disabled/restricted):
    "suspended", "disabled", "restricted", "blocked"
    "verify your identity", "confirm your identity"
    "upload a photo", "account quality"
    "your account has been"
"""
import sys, time, re, json
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
sys.path.insert(0, str(PROJECT_ROOT))
from core.geelark_client import GeelarKClient, _post

PHONE_ID = "629193102808055908"
AGENT_DIR = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents\facebook")

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})
    return (r.get("output", "") or "")

def read_remote_file(remote_path, chunk_size=1800):
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

# ── MARKER DEFINITIONS ────────────────────────────────────────────────────────
POSITIVE_MARKERS = {
    "What's on your mind": "FEED_COMPOSER",     # STRONGEST login-proof
    "Go to profile": "PROFILE_BUTTON",
    "Story tray": "STORY_TRAY",
}

NEUTRAL_MARKERS = {
    "Create new account": "SIGNUP_ENTRY",
    "Get started": "ONBOARDING",
}

LOGIN_MARKERS = {
    "Mobile number or email": "LOGIN_EMAIL_FIELD",
    "Enter your password": "PASSWORD_ENTRY",
    "You might already have a Facebook account": "ACCOUNT_RECOVERY",
}

BAD_MARKERS = {
    "suspended": "ACCOUNT_SUSPENDED",
    "disabled": "ACCOUNT_DISABLED",
    "restricted": "ACCOUNT_RESTRICTED",
    "blocked": "ACCOUNT_BLOCKED",
    "verify your identity": "IDENTITY_VERIFY",
    "confirm your identity": "IDENTITY_CONFIRM",
    "upload a photo": "PHOTO_VERIFY",
    "account quality": "ACCOUNT_QUALITY",
    "your account has been": "ACCOUNT_ACTIONED",
    "We received your information": "INFO_RECEIVED",
}

# ── CORE CHECK ────────────────────────────────────────────────────────────────
def check_login_state(launch_fb=True):
    """Check Facebook login state. Returns dict with state and details."""
    client = GeelarKClient()

    # Ensure phone running
    health = client.check_phone_health(PHONE_ID)
    if health.get("status") != 0:
        client.start_phone(PHONE_ID)
        for _ in range(36):
            time.sleep(5)
            if client.check_phone_health(PHONE_ID).get("status") == 0:
                break

    # Verify FB installed
    pkg_out = sh("pm list packages com.facebook.katana")
    has_katana = "com.facebook.katana" in pkg_out
    if not has_katana:
        return {"state": "NOT_INSTALLED", "details": "Facebook app not installed"}

    # Launch Facebook if requested
    if launch_fb:
        sh("monkey -p com.facebook.katana -c android.intent.category.LAUNCHER 1")
        time.sleep(5)

    # Dump UI
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_remote = "/sdcard/fb_lc_%s.xml" % ts
    sh("uiautomator dump %s" % dump_remote)
    time.sleep(0.8)
    xml = read_remote_file(dump_remote)

    # Scan all markers
    xml_lower = xml.lower()
    found_positive = {k: v for k, v in POSITIVE_MARKERS.items() if k.lower() in xml_lower}
    found_neutral = {k: v for k, v in NEUTRAL_MARKERS.items() if k.lower() in xml_lower}
    found_login = {k: v for k, v in LOGIN_MARKERS.items() if k.lower() in xml_lower}
    found_bad = {k: v for k, v in BAD_MARKERS.items() if k.lower() in xml_lower}

    # Determine state
    if found_bad:
        state = "FLAGGED"
    elif found_positive:
        state = "HEALTHY"
    elif found_login:
        state = "LOGGED_OUT"
    elif found_neutral:
        state = "FRESH"
    else:
        state = "UNKNOWN"

    # Determine visible package
    m = re.search(r'package="([^"]+)"', xml)
    pkg = m.group(1) if m else "unknown"

    result = {
        "state": state,
        "visible_package": pkg,
        "positive_markers": list(found_positive.values()),
        "neutral_markers": list(found_neutral.values()),
        "login_markers": list(found_login.values()),
        "bad_markers": list(found_bad.values()),
        "check_timestamp": datetime.now(timezone.utc).isoformat(),
        "has_feed_composer": "What's on your mind" in xml,
        "phone_id": PHONE_ID,
    }

    # Save diagnostic dump
    (AGENT_DIR / "dumps" / ("fb_login_check_%s.xml" % ts)).write_text(xml, encoding="utf-8")

    # Save screenshot
    try:
        shot = client.take_screenshot(PHONE_ID)
        if shot:
            sp = AGENT_DIR / "screenshots" / ("fb_login_check_%s.png" % ts)
            sp.write_bytes(shot)
            result["screenshot"] = str(sp)
    except Exception:
        pass

    # Persist result
    (AGENT_DIR / "state" / "login_check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    return result

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    json_mode = "--json" in sys.argv
    launch = "--no-launch" not in sys.argv

    if not json_mode:
        print("Facebook Login Check — Phone %s" % PHONE_ID)
        print("=" * 60)

    result = check_login_state(launch_fb=launch)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        state = result["state"]
        print("State: %s" % state)
        print("Visible package: %s" % result["visible_package"])
        print("Feed composer: %s" % ("YES" if result["has_feed_composer"] else "NO"))

        if result["positive_markers"]:
            print("Positive: %s" % ", ".join(result["positive_markers"]))
        if result["login_markers"]:
            print("Login screen: %s" % ", ".join(result["login_markers"]))
        if result["bad_markers"]:
            print("BAD SIGNALS: %s" % ", ".join(result["bad_markers"]))
        if result.get("screenshot"):
            print("Screenshot: %s" % result["screenshot"])

        print()
        if state == "HEALTHY":
            print("Account is HEALTHY and logged in. Ready for warming.")
        elif state == "LOGGED_OUT":
            print("Account is LOGGED OUT. Run create_facebook.py to re-login.")
        elif state == "FLAGGED":
            print("WARNING: Account may be FLAGGED or DISABLED! Check bad signals above.")
        elif state == "FRESH":
            print("On signup/onboarding screen. No account yet.")
        else:
            print("State unknown. Check UI dump manually.")
