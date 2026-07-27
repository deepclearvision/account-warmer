"""
PRIMARY GMAIL WARMING SCRIPT
============================================================================
Reference: GM-WARM-001
Created:   2026-07-24

Handles Gmail first-time setup via ADB shell, then scrolls inbox, opens
emails, and performs light interactions (star, archive, search, draft).

If the setup screens prove too complex for shell commands, a small GeelarK
RPA flow can be substituted for the setup portion only.
============================================================================
"""

import logging
import random
import time

log = logging.getLogger("mobile_gmail")

# NOTE: imports are inside functions to avoid module-level import conflicts
# with activities/calendar.py shadowing Python's built-in calendar module.

GMAIL_PACKAGE = "com.google.android.gm"

_INBOX_SEARCHES = [
    "receipt", "booking", "confirmation", "welcome",
    "newsletter", "order", "delivery", "invoice",
    "appointment", "verification",
]


def _dismiss_gmail_setup(phone_id: str, acc_id: str) -> None:
    """
    Handle Gmail's first-time setup screens via ADB.

    Gmail opens to WelcomeTourActivity which shows feature highlights
    ("Chat, Meet, Spaces all in one place", etc.) with a bottom button.
    The button text varies but is usually "Got it" / "Take me to Gmail".

    We try text matching first, then coordinate taps for the bottom button.
    """
    from activities.google_login_mobile import _shell, _find_and_tap
    log.info("[%s] Dismissing Gmail setup screens...", acc_id)

    # Pre-grant notification permission to suppress popup
    _shell(phone_id, f"pm grant {GMAIL_PACKAGE} android.permission.POST_NOTIFICATIONS")

    # Try text-based dismissal for each screen
    setup_buttons = [
        "Take me to Gmail", "TAKE ME TO GMAIL",
        "Got it", "GOT IT", "Got It",
        "Next", "NEXT",
        "Continue", "CONTINUE",
        "Skip", "SKIP",
        "Not now", "NOT NOW", "Not Now",
        "OK", "Okay", "OKAY",
        "I agree", "I AGREE",
        "Allow", "ALLOW",
        "Yes", "YES",
    ]

    # Multiple rounds — Gmail can show 3-5 setup screens.
    # First wait for the popup to fully appear before trying to dismiss it.
    time.sleep(3)
    for round_num in range(6):
        # Try text match AND coordinate tap each round (popup may be in WebView)
        tapped = _find_and_tap(phone_id, setup_buttons)
        # Also tap bottom positions — "Got it" is at the very bottom on 1080x2400 screens
        _shell(phone_id, "input tap 540 2200")
        time.sleep(0.5)
        _shell(phone_id, "input tap 540 2100")
        time.sleep(0.5)
        _shell(phone_id, "input tap 540 2000")
        time.sleep(0.5)
        if tapped:
            log.info("[%s] Setup round %d: text match + coord taps", acc_id, round_num + 1)
        else:
            log.info("[%s] Setup round %d: coord taps only", acc_id, round_num + 1)
        time.sleep(2)

    # Final: ensure we're at the inbox by checking for inbox elements
    _find_and_tap(phone_id, ["Take me to Gmail", "Got it", "Skip", "Inbox"])
    time.sleep(1)


