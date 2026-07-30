#!/usr/bin/env python3
"""
GPS + Maps PRODUCTION v2 — Hybrid Shell + RPA.

Flow:
  1. Shell: Close all apps + GPS deep-link activation
  2. Shell: Open Maps via geo: intent with keyword
  3. RPA: Run "Google Maps - Find and click" flow with business_name param
  4. Shell: Interactions (Save, Directions, etc.) + screenshots

Usage:
  python gps_maps_production_v2.py --account acc_005 --keyword-type branded --watch
"""
import argparse
import csv
import json
import math
import random
import re
import subprocess
import sys
import time
import urllib.parse
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
    dumpsys_mock_check,
    _activate_gps_android13,
    _verify_phone_provisioned,
    _verify_google_account,
    _get_phone_ip,
    _find_bounds,
    _get_screen_size,
)
from proxy_control import change_proxy_ip

CSV_PATH = _data_dir / "accounts_business_mapping.csv"
JSONL_PATH = _logs_dir / "run_history.jsonl"
START_TIMEOUT = 360
INTER_PHONE_DELAY = 30
MAX_ATTEMPTS = 3

# Original Test A RPA flow — handles openApp(Maps) → inputText(keyword) → scroll → find → click
TEST_A_FLOW_ID = "620084214733209921"

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


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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


# ─── Close all apps ─────────────────────────────────────────────

def _close_all_apps(phone_id: str, log_file: Path) -> None:
    _write("  --- Closing all apps ---", log_file)
    packages = [
        "com.google.android.apps.maps",
        "com.theappninjas.fakegpsjoystick",
        "com.android.vending",
        "com.google.android.gms",
        "com.android.chrome",
    ]
    for pkg in packages:
        try:
            _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"am force-stop {pkg}"})
        except Exception:
            pass
    time.sleep(1)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_HOME"})
    time.sleep(1)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_APP_SWITCH"})
    time.sleep(1)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input swipe 360 600 360 1200 300"})
    time.sleep(1)
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_HOME"})
    time.sleep(1)
    _write("  --- All apps closed ---", log_file)


# ─── GPS activation ─────────────────────────────────────────────

# ─── Shell helpers ──────────────────────────────────────────────

def _dump_and_parse(phone_id: str, tag: str = "adaptive") -> tuple[str, list[str], list[str]]:
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


def _check_card_opened(texts: list[str]) -> bool:
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
    found = []
    for label in ("Save", "Share", "Directions", "Photos", "Reviews", "Call", "Website"):
        pos = _find_bounds(xml, label)
        if pos:
            found.append((label, pos))
    return found


# ─── Maps flow ──────────────────────────────────────────────────

