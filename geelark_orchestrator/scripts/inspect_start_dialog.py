#!/usr/bin/env python3
"""
Inspect the START-dialog in Fake GPS JoyStick by dispatching the GPS flow
and capturing uiautomator dumps every 2-3 seconds.
"""
import sys
import time
import threading
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
_orchestrator_root = _repo_root / "geelark_orchestrator"
_orchestrator_src = _orchestrator_root / "src"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient, _post

PHONE_ID = "614216822245294147"
GPS_FLOW_ID = "621208386251260258"
OUT_DIR = Path(__file__).resolve().parent.parent / "logs" / "ad_inspect"

def dump_worker(client: GeelarKClient, stop_event: threading.Event) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    while not stop_event.is_set():
        try:
            # Trigger dump
            _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "uiautomator dump /sdcard/window_dump.xml"}, timeout=15)
            time.sleep(0.5)
            r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "cat /sdcard/window_dump.xml"}, timeout=15)
            xml = r.get("output", "")
            if xml and "hierarchy" in xml:
                path = OUT_DIR / f"dump_{count:03d}.xml"
                path.write_text(xml, encoding="utf-8")
                # Quick grep for interesting text
                interesting = []
                for keyword in ("CONTINUE WITH ADS", "Continue with Ads", "Watch Video", "watch video",
                                "Go ad free", "ad free", "START", "STOP", "Consent", "CONSENT",
                                "Interstitial", "AdActivity", "Skip", "skip", "Dismiss", "CLOSE", "X"):
                    if keyword.lower() in xml.lower():
                        interesting.append(keyword)
                if interesting:
                    print(f"  Dump {count}: INTERESTING -> {interesting}")
                else:
                    print(f"  Dump {count}: saved")
                count += 1
        except Exception as e:
            print(f"  Dump error: {e}")
        time.sleep(2)


def main() -> int:
    client = GeelarKClient()
    print("Dispatching GPS flow ...")
    gps_task_id = client.run_custom_flow(
        flow_id=GPS_FLOW_ID,
        phone_id=PHONE_ID,
        param_map={},
        task_name=f"ad_inspect_gps_{int(time.time())}",
        schedule_delay=5,
    )
    print(f"gps_task_id = {gps_task_id}")

    stop_event = threading.Event()
    thread = threading.Thread(target=dump_worker, args=(client, stop_event), daemon=True)
    thread.start()

    print("Polling GPS task ...")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(10)
        try:
            items = client.query_tasks([gps_task_id])
            if items:
                status = items[0].get("status")
                print(f"  status={status}")
                if status in (3, 4, 7):
                    break
        except Exception as e:
            print(f"  poll error: {e}")

    stop_event.set()
    thread.join(timeout=30)
    print(f"Done. Dumps in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
