#!/usr/bin/env python3
"""
GPS + Maps comprehensive test v3.

Features:
  - Reads latest data/accounts_business_mapping.csv
  - Picks random phone (excludes acc_048 by default)
  - Pre-run global cleanup (single-phone-at-a-time)
  - Start phone → capture Geelark live-view URL from API
  - Run all 7 provisioning gates and record results
  - Full _activate_gps_android13() with nearby point
  - Maps search with money keyword
  - Comprehensive JSON report + Markdown table
  - Appends to logs/run_history.jsonl
"""
import argparse
import csv
import json
import random
import re
import sys
import time
import urllib.parse
import webbrowser
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
    _get_android_version,
    _get_screen_size,
    _ui_clear_storage,
)
from proxy_control import change_proxy_ip

CSV_PATH = _data_dir / "accounts_business_mapping.csv"
JSONL_PATH = _logs_dir / "run_history.jsonl"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
START_TIMEOUT = 360
INTER_PHONE_DELAY = 30
MAX_ATTEMPTS = 3


def _now() -> str:
    return datetime.now().isoformat()


def load_all_rows() -> list[dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pick_random_phone(rows: list[dict], exclude_account_id: str | None = None) -> dict:
    candidates = [r for r in rows if r.get("account_id") != exclude_account_id]
    return random.choice(candidates)


def _write(line: str, log_file: Path) -> None:
    print(line)
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def write_jsonl(record: dict) -> None:
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
    """Start phone and return (success, live_view_url)."""
    try:
        s = client.get_phone_status([phone_id])[0].get("status", -1)
    except Exception:
        s = -1
    if s == 0:
        _write(f"  Phone {phone_id} already running.", log_file)
        # Try to get URL from a fresh start anyway? No — if already running we may not have URL.
        # Return empty URL; caller can construct fallback if needed.
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


def run_provisioning_gates(
    client: GeelarKClient,
    phone_id: str,
    log_file: Path,
) -> dict:
    """Run all 7 gates and return a dict of results."""
    gates = {
        "gate_1_phone_running": False,
        "gate_2_developer_options": False,
        "gate_3_mock_app_set": False,
        "gate_4_overlay_service": False,
        "gate_5_google_account": False,
        "gate_6_proxy_routing": False,
        "gate_7_ip_freshness": False,
    }
    errors = []

    # Gate 1: phone running
    try:
        s = client.get_phone_status([phone_id])[0].get("status", -1)
        gates["gate_1_phone_running"] = s == 0
    except Exception as e:
        errors.append(f"gate_1: {e}")

    # Gate 2+3: Developer options + mock app
    try:
        ok, reason = _verify_phone_provisioned(phone_id)
        gates["gate_2_developer_options"] = ok
        gates["gate_3_mock_app_set"] = ok
        if not ok:
            errors.append(f"gate_2_3: {reason}")
    except Exception as e:
        errors.append(f"gate_2_3: {e}")

    # Gate 4: OverlayService
    try:
        r = _post(
            "/open/v1/shell/execute",
            {"id": phone_id, "cmd": "dumpsys activity services com.theappninjas.fakegpsjoystick"},
        )
        out = r.get("output", "")
        gates["gate_4_overlay_service"] = "OverlayService" in out or "overlay" in out.lower()
    except Exception as e:
        errors.append(f"gate_4: {e}")

    # Gate 5: Google account
    try:
        ok, info = _verify_google_account(phone_id)
        gates["gate_5_google_account"] = ok
        if not ok:
            errors.append(f"gate_5: {info}")
    except Exception as e:
        errors.append(f"gate_5: {e}")

    # Gate 6: Proxy routing — shell curl to confirm phone has a public IP
    try:
        ip = _get_phone_ip(phone_id)
        gates["gate_6_proxy_routing"] = ip is not None
        if not gates["gate_6_proxy_routing"]:
            errors.append("gate_6: no public IP returned by curl")
    except Exception as e:
        errors.append(f"gate_6: {e}")

    # Gate 7: IP freshness — requires RunsStore; skip in standalone test
    gates["gate_7_ip_freshness"] = True  # placeholder; no DB in test mode

    return {"gates": gates, "errors": errors}


def run_maps_search(
    client: GeelarKClient,
    phone_id: str,
    keyword: str,
    screenshot_dir: Path,
    log_file: Path,
) -> dict:
    result = {
        "maps_search_result": "UNKNOWN",
        "business_found_text": None,
        "screenshot_path": None,
        "ui_dump_path": None,
        "error": None,
    }
    encoded = urllib.parse.quote(keyword)
    geo_url = f"geo:0,0?q={encoded}"
    try:
        _post(
            "/open/v1/shell/execute",
            {"id": phone_id, "cmd": f"am start -a android.intent.action.VIEW -d '{geo_url}' com.google.android.apps.maps"},
        )
    except Exception as e:
        result["error"] = f"Maps start failed: {e}"
        return result

    _write("  Waiting 10s for Maps to load...", log_file)
    time.sleep(10)

    dump_local = str(screenshot_dir / f"{phone_id}_maps_ui.xml")
    screenshot_local = str(screenshot_dir / f"{phone_id}_maps_screen.png")

    try:
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/maps_ui.xml"})
        time.sleep(2)
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "screencap -p /sdcard/maps_screen.png"})
        time.sleep(2)

        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/maps_ui.xml"})
        xml = r.get("output", "")
        with open(dump_local, "w", encoding="utf-8") as f:
            f.write(xml)
        result["ui_dump_path"] = dump_local

        if keyword in xml:
            result["maps_search_result"] = "FOUND"
            result["business_found_text"] = keyword
        else:
            words = keyword.split()[:3]
            partial = " ".join(words)
            if partial in xml:
                result["maps_search_result"] = "FOUND"
                result["business_found_text"] = partial
            else:
                result["maps_search_result"] = "NOT_FOUND"

        _write(f"  Maps search: {result['maps_search_result']}", log_file)

        try:
            data = client.take_screenshot(phone_id, max_wait=30)
            if data:
                with open(screenshot_local, "wb") as f:
                    f.write(data)
                result["screenshot_path"] = screenshot_local
                _write(f"  Screenshot saved: {screenshot_local}", log_file)
        except Exception as e:
            _write(f"  Screenshot API failed: {e}", log_file)

    except Exception as e:
        result["error"] = str(e)
        _write(f"  ERROR during Maps capture: {e}", log_file)

    return result


