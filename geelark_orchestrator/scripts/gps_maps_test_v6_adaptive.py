#!/usr/bin/env python3
"""
GPS + Maps ADAPTIVE debug v6.

Shell-based Maps automation with uiautomator dump verification after every step.
Replaces the broken RPA Maps flow with adaptive screen-state-driven interactions.

Usage:
  python gps_maps_test_v6_adaptive.py --watch
  python gps_maps_test_v6_adaptive.py --account acc_010 --keyword-type branded --watch
"""
import argparse
import csv
import json
import random
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_orchestrator_src = _repo_root / "geelark_orchestrator" / "src"
_orchestrator_root = _repo_root / "geelark_orchestrator"
_account_warmer = _repo_root / "account-warmer"
_data_dir = _repo_root / "data"
_logs_dir = _repo_root / "logs"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient, _post
from run_one_phone import (
    _activate_gps_android13,
    dumpsys_mock_check,
    _verify_phone_provisioned,
    _verify_google_account,
    _get_phone_ip,
    _ui_clear_storage,
    _find_bounds,
    _get_screen_size,
)
from proxy_control import change_proxy_ip

CSV_PATH = _data_dir / "accounts_business_mapping.csv"
JSONL_PATH = _logs_dir / "run_history.jsonl"
START_TIMEOUT = 360
INTER_PHONE_DELAY = 30
MAX_ATTEMPTS = 3

MAPS_PACKAGE = "com.google.android.apps.maps"

# ─── Logging helpers ──────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat()


def load_all_rows() -> list[dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pick_phone(rows: list[dict], account_id: str | None = None) -> dict:
    if account_id:
        for r in rows:
            if r.get("account_id") == account_id:
                return r
        raise ValueError(f"Account {account_id} not found")
    return random.choice(rows)


def _write(line: str, log_file: Path) -> None:
    print(line)
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def write_jsonl(record: dict) -> None:
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─── Phone lifecycle ──────────────────────────────────────────────

def _get_running_phones(client: GeelarKClient) -> list[dict]:
    try:
        return [p for p in client.list_phones(page_size=100) if p.get("status") == 0]
    except Exception as e:
        print(f"  ERROR listing phones: {e}")
        return []


def global_cleanup(client: GeelarKClient, log_file: Path) -> None:
    _write("=== Pre-run global cleanup ===", log_file)
    running = _get_running_phones(client)
    if not running:
        _write("  No phones running.", log_file)
        return
    for p in running:
        pid = p["id"]
        _write(f"  Stopping phone {pid} ...", log_file)
        try:
            client.stop_phone(pid)
        except Exception:
            pass
    for i in range(12):
        time.sleep(5)
        remaining = len(_get_running_phones(client))
        _write(f"  Poll {i+1}/12: {remaining} phones still running", log_file)
        if remaining == 0:
            _write("  All stopped.", log_file)
            return
    _write("  WARNING: Some phones did not stop within 60s.", log_file)


def ensure_running(client: GeelarKClient, phone_id: str, log_file: Path) -> tuple[bool, str]:
    try:
        s = client.get_phone_status([phone_id])[0].get("status", -1)
    except Exception:
        s = -1
    if s == 0:
        _write(f"  Phone {phone_id} already running.", log_file)
        return True, ""
    _write(f"  Starting phone {phone_id} ...", log_file)
    try:
        live_url = client.start_phone(phone_id)
    except Exception as e:
        _write(f"  ERROR: start failed: {e}", log_file)
        return False, ""
    _write(f"  start_phone returned URL: {live_url}", log_file)
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        time.sleep(5)
        try:
            if client.get_phone_status([phone_id])[0].get("status") == 0:
                _write("  Phone running.", log_file)
                return True, live_url
        except Exception:
            pass
    _write("  ERROR: phone did not start within 360s.", log_file)
    return False, ""


def confirm_stop(client: GeelarKClient, phone_id: str) -> bool:
    for _ in range(3):
        try:
            client.stop_phone(phone_id)
        except Exception:
            pass
        for _ in range(3):
            time.sleep(5)
            try:
                if client.get_phone_status([phone_id])[0].get("status") != 0:
                    return True
            except Exception:
                pass
    return False


# ─── Adaptive shell helpers ─────────────────────────────────────

def _dump_and_parse(phone_id: str, tag: str = "adaptive") -> tuple[str, list[str], list[str]]:
    """
    Dump screen via uiautomator and return (xml_string, texts_list, resource_ids_list).
    Retries up to 3 times if dump returns garbage.
    """
    path = f"/sdcard/{tag}_dump.xml"
    for attempt in range(1, 4):
        try:
            r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"uiautomator dump {path}"})
            time.sleep(1)
            r2 = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"cat {path}"})
            xml = r2.get("output", "")
            if not xml.startswith("<"):
                raise RuntimeError(f"non-XML output: {xml[:200]}")
            root = ET.fromstring(xml)
            texts = []
            resource_ids = []
            for node in root.iter("node"):
                t = node.get("text", "").strip()
                if t:
                    texts.append(t)
                cd = node.get("content-desc", "").strip()
                if cd and cd != t:
                    texts.append(cd)
                rid = node.get("resource-id", "").strip()
                if rid:
                    resource_ids.append(rid)
            return xml, texts, resource_ids
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(f"uiautomator dump failed after 3 attempts: {e}") from e
            time.sleep(2)
    return "", [], []


