import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from gps_flow_baker import bake_and_import as bake_gps_flow
from flow_registry import get_reusable_flow_ids, register_flow_id
from run_one_phone import dumpsys_mock_check, _activate_gps_via_deeplink

PHONE_ID = "614216890796998723"
GPS_TEMPLATE_FLOW_ID = "620512663138468211"

print(f"=== GPS Setup for X27 ({PHONE_ID}) ===")

client = GeelarKClient()

reusable = get_reusable_flow_ids(PHONE_ID)
gps_reuse_id = reusable.get("gps_flow_id")
if gps_reuse_id:
    print(f"  Reusing existing GPS flow: {gps_reuse_id}")

try:
    ephemeral_gps_id = bake_gps_flow(
        client=client,
        lat="51.5224",
        lng="-0.1026",
        template_flow_id=GPS_TEMPLATE_FLOW_ID,
        reuse_flow_id=gps_reuse_id,
    )
    print(f"  GPS flow id: {ephemeral_gps_id}")
    if not gps_reuse_id:
        register_flow_id(PHONE_ID, gps_flow_id=ephemeral_gps_id)
except Exception as e:
    print(f"  ERROR: {e}")
    sys.exit(1)

print("\nDispatching GPS flow...")
try:
    gps_task_id = client.run_custom_flow(
        flow_id=ephemeral_gps_id,
        phone_id=PHONE_ID,
        param_map={},
        task_name=f"gps_setup_x27",
        schedule_delay=5,
    )
    print(f"  gps_task_id = {gps_task_id}")
except Exception as e:
    print(f"  ERROR: {e}")
    sys.exit(1)

print("\nPolling...")
deadline = time.time() + (10 * 60)
poll_count = 0
interval = 10
while time.time() < deadline:
    time.sleep(interval)
    poll_count += 1
    if poll_count == 3:
        interval = 30
        print("    (backoff to 30s)")
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
        if status == 7:
            print("  cancelled")
            sys.exit(1)
    except Exception as e:
        print(f"  poll error: {e}")

print("\nChecking dumpsys...")
time.sleep(3)
mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
print(f"  Mock: {mock_ok} | {mock_info}")

if not mock_ok:
    print("\nTrying deep-link fallback...")
    _activate_gps_via_deeplink(PHONE_ID, 51.5224, -0.1026)
    time.sleep(3)
    mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
    print(f"  Mock after fallback: {mock_ok} | {mock_info}")

if mock_ok:
    print("\n=== SUCCESS ===")
else:
    print("\n=== FAILED ===")
