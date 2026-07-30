#!/usr/bin/env python3
"""
Tiny helper: send a shell command to a GeelarK phone and print the output.

Usage:
  python phone_shell.py <phone_id> <command>

Examples:
  python phone_shell.py 614216769833271363 "dumpsys location | grep mock"
  python phone_shell.py 614216769833271363 "input tap 360 1150"
  python phone_shell.py 614216769833271363 "uiautomator dump /sdcard/test.xml && cat /sdcard/test.xml"
"""
import sys
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import _post

if len(sys.argv) < 3:
    print("Usage: python phone_shell.py <phone_id> <command>")
    print("Example: python phone_shell.py 614216769833271363 \"dumpsys location | grep mock\"")
    sys.exit(1)

phone_id = sys.argv[1]
cmd = sys.argv[2]

print(f"Phone: {phone_id}")
print(f"Cmd:   {cmd}")
print("-" * 50)

try:
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
    output = r.get("output", "") or "(empty)"
    print(output)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
