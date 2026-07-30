#!/usr/bin/env python3
"""Click on first result and inspect business card."""
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

# Click on the first business result (at approximate position y=300 on results list)
print("--- Clicking first result ---")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input tap 360 300"})
time.sleep(4)
for t in get_texts()[:50]:
    print(t.encode('utf-8', errors='replace').decode('utf-8'))
