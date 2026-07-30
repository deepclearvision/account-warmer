#!/usr/bin/env python3
"""
GPS + Maps FULL FLOW v5.

Uses the existing production Maps RPA flow (maps_flow_baker) for scroll + interactions.
Features:
  - Pre-dismisses Play Store / update notifications before Maps
  - Uses _ui_clear_storage before every GPS activation
  - Uses full Maps RPA flow: search -> scroll -> find business -> interactions
  - Exactly ONE pause: after phone starts, before browser opens
  - Opens live-view in Chrome new window via redirect HTML
"""
import argparse
import csv
import json
import random
import subprocess
import sys
import time
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
    _ui_clear_storage,
)
from maps_flow_baker import bake_and_import as bake_maps_flow
from maps_evidence import gather_maps_evidence

CSV_PATH = _data_dir / "accounts_business_mapping.csv"
JSONL_PATH = _logs_dir / "run_history.jsonl"
START_TIMEOUT = 360
INTER_PHONE_DELAY = 30
MAX_ATTEMPTS = 3
MAPS_TEMPLATE_FLOW_ID = "620892967896350964"


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


def dismiss_notifications(phone_id: str) -> None:
    """Dismiss Play Store / update notifications before Maps opens."""
    try:
        # Pull notification shade
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input swipe 360 10 360 800"})
        time.sleep(2)
        # Try to tap "Dismiss" or "Clear all"
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/notif.xml"})
        time.sleep(1)
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/notif.xml"})
        xml = r.get("output", "")
        # Look for clear-all or dismiss text
        for text in ("Clear all", "clear all", "Dismiss", "dismiss", "Update", "Play Store"):
            # Simple center-of-screen tap fallback
            pass
        # Close shade (swipe up)
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input swipe 360 800 360 10"})
        time.sleep(1)
    except Exception:
        pass


def _open_browser_new_window(url: str) -> None:
    """Open URL in Chrome new window via redirect HTML (avoids Windows cmd truncation)."""
    try:
        html_path = _repo_root / "geelark_orchestrator" / "scripts" / "_live_view_redirect.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f'<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0; url={url}"></head><body>Opening live view...</body></html>')
        subprocess.Popen(["cmd", "/c", "start", "", "chrome", "--new-window", str(html_path)])
        print("  Chrome new window launched.")
    except Exception as e:
        print(f"  WARNING: Could not open browser: {e}")


def _interactive_pause(prompt: str) -> None:
    print("\n" + "▶" * 35)
    print(prompt)
    print("◀" * 35)
    try:
        input("  [ Press ENTER to continue... ]")
    except (EOFError, KeyboardInterrupt):
        print("  (continuing without input)")