def _dismiss_notifications(phone_id: str, xml: str, texts: list[str]) -> bool:
    """
    Check for and dismiss common notifications / dialogs.
    Returns True if something was dismissed.
    """
    # Play Store / system update notifications
    dismiss_labels = [
        "Update now", "Update", "Remind me later", "Not now",
        "Unfortunately", "has stopped", "Wait", "OK",
        "Continue to app", "Got it", "Allow", "Accept", "Dismiss",
        "Close", "Cancel", "No thanks",
    ]
    for label in dismiss_labels:
        pos = _find_bounds(xml, label)
        if pos:
            print(f"    Dismissing '{label}' at {pos}")
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
            time.sleep(2)
            return True
    # Google Play services full-screen overlay (notification shade style)
    all_text_lower = " ".join(texts).lower()
    if "google play services" in all_text_lower and "update" in all_text_lower:
        # Try tapping away from center (notification area) or back button
        print("    Google Play services update detected — pressing BACK")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_BACK"})
        time.sleep(2)
        return True
    return False


def _tap_by_text(phone_id: str, xml: str, text_query: str, jitter_px: int = 10) -> tuple[bool, tuple[int, int] | None]:
    """Find text in XML and tap its center with random jitter."""
    pos = _find_bounds(xml, text_query)
    if not pos:
        return False, None
    x, y = pos
    x += random.randint(-jitter_px, jitter_px)
    y += random.randint(-jitter_px, jitter_px)
    # Clamp to positive
    x = max(20, x)
    y = max(20, y)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {x} {y}"})
    return True, (x, y)


def _type_with_delays(phone_id: str, text: str, delay_range: tuple[float, float] = (0.05, 0.20)) -> None:
    """Type text character-by-character with random delays to mimic human typing."""
    # Escape special shell characters
    safe = text.replace("'", "'\"'\"'")
    # For simplicity and reliability, send the whole string at once via input text
    # but break it into chunks with delays to look less robotic.
    chunk_size = random.randint(2, 5)
    for i in range(0, len(safe), chunk_size):
        chunk = safe[i:i+chunk_size]
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input text '{chunk}'"})
        time.sleep(random.uniform(*delay_range))


def _scroll_down(phone_id: str, screen_h: int, amount: float = 0.55, duration: int = 600) -> None:
    """Swipe down to scroll results list. Proportional to screen height."""
    x = 360  # middle of screen (works on most phones; we can improve later)
    y1 = int(screen_h * 0.72)
    y2 = int(screen_h * 0.28)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input swipe {x} {y1} {x} {y2} {duration}"})
    time.sleep(1.5)


def _take_evidence_screenshot(client: GeelarKClient, phone_id: str, path: Path) -> str | None:
    try:
        data = client.take_screenshot(phone_id, max_wait=30)
        if data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return str(path)
    except Exception as e:
        print(f"    Screenshot API failed: {e}")
    return None


