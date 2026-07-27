"""
Mobile YouTube Warming — Standalone script for YouTube watch sessions.

Pattern follows the proven _warmup_maps() structure from mobile_warmup.py:
  - open app → find search → type → find result → interact → home
"""

import logging
import random
import re
import time

log = logging.getLogger("mobile_youtube")

from activities.google_login_mobile import (
    _shell, _find_and_tap, _wait_for_foreground_app,
    _rotate_proxy_ip,
)
from activities.mobile_warmup import (
    _open_app, _press_home, _press_back, _swipe_down,
    _refresh_gps, _wait_for_phone_ready,
)

YOUTUBE_PACKAGE = "com.google.android.youtube"

_YOUTUBE_SEARCHES = [
    "london street food", "manchester city highlights", "uk news today",
    "recipe pasta", "funny moments football", "london vlog",
    "best restaurants london", "morning workout routine", "travel uk",
    "cooking at home", "premier league goals", "london weather forecast",
    "how to make sourdough", "british sitcom clips", "chelsea fc highlights",
    "top gear clips", "gordon ramsay recipe", "uk comedy",
    "car review 2025", "london walking tour", "guitar tutorial beginner",
    "diy home improvement", "bbc news headlines", "champions league highlights",
    "smart home gadgets",
]


def _dump_ui(phone_id: str, label: str = "") -> str:
    """Dump the current UI hierarchy and return package + visible text."""
    ok, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
    if not ok or not xml:
        return f"[{label}] UI dump failed"
    # Package is in the root hierarchy node: <hierarchy rotation="0"> ... first <node package="...">
    pkg_match = re.search(r'package="([^"]+)"', xml)
    pkg = pkg_match.group(1) if pkg_match else "?"
    # Get current focus
    focus_match = re.search(r'mCurrentFocus=Window\{[^}]*\s+(\S+)\}', xml)
    texts = re.findall(r'text="([^"]+)"', xml)
    descs = re.findall(r'content-desc="([^"]+)"', xml)
    visible = [t for t in texts + descs if t.strip()]
    summary = ", ".join(visible[:20]) if visible else "(no visible text)"
    # Also look for focused/selected elements
    focused = re.findall(r'focused="true"[^>]*text="([^"]*)"', xml)
    if focused and any(f.strip() for f in focused):
        summary += f" | focused: {', '.join(f for f in focused if f.strip())}"
    return f"[{label}] pkg={pkg} | {summary}"


def _type_and_search(phone_id: str, text: str) -> None:
    """Type text into the current input field and press Enter."""
    escaped = text.replace(" ", "%s")
    _shell(phone_id, f"input text {escaped}")
    time.sleep(0.5)
    _shell(phone_id, "input keyevent 66")
    time.sleep(3)


