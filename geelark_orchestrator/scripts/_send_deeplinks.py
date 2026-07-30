#!/usr/bin/env python3
"""Send deep links to activate GPS mock. Usage: python _send_deeplinks.py <phone_id> <lat> <lng>"""
import sys, time, re
from pathlib import Path

PHONE = sys.argv[1]
LAT = sys.argv[2]
LNG = sys.argv[3]
GPS = "com.theappninjas.fakegpsjoystick"
WAIT = 8

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return r.get("output", "") or ""

print("Sending deep links to activate mock at %s, %s" % (LAT, LNG))
url = "gpsjoystick://teleport?lat=%s&lng=%s" % (LAT, LNG)
print("URL: %s" % url)

# First deep link
print("\n--- First deep link ---")
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
time.sleep(5)
print(sh("dumpsys window | grep mCurrentFocus"))

# HOME
sh("input keyevent KEYCODE_HOME")
time.sleep(3)

# Second deep link from home (the breakthrough that makes it reliable)
print("\n--- Second deep link (from home) ---")
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
time.sleep(WAIT)
print(sh("dumpsys window | grep mCurrentFocus"))

# HOME
sh("input keyevent KEYCODE_HOME")
time.sleep(3)

# Verify mock
print("\n=== GPS VERIFICATION ===")
out = sh("dumpsys location")
mock_active = False
coords_match = False
target_lat, target_lng = float(LAT), float(LNG)

for line in out.splitlines():
    lower = line.lower()
    if "last mock location" in lower:
        m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
        if m:
            mlat, mlng = float(m.group(1)), float(m.group(2))
            match = abs(mlat - target_lat) <= 0.0002 and abs(mlng - target_lng) <= 0.0002
            print("Mock: %s,%s (match=%s)" % (m.group(1), m.group(2), match))
            mock_active = True
            coords_match = match
    if "[mock]" in lower:
        print(line.strip())

if not mock_active:
    print("\n[FAIL] No mock location found - mock NOT active")
elif not coords_match:
    print("\n[WARN] Mock active but coords don't match target")
else:
    print("\n[OK] GPS MOCK ACTIVE at correct coordinates!")