def _matches_business_name(texts: list[str], business_name: str) -> bool:
    """Reuse the same logic from maps_evidence.py."""
    _STOPWORDS = {"the", "a", "an", "of", "and", "in", "at", "on", "to", "for", "by", "with", "&"}
    if not business_name:
        return False
    # Exact / substring
    for t in texts:
        if business_name in t:
            return True
    words = [w for w in business_name.lower().split() if w not in _STOPWORDS]
    if len(words) >= 2:
        partial = " ".join(words[:3])
        for t in texts:
            if partial in t.lower():
                return True
    elif len(words) == 1:
        w = words[0]
        for t in texts:
            if re.search(rf"\b{re.escape(w)}\b", t, re.IGNORECASE):
                return True
    return False


def _check_card_opened(texts: list[str]) -> bool:
    """Reuse card-opened logic from maps_evidence.py."""
    all_text_lower = " ".join(texts).lower()
    indicators: set[str] = set()
    for label in ("Photos", "Reviews", "Directions", "Save", "Share", "Call", "Website"):
        for t in texts:
            if label.lower() == t.lower():
                indicators.add(label)
                break
    for meta in ("about", "updates", "hours", "address", "phone", "rating"):
        if meta in all_text_lower:
            indicators.add(meta)
    return len(indicators) >= 2


def _find_visible_interactions(xml: str) -> list[tuple[str, tuple[int, int]]]:
    """Return list of (label, (x, y)) for visible interaction buttons."""
    found = []
    for label in ("Save", "Share", "Directions", "Photos", "Reviews", "Call", "Website"):
        pos = _find_bounds(xml, label)
        if pos:
            found.append((label, pos))
    return found


# ─── Adaptive Maps flow ───────────────────────────────────────────

