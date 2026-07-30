import sys
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "614216911852404803"

for cmd in ['iptables --version', 'iptables -L -n', 'ip rule', 'ip route']:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})
    out = r.get("output", "").strip()
    err = r.get("error", "").strip()
    print(f"{cmd}:")
    print(f"  out: {out[:300] if out else '(empty)'}")
    print(f"  err: {err[:300] if err else '(empty)'}")
    print()