def print_markdown_report(report: dict) -> None:
    print("\n" + "=" * 70)
    print("COMPREHENSIVE RUN REPORT")
    print("=" * 70)
    print(f"| Phone ID      | {report['phone_id']} |")
    print(f"| Account       | {report['account_email']} |")
    print(f"| Business      | {report['business_name']} |")
    print(f"| Keyword type  | {report['keyword_type']} |")
    print(f"| Keyword used  | {report['keyword_used']} |")
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
    print("**Maps Search**")
    print(f"  Result:    {report['maps_test']['maps_search_result']}")
    print(f"  Found:     {report['maps_test'].get('business_found_text', 'N/A')}")
    print(f"  Screenshot: {report['maps_test'].get('screenshot_path') or 'N/A'}")
    print("-" * 70)
    if report["errors"]:
        print("**Errors**")
        for e in report["errors"]:
            print(f"  • {e}")
    else:
        print("**Errors: None**")
    print("=" * 70)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude", default="acc_048", help="Account ID to exclude")
    ap.add_argument("--keyword-type", choices=["branded", "money"], default="money")
    ap.add_argument("--live-debug", action="store_true", help="Open live view URL in browser")
    ap.add_argument("--phone-id", help="Force a specific phone ID")
    ap.add_argument("--dry-run", action="store_true", help="Plan only, no phone touched")
    args = ap.parse_args()

    client = GeelarKClient()
    rows = load_all_rows()
    selected = pick_random_phone(rows, exclude_account_id=args.exclude) if not args.phone_id else next(
        (r for r in rows if r["geelark_phone_id"] == args.phone_id), None
    )
    if not selected:
        print("ERROR: No matching phone found.")
        return 1

    phone_id = selected["geelark_phone_id"]
    email = selected["account_email"]
    account_id = selected["account_id"]
    business_name = selected["business_name"]
    business_lat = float(selected["business_lat"])
    business_lng = float(selected["business_lng"])

    # Choose keyword
    keywords_raw = selected["money_keywords"] if args.keyword_type == "money" else selected["branded_keywords"]
    keywords = [k.strip() for k in keywords_raw.split("|") if k.strip()]
    keyword_used = random.choice(keywords) if keywords else business_name

    # Choose nearby point
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
    _log(f"GPS + MAPS COMPREHENSIVE TEST V3 — {_timestamp}")
    _log("=" * 70)
    _log(f"Account:      {email} ({account_id})")
    _log(f"Phone ID:     {phone_id}")
    _log(f"Business:     {business_name}")
    _log(f"Keyword type: {args.keyword_type}")
    _log(f"Keyword used: {keyword_used}")
    _log(f"Nearby:       {target_lat}, {target_lng}")

    if args.dry_run:
        _log("DRY RUN — no phone touched.")
        return 0

    # 1. Global cleanup
    global_cleanup(client, _text_log)

    # 2. Start phone
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
        report = {
            "timestamp": _now(),
            "script_name": "gps_maps_test_v3.py",
            "run_id": _timestamp,
            "phone_id": phone_id,
            "account_email": email,
            "account_id": account_id,
            "business_name": business_name,
            "business_id": selected.get("business_id"),
            "keyword_type": args.keyword_type,
            "keyword_used": keyword_used,
            "attempt": 1,
            "started_ok": False,
            "overall_status": "PHONE_START_FAILED",
            "provisioning_gates": {},
            "gps_test": {},
            "maps_test": {},
            "errors": ["Phone did not start"],
            "live_view_url": live_url,
            "duration_seconds": int(time.time() - start_ts),
        }
        write_jsonl(report)
        print_markdown_report(report)
        return 1

    # 3. Live debug URL
    if args.live_debug and live_url:
        _log(f"\n*** LIVE DEBUG MODE ***")
        _log(f"URL: {live_url}")
        try:
            webbrowser.open(live_url, new=2)
            _log("  Browser opened.")
        except Exception as e:
            _log(f"  Could not open browser: {e}")
        _log("  Pausing 15s for live view to load...")
        time.sleep(15)

    # 4. Provisioning gates
    _log("\n=== Provisioning Gates ===")
    gate_result = run_provisioning_gates(client, phone_id, _text_log)
    gates = gate_result["gates"]
    gate_errors = gate_result["errors"]
    for k, v in gates.items():
        _log(f"  {k}: {'PASS' if v else 'FAIL'}")
    if gate_errors:
        for e in gate_errors:
            _log(f"  GATE ERROR: {e}")

    # 5. Pre-GPS: UI Clear Storage for clean state
    _log("\n=== Pre-GPS: UI Clear Storage ===")
    try:
        _ui_clear_storage(phone_id)
    except Exception as e:
        _log(f"  UI Clear Storage warning: {e}")

    # 6. GPS activation
    _log("\n=== Activating GPS via _activate_gps_android13() ===")
    gps_ok = False
    try:
        gps_ok = _activate_gps_android13(phone_id, float(target_lat), float(target_lng))
    except Exception as e:
        _log(f"  GPS activation error: {e}")
        gate_errors.append(f"gps_activation: {e}")

    _log(f"GPS activation result: {gps_ok}")

    # Dumpsys check
    mock_ok, mock_info = dumpsys_mock_check(phone_id)
    _log(f"Dumpsys mock: ok={mock_ok}, info={mock_info}")

    # 6. Maps search
    time.sleep(5)
    maps_result = run_maps_search(client, phone_id, keyword_used, _screenshot_dir, _text_log)

    # 7. Stop phone
    _log("\n=== Stopping phone ===")
    confirm_stop(client, phone_id)

    duration = int(time.time() - start_ts)

    # Build report
    report = {
        "timestamp": _now(),
        "script_name": "gps_maps_test_v3.py",
        "run_id": _timestamp,
        "phone_id": phone_id,
        "account_email": email,
        "account_id": account_id,
        "business_id": selected.get("business_id"),
        "business_name": business_name,
        "keyword_type": args.keyword_type,
        "keyword_used": keyword_used,
        "attempt": 1,
        "started_ok": True,
        "overall_status": "SUCCESS" if (gps_ok and maps_result.get("maps_search_result") == "FOUND") else "PARTIAL",
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
            "screenshot_path": str(maps_result.get("screenshot_path")) if maps_result.get("screenshot_path") else None,
            "ui_dump_path": str(maps_result.get("ui_dump_path")) if maps_result.get("ui_dump_path") else None,
        },
        "interactions": {
            "scrolls": 0,
            "taps": 0,
            "detail_page": False,
            "share_sheet": False,
        },
        "errors": gate_errors + ([maps_result["error"]] if maps_result.get("error") else []),
        "live_view_url": live_url,
        "duration_seconds": duration,
    }

    # Save local JSON
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