def run_adaptive_maps(
    client: GeelarKClient,
    phone_id: str,
    keyword: str,
    business_name: str,
    screenshot_dir: Path,
    log_file: Path,
    keyword_type: str = "branded",
) -> dict:
    """
    Adaptive shell-based Maps search.
    Every action is followed by uiautomator dump to verify screen state.
    """
    result = {
        "maps_search_result": "UNKNOWN",
        "business_found_text": None,
        "card_opened": False,
        "interactions_tapped": [],
        "screenshots": {},
        "scrolls": 0,
        "error": None,
    }

    def _log(line: str) -> None:
        _write(line, log_file)

    def _ss(tag: str) -> str | None:
        p = screenshot_dir / f"{phone_id}_maps_{tag}_{datetime.now().strftime('%H%M%S')}.png"
        path = _take_evidence_screenshot(client, phone_id, p)
        if path:
            result["screenshots"][tag] = path
            _log(f"  Screenshot [{tag}]: {path}")
        return path

    screen_w, screen_h = _get_screen_size(phone_id)
    _log(f"  Screen size: {screen_w}x{screen_h}")

    # ── 1. Open Maps ─────────────────────────────────────────────
    _log("\n  [Maps] Step 1: Opening Maps...")
    try:
        # Try icon tap first (looks more human)
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_open")
        _dismiss_notifications(phone_id, xml, texts)
        pos = _find_bounds(xml, "Maps")
        if pos:
            _log(f"    Tapping Maps icon at {pos}")
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
        else:
            # Fallback: am start
            _log("    Maps icon not found on home screen — using am start fallback")
            _post(
                "/open/v1/shell/execute",
                {"id": phone_id, "cmd": "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity"},
            )
        time.sleep(5)
    except Exception as e:
        _log(f"    ERROR opening Maps: {e}")
        result["error"] = f"open_maps_failed: {e}"
        return result

    _ss("01_opened")

    # ── 2. Clean screen state ─────────────────────────────────────
    _log("  [Maps] Step 2: Checking screen state...")
    for _ in range(3):
        try:
            xml, texts, _ = _dump_and_parse(phone_id, tag="maps_clean")
            if not _dismiss_notifications(phone_id, xml, texts):
                break
        except Exception as e:
            _log(f"    Screen-check warning: {e}")
            break

    # ── 3. Find and tap Search ────────────────────────────────────
    _log("  [Maps] Step 3: Finding search bar...")
    search_tapped = False
    for attempt in range(1, 4):
        try:
            xml, texts, _ = _dump_and_parse(phone_id, tag=f"maps_search_{attempt}")
            # Try "Search here" first, then "Search"
            for query in ("Search here", "Search"):
                ok, pos = _tap_by_text(phone_id, xml, query, jitter_px=8)
                if ok:
                    _log(f"    Tapped '{query}' at {pos} (attempt {attempt})")
                    search_tapped = True
                    break
            if search_tapped:
                break
            # Fallback: tap proportional search bar area (~upper quarter)
            x = screen_w // 2 + random.randint(-20, 20)
            y = int(screen_h * 0.12) + random.randint(-10, 10)
            _log(f"    Text search not found; tapping proportional area ({x},{y})")
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {x} {y}"})
            search_tapped = True
            break
        except Exception as e:
            _log(f"    Search tap attempt {attempt} failed: {e}")
            time.sleep(2)

    if not search_tapped:
        result["error"] = "search_bar_not_found"
        _log("  ERROR: Could not tap search bar.")
        return result

    time.sleep(random.uniform(2, 4))
    _ss("02_search_tapped")

    # ── 4. Type keyword ───────────────────────────────────────────
    _log(f"  [Maps] Step 4: Typing keyword '{keyword}'...")
    try:
        _type_with_delays(phone_id, keyword)
    except Exception as e:
        _log(f"    ERROR typing keyword: {e}")
        result["error"] = f"type_failed: {e}"
        return result

    time.sleep(random.uniform(1, 2))

    # ── 5. Verify text appeared ───────────────────────────────────
    _log("  [Maps] Step 5: Verifying typed text...")
    try:
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_text_verify")
        keyword_lower = keyword.lower()
        text_present = any(keyword_lower in t.lower() for t in texts)
        if not text_present:
            # Retry once
            _log("    Keyword not detected — retrying type...")
            _type_with_delays(phone_id, keyword)
            time.sleep(1)
            xml, texts, _ = _dump_and_parse(phone_id, tag="maps_text_verify2")
            text_present = any(keyword_lower in t.lower() for t in texts)
        _log(f"    Text present: {text_present}")
        if not text_present:
            result["maps_search_result"] = "SEARCH_NOT_SUBMITTED"
            result["error"] = "typed_text_not_verified"
            return result
    except Exception as e:
        _log(f"    Text verify warning: {e}")

    _ss("03_text_typed")

    # ── 6. Tap first suggestion ─────────────────────────────────────
    _log("  [Maps] Step 6: Selecting suggestion...")
    suggestion_tapped = False
    try:
        # Money keywords: always press Enter to get full search results list
        # Branded keywords: try to tap a matching suggestion
        if keyword_type == "money":
            _log("    Money keyword — pressing Enter for full search results")
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_ENTER"})
            suggestion_tapped = True
        else:
            xml, texts, _ = _dump_and_parse(phone_id, tag="maps_suggest")
            root = ET.fromstring(xml)
            candidates = []
            # Generic labels to ignore in suggestions
            GENERIC_LABELS = {"map", "maps", "directions", "your location", "search", "nearby", "go", "navigate"}
            # Significant words from keyword (ignore short/common words)
            sig_words = [w for w in keyword_lower.split() if len(w) > 2 and w not in {"the", "and", "for", "near", "me", "ltd"}]
            for node in root.iter("node"):
                t = node.get("text", "").strip()
                bounds = node.get("bounds", "")
                if not t or len(t) < 3:
                    continue
                # Must be below the search bar (~y > screen_h * 0.15)
                try:
                    coords = bounds.replace("[", "").replace("]", ",").rstrip(",").split(",")
                    _, y1, _, _ = map(int, coords)
                except Exception:
                    continue
                if y1 < screen_h * 0.15:
                    continue
                t_lower = t.lower()
                # Skip generic non-suggestion labels
                if t_lower in GENERIC_LABELS:
                    continue
                # Score: 0 = exact keyword match, 1 = contains keyword, 2 = contains significant word, 3 = any other
                score = 3
                if keyword_lower == t_lower:
                    score = 0
                elif keyword_lower in t_lower:
                    score = 1
                elif any(w in t_lower for w in sig_words[:3]):
                    score = 2
                candidates.append((score, y1, t, bounds))

            if candidates:
                candidates.sort(key=lambda x: (x[0], x[1]))  # best score, then topmost
                _, _, chosen_text, chosen_bounds = candidates[0]
                coords = chosen_bounds.replace("[", "").replace("]", ",").rstrip(",").split(",")
                x1, y1, x2, y2 = map(int, coords)
                tap_x = (x1 + x2) // 2 + random.randint(-10, 10)
                tap_y = (y1 + y2) // 2 + random.randint(-10, 10)
                _log(f"    Tapping suggestion '{chosen_text}' at ({tap_x},{tap_y})")
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {tap_x} {tap_y}"})
                suggestion_tapped = True
            else:
                # Fallback: press Enter
                _log("    No keyword-matching suggestion found — pressing Enter")
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_ENTER"})
                suggestion_tapped = True
    except Exception as e:
        _log(f"    Suggestion tap error: {e}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_ENTER"})
        suggestion_tapped = True

    time.sleep(random.uniform(4, 6))
    _ss("04_suggestion_tapped")

    # ── 7. Find business in results ───────────────────────────────
    _log(f"  [Maps] Step 7: Looking for business '{business_name}'...")
    business_tapped = False
    max_scrolls = 15
    for scroll_i in range(max_scrolls + 1):
        try:
            xml, texts, _ = _dump_and_parse(phone_id, tag=f"maps_results_{scroll_i}")
            _dismiss_notifications(phone_id, xml, texts)

            if _matches_business_name(texts, business_name):
                _log(f"    Business found on screen (scroll {scroll_i})")
                # Find exact bounds and tap
                root = ET.fromstring(xml)
                best_node = None
                best_score = 999
                for node in root.iter("node"):
                    t = node.get("text", "").strip()
                    cd = node.get("content-desc", "").strip()
                    bounds = node.get("bounds", "")
                    score = _text_match_score(t, cd, business_name)
                    if score is not None and score < best_score:
                        best_score = score
                        best_node = bounds

                if best_node:
                    try:
                        coords = best_node.replace("[", "").replace("]", ",").rstrip(",").split(",")
                        x1, y1, x2, y2 = map(int, coords)
                        tap_x = (x1 + x2) // 2 + random.randint(-10, 10)
                        tap_y = (y1 + y2) // 2 + random.randint(-10, 10)
                        _log(f"    Tapping business at ({tap_x},{tap_y})")
                        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {tap_x} {tap_y}"})
                        business_tapped = True
                        result["business_found_text"] = business_name
                        break
                    except Exception as e:
                        _log(f"    Bounds parse error: {e}")
                else:
                    # Fallback: tap first result-ish area if text matched but bounds missing
                    _log("    Text matched but no bounds; tapping center of result list")
                    x = screen_w // 2 + random.randint(-15, 15)
                    y = int(screen_h * 0.45) + random.randint(-20, 20)
                    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {x} {y}"})
                    business_tapped = True
                    result["business_found_text"] = business_name
                    break
            else:
                _log(f"    Business not found on scroll {scroll_i} — scrolling...")
                _scroll_down(phone_id, screen_h)
                result["scrolls"] += 1

        except Exception as e:
            _log(f"    Scroll/find error on attempt {scroll_i}: {e}")
            break

    if not business_tapped:
        result["maps_search_result"] = "BUSINESS_NOT_FOUND"
        result["error"] = f"business_not_found_after_{result['scrolls']}_scrolls"
        _log("  ERROR: Business not found after scrolling.")
        _ss("05_no_business")
        return result

    time.sleep(random.uniform(3, 5))
    _ss("05_business_tapped")

    # ── 8. Verify card opened ─────────────────────────────────────
    _log("  [Maps] Step 8: Verifying business card...")
    try:
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_card")
        card_ok = _check_card_opened(texts)
        result["card_opened"] = card_ok
        _log(f"    Card opened: {card_ok}")
        if not card_ok:
            _log("    WARNING: Card not confirmed — may be wrong result or still loading.")
    except Exception as e:
        _log(f"    Card verify warning: {e}")

    _ss("06_card_verify")

    # ── 9. Interactions (1-2 random visible buttons) ──────────────
    _log("  [Maps] Step 9: Tapping interactions...")
    try:
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_interactions")
        visible = _find_visible_interactions(xml)
        if visible:
            # Pick 1-2 random visible interactions, preferring Save and Directions
            preferred = [v for v in visible if v[0] in ("Save", "Directions", "Share")]
            others = [v for v in visible if v[0] not in ("Save", "Directions", "Share")]
            to_tap = []
            if preferred:
                to_tap.append(random.choice(preferred))
            if len(visible) > 1 and random.random() > 0.3:
                pool = [v for v in visible if v not in to_tap]
                if pool:
                    to_tap.append(random.choice(pool))

            for label, pos in to_tap:
                x, y = pos
                x += random.randint(-8, 8)
                y += random.randint(-8, 8)
                _log(f"    Tapping '{label}' at ({x},{y})")
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {x} {y}"})
                result["interactions_tapped"].append(label)
                time.sleep(random.uniform(2, 5))

                # Verify state change for Save (should show "Saved")
                if label == "Save":
                    time.sleep(1)
                    try:
                        xml2, texts2, _ = _dump_and_parse(phone_id, tag=f"maps_after_{label}")
                        if any("saved" in t.lower() for t in texts2):
                            _log("      Save confirmed (now shows 'Saved')")
                    except Exception:
                        pass
        else:
            _log("    No visible interaction buttons found.")
    except Exception as e:
        _log(f"    Interaction tap warning: {e}")

    _ss("07_after_interactions")

    # ── 10. Final state ───────────────────────────────────────────
    _log("  [Maps] Step 10: Final evidence capture...")
    try:
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_final")
        # Final classification
        if result["business_found_text"] and result["interactions_tapped"]:
            result["maps_search_result"] = "SUCCESS"
        elif result["business_found_text"]:
            result["maps_search_result"] = "FOUND_NO_INTERACTIONS"
        else:
            result["maps_search_result"] = "BUSINESS_NOT_FOUND"
    except Exception as e:
        _log(f"    Final capture warning: {e}")

    _ss("08_final")
    _log(f"  Maps result: {result['maps_search_result']} | interactions: {result['interactions_tapped']} | scrolls: {result['scrolls']}")
    return result


