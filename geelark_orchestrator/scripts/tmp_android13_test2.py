import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post
from proxy_control import check_proxy_ip
from run_one_phone import _find_bounds

PHONE_ID = "614216911852404803"
MODEL = "Pixel 7"

print(f"=== Android 13 Proxy Fix: Test 2 (disable rmnet_data0) on {MODEL} ({PHONE_ID}) ===")

proxy_ip = check_proxy_ip()
print(f"Proxy: {proxy_ip}")

# Step 1: Check current interfaces
print("\n[1] Current interfaces...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ip addr"})
print(r.get("output", "")[:1500])

# Step 2: Disable rmnet_data0
print("\n[2] Disabling rmnet_data0...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ip link set rmnet_data0 down"})
print(f"    result: {r.get('output', '').strip() or 'OK'}")
time.sleep(2)

# Verify it's down
print("\n[3] Verifying rmnet_data0 is down...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ip addr show rmnet_data0"})
print(f"    {r.get('output', '').strip()[:500]}")

# Step 4: Proxy check via shell curl
print("\n[4] Proxy check (shell curl)...")
found_ip = None
for url in ("http://ifconfig.me", "https://icanhazip.com"):
    try:
        r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": f"curl -s {url}"})
        out = r.get("output", "").strip()
        if out and __import__('re').match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", out):
            found_ip = out
            print(f"    curl {url} = {found_ip}")
            break
    except Exception as e:
        print(f"    curl {url} failed: {e}")

if not found_ip:
    print("    FAIL: no IP detected via curl")

# Step 5: Check if phone still functional (network connectivity)
print("\n[5] Checking network connectivity...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "curl -s --max-time 10 http://ifconfig.me"})
out = r.get("output", "").strip()
print(f"    shell curl result: {out}")

# Re-enable rmnet_data0
print("\n[6] Re-enabling rmnet_data0...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ip link set rmnet_data0 up"})
print(f"    result: {r.get('output', '').strip() or 'OK'}")

print(f"\n=== Test 2 result: {'FIXED' if found_ip == proxy_ip else 'FAILED'} ===")