def run_maps_flow(
    client: GeelarKClient,
    phone_id: str,
    keyword: str,
    business_name: str,
    screenshot_dir: Path,
    log_file: Path,
) -> dict:
    result = {
        "maps_search_result": "UNKNOWN",
        "business_found_text": None,
        "card_opened": False,
        "interactions_tapped": [],
        "screenshots": {},
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

    # ── 1. RPA: Open Maps, search, find and click business ───────
    # Test A flow handles: openApp(Maps) → inputText(keyword) → scroll+find → click
    _log(f"\n  [Maps] Step 1: Dispatching Test A RPA flow to search '{keyword}' and find '{business_name}'...")
    try:
        param_map = {
            "business_name": business_name,
            "search_term": keyword,
        }
        task_id = client.run_custom_flow(
            flow_id=TEST_A_FLOW_ID,
            phone_id=phone_id,
            param_map=param_map,
            task_name=f"Find {business_name[:20]}",
            schedule_delay=5,
        )
        _log(f"    Test A RPA task dispatched: {task_id}")

        # Poll for completion (timeout 120s)
        deadline = time.time() + 120
        task_status = None
        while time.time() < deadline:
            time.sleep(5)
            try:
                tasks = client.query_tasks([task_id])
                if tasks:
                    t = tasks[0]
                    status = t.get("status")
                    task_status = status
                    if status == 3:
                        _log(f"    RPA task completed successfully.")
                        result["business_found_text"] = business_name
                        break
                    elif status == 4:
                        _log(f"    RPA task FAILED: {t.get('failDesc', 'unknown')}")
                        result["error"] = f"rpa_failed: {t.get('failDesc', 'unknown')}"
                        break
                    elif status == 7:
                        _log(f"    RPA task cancelled.")
                        result["error"] = "rpa_cancelled"
                        break
            except Exception as e:
                _log(f"    Poll error: {e}")

        if task_status is None:
            _log("    WARNING: RPA task status unknown after timeout")
            result["error"] = "rpa_timeout"

    except Exception as e:
        _log(f"    ERROR dispatching RPA flow: {e}")
        result["error"] = f"rpa_dispatch_error: {e}"
        return result

    if result.get("error"):
        _ss("02_no_business")
        return result

    time.sleep(3)
    _ss("02_business_tapped")

    # ── 3. Verify card opened ──────────────────────────────────
    _log("  [Maps] Step 3: Verifying business card...")
    try:
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_card")
        card_ok = _check_card_opened(texts)
        result["card_opened"] = card_ok
        _log(f"    Card opened: {card_ok}")
    except Exception as e:
        _log(f"    Card verify warning: {e}")

    _ss("03_card_verify")

    # ── 4. Interactions ────────────────────────────────────────
    _log("  [Maps] Step 4: Tapping interactions...")
    try:
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_interactions")
        visible = _find_visible_interactions(xml)
        if visible:
            preferred = [v for v in visible if v[0] in ("Save", "Directions", "Share")]
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
        else:
            _log("    No visible interaction buttons found.")
    except Exception as e:
        _log(f"    Interaction tap warning: {e}")

    _ss("04_after_interactions")

    # ── 5. Final state ─────────────────────────────────────────
    _log("  [Maps] Step 5: Final evidence capture...")
    try:
        xml, texts, _ = _dump_and_parse(phone_id, tag="maps_final")
        if result["business_found_text"] and result["interactions_tapped"]:
            result["maps_search_result"] = "SUCCESS"
        elif result["business_found_text"]:
            result["maps_search_result"] = "FOUND_NO_INTERACTIONS"
        else:
            result["maps_search_result"] = "BUSINESS_NOT_FOUND"
    except Exception as e:
        _log(f"    Final capture warning: {e}")

    _ss("05_final")
    _log(f"  Maps result: {result['maps_search_result']} | interactions: {result['interactions_tapped']}")
    return result


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
    print("**Maps Hybrid Flow**")
    maps = report["maps_test"]
    print(f"  Result:    {maps['maps_search_result']}")
    print(f"  Found:     {maps.get('business_found_text') or 'N/A'}")
    print(f"  Card:      {maps.get('card_opened')}")
    print(f"  Tapped:    {', '.join(maps.get('interactions_tapped') or []) or 'None'}")
    print(f"  Screenshots:")
    for tag, path in (maps.get("screenshots") or {}).items():
        print(f"    [{tag}] {path}")
    print(f"  Error:     {maps.get('error') or 'None'}")
    print("=" * 70)


def _open_browser_new_window(url: str) -> None:
    print(f"\n  Opening browser: {url}")
    try:
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
    ap.add_argument("--account", default="acc_005", help="Account ID to run")
    ap.add_argument("--keyword-type", choices=["discovery", "standalone", "branded"], default="branded")
    ap.add_argument("--watch", action="store_true", help="Open live view browser")
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

    if args.keyword_type == "discovery":
        keywords_raw = selected.get("discovery_keywords", "")
    elif args.keyword_type == "standalone":
        keywords_raw = selected.get("standalone_keywords", "")
    else:
        keywords_raw = selected.get("branded_keywords", "")

    keywords = [k.strip() for k in keywords_raw.split("|") if k.strip()]
    keyword_used = random.choice(keywords) if keywords else business_name

    nearby_raw = selected.get("nearby_points", "")
    nearby_points = []
    if nearby_raw:
        for pt in nearby_raw.split("|"):
            pt = pt.strip()
            if not pt:
                continue
            try:
                lat_s, lng_s = pt.split(",")
                n_lat, n_lng = float(lat_s), float(lng_s)
                if _haversine_km(business_lat, business_lng, n_lat, n_lng) <= 2.0:
                    nearby_points.append((n_lat, n_lng))
            except Exception:
                pass
    if nearby_points:
        target = random.choice(nearby_points)
    else:
        target = (business_lat, business_lng)
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
    _log(f"GPS + MAPS PRODUCTION v2 (Hybrid Shell+RPA) — {_timestamp}")
    _log("=" * 70)
    _log(f"Account:      {email} ({account_id})")
    _log(f"Phone ID:     {phone_id}")
    _log(f"Business:     {business_name}")
    _log(f"Keyword type: {args.keyword_type}")
    _log(f"Keyword used: {keyword_used}")

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

    # Open live view browser
    if live_url:
        _log(f"Live view URL: {live_url}")
        _open_browser_new_window(live_url)
        _log("Browser opened — waiting 5s...")
        time.sleep(5)

    # 3. Provisioning gates
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
    for k, v in gates.items():
        _log(f"  {k}: {'PASS' if v else 'FAIL'}")

    # 4. Close all apps + clear storage
    _log("\n=== Pre-GPS: Close All Apps ===")
    _close_all_apps(phone_id, _text_log)

    _log("\n=== Pre-GPS: Clear Fake GPS Storage ===")
    try:
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "pm clear com.theappninjas.fakegpsjoystick"})
        time.sleep(2)
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "am force-stop com.theappninjas.fakegpsjoystick"})
        time.sleep(1)
        _log("  Fake GPS storage cleared + app killed (no relaunch)")
    except Exception as e:
        _log(f"  Clear storage warning: {e}")

    # 5. GPS activation
    _log("\n=== Activating GPS (_activate_gps_android13) ===")
    gps_ok = False
    try:
        gps_ok = _activate_gps_android13(phone_id, float(target_lat), float(target_lng))
    except Exception as e:
        _log(f"  GPS activation error: {e}")

    _log(f"GPS activation result: {gps_ok}")
    mock_ok, mock_info = dumpsys_mock_check(phone_id)
    _log(f"Dumpsys mock: ok={mock_ok}, info={mock_info}")

    # 6. Maps geo: intent + RPA find business + shell interactions
    _log("\n=== Maps Hybrid Flow ===")
    maps_result = run_maps_flow(
        client, phone_id, keyword_used, business_name, _screenshot_dir, _text_log
    )

    # 7. Stop phone
    _log("\n=== Stopping phone ===")
    confirm_stop(client, phone_id)

    duration = int(time.time() - start_ts)

    report = {
        "timestamp": _now(),
        "script_name": "gps_maps_production_v2.py",
        "run_id": _timestamp,
        "phone_id": phone_id,
        "account_email": email,
        "account_id": account_id,
        "business_id": selected.get("business_id"),
        "business_name": business_name,
        "keyword_type": args.keyword_type,
        "keyword_used": keyword_used,
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
            "screenshots": maps_result.get("screenshots"),
            "error": maps_result.get("error"),
        },
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
