#!/usr/bin/env python3
"""Search Maps for real businesses and inspect results."""
import sys
import time
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post
import xml.etree.ElementTree as ET

PHONE_ID = "614216822245294147"

def get_texts():
    _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "uiautomator dump /sdcard/window_dump.xml"})
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "cat /sdcard/window_dump.xml"})
    xml_data = r.get("output", "")
    root = ET.fromstring(xml_data)
    texts = []
    for node in root.iter("node"):
        t = node.get("text", "").strip()
        if t and len(t) > 2:
            texts.append(t)
    return texts

# First go back to home
print("--- Going back to Maps home ---")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity"})
time.sleep(5)
for t in get_texts()[:20]:
    print(f"  {t}")

# Tap search box
print("\n--- Tapping search box ---")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input tap 360 100"})
time.sleep(2)
for t in get_texts()[:20]:
    print(f"  {t}")

# Type search
print("\n--- Typing 'restaurant' ---")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input text 'restaurant'"})
time.sleep(1)
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input keyevent KEYCODE_ENTER"})
time.sleep(8)
for t in get_texts()[:40]:
    print(f"  {t}")