def run_youtube_session(account: dict) -> dict:
    acc_id = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id", "")

    if not phone_id:
        return {"success": False, "error": "No phone ID"}

    from core.geelark_client import GeelarKClient
    client = GeelarKClient()

    result = {"success": False, "watch_time_s": 0, "search_term": "", "video_count": 0, "error": ""}

    try:
        # ── 1. Rotate proxy ─────────────────────────────────────────────────
        ip = _rotate_proxy_ip(acc_id)
        log.info("[%s] Proxy IP: %s", acc_id, ip)

        # ── 2. Start phone + open browser ────────────────────────────────────
        viewer = client.start_phone(phone_id)
        import subprocess
        subprocess.Popen(
            ["powershell", "-Command", f'Start-Process "{viewer}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # ── 3. Wait for boot ─────────────────────────────────────────────────
        if not _wait_for_phone_ready(phone_id, acc_id, timeout=300):
            return {"success": False, "error": "Boot timeout"}
        log.info(_dump_ui(phone_id, "after boot"))

        # ── 4. Set GPS ───────────────────────────────────────────────────────
        #    Same pattern as Maps: _refresh_gps handles everything internally
        #    (mock location broadcast or GeelarK API fallback).
        _refresh_gps(phone_id, account, acc_id)
        # Open Maps briefly to confirm GPS registers with Google services
        _shell(phone_id, "monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1")
        time.sleep(4)
        _shell(phone_id, "input tap 1010 1650")  # My Location button
        time.sleep(3)
        log.info(_dump_ui(phone_id, "after GPS + Maps"))
        _shell(phone_id, "input keyevent KEYCODE_HOME")
        time.sleep(1)

        # ── 5. Open YouTube ─────────────────────────────────────────────────
        _shell(phone_id, "pm grant com.google.android.youtube android.permission.POST_NOTIFICATIONS")
        _open_app(phone_id, YOUTUBE_PACKAGE, acc_id)
        time.sleep(3)
        _find_and_tap(phone_id, ["Allow", "ALLOW"])
        time.sleep(1)
        log.info(_dump_ui(phone_id, "YouTube opened"))

        # ── 6. Tap search icon ──────────────────────────────────────────────
        tapped = _find_and_tap(phone_id, ["Search", "Search YouTube"])
        if not tapped:
            _shell(phone_id, "input tap 900 150")
            time.sleep(1)
        time.sleep(2)
        log.info(_dump_ui(phone_id, "after search tap"))

        # ── 7. Type search query ────────────────────────────────────────────
        query = random.choice(_YOUTUBE_SEARCHES)
        result["search_term"] = query
        log.info("[%s] Searching: %s", acc_id, query)
        _type_and_search(phone_id, query)
        time.sleep(3)
        log.info(_dump_ui(phone_id, "after search"))

        # ── 8. Scroll past ads, tap first organic result ────────────────────
        _swipe_down(phone_id)
        time.sleep(2)
        # Try text match for video results, fall back to coordinate
        tapped_vid = _find_and_tap(phone_id, ["views", "ago", "Watch", "Play"])
        if not tapped_vid:
            _shell(phone_id, "input tap 540 500")
        time.sleep(4)
        log.info(_dump_ui(phone_id, "after tapping video"))

        # ── 9. Skip pre-roll ad ──────────────────────────────────────────────
        time.sleep(5)
        _find_and_tap(phone_id, ["Skip", "Skip Ad", "Skip ad", "SKIP"])
        time.sleep(1)
        log.info(_dump_ui(phone_id, "after skip-ad check"))

        # ── 10. Watch video ─────────────────────────────────────────────────
        watch_time = random.randint(60, 180)
        result["watch_time_s"] = watch_time
        log.info("[%s] Watching for %ds", acc_id, watch_time)

        first_segment = watch_time // 3
        time.sleep(first_segment)

        # Mid-watch tap (shows controls — natural viewer behaviour)
        _shell(phone_id, "input tap 540 960")
        time.sleep(random.uniform(1.5, 3.0))
        _find_and_tap(phone_id, ["Skip", "Skip Ad"])

        time.sleep(watch_time - first_segment)

        # Scroll comments 50% chance
        if random.random() < 0.5:
            _swipe_down(phone_id)
            time.sleep(random.uniform(3.0, 6.0))
            _shell(phone_id, "input swipe 540 400 540 1200 500")
            time.sleep(2)

        # ── 11. Exit video ──────────────────────────────────────────────────
        _press_back(phone_id)
        time.sleep(2)
        log.info(_dump_ui(phone_id, "after back from video"))

        # ── 12. Home ────────────────────────────────────────────────────────
        _shell(phone_id, "input keyevent KEYCODE_HOME")
        time.sleep(1)
        log.info(_dump_ui(phone_id, "final home"))

        result["success"] = True
        result["video_count"] = 1
        log.info("[%s] YouTube session complete.", acc_id)

    except Exception as e:
        log.error("[%s] YouTube failed: %s", acc_id, e)
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


def run_youtube_batch(accounts: list) -> list:
    results = []
    for idx, account in enumerate(accounts):
        acc_id = account.get("id", "unknown")
        log.info("=== [%d/%d] %s ===", idx + 1, len(accounts), acc_id)
        result = run_youtube_session(account)
        results.append(result)
        log.info("Result: %s", "OK" if result["success"] else f"FAILED - {result['error']}")
    return results
