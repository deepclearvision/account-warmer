import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient

PHONE_ID = "614216822245294147"
GPS_SETUP_FLOW_ID = "620512663138468211"

client = GeelarKClient()

print("========================================")
print("RUN GPS SETUP FLOW ON PHONE 24")
print("========================================\n")

# Start phone if needed
try:
    statuses = client.get_phone_status([PHONE_ID])
    s = statuses[0].get("status", -1) if statuses else -1
except Exception:
    s = -1

if s != 0:
    print("Starting phone...")
    try:
        client.start_phone(PHONE_ID)
    except Exception as e:
        print(f"Start error: {e}")
        sys.exit(1)

    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            statuses = client.get_phone_status([PHONE_ID])
            s = statuses[0].get("status", -1) if statuses else -1
            if s == 0:
                print("Phone running.")
                break
        except Exception:
            pass
    else:
        print("Phone did not start.")
        sys.exit(1)
else:
    print("Phone already running.")

print("\nDispatching GPS setup flow...")
try:
    task_id = client.run_custom_flow(
        flow_id=GPS_SETUP_FLOW_ID,
        phone_id=PHONE_ID,
        param_map={},
        task_name="GPS_Setup_serial_24",
        schedule_delay=5,
    )
    print(f"task_id = {task_id}")
except Exception as e:
    print(f"Dispatch error: {e}")
    sys.exit(1)

# Poll for completion
print("\nPolling task...")
deadline = time.time() + 300
interval = 10
while time.time() < deadline:
    time.sleep(interval)
    try:
        items = client.query_tasks([task_id])
        if not items:
            continue
        t = items[0]
        status = t.get("status")
        if status == 3:
            print("Task completed.")
            break
        if status == 4:
            print(f"Task failed: {t.get('failDesc', t.get('failCode', 'unknown'))}")
            break
        if status == 7:
            print("Task cancelled.")
            break
        print(f"  status={status} (1=waiting 2=in_progress)")
    except Exception as e:
        print(f"  poll error: {e}")

# Post-check dumpsys
print("\n--- Post-setup dumpsys ---")
try:
    from core.geelark_client import _post
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys location | head -n 40"})
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Post-setup mock grep ---")
try:
    from core.geelark_client import _post
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": r"dumpsys location | grep -i 'mock\|fakegps\|theappninjas'"})
    print(r.get("output", "(no output)"))
except Exception as e:
    print(f"ERROR: {e}")

print("\n========================================")
print("DONE")
print("========================================")
