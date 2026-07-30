#!/usr/bin/env python3
"""
Statistical batch runner for ad-dismissal evidence.
Runs N GPS flows sequentially, captures screenshots per run,
records duration, status, and ad-appearance evidence.
"""
import sys
import time
import csv
import json
import threading
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

# Explicit absolute paths — never rely on __file__
_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_orchestrator_src = _repo_root / "geelark_orchestrator" / "src"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_repo_root / "geelark_orchestrator"))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient
from gps_flow_baker import bake_and_import as bake_gps_flow
from flow_registry import get_reusable_flow_ids, register_flow_id

PHONE_ID = "614216822245294147"
GPS_TEMPLATE_FLOW_ID = "620512663138468211"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
BASE_OUT = _repo_root / "geelark_orchestrator" / "logs" / "ad_inspect_ui_clear"
RUNS = 10  # Head-to-head against baseline (was 15)

# Duration threshold: runs longer than this are flagged for ad-event inspection.
# Baseline no-ad from 6-run batch: mean 244s, max 251s. 260s is a safe flag.
AD_FLAG_THRESHOLD_S = 260


def _find_bounds(xml_data: str, text_query: str) -> tuple[int, int] | None:
    """Find center point of element matching text_query."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return None
    best = None
    best_score = 999
    q = text_query.lower()
    for node in root.iter("node"):
        t = node.get("text", "").strip().lower()
        cd = node.get("content-desc", "").strip().lower()
        if t == q or cd == q:
            score = 0
        elif t.startswith(q) or cd.startswith(q):
            score = 1
        elif q in t or q in cd:
            score = 2
        else:
            continue
        if score < best_score:
            best_score = score
            bounds = node.get("bounds", "")
            try:
                coords = bounds.replace("[", "").replace("]", ",").rstrip(",").split(",")
                x1, y1, x2, y2 = map(int, coords)
                return ((x1 + x2) // 2, (y1 + y2) // 2)
            except Exception:
                pass
    return best


def _ui_clear_storage(phone_id: str) -> None:
    """Lean UI Clear Storage: Settings -> App Info -> Storage & cache -> CLEAR STORAGE -> OK."""
    from core.geelark_client import _post

    print("  --- UI Clear Storage (pre-run) ---")
    # 1. Open App Info
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": f"am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d package:{FAKE_GPS_PACKAGE}"},
    )
    time.sleep(2)

    # 2. Find and tap "Storage & cache"
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/ui_clear_1.xml"})
    time.sleep(0.5)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/ui_clear_1.xml"})
    xml1 = r.get("output", "")
    pos = _find_bounds(xml1, "Storage & cache") or _find_bounds(xml1, "Storage")
    if pos:
        print(f"    Tapping Storage at {pos}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
        time.sleep(2)
    else:
        print("    WARNING: Storage button not found")

    # 3. Find and tap "CLEAR STORAGE"
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/ui_clear_2.xml"})
    time.sleep(0.5)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/ui_clear_2.xml"})
    xml2 = r.get("output", "")
    pos = _find_bounds(xml2, "CLEAR STORAGE")
    if pos:
        print(f"    Tapping CLEAR STORAGE at {pos}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
        time.sleep(1)
    else:
        print("    WARNING: CLEAR STORAGE button not found")

    # 4. Find and tap "OK" on confirmation dialog
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "uiautomator dump /sdcard/ui_clear_3.xml"})
    time.sleep(0.5)
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "cat /sdcard/ui_clear_3.xml"})
    xml3 = r.get("output", "")
    pos = _find_bounds(xml3, "OK") or _find_bounds(xml3, "CLEAR") or _find_bounds(xml3, "DELETE")
    if pos:
        print(f"    Tapping OK at {pos}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
        time.sleep(2)
    else:
        print("    WARNING: OK button not found")

    # 5. Return home
    _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "input keyevent KEYCODE_HOME"})
    time.sleep(1)
    print("  --- UI Clear Storage complete ---")


def screenshot_worker(client: GeelarKClient, out_dir: Path, stop_event: threading.Event) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    while not stop_event.is_set():
        try:
            data = client.take_screenshot(PHONE_ID, max_wait=30)
            if data:
                path = out_dir / f"inspect_{count:03d}.png"
                path.write_bytes(data)
                print(f"  Screenshot {count}")
                count += 1
        except Exception as e:
            print(f"  Screenshot error: {e}")
        time.sleep(5)


# Fixed coordinate for ad-batch (GPS flow behaviour is coord-agnostic for ad detection)
BATCH_LAT = "51.501364"
BATCH_LNG = "-0.088611"


def run_one(client: GeelarKClient, run_idx: int) -> dict:
    out_dir = BASE_OUT / f"run_{run_idx:02d}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== RUN {run_idx + 1}/{RUNS} ===")
    t0 = time.time()

    # Pre-run: UI Clear Storage (head-to-head variant vs baseline)
    _ui_clear_storage(PHONE_ID)

    # Bake fresh GPS flow from template (guarantees dismissal loops are present)
    reusable = get_reusable_flow_ids(PHONE_ID)
    gps_reuse_id = reusable.get("gps_flow_id")
    if gps_reuse_id:
        print(f"  Reusing existing GPS flow: {gps_reuse_id}")

    ephemeral_gps_id = bake_gps_flow(
        client=client,
        lat=BATCH_LAT,
        lng=BATCH_LNG,
        template_flow_id=GPS_TEMPLATE_FLOW_ID,
        reuse_flow_id=gps_reuse_id,
    )
    print(f"  GPS flow id: {ephemeral_gps_id}")
    if not gps_reuse_id:
        register_flow_id(PHONE_ID, gps_flow_id=ephemeral_gps_id)

    gps_task_id = client.run_custom_flow(
        flow_id=ephemeral_gps_id,
        phone_id=PHONE_ID,
        param_map={},
        task_name=f"ad_batch_{run_idx}_{int(time.time())}",
        schedule_delay=5,
    )
    print(f"  gps_task_id = {gps_task_id}")

    stop_event = threading.Event()
    thread = threading.Thread(target=screenshot_worker, args=(client, out_dir, stop_event), daemon=True)
    thread.start()

    deadline = time.time() + 600
    status = None
    cost = None
    while time.time() < deadline:
        time.sleep(10)
        try:
            items = client.query_tasks([gps_task_id])
            if items:
                status = items[0].get("status")
                cost = items[0].get("cost")
                print(f"  status={status} cost={cost}s")
                if status in (3, 4, 7):
                    break
        except Exception as e:
            print(f"  poll error: {e}")

    stop_event.set()
    thread.join(timeout=30)

    elapsed = time.time() - t0
    # Count screenshots
    screenshot_count = len(list(out_dir.glob("*.png")))

    # Infer ad appearance from duration
    ad_appeared = False
    notes = ""
    if cost is not None and cost > AD_FLAG_THRESHOLD_S:
        ad_appeared = True
        notes = f"FLAG: duration {cost}s > {AD_FLAG_THRESHOLD_S}s threshold — inspect screenshots for ad-dismissal evidence"
    elif cost is not None and cost <= AD_FLAG_THRESHOLD_S:
        notes = f"No-ad baseline: {cost}s <= {AD_FLAG_THRESHOLD_S}s"
    else:
        notes = "Duration unknown (cost=None)"

    result = {
        "run": run_idx + 1,
        "task_id": gps_task_id,
        "status": status,
        "cost_seconds": cost,
        "elapsed_seconds": round(elapsed, 1),
        "screenshot_count": screenshot_count,
        "screenshot_dir": str(out_dir),
        "ad_appeared": ad_appeared,
        "notes": notes,
    }

    print(f"  RUN RESULT: cost={cost}s | ad_appeared={ad_appeared} | {notes}")
    return result


def main() -> int:
    client = GeelarKClient()
    results = []
    for i in range(RUNS):
        result = run_one(client, i)
        results.append(result)
        # Brief pause between runs to let phone settle
        if i < RUNS - 1:
            print("  pausing 10s before next run ...")
            time.sleep(10)

    # Write structured results
    csv_path = BASE_OUT / "batch_results.csv"
    json_path = BASE_OUT / "batch_results.json"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["run", "task_id", "status", "cost_seconds", "elapsed_seconds", "screenshot_count", "ad_appeared", "notes", "screenshot_dir"])
        writer.writeheader()
        writer.writerows(results)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    for r in results:
        flag = " [FLAG - INSPECT]" if r["ad_appeared"] else ""
        print(f"Run {r['run']}: task={r['task_id']} status={r['status']} cost={r['cost_seconds']}s screenshots={r['screenshot_count']}{flag}")
        if r["ad_appeared"]:
            print(f"  -> {r['notes']}")
    print(f"\nBaseline reference: 237s–251s (no-ad, 6-run batch), ~261s–296s (recent patched runs)")
    print(f"Ad-flag threshold: >{AD_FLAG_THRESHOLD_S}s")
    print(f"Results CSV: {csv_path}")
    print(f"Results JSON: {json_path}")
    print(f"Screenshot dirs under: {BASE_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
