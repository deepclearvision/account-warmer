import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post
from proxy_control import check_proxy_ip

PHONE_ID = "614216911852404803"
MODEL = "Pixel 7"

print(f"=== Android 13 Proxy Fix: Test 1 (global proxy) on {MODEL} ({PHONE_ID}) ===")

# Get current proxy IP
proxy_ip = check_proxy_ip()
PROXY_ADDR = f"{proxy_ip}:7002"
print(f"Proxy: {PROXY_ADDR}")

# Step 1: Set global proxy
print("\n[1] Setting global http_proxy...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": f"settings put global http_proxy {PROXY_ADDR}"})
print(f"    result: {r.get('output', '').strip() or 'OK'}")
time.sleep(2)

# Verify it was set
print("\n[2] Verifying global proxy...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "settings get global http_proxy"})
set_value = r.get("output", "").strip()
print(f"    http_proxy = {set_value}")

# Step 2: Proxy check via shell curl
print("\n[3] Proxy check (shell curl)...")
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

# Reversible — clear proxy
print("\n[4] Clearing global proxy (reversible)...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "settings put global http_proxy :0"})
print(f"    result: {r.get('output', '').strip() or 'OK'}")

print(f"\n=== Test 1 result: {'FIXED' if found_ip == proxy_ip else 'FAILED'} ===")
