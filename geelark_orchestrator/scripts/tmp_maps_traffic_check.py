import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216911852404803"
MODEL = "Pixel 7"
PROXY_IP = "31.94.24.42"

print(f"=== Maps Traffic Check on {MODEL} ({PHONE_ID}) ===")
print(f"Expected proxy IP: {PROXY_IP}")

# 1. Open Maps
print("\n[1] Opening Google Maps...")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am force-stop com.google.android.apps.maps"})
time.sleep(2)
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am start -a android.intent.action.VIEW -d 'geo:51.5224,-0.1026?q=The+Dickens+Inn' com.google.android.apps.maps"})
time.sleep(8)

# 2. Check network connections during search
print("\n[2] Checking established connections (ss)...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ss -anp | grep maps"})
print(r.get("output", "")[:1500] if r.get("output") else "(empty)")

print("\n[3] Checking netstat connections...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "netstat -anp 2>/dev/null | grep maps"})
print(r.get("output", "")[:1500] if r.get("output") else "(empty)")

# 3. Check netstats per interface for Maps
print("\n[4] Checking dumpsys netstats for Maps traffic...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys netstats | grep -A 20 'com.google.android.apps.maps'"})
print(r.get("output", "")[:2000] if r.get("output") else "(empty)")

# 4. Check what IP Maps shows for outbound (if any diagnostic URLs)
print("\n[5] Checking active connections to Google endpoints...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ss -tan | grep -E '(:443|:80)' | head -20"})
print(r.get("output", "")[:2000] if r.get("output") else "(empty)")

# 5. Side test: shell curl to confirm proxy IP
print("\n[6] Side test: shell curl...")
found_ip = None
for url in ("http://ifconfig.me", "https://icanhazip.com"):
    try:
        r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": f"curl -s {url}"})
        out = r.get("output", "").strip()
        if out and __import__('re').match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", out):
            found_ip = out
            print(f"  curl {url} = {found_ip}")
            break
    except Exception as e:
        print(f"  curl {url} failed: {e}")

if not found_ip:
    print("  FAIL: no IP detected via curl")

print(f"\n=== Summary ===")
print(f"Shell curl IP: {found_ip}")
print(f"Expected proxy: {PROXY_IP}")
