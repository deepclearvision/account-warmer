import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from gps_flow_baker import bake_and_import as bake_gps_flow
from flow_registry import get_reusable_flow_ids, register_flow_id
from run_one_phone import dumpsys_mock_check, _activate_gps_via_deeplink

PHONE_ID = "614216903581237315"
GPS_TEMPLATE_FLOW_ID = "620512663138468211"

print(f"=== GPS Pipeline Test on FIND X ({PHONE_ID}) ===")

client = GeelarKClient()

# 1. Bake and dispatch GPS flow (same as run_one_phone.py)
reusable = get_reusable_flow_ids(PHONE_ID)
gps_reuse_id = reusable.get("gps_flow_id")

ephemeral_gps_id = bake_gps_flow(
    client=client,
    lat="51.5224",
    lng="-0.1026",
    template_flow_id=GPS_TEMPLATE_FLOW_ID,
    reuse_flow_id=gps_reuse_id,
)
print(f"GPS flow id: {ephemeral_gps_id}")
if not gps_reuse_id:
    register_flow_id(PHONE_ID, gps_flow_id=ephemeral_gps_id)

gps_task_id = client.run_custom_flow(
    flow_id=ephemeral_gps_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name=f"gps_setup_findx",
    schedule_delay=5,
)
print(f"gps_task_id = {gps_task_id}")

# Poll
print("Polling...")
deadline = time.time() + (10 * 60)
poll_count = 0
interval = 10
while time.time() < deadline:
    time.sleep(interval)
    poll_count += 1
    if poll_count == 3:
        interval = 30
        print("  (backoff to 30s)")
    try:
        items = client.query_tasks([gps_task_id])
        if not items:
            continue
        t = items[0]
        status = t.get("status")
        if status == 3:
            print("  completed")
            break
        if status == 4:
            print(f"  failed: {t.get('failDesc', 'unknown')}")
            sys.exit(1)
    except Exception as e:
        print(f"  poll error: {e}")

# 2. Check dumpsys (same as run_one_phone.py)
print("\nChecking dumpsys...")
time.sleep(3)
mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
print(f"  Mock: {mock_ok} | {mock_info}")

# 3. If mismatch, deep-link fallback (same as run_one_phone.py, now WITHOUT force-stop)
if not mock_ok:
    print("\nDeep-link fallback (no force-stop)...")
    _activate_gps_via_deeplink(PHONE_ID, 51.5224, -0.1026)
    time.sleep(3)
    mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
    print(f"  Mock after fallback: {mock_ok} | {mock_info}")

# 4. Verify persistence
if mock_ok:
    print("\nVerifying persistence (30s)...")
    for i in range(6):
        time.sleep(5)
        ok, info = dumpsys_mock_check(PHONE_ID)
        print(f"  T+{i*5}s: {ok} | {info}")
    print("\n=== SUCCESS ===")
else:
    print("\n=== FAILED ===")
