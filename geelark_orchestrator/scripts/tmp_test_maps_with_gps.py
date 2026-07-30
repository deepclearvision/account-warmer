import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from maps_flow_baker import bake_and_import as bake_maps_flow
from flow_registry import get_reusable_flow_ids, register_flow_id
from run_one_phone import poll_task, gather_maps_evidence, classify_run_status, foreground_check, dumpsys_mock_check

PHONE_ID = "614216903581237315"
MAPS_TEMPLATE_FLOW_ID = "620892967896350964"

print(f"=== Test Maps with Fake GPS running on FIND X ({PHONE_ID}) ===")

# Verify mock is still active
mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
print(f"Pre-Maps mock: {mock_ok} | {mock_info}")
if not mock_ok:
    print("ABORT: mock not active")
    sys.exit(1)

# Bake Maps flow
client = GeelarKClient()
print("\nBaking Maps flow...")
reusable = get_reusable_flow_ids(PHONE_ID)
maps_reuse_id = reusable.get("maps_flow_id")
if maps_reuse_id:
    print(f"  Reusing: {maps_reuse_id}")

try:
    ephemeral_maps_id = bake_maps_flow(
        client=client,
        lat="51.5224",
        lng="-0.1026",
        search_term="plumber near me",
        business_name="Milestone Test Business",
        template_flow_id=MAPS_TEMPLATE_FLOW_ID,
        reuse_flow_id=maps_reuse_id,
    )
    print(f"  Maps flow id: {ephemeral_maps_id}")
    if not maps_reuse_id:
        register_flow_id(PHONE_ID, maps_flow_id=ephemeral_maps_id)
except Exception as e:
    print(f"  ERROR: {e}")
    sys.exit(1)

# Dispatch Maps flow
print("\nDispatching Maps flow...")
try:
    maps_task_id = client.run_custom_flow(
        flow_id=ephemeral_maps_id,
        phone_id=PHONE_ID,
        param_map={},
        task_name="maps_test_findx",
        schedule_delay=5,
    )
    print(f"  maps_task_id = {maps_task_id}")
except Exception as e:
    print(f"  ERROR: {e}")
    sys.exit(1)

# Poll
print("\nPolling Maps task...")
maps_task = poll_task(client, maps_task_id, timeout_minutes=15)
maps_status = maps_task.get("status")
maps_fail_desc = maps_task.get("failDesc") or str(maps_task.get("failCode", ""))
duration = maps_task.get("cost")

print(f"  Status: {maps_status} | Fail: {maps_fail_desc} | Duration: {duration}s")

# Post-run evidence
print("\nGathering Maps evidence...")
time.sleep(2)
maps_evidence = gather_maps_evidence(PHONE_ID, business_name="Milestone Test Business")
print(f"  search_submitted: {maps_evidence['search_submitted']}")
print(f"  business_found: {maps_evidence['business_found']}")
print(f"  card_opened: {maps_evidence.get('card_opened')}")
print(f"  interactions: {maps_evidence['interactions_present']}")
print(f"  screen_package: {maps_evidence['screen_package']}")

# Classify
run_status, failed_step = classify_run_status(
    gps_verified=1,
    evidence=maps_evidence,
    maps_task_status=maps_status,
)
print(f"\nRun status: {run_status} | failed_step: {failed_step}")

# Final foreground
fg_ok, fg_pkg = foreground_check(PHONE_ID)
print(f"Foreground: {fg_pkg}")

# Mock still active?
mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
print(f"Post-Maps mock: {mock_ok} | {mock_info}")

print("\n=== Test complete ===")
