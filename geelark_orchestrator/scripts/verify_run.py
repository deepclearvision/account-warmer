#!/usr/bin/env python3
"""verify_run.py — Pull evidence for task 620760782241529973 (acc_049)."""
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient, _post

TASK_ID = "620760782241529973"
PHONE_ID = "614216908010422642"

client = GeelarKClient()

print("=== 1. Task query (full response) ===")
try:
    items = client.query_tasks([TASK_ID])
    if items:
        import json
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print("No task data returned.")
except Exception as e:
    print(f"Query failed: {e}")

print("\n=== 2. Screenshot (current phone state) ===")
try:
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if data:
        p = Path("verify_screenshot.png")
        p.write_bytes(data)
        print(f"Screenshot saved: {p.resolve()}")
    else:
        print("Screenshot returned None.")
except Exception as e:
    print(f"Screenshot failed: {e}")

print("\n=== 3. Fake GPS state via shell ===")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": "dumpsys location | head -20",
    }, timeout=30)
    print("dumpsys location (first 20 lines):")
    print(r.get("output", "(no output)"))
except Exception as e:
    print(f"Shell failed: {e}")

print("\n=== 4. Fake GPS app SharedPreferences (if accessible) ===")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": "cat /data/data/com.theappninjas.fakegpsjoystick/shared_prefs/com.theappninjas.fakegpsjoystick_preferences.xml 2>/dev/null || echo 'FILE_NOT_FOUND'",
    }, timeout=30)
    out = r.get("output", "")
    if "FILE_NOT_FOUND" in out:
        print("Preferences file not found (expected on first run or cleared cache).")
    else:
        print(out[:2000])
except Exception as e:
    print(f"Shell failed: {e}")

print("\n=== 5. Last known location from settings ===")
try:
    r = _post("/open/v1/shell/execute", {
        "id": PHONE_ID,
        "cmd": "settings get secure location_providers_allowed",
    }, timeout=30)
    print("location_providers_allowed:", r.get("output", "(no output)").strip())
except Exception as e:
    print(f"Shell failed: {e}")

print("\nDone.")
