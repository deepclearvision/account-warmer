import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("--- Screen resolution ---")
try:
    r = shell("wm size")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Screen density ---")
try:
    r = shell("wm density")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Display info ---")
try:
    r = shell("dumpsys display | grep -i 'width\|height\|density\|resolution'")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")
