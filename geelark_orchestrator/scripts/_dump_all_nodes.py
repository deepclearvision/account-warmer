#!/usr/bin/env python3
"""Dump all UI nodes with full attributes. Usage: python _dump_all_nodes.py <phone_id>"""
import sys, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return r.get("output", "") or ""

xml = sh("uiautomator dump /sdcard/step6_full.xml && cat /sdcard/step6_full.xml")

# Skip the status line
xml_start = xml.find("<?xml")
if xml_start > 0:
    xml = xml[xml_start:]

root = ET.fromstring(xml)

print("=== ALL NODES (index, class, text, desc, clickable, bounds, resource-id) ===")
for i, node in enumerate(root.iter("node")):
    cls = node.get("class", "")
    short_cls = cls.split(".")[-1] if "." in cls else cls
    txt = (node.get("text", "") or "")[:40]
    cd = (node.get("content-desc", "") or "")[:40]
    bounds = node.get("bounds", "")
    click = node.get("clickable", "")
    enabled = node.get("enabled", "")
    rid = node.get("resource-id", "") or ""
    print("[{}] {} | text=[{}] desc=[{}] click={} enabled={} bounds={} id=[{}]".format(
        i, short_cls, txt, cd, click, enabled, bounds, rid[:50]))
