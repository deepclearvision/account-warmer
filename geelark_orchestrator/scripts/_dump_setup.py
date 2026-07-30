#!/usr/bin/env python3
"""Dump SetupWizardActivity screen. Usage: python _dump_setup.py <phone_id>"""
import sys, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
OUT = Path(__file__).parent / "_setup_dump_output.txt"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return r.get("output", "") or ""

lines = []

lines.append("=== FOCUS ===")
lines.append(sh("dumpsys window | grep mCurrentFocus"))
lines.append("")

xml = sh("uiautomator dump /sdcard/step6_raw.xml && cat /sdcard/step6_raw.xml")
lines.append(f"=== RAW XML LENGTH: {len(xml)} ===")
lines.append("")

if xml.startswith("<"):
    root = ET.fromstring(xml)
    lines.append("=== WEBVIEW ELEMENTS ===")
    found_webview = False
    for node in root.iter("node"):
        cls = node.get("class", "")
        if "webview" in cls.lower():
            found_webview = True
            bounds = node.get("bounds", "")
            rid = node.get("resource-id", "")
            desc = node.get("content-desc", "") or ""
            lines.append(f"  class={cls} bounds={bounds} id={rid} desc={desc[:100]}")
    if not found_webview:
        lines.append("  (none)")
    lines.append("")

    lines.append("=== ALL ELEMENTS (depth 4) ===")
    def show(node, depth=0):
        if depth > 4:
            return
        cls = node.get("class", "")
        txt = (node.get("text", "") or "")[:60]
        cd = (node.get("content-desc", "") or "")[:60]
        bounds = node.get("bounds", "")
        click = node.get("clickable", "")
        rid = node.get("resource-id", "") or ""
        indent = "  " * depth
        info = f"{indent}{cls}"
        if txt: info += f' text="{txt}"'
        if cd: info += f' desc="{cd}"'
        if click == "true": info += " [CLICKABLE]"
        if rid: info += f" id={rid}"
        if bounds: info += f" {bounds}"
        lines.append(info)
        for child in node:
            show(child, depth + 1)
    show(root)

text = "\n".join(lines)
# Write to file
OUT.write_text(text, encoding="utf-8")
print(f"Output written to: {OUT}")
print(f"Total lines: {len(lines)}")
# Also print safely
for line in lines:
    safe = line.encode("ascii", errors="replace").decode("ascii")
    print(safe)
