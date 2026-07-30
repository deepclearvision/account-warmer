#!/usr/bin/env python3
"""Emergency mock activation - OverlayService + deep link + map tap"""
import sys, time, re
from pathlib import Path

PHONE = sys.argv[1]
LAT = sys.argv[2]
LNG = sys.argv[3]
GPS = "com.theappninjas.fakegpsjoystick"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    return (_post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd}).get("output", "") or "")

# Start OverlayService
print("Starting OverlayService...")
sh("am startservice -n %s/.service.OverlayService" % GPS)
time.sleep(5)

# Deep link
url = "gpsjoystick://teleport?lat=%s&lng=%s" % (LAT, LNG)
print("Sending: %s" % url)
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
time.sleep(5)
sh("input keyevent KEYCODE_HOME"); time.sleep(3)
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
time.sleep(8)
sh("input keyevent KEYCODE_HOME"); time.sleep(3)

# Tap map area
print("Tapping map area...")
sh("input tap 360 1160")
time.sleep(5)

# Verify
print("\n=== DUMPSYS ===")
out = sh("dumpsys location")
mock_found = False
for line in out.splitlines():
    lower = line.lower()
    if "[mock]" in lower:
        print(line.strip())
        mock_found = True
    if "last mock location" in lower:
        m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
        if m:
            match = abs(float(m.group(1)) - float(LAT)) <= 0.0002 and abs(float(m.group(2)) - float(LNG)) <= 0.0002
            print("Coords: %s,%s (match=%s)" % (m.group(1), m.group(2), match))

if mock_found:
    print("\n[OK] Mock active")
else:
    print("\n[FAIL] Still no mock")