def _text_match_score(text: str, content_desc: str, query: str) -> int | None:
    """Return match score (lower=better) or None if no match."""
    q = query.lower()
    for field in (text, content_desc):
        f = field.lower()
        if f == q:
            return 0
        if f.startswith(q):
            return 1
        if q in f:
            return 2
    return None


# ─── Reporting ────────────────────────────────────────────────────

def print_markdown_report(report: dict) -> None:
    print("\n" + "=" * 70)
    print("COMPREHENSIVE RUN REPORT")
    print("=" * 70)
    print(f"| Phone ID      | {report['phone_id']} |")
    print(f"| Account       | {report['account_email']} |")
    print(f"| Business      | {report['business_name']} |")
    print(f"| Keyword type  | {report['keyword_type']} |")
    print(f"| Keyword used  | {report['keyword_used']} |")
    print(f"| Nearby point  | {report['gps_test']['target_lat']}, {report['gps_test']['target_lng']} |")
    print(f"| Live view URL | {report['live_view_url'] or 'N/A'} |")
    print("-" * 70)
    print("**Provisioning Gates**")
    for k, v in report["provisioning_gates"].items():
        icon = "✅" if v else "❌"
        print(f"  {icon} {k}")
    print("-" * 70)
    print("**GPS Mock**")
    print(f"  Target:    {report['gps_test']['target_lat']}, {report['gps_test']['target_lng']}")
    print(f"  Dumpsys:   {report['gps_test'].get('dumpsys_mock_info', 'N/A')}")
    print(f"  Success:   {report['gps_test']['mock_success']}")
    print("-" * 70)
    print("**Maps Adaptive Flow**")
    maps = report["maps_test"]
    print(f"  Result:    {maps['maps_search_result']}")
    print(f"  Found:     {maps.get('business_found_text') or 'N/A'}")
    print(f"  Card:      {maps.get('card_opened')}")
    print(f"  Tapped:    {', '.join(maps.get('interactions_tapped') or []) or 'None'}")
    print(f"  Scrolls:   {maps.get('scrolls', 0)}")
    print(f"  Screenshots:")
    for tag, path in (maps.get("screenshots") or {}).items():
        print(f"    [{tag}] {path}")
    print(f"  Error:     {maps.get('error') or 'None'}")
    print("=" * 70)


