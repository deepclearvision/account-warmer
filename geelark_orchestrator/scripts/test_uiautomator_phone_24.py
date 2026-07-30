#!/usr/bin/env python3
"""Test if uiautomator dump works on the phone for post-run verification."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"

print("--- Testing uiautomator dump ---")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "uiautomator dump /sdcard/window_dump.xml"})
    print(f"stdout: {r.get('output', '')}")
    print(f"stderr: {r.get('error', '')}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Reading dump file ---")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "cat /sdcard/window_dump.xml | head -100"})
    out = r.get("output", "")
    print(f"First 100 lines of dump:\n{out[:2000]}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Alternative: dumpsys window ---")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys window windows | grep -i 'mCurrentFocus'"})
    print(f"stdout: {r.get('output', '')}")
except Exception as e:
    print(f"ERROR: {e}")
