import sys
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "614216911852404803"

for cmd in [
    'settings list global | grep proxy',
    'settings list secure | grep proxy',
    'settings list system | grep proxy',
    'getprop | grep proxy',
]:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})
    out = r.get("output", "").strip()
    print(f"{cmd}:")
    print(f"  {out[:500] if out else '(empty)'}")
    print()