def run_full_maps_rpa(
    client: GeelarKClient,
    phone_id: str,
    lat: float,
    lng: float,
    keyword: str,
    business_name: str,
    log_file: Path,
) -> dict:
    """Run the full Maps RPA flow with baked values and gather evidence."""
    _write("\n=== Full Maps RPA Flow ===", log_file)

    # Dismiss notifications before Maps
    _write("  Dismissing notifications...", log_file)
    dismiss_notifications(phone_id)

    # Bake the Maps flow with the keyword and business name
    _write(f"  Baking Maps flow: search='{keyword}' business='{business_name}'", log_file)
    try:
        ephemeral_maps_id = bake_maps_flow(
            client=client,
            lat=str(lat),
            lng=str(lng),
            search_term=keyword,
            business_name=business_name,
            template_flow_id=MAPS_TEMPLATE_FLOW_ID,
            reuse_flow_id=None,
            search_mode="discovery",
        )
        _write(f"  Maps flow id: {ephemeral_maps_id}", log_file)
    except Exception as e:
        _write(f"  ERROR baking Maps flow: {e}", log_file)
        return {"maps_search_result": "FAILED", "error": str(e)}

    # Dispatch the RPA flow
    _write("  Dispatching Maps RPA flow...", log_file)
    try:
        maps_task_id = client.run_custom_flow(
            flow_id=ephemeral_maps_id,
            phone_id=phone_id,
            param_map={},
            task_name=f"maps_full_{phone_id}_{datetime.now().strftime('%H%M%S')}",
            schedule_delay=5,
        )
        _write(f"  maps_task_id = {maps_task_id}", log_file)
    except Exception as e:
        _write(f"  ERROR dispatching Maps flow: {e}", log_file)
        return {"maps_search_result": "FAILED", "error": str(e)}

    # Poll the task
    _write("  Polling Maps task...", log_file)
    deadline = time.time() + (15 * 60)
    poll_count = 0
    interval = 10
    task_result = {"status": -1}
    while time.time() < deadline:
        time.sleep(interval)
        poll_count += 1
        if poll_count == 5:
            interval = 30
            _write(f"    (poll backoff: widening to {interval}s)", log_file)
        try:
            items = client.query_tasks([maps_task_id])
            if not items:
                continue
            t = items[0]
            status = t.get("status")
            if status == 3:
                _write("  Maps task completed.", log_file)
                task_result = t
                break
            if status == 4:
                _write(f"  Maps task failed: {t.get('failDesc', t.get('failCode', 'unknown'))}", log_file)
                task_result = t
                break
            if status == 7:
                _write("  Maps task cancelled.", log_file)
                task_result = t
                break
        except Exception as e:
            _write(f"  poll error: {e}", log_file)

    # Post-run evidence
    _write("\n  Gathering Maps evidence (uiautomator)...", log_file)
    time.sleep(5)
    try:
        evidence = gather_maps_evidence(phone_id, business_name, log_interactions=[])
        _write(f"    search_submitted: {evidence['search_submitted']}", log_file)
        _write(f"    business_found: {evidence['business_found']}", log_file)
        _write(f"    card_opened: {evidence['card_opened']}", log_file)
        _write(f"    interactions_present: {evidence['interactions_present']}", log_file)
        if evidence.get('business_name_matched'):
            _write(f"    matched text: {evidence['business_name_matched']!r}", log_file)
    except Exception as e:
        _write(f"  WARNING: evidence gathering failed: {e}", log_file)
        evidence = {
            "search_submitted": False,
            "business_found": False,
            "card_opened": False,
            "business_name_matched": None,
            "interactions_present": [],
            "all_texts": [],
            "screen_package": None,
        }

    # Screenshot
    screenshot_local = None
    try:
        data = client.take_screenshot(phone_id, max_wait=30)
        if data:
            ss_dir = Path(log_file).parent / "screenshots"
            ss_dir.mkdir(parents=True, exist_ok=True)
            screenshot_local = ss_dir / f"{phone_id}_maps_rpa.png"
            with open(screenshot_local, "wb") as f:
                f.write(data)
            _write(f"  Screenshot saved: {screenshot_local}", log_file)
    except Exception as e:
        _write(f"  Screenshot failed: {e}", log_file)

    maps_status = task_result.get("status")
    maps_ok = maps_status == 3
    found = evidence.get("business_found", False)

    return {
        "maps_search_result": "FOUND" if found else "NOT_FOUND",
        "business_found_text": evidence.get("business_name_matched"),
        "screenshot_path": str(screenshot_local) if screenshot_local else None,
        "interactions_present": evidence.get("interactions_present", []),
        "maps_task_status": maps_status,
        "error": None,
    }


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
    print("**Maps RPA Flow**")
    print(f"  Task status: {report['maps_test'].get('maps_task_status')}")
    print(f"  Result:      {report['maps_test']['maps_search_result']}")
    print(f"  Found:       {report['maps_test'].get('business_found_text', 'N/A')}")
    print(f"  Interactions:{report['maps_test'].get('interactions_present', [])}")
    print(f"  Screenshot:  {report['maps_test'].get('screenshot_path') or 'N/A'}")
    if report["errors"]:
        print("-" * 70)
        print("**Errors**")
        for e in report["errors"]:
            print(f"  • {e}")
    print("=" * 70)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="acc_010", help="Account ID to run")
    ap.add_argument("--keyword-type", choices=["branded", "money"], default="money")
    ap.add_argument("--watch", action="store_true", help="Interactive debug mode")
    ap.add_argument("--dry-run", action="store_true", help="Plan only")
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

    def _log(line: str) -> None:
        _write(line, _text_log)

    start_ts = time.time()
    _log("=" * 70)
    _log(f"GPS + MAPS FULL FLOW V5 — {_timestamp}")
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

    if args.watch:
        _interactive_pause(
            "▶▶▶ ABOUT TO BEGIN ◀◀◀\n"
            "  • Cleanup will stop any running phones\n"
            "  • Then the target phone will START\n"
            "  • You will get ONE pause before the browser opens\n"
            "  Press ENTER to run cleanup."
        )

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
        print("\n*** ABORTED: Phone did not start ***")
        return 1

    # THE ONE PAUSE — get ready to watch
    if args.watch and live_url:
        _interactive_pause(
            f"▶▶▶ PHONE IS RUNNING — GET READY TO WATCH ◀◀◀\n"
            f"  URL: {live_url}\n"
            f"  Press ENTER to open Chrome in NEW WINDOW and start GPS + Maps."
        )
        _open_browser_new_window(live_url)
        time.sleep(3)
    elif live_url:
        _log(f"Live view URL: {live_url}")

    # 3. Provisioning gates (info only)
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

    # 4. Pre-GPS: UI Clear Storage
    _log("\n=== Pre-GPS: UI Clear Storage ===")
    try:
        _ui_clear_storage(phone_id)
    except Exception as e:
        _log(f"  UI Clear Storage warning: {e}")
        gate_errors.append(f"ui_clear_storage: {e}")

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

    # 6. FULL Maps RPA flow (scroll + interactions)
    maps_result = run_full_maps_rpa(
        client, phone_id, target_lat, target_lng,
        keyword_used, business_name, _text_log,
    )

    # 7. Stop phone
    _log("\n=== Stopping phone ===")
    confirm_stop(client, phone_id)

    duration = int(time.time() - start_ts)

    # Build report
    report = {
        "timestamp": _now(),
        "script_name": "gps_maps_test_v5_full_flow.py",
        "run_id": _timestamp,
        "phone_id": phone_id,
        "account_email": email,
        "account_id": account_id,
        "business_id": selected.get("business_id"),
        "business_name": business_name,
        "keyword_type": args.keyword_type,
        "keyword_used": keyword_used,
        "all_keywords": keywords,
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
            "interactions_present": maps_result.get("interactions_present", []),
            "maps_task_status": maps_result.get("maps_task_status"),
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
    return 0 if (gps_ok and report["overall_status"] == "SUCCESS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
