#!/usr/bin/env python3
"""
Inspect the Fake GPS JoyStick consent/ad screen during GPS flow execution.
Runs the GPS flow and captures screenshots every 5s to see what appears.
"""
import sys
import time
import threading
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
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

def screenshot_worker(client: GeelarKClient, stop_event: threading.Event) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    while not stop_event.is_set():
        try:
            data = client.take_screenshot(PHONE_ID, max_wait=30)
            if data:
                path = OUT_DIR / f"inspect_{count:03d}.png"
                path.write_bytes(data)
                print(f"  Screenshot {count}: {path}")
                count += 1
        except Exception as e:
            print(f"  Screenshot error: {e}")
        time.sleep(5)


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
    thread = threading.Thread(target=screenshot_worker, args=(client, stop_event), daemon=True)
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
    print(f"Done. Screenshots in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
