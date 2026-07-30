#!/usr/bin/env python3
"""Test deep link from any screen state. Usage: python _test_deeplink.py <phone_id> <lat> <lng>"""
import sys, time
from pathlib import Path

PHONE = sys.argv[1]
LAT = sys.argv[2]
LNG = sys.argv[3]
GPS = "com.theappninjas.fakegpsjoystick"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return r.get("output", "") or ""

print("=== Current focus before deep link ===")
print(sh("dumpsys window | grep mCurrentFocus"))

url = f"gpsjoystick://teleport?lat={LAT}&lng={LNG}"
print(f"\nSending: {url}")
sh(f"am start -a android.intent.action.VIEW -d '{url}' {GPS}")
time.sleep(5)

print("\n=== Focus after first deep link ===")
print(sh("dumpsys window | grep mCurrentFocus"))

# HOME then re-send
sh("input keyevent KEYCODE_HOME")
time.sleep(3)

print("\n=== Re-sending from home ===")
sh(f"am start -a android.intent.action.VIEW -d '{url}' {GPS}")
time.sleep(8)

print("\n=== Focus after second deep link ===")
print(sh("dumpsys window | grep mCurrentFocus"))

sh("input keyevent KEYCODE_HOME")
time.sleep(3)

# Check dumpsys
print("\n=== DUMPSYS LOCATION ===")
out = sh("dumpsys location")
for line in out.splitlines():
    lower = line.lower()
    if "mock" in lower:
        print(line.strip())
    if "last location" in lower:
        print(line.strip())
