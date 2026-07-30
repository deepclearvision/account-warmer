#!/usr/bin/env python3
"""Inspect current screen on phone 24."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post
import xml.etree.ElementTree as ET

PHONE_ID = "614216822245294147"

# Dump screen
print("--- Dumping screen ---")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "uiautomator dump /sdcard/window_dump.xml"})
print(f"dump: {r.get('output', '')}")

# Read dump
r2 = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "cat /sdcard/window_dump.xml"})
xml_data = r2.get("output", "")

# Extract all text
texts = []
root = ET.fromstring(xml_data)
for node in root.iter("node"):
    t = node.get("text", "").strip()
    if t and len(t) > 2:
        texts.append(t)

print(f"\n--- Visible text on screen ({len(texts)} items) ---")
for t in texts[:40]:
    print(f"  {t}")
if len(texts) > 40:
    print(f"  ... ({len(texts) - 40} more)")
