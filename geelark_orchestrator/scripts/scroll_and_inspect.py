#!/usr/bin/env python3
"""Scroll results list and inspect businesses."""
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

print("--- Current screen texts ---")
for t in get_texts()[:30]:
    print(f"  {t}")

# Scroll down
print("\n--- Scrolling down ---")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input swipe 540 960 540 360 500"})
time.sleep(2)

print("\n--- After scroll 1 ---")
for t in get_texts()[:30]:
    print(f"  {t}")

# Scroll down again
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input swipe 540 960 540 360 500"})
time.sleep(2)

print("\n--- After scroll 2 ---")
for t in get_texts()[:30]:
    print(f"  {t}")
