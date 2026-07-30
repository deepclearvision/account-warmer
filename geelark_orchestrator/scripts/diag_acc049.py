import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post
import time

client = GeelarKClient()
phone_id = "614216908010422642"

print("=== Manually opening Maps via ADB ===")
try:
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity"})
    print(f"Shell output: {r.get('output', '')}")
    print(f"Error output: {r.get('error', '')}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(5)

print()
print("=== Screenshot after manual open ===")
try:
    data = client.take_screenshot(phone_id, max_wait=30)
    if data:
        from pathlib import Path
        p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
        p.mkdir(parents=True, exist_ok=True)
        path = p / "diag_manual_maps_open.png"
        path.write_bytes(data)
        print(f"Screenshot saved: {path}")
    else:
        print("Screenshot failed")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Current foreground activity ===")
try:
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "dumpsys activity activities | grep mResumedActivity"})
    print(r.get("output", ""))
except Exception as e:
    print(f"Error: {e}")
