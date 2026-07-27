"""
PRIMARY CHROME WARMING SCRIPT
============================================================================
Reference: CH-WARM-001
Created:   2026-07-24

Opens Chrome, searches Google, browses results, clicks links. Uses the
exact same startup pattern as the Maps session runner.
============================================================================
"""

import logging
import random
import time

log = logging.getLogger("mobile_chrome")

CHROME_PACKAGE = "com.android.chrome"

# Imported inside run_chrome_session to avoid calendar.py conflict


def run_chrome_session(account: dict) -> dict:
    acc_id = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id", "")

    if not phone_id:
        return {"success": False, "error": "No phone ID"}

    # Fix: activities/calendar.py shadows Python's built-in calendar module
    import sys as _sys
    _act = str(__import__("pathlib").Path(__file__).parent)
    _sys.path = [p for p in _sys.path if p != _act] + [_act]

    from core.geelark_client import GeelarKClient
    client = GeelarKClient()

    from activities.google_login_mobile import (
        _shell, _find_and_tap, _wait_for_foreground_app, _rotate_proxy_ip,
    )
    from activities.mobile_warmup import (
        _press_home, _press_back, _swipe_down,
        _refresh_gps, _wait_for_phone_ready, _GOOGLE_SEARCHES,
    )

    result = {"success": False, "searches_done": 0, "search_terms": [], "error": ""}

    try:
        # ── 1. Rotate proxy ──────────────────────────────────────────────
        ip = _rotate_proxy_ip(acc_id)
        log.info("[%s] Proxy IP: %s", acc_id, ip)
        time.sleep(60)

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

        # ── 4. Set GPS ───────────────────────────────────────────────────
        _refresh_gps(phone_id, account, acc_id)

        # ── 5. Open Chrome to google.co.uk ───────────────────────────────
        log.info("[%s] Opening Chrome...", acc_id)
        _shell(phone_id,
            "am start -a android.intent.action.VIEW "
            "-d 'https://www.google.co.uk' com.android.chrome")
        _wait_for_foreground_app(phone_id, "chrome", timeout=15, acc_id=acc_id)

        # Dismiss Chrome first-run dialogs
        _find_and_tap(phone_id, ["Accept & continue", "Got it"])
        time.sleep(2)
        _find_and_tap(phone_id, ["No thanks", "Skip", "Not now", "Later"])
        time.sleep(2)

        # ── 6. Run 2-3 Google searches ───────────────────────────────────
        num_searches = random.randint(2, 3)
        for i in range(num_searches):
            # Tap search bar
            _find_and_tap(phone_id, [
                "Search or type URL", "Search Google or type a URL",
                "Search or type web address", "Address bar",
            ])
            time.sleep(2)

            # Type query
            query = random.choice(_GOOGLE_SEARCHES)
            escaped = query.replace(" ", "%s")
            _shell(phone_id, f"input text {escaped}")
            time.sleep(0.5)
            _shell(phone_id, "input keyevent 66")
            time.sleep(4)
            result["search_terms"].append(query)
            log.info("[%s] Search %d: %s", acc_id, i + 1, query)

            # Scroll results
            _swipe_down(phone_id)
            time.sleep(random.uniform(2.0, 3.5))

            # Click an organic result
            _shell(phone_id, "input tap 540 600")
            time.sleep(5)

            # Browse landing page
            _swipe_down(phone_id)
            time.sleep(random.uniform(4.0, 8.0))

            # Go back to results
            _press_back(phone_id)
            time.sleep(3)

        result["searches_done"] = num_searches
        result["success"] = True

        # ── 7. Home ──────────────────────────────────────────────────────
        _press_home(phone_id)
        time.sleep(1)
        log.info("[%s] Chrome session complete.", acc_id)

    except Exception as e:
        log.error("[%s] Chrome failed: %s", acc_id, e)
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


def run_chrome_batch(accounts: list) -> list:
    results = []
    for idx, account in enumerate(accounts):
        acc_id = account.get("id", "unknown")
        log.info("=== [%d/%d] %s ===", idx + 1, len(accounts), acc_id)
        result = run_chrome_session(account)
        results.append(result)
        status = "OK" if result["success"] else f"FAILED - {result['error']}"
        log.info("Result: %s", status)
    return results
