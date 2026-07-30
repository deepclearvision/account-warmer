"""Try to fix Play Services update prompt on acc_049."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post

PHONE_ID = "614216908010422642"

print("=== Current foreground activity ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys activity activities | grep mResumedActivity | head -n 1"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Try to update Play Services via pm ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "pm install-existing com.google.android.gms"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Clear Play Services data (forces refresh) ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "pm clear com.google.android.gms"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Clear Play Store data ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "pm clear com.android.vending"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Screenshot after fix ===")
client = GeelarKClient()
import time
time.sleep(3)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    p.mkdir(parents=True, exist_ok=True)
    path = p / "fix_play_services_after.png"
    path.write_bytes(data)
    print(f"Screenshot saved: {path}")