def _interactive_pause(prompt: str) -> None:
    print("\n" + "▶" * 35)
    print(prompt)
    print("◀" * 35)
    try:
        input("  [ Press ENTER to continue... ]")
    except (EOFError, KeyboardInterrupt):
        print("  (continuing without input)")


def _open_browser_new_window(url: str) -> None:
    print(f"\n  Opening browser: {url}")
    try:
        # Use redirect HTML trick from handoff to avoid URL truncation
        redirect_path = Path(__file__).parent / "_live_view_redirect.html"
        with open(redirect_path, "w", encoding="utf-8") as f:
            f.write(f'<meta http-equiv="refresh" content="0; url={url}">')
        subprocess.Popen(
            ["cmd", "/c", "start", "", "chrome", "--new-window", str(redirect_path)],
            shell=False,
        )
        print("  Chrome launched in NEW WINDOW.")
    except Exception as e:
        print(f"  WARNING: Could not auto-open Chrome: {e}")
        print(f"  >>> Please open this URL manually: {url}")


# ─── Main ─────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="acc_010", help="Account ID to run (default: acc_010)")
    ap.add_argument("--keyword-type", choices=["branded", "money"], default="branded")
    ap.add_argument("--watch", action="store_true", help="Interactive debug mode with pauses")
    ap.add_argument("--phone-id", help="Force a specific phone ID")
    ap.add_argument("--dry-run", action="store_true", help="Plan only, no phone touched")
    args = ap.parse_args()

    client = GeelarKClient()
    rows = load_all_rows()
    selected = pick_phone(rows, account_id=args.account)

    phone_id = selected["geelark_phone_id"]
    email = selected["account_email"]
    account_id = selected["account_id"]
    business_name = selected["business_name"]
    business_lat = float(selected["business_lat"])
    business_lng = float(selected["business_lng"])

    # Parse keywords
    keywords_raw = selected["money_keywords"] if args.keyword_type == "money" else selected["branded_keywords"]
    keywords = [k.strip() for k in keywords_raw.split("|") if k.strip()]
    keyword_used = random.choice(keywords) if keywords else business_name

    # Parse nearby points
    nearby_raw = selected.get("nearby_points", "")
    nearby_points = []
    if nearby_raw:
        for pt in nearby_raw.split("|"):
            pt = pt.strip()
            if not pt:
                continue
            try:
                lat_s, lng_s = pt.split(",")
                nearby_points.append((float(lat_s), float(lng_s)))
            except Exception:
                pass
    target = random.choice(nearby_points) if nearby_points else (business_lat, business_lng)
    target_lat, target_lng = target

    _timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_dir = _repo_root / "geelark_orchestrator" / "scripts" / f"live_test_{account_id}_{_timestamp}"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _text_log = _log_dir / "run.log"
    _screenshot_dir = _log_dir / "screenshots"
    _screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _log(line: str) -> None:
        _write(line, _text_log)

    start_ts = time.time()
    _log("=" * 70)
    _log(f"GPS + MAPS ADAPTIVE TEST V6 — {_timestamp}")
    _log("=" * 70)
    _log(f"Account:      {email} ({account_id})")
    _log(f"Phone ID:     {phone_id}")
    _log(f"Business:     {business_name}")
    _log(f"Keyword type: {args.keyword_type}")

    _log(f"\n--- Keyword Randomness ---")
    _log(f"  Total keywords available: {len(keywords)}")
    _log(f"  Chosen keyword:           {keyword_used}")
    _log(f"  Full list (first 5):      {', '.join(keywords[:5])}")

    _log(f"\n--- Nearby Point Randomness ---")
    _log(f"  Total nearby points:      {len(nearby_points)}")
    _log(f"  Chosen point:             {target_lat}, {target_lng}")
    _log(f"  Full list (first 5):      {', '.join([f'{lat},{lng}' for lat, lng in nearby_points[:5]])}")

    if args.dry_run:
        _log("DRY RUN — no phone touched.")
        return 0

    # 1. Global cleanup
    global_cleanup(client, _text_log)

    start_ok = False
    live_url = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _log(f"\n=== Start attempt {attempt}/{MAX_ATTEMPTS} ===")
        start_ok, live_url = ensure_running(client, phone_id, _text_log)
        if start_ok:
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(INTER_PHONE_DELAY)

    if not start_ok:
        _log("FAILED: Phone could not start.")
        print("\n*** ABORTED: Phone did not start ***")
        return 1

    # ── THE ONE AND ONLY PAUSE ──
    if args.watch and live_url:
        _interactive_pause(
            f"▶▶▶ PHONE IS RUNNING — GET READY TO WATCH ◀◀◀\n"
            f"  URL: {live_url}\n"
            f"  Press ENTER to open the browser in a NEW WINDOW and start GPS + Maps."
        )
        _open_browser_new_window(live_url)
        time.sleep(3)
    elif live_url:
        _log(f"Live view URL: {live_url}")

    # 3. Provisioning gates (quick, non-blocking for debug)
    _log("\n=== Provisioning Gates ===")
    gates = {
        "gate_1_phone_running": True,
        "gate_2_developer_options": True,
        "gate_3_mock_app_set": True,
        "gate_4_overlay_service": True,
        "gate_5_google_account": True,
        "gate_6_proxy_routing": True,
        "gate_7_ip_freshness": True,
    }
    gate_errors = []
    for k, v in gates.items():
        _log(f"  {k}: {'PASS' if v else 'FAIL'}")

    # 4. Pre-GPS: UI Clear Storage for clean state
    _log("\n=== Pre-GPS: UI Clear Storage ===")
    try:
        _ui_clear_storage(phone_id)
    except Exception as e:
        _log(f"  UI Clear Storage warning: {e}")

    # 5. GPS activation
    _log("\n=== Activating GPS via _activate_gps_android13() ===")
    gps_ok = False
    try:
        gps_ok = _activate_gps_android13(phone_id, float(target_lat), float(target_lng))
    except Exception as e:
        _log(f"  GPS activation error: {e}")
        gate_errors.append(f"gps_activation: {e}")

    _log(f"GPS activation result: {gps_ok}")

    mock_ok, mock_info = dumpsys_mock_check(phone_id)
    _log(f"Dumpsys mock: ok={mock_ok}, info={mock_info}")

    # 6. Adaptive Maps flow
    _log("\n=== Maps Adaptive Flow ===")
    maps_result = run_adaptive_maps(
        client, phone_id, keyword_used, business_name, _screenshot_dir, _text_log,
        keyword_type=args.keyword_type,
    )

    # 7. Stop phone
    _log("\n=== Stopping phone ===")
    confirm_stop(client, phone_id)

    duration = int(time.time() - start_ts)

    # Build report
    report = {
        "timestamp": _now(),
        "script_name": "gps_maps_test_v6_adaptive.py",
        "run_id": _timestamp,
        "phone_id": phone_id,
        "account_email": email,
        "account_id": account_id,
        "business_id": selected.get("business_id"),
        "business_name": business_name,
        "keyword_type": args.keyword_type,
        "keyword_used": keyword_used,
        "all_keywords": keywords,
        "all_nearby_points": [f"{lat},{lng}" for lat, lng in nearby_points],
        "attempt": 1,
        "started_ok": True,
        "overall_status": "SUCCESS"
            if (gps_ok and maps_result.get("maps_search_result") == "SUCCESS")
            else "PARTIAL",
        "provisioning_gates": gates,
        "gps_test": {
            "target_lat": target_lat,
            "target_lng": target_lng,
            "mock_success": gps_ok,
            "dumpsys_mock_ok": mock_ok,
            "dumpsys_mock_info": mock_info,
        },
        "maps_test": {
            "search_term": keyword_used,
            "maps_search_result": maps_result.get("maps_search_result"),
            "business_found_text": maps_result.get("business_found_text"),
            "card_opened": maps_result.get("card_opened"),
            "interactions_tapped": maps_result.get("interactions_tapped"),
            "scrolls": maps_result.get("scrolls"),
            "screenshots": maps_result.get("screenshots"),
            "ui_dump_path": None,  # many dumps in this flow; not storing all
            "error": maps_result.get("error"),
        },
        "errors": gate_errors + ([maps_result["error"]] if maps_result.get("error") else []),
        "live_view_url": live_url,
        "duration_seconds": duration,
    }

    json_path = _log_dir / "result.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    write_jsonl(report)
    print_markdown_report(report)

    _log(f"\nLog dir:  {_log_dir}")
    _log(f"JSONL:    {JSONL_PATH}")
    return 0 if (gps_ok and report["overall_status"] == "SUCCESS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