def run_gmail_session(account: dict) -> dict:
    """
    Run a Gmail warming session on a single phone.

    Args:
        account: dict from geelark_accounts.yaml

    Returns:
        dict with success, emails_opened, actions, error
    """
    acc_id = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id", "")

    if not phone_id:
        return {"success": False, "error": "No phone ID"}

    # Fix: activities/calendar.py shadows Python's built-in calendar module.
    # Move activities dir to END of sys.path so stdlib takes priority,
    # then use __import__ for our activities modules.
    import sys as _sys
    _act = str(__import__('pathlib').Path(__file__).parent)
    _sys.path = [p for p in _sys.path if p != _act] + [_act]

    from core.geelark_client import GeelarKClient
    client = GeelarKClient()

    from activities.google_login_mobile import (
        _shell, _find_and_tap, _wait_for_foreground_app, _rotate_proxy_ip,
    )
    from activities.mobile_warmup import (
        _open_app, _press_home, _press_back, _swipe_down,
        _refresh_gps, _wait_for_phone_ready,
    )

    result = {"success": False, "emails_opened": 0, "actions": [], "error": ""}

    try:
        # ── 1. Rotate proxy ──────────────────────────────────────────────
        ip = _rotate_proxy_ip(acc_id)
        log.info("[%s] Proxy IP: %s", acc_id, ip)
        time.sleep(60)  # settling buffer

        # ── 2. Start phone + open browser ────────────────────────────────
        viewer = client.start_phone(phone_id)
        import subprocess
        subprocess.Popen(
            ["powershell", "-Command", f'Start-Process "{viewer}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # ── 3. Wait for boot ─────────────────────────────────────────────
        if not _wait_for_phone_ready(phone_id, acc_id, timeout=90):
            return {"success": False, "error": "Boot timeout"}

        # ── 4. Set GPS (Maps session-runner pattern — just _refresh_gps) ─
        _refresh_gps(phone_id, account, acc_id)

        # ── 5. Open Gmail ────────────────────────────────────────────────
        log.info("[%s] Opening Gmail...", acc_id)
        _shell(phone_id, f"pm grant {GMAIL_PACKAGE} android.permission.POST_NOTIFICATIONS")
        _open_app(phone_id, GMAIL_PACKAGE, acc_id)
        time.sleep(3)

        # ── 6. Dismiss any first-time setup screens ──────────────────────
        _dismiss_gmail_setup(phone_id, acc_id)
        time.sleep(2)

        # ── 7. Scroll inbox ──────────────────────────────────────────────
        log.info("[%s] Scrolling inbox...", acc_id)
        for i in range(random.randint(2, 4)):
            _swipe_down(phone_id)
            time.sleep(random.uniform(2.0, 4.0))
        result["actions"].append("scroll_inbox")

        # ── 8. Open first email in inbox ─────────────────────────────────
        # Tap where the first email typically sits in the list
        log.info("[%s] Opening first email...", acc_id)
        _shell(phone_id, "input tap 540 380")
        time.sleep(3)

        # Check if we're still in inbox (tap may have missed) — try again
        _shell(phone_id, "input tap 540 380")
        time.sleep(3)

        # Scroll through email body
        for _ in range(random.randint(1, 3)):
            _swipe_down(phone_id)
            time.sleep(random.uniform(3.0, 6.0))
        result["emails_opened"] += 1
        result["actions"].append("read_email")

        # ── 9. Random interaction on the email ────────────────────────────
        action = random.choice(["star", "archive", "none"])
        if action == "star":
            _shell(phone_id, "input tap 900 200")  # star icon top-right
            result["actions"].append("starred_email")
            log.info("[%s] Starred email", acc_id)
        elif action == "archive":
            _find_and_tap(phone_id, ["Archive", "archive"])
            result["actions"].append("archived_email")
            log.info("[%s] Archived email", acc_id)
        time.sleep(1.5)

        # ── 10. Go back to inbox ─────────────────────────────────────────
        _press_back(phone_id)
        time.sleep(2)

        # ── 11. Open a second email (different position) ──────────────────
        _shell(phone_id, "input tap 540 700")
        time.sleep(3)
        for _ in range(random.randint(1, 2)):
            _swipe_down(phone_id)
            time.sleep(random.uniform(2.0, 4.0))
        result["emails_opened"] += 1
        _press_back(phone_id)
        time.sleep(2)
        result["actions"].append("read_second_email")

        # ── 12. Search inbox (40% chance) ─────────────────────────────────
        if random.random() < 0.4:
            _find_and_tap(phone_id, ["Search", "Search mail", "search"])
            time.sleep(1)
            query = random.choice(_INBOX_SEARCHES)
            escaped = query.replace(" ", "%s")
            _shell(phone_id, f"input text {escaped}")
            time.sleep(0.5)
            _shell(phone_id, "input keyevent 66")
            time.sleep(3)
            # Browse results briefly
            _swipe_down(phone_id)
            time.sleep(random.uniform(3.0, 5.0))
            _press_back(phone_id)
            time.sleep(2)
            result["actions"].append(f"searched_{query}")
            log.info("[%s] Searched inbox: %s", acc_id, query)

        # ── 13. Compose a draft (30% chance, don't send) ──────────────────
        if random.random() < 0.3:
            _find_and_tap(phone_id, ["Compose", "compose", "Create"])
            time.sleep(2)
            # Type a subject
            _shell(phone_id, "input text Checking%sin")
            time.sleep(0.5)
            # Discard
            _press_back(phone_id)
            time.sleep(1)
            _find_and_tap(phone_id, ["Discard", "discard", "Delete"])
            time.sleep(1)
            result["actions"].append("drafted_email")
            log.info("[%s] Drafted and discarded email", acc_id)

        # ── 14. Home ──────────────────────────────────────────────────────
        _press_home(phone_id)
        time.sleep(1)

        result["success"] = True
        log.info("[%s] Gmail session complete.", acc_id)

    except Exception as e:
        log.error("[%s] Gmail failed: %s", acc_id, e)
        result["error"] = str(e)
        try:
            _shell(phone_id, "input keyevent KEYCODE_HOME")
        except Exception:
            pass

    finally:
        try:
            client.stop_phone(phone_id)
        except Exception:
            pass

    return result


def run_gmail_batch(accounts: list) -> list:
    import logging as _log
    _log.info("Starting Gmail batch for %d accounts", len(accounts))
    results = []
    for idx, account in enumerate(accounts):
        acc_id = account.get("id", "unknown")
        log.info("=== [%d/%d] %s ===", idx + 1, len(accounts), acc_id)
        result = run_gmail_session(account)
        results.append(result)
        status = "OK" if result["success"] else f"FAILED - {result['error']}"
        log.info("Result: %s", status)
    return results
