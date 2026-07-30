import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post

PHONE_ID = "614216822245294147"
PROFILE_KEY = "serial_24"

client = GeelarKClient()

print("========================================")
print("PHONE START + VERIFICATION SCRIPT")
print(f"Phone ID: {PHONE_ID}")
print("========================================\n")

# ---------------------------------------------------------------------------
# STEP 0: Ensure phone is running
# ---------------------------------------------------------------------------
print("--- STEP 0: Start phone if not running ---")
try:
    statuses = client.get_phone_status([PHONE_ID])
    s = statuses[0].get("status", -1) if statuses else -1
    print(f"Current status code: {s}")
except Exception as e:
    print(f"Status check error: {e}")
    s = -1

if s != 0:
    print("Phone not running. Starting...")
    try:
        url = client.start_phone(PHONE_ID)
        print(f"Start requested. Viewer URL: {url}")
    except Exception as e:
        print(f"Start error: {e}")
        sys.exit(1)

    # Poll until running
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            statuses = client.get_phone_status([PHONE_ID])
            s = statuses[0].get("status", -1) if statuses else -1
            print(f"  Poll status: {s}")
            if s == 0:
                print("Phone is running.")
                break
        except Exception as e:
            print(f"  Poll error: {e}")
    else:
        print("ERROR: Phone did not start within 120s.")
        sys.exit(1)
else:
    print("Phone already running.")

print("\nWaiting 10s for ADB / shell to be ready...")
time.sleep(10)

# ---------------------------------------------------------------------------
# CHECK 1: dumpsys location — Fake GPS must be active mock provider
# ---------------------------------------------------------------------------
print("\n--- CHECK 1: dumpsys location (mock provider) ---")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": r"dumpsys location | grep -i 'mock\|fakegps\|theappninjas'"
    })
    output = r.get("output", "")
    error = r.get("error", "")
    print("STDOUT:")
    print(output if output else "(empty)")
    print("STDERR:")
    print(error if error else "(empty)")
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- CHECK 1b: dumpsys location providers (top 40 lines) ---")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": r"dumpsys location | head -n 40"
    })
    output = r.get("output", "")
    print(output if output else "(empty)")
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# CHECK 2: Open Maps via ADB intent
# ---------------------------------------------------------------------------
print("\n--- CHECK 2: Opening Maps via ADB intent ---")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity"
    })
    output = r.get("output", "")
    error = r.get("error", "")
    print("STDOUT:")
    print(output if output else "(empty)")
    print("STDERR:")
    print(error if error else "(empty)")
except Exception as e:
    print(f"ERROR: {e}")

print("\nWaiting 7s for Maps to settle...")
time.sleep(7)

# ---------------------------------------------------------------------------
# CHECK 3: Foreground package
# ---------------------------------------------------------------------------
print("\n--- CHECK 3: Foreground package after Maps open ---")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": "dumpsys activity activities | grep mResumedActivity"
    })
    output = r.get("output", "")
    print("mResumedActivity:")
    print(output if output else "(empty)")
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- CHECK 3b: mCurrentFocus / mFocusedApp ---")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": r"dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'"
    })
    output = r.get("output", "")
    print(output if output else "(empty)")
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# CHECK 4: Screenshot for disk record
# ---------------------------------------------------------------------------
print("\n--- CHECK 4: Screenshot for disk record ---")
try:
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if data:
        p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
        p.mkdir(parents=True, exist_ok=True)
        path = p / f"verify_{PROFILE_KEY}_maps_open.png"
        path.write_bytes(data)
        print(f"Screenshot saved: {path}")
    else:
        print("Screenshot returned no data")
except Exception as e:
    print(f"Screenshot error: {e}")

print("\n========================================")
print("VERIFICATION COMPLETE")
print("========================================")
