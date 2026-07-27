"""
PRIMARY YOUTUBE WARMING SCRIPT — DO NOT DELETE OR REPLACE
============================================================================
Reference: YT-WARM-001
Created:   2026-07-24
Based on:  GeelarK RPA flow "YouTube account warmup" (ID 629648416401522754)

This is the canonical YouTube warming script for the Account Warmer project.
It uses the native GeelarK RPA engine which handles all UI interaction
internally — no ADB WebView blindness issues.

Flow behaviour:
  1. Opens YouTube, dismisses dialogs (GOT IT, No thanks, Close)
  2. Verifies logged in (checks Shorts button exists)
  3. IF SearchKeyword provided: searches YouTube, clicks Shorts tab, plays Shorts
  4. IF SearchKeyword empty: browses Shorts feed directly
  5. Loops through N Shorts (ExpectedNumberOfVideosViewed):
     - Watches 8-30s per video
     - 10% chance: like video
     - 10% chance: view comments (5-10s dwell)
     - 10% chance: share (copy link, no actual share)
     - 10% chance: subscribe to channel
     - Scrolls to next Short
  6. Stops YouTube app

Editable parameters (edit in GeelarK dashboard flow editor):
  - Video watch time: 8-30s per Short
  - Like probability: 10%
  - Comment probability: 10%
  - Share probability: 10%
  - Subscribe probability: 10%
  - Scroll distance: 140-550px

Script-level parameters (edit below in _SEARCH_KEYWORDS or run_youtube_session):
  - video_count: random.randint(3, 8)
  - use_search: 60% chance
  - search keywords: _SEARCH_KEYWORDS list (20 UK terms)

GelarK flow params passed at runtime:
  - ExpectedNumberOfVideosViewed (number, required)
  - SearchKeyword (text, optional — empty = browse feed directly)
============================================================================
"""

import logging
import random
import time

log = logging.getLogger("mobile_youtube_v2")

from activities.google_login_mobile import (
    _shell, _rotate_proxy_ip, _wait_for_foreground_app,
)
from activities.mobile_warmup import _refresh_gps, _wait_for_phone_ready

YOUTUBE_FLOW_ID = "629648416401522754"
YOUTUBE_PACKAGE = "com.google.android.youtube"

_SEARCH_KEYWORDS = [
    "london street food", "manchester city highlights", "uk news",
    "recipe pasta", "funny football moments", "london vlog",
    "best restaurants london", "morning workout", "travel uk",
    "cooking at home", "premier league goals", "chelsea fc",
    "top gear clips", "gordon ramsay", "uk comedy",
    "car review", "guitar tutorial", "bbc news",
    "champions league", "smart home gadgets", "diy home",
]


def run_youtube_session_v2(account: dict) -> dict:
    """
    Run YouTube warming using the GeelarK RPA flow.

    Args:
        account: dict from geelark_accounts.yaml

    Returns:
        dict with success, video_count, search_keyword, error
    """
    acc_id = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id", "")

    if not phone_id:
        return {"success": False, "error": "No phone ID"}

    from core.geelark_client import GeelarKClient
    client = GeelarKClient()

    result = {"success": False, "video_count": 0, "search_keyword": "", "error": ""}

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

        # ── 5. Grant permissions (suppress popups during flow) ───────────
        _shell(phone_id, "pm grant com.google.android.youtube android.permission.POST_NOTIFICATIONS")

        # ── 6. Pick random params ────────────────────────────────────────
        video_count = random.randint(3, 8)
        use_search = random.random() < 0.6  # 60% search, 40% browse Shorts feed
        keyword = random.choice(_SEARCH_KEYWORDS) if use_search else ""

        result["video_count"] = video_count
        result["search_keyword"] = keyword

        # ── 7. Run the RPA flow ─────────────────────────────────────────
        param_map = {
            "ExpectedNumberOfVideosViewed": str(video_count),
        }
        if keyword:
            param_map["SearchKeyword"] = keyword
            log.info("[%s] Flow: search='%s', videos=%d", acc_id, keyword, video_count)
        else:
            log.info("[%s] Flow: browse Shorts feed, videos=%d", acc_id, video_count)

        task_id = client.run_custom_flow(
            flow_id=YOUTUBE_FLOW_ID,
            phone_id=phone_id,
            param_map=param_map,
            task_name=f"YouTube warmup — {acc_id}",
        )
        log.info("[%s] RPA task: %s", acc_id, task_id)

        # ── 8. Poll for completion ──────────────────────────────────────
        import datetime
        deadline = time.time() + 900  # 15 min max
        flow_ok = False
        while time.time() < deadline:
            time.sleep(5)
            try:
                tasks = client.query_tasks([task_id])
                if tasks:
                    t = tasks[0]
                    s = t.get("status", 0)
                    cost = t.get("cost", 0)
                    dots = "." * (1 + (int(time.time()) // 3) % 3)
                    print(f"\r  [{acc_id}] Flow {cost}s{dots}   ", end="", flush=True)
                    if s == 3:
                        print(f"\r  [{acc_id}] Flow COMPLETED ({cost}s)")
                        flow_ok = True
                        break
                    elif s == 4:
                        fail = (t.get("failDesc", "") or "")[:200]
                        print(f"\r  [{acc_id}] Flow FAILED: {fail}")
                        result["error"] = fail
                        break
                    elif s == 7:
                        print(f"\r  [{acc_id}] Flow CANCELLED")
                        result["error"] = "Cancelled"
                        break
            except Exception:
                pass

        result["success"] = flow_ok
        if flow_ok:
            log.info("[%s] YouTube V2 session complete.", acc_id)

    except Exception as e:
        log.error("[%s] YouTube V2 failed: %s", acc_id, e)
        result["error"] = str(e)

    finally:
        try:
            _shell(phone_id, "input keyevent KEYCODE_HOME")
        except Exception:
            pass
        try:
            client.stop_phone(phone_id)
        except Exception:
            pass

    return result


def run_youtube_batch_v2(accounts: list) -> list:
    results = []
    for idx, account in enumerate(accounts):
        acc_id = account.get("id", "unknown")
        log.info("=== [%d/%d] %s ===", idx + 1, len(accounts), acc_id)
        result = run_youtube_session_v2(account)
        results.append(result)
        status = "OK" if result["success"] else f"FAILED - {result['error']}"
        log.info("Result: %s", status)
    return results
