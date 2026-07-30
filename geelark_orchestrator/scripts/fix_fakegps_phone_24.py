import sys
import time
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("========================================")
print("FAKE GPS UI-BASED SETUP")
print("========================================\n")

# ---------------------------------------------------------------------------
# Open Fake GPS
# ---------------------------------------------------------------------------
print("--- Open Fake GPS Joystick ---")
try:
    r = shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.MainActivity")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

print("Waiting 5s for app to load...")
time.sleep(5)

# ---------------------------------------------------------------------------
# Dump UI and parse
# ---------------------------------------------------------------------------
print("\n--- Dump UI layout ---")
try:
    shell("uiautomator dump /sdcard/fakegps_ui.xml")
    r = shell("cat /sdcard/fakegps_ui.xml")
    xml_text = r.get("output", "")
    if not xml_text:
        print("No XML output")
    else:
        # Parse for actionable elements
        root = ET.fromstring(xml_text)
        actionable = []
        for node in root.iter("node"):
            text = (node.get("text") or "").strip()
            desc = (node.get("content-desc") or "").strip()
            cls = (node.get("class") or "").strip()
            bounds = node.get("bounds", "")
            clickable = node.get("clickable", "false")
            if (text or desc) and clickable == "true":
                actionable.append((text, desc, cls, bounds))
        print(f"Found {len(actionable)} clickable elements:")
        for text, desc, cls, bounds in actionable:
            print(f"  text='{text}' desc='{desc}' class={cls} bounds={bounds}")
except Exception as e:
    print(f"UI parse error: {e}")

# ---------------------------------------------------------------------------
# Screenshot for record
# ---------------------------------------------------------------------------
print("\n--- Screenshot (Fake GPS open) ---")
client = GeelarKClient()
try:
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if data:
        p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
        p.mkdir(parents=True, exist_ok=True)
        path = p / "fakegps_24_open.png"
        path.write_bytes(data)
        print(f"Screenshot saved: {path}")
    else:
        print("No screenshot data")
except Exception as e:
    print(f"Screenshot error: {e}")

# ---------------------------------------------------------------------------
# Try to find and tap "Set Location" or "Start" buttons
# ---------------------------------------------------------------------------
print("\n--- Attempt blind taps on known button areas ---")
# Based on Fake GPS Joystick typical layout:
# - Map fills most of screen; tap center to set pin
# - Bottom has "Set Location" and "Start" buttons

taps = [
    ("Center map (set pin)", 360, 600),
    ("Bottom-left button area", 180, 1150),
    ("Bottom-center button area", 360, 1150),
    ("Bottom-right button area", 540, 1150),
    ("Start button area", 360, 1250),
]

for label, x, y in taps:
    print(f"  {label}: tap {x},{y}")
    shell(f"input tap {x} {y}")
    time.sleep(2)

# ---------------------------------------------------------------------------
# Final dumpsys check
# ---------------------------------------------------------------------------
print("\n--- Final dumpsys location check ---")
try:
    r = shell("dumpsys location | head -n 60")
    out = r.get("output", "")
    # Print only relevant lines
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")

print("\n========================================")
print("DONE")
print("========================================")
