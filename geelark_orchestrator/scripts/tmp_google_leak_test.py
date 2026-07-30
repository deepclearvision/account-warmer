import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post
from proxy_control import change_proxy_ip, check_proxy_ip

PHONE_ID = "614216911852404803"
MODEL = "Pixel 7"

sys.stdout.reconfigure(encoding='utf-8')

print(f"=== Google Leak Test: {MODEL} ({PHONE_ID}) ===")

# 1. Rotate proxy and record IP
print("\n[1] Rotating proxy...")
try:
    proxy_ip = change_proxy_ip()
    print(f"    Proxy rotated to: {proxy_ip}")
except Exception as e:
    print(f"    ERROR: {e}")
    proxy_ip = check_proxy_ip()
    print(f"    Current proxy: {proxy_ip}")

time.sleep(5)

# 2. Verify proxy via shell curl
print("\n[2] Verifying proxy (shell curl)...")
app_ip = None
for url in ("http://ifconfig.me", "https://icanhazip.com"):
    try:
        r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": f"curl -s {url}"})
        out = r.get("output", "").strip()
        if out and __import__('re').match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", out):
            app_ip = out
            print(f"    curl {url} = {app_ip}")
            break
    except Exception as e:
        print(f"    curl {url} failed: {e}")

print(f"\n    Shell IP recorded: {app_ip}")
print(f"    Proxy IP recorded: {proxy_ip}")

# 3. Check Google accounts on phone
print("\n[3] Checking Google accounts on phone...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys account | grep -A 2 'Account {' | head -20"})
accounts_out = r.get("output", "") or ""
print(accounts_out[:1000] if accounts_out else "(no accounts found)")

# 4. Check Google Play Services activity
print("\n[4] Checking Google Play Services state...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys activity services | grep 'com.google.android.gms' | head -10"})
print(r.get("output", "")[:800] if r.get("output") else "(empty)")

# 5. Open Maps and perform authentic activity
print("\n[5] Opening Maps and performing activity...")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am force-stop com.google.android.apps.maps"})
time.sleep(2)
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am start -a android.intent.action.VIEW -d 'geo:51.5224,-0.1026?q=The Dickens Inn' com.google.android.apps.maps"})
print("    Maps opened with search for 'The Dickens Inn'")
time.sleep(10)

# Take a screenshot to confirm state
print("\n[6] Checking foreground...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys activity activities | grep mResumedActivity"})
print(f"    {r.get('output', '').strip()}")

# 7. Check network connections during Maps activity
print("\n[7] Active connections during Maps activity...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "netstat -anp 2>/dev/null | grep 'com.google' | grep ESTABLISHED | head -20"})
out = r.get("output", "") or ""
lines = out.splitlines() if out else []
print(f"    {len(lines)} active Google connections:")
for line in lines[:15]:
    print(f"      {line}")

# 8. Check QUIC / UDP connections
print("\n[8] QUIC / UDP connections...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "ss -u -anp | grep 'com.google' | head -10"})
print(r.get("output", "")[:800] if r.get("output") else "(no UDP/QUIC connections)")

# 9. Check DNS lookups
print("\n[9] DNS activity...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys connectivity | grep -i dns | head -10"})
print(r.get("output", "")[:800] if r.get("output") else "(empty)")

print("\n=== Test complete ===")
print(f"Proxy IP at test start: {proxy_ip}")
print(f"Shell curl IP: {app_ip}")
print("Next step: check Google account activity page from a different machine")
print("Account to check:", accounts_out[:200] if accounts_out else "(unknown)")
