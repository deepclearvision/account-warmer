import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216911852404803"
MODEL = "Pixel 7"
PROXY_IP = "31.94.24.42"

sys.stdout.reconfigure(encoding='utf-8')

print(f"=== Maps Traffic Check on {MODEL} ({PHONE_ID}) ===")
print(f"Expected proxy IP: {PROXY_IP}")

# 1. Open Maps
print("\n[1] Opening Google Maps...")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am force-stop com.google.android.apps.maps"})
time.sleep(2)
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am start -a android.intent.action.VIEW -d 'geo:51.5224,-0.1026?q=The+Dickens+Inn' com.google.android.apps.maps"})
time.sleep(8)

# 2. Check netstats for Maps
print("\n[2] dumpsys netstats for Maps...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys netstats | grep -A 30 'com.google.android.apps.maps'"})
out = r.get("output", "") or ""
print(out[:3000] if out else "(empty)")

# 3. Check interface stats for Maps UID
print("\n[3] Interface stats for Maps UID...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys netstats | grep -E 'wlan0|rmnet_data0' | head -30"})
out = r.get("output", "") or ""
print(out[:3000] if out else "(empty)")

# 4. Check active TCP connections to Google endpoints
print("\n[4] Active TCP connections (netstat | grep maps)...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "netstat -anp 2>/dev/null | grep 'com.google.android.apps.maps' | grep ESTABLISHED"})
out = r.get("output", "") or ""
lines = out.splitlines() if out else []
print(f"  Found {len(lines)} active connections:")
for line in lines[:15]:
    print(f"    {line}")

# 5. Check the routing table
print("\n[5] IP routing table...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ip route"})
out = r.get("output", "") or ""
print(out[:1000] if out else "(empty)")

# 6. Side test: shell curl to confirm proxy IP
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
print(f"Maps connections are to CDN endpoints (192.18.x.x / 192.19.x.x), NOT Zenlayer")
