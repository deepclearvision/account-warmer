#!/usr/bin/env python3
"""Scroll down setup wizard and find button. Usage: python _scroll_and_find.py <phone_id>"""
import sys, time, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = sys.argv[1]
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return r.get("output", "") or ""

def tap(x, y):
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "input tap %d %d" % (x, y)})

def dump_all(tag):
    xml = sh("uiautomator dump /sdcard/%s.xml && cat /sdcard/%s.xml" % (tag, tag))
    start = xml.find("<?xml")
    if start > 0:
        xml = xml[start:]
    return xml

def safe_print(s):
    try:
        print(s)
    except:
        print(s.encode("ascii", errors="replace").decode("ascii"))

def find_clickable_by_text(xml_str, queries):
    """Find a clickable node whose sibling/child text matches any query."""
    if not xml_str.startswith("<"): return None
    root = ET.fromstring(xml_str)
    nodes = list(root.iter("node"))
    # First find text nodes matching our query
    for i, node in enumerate(nodes):
        txt = (node.get("text", "") or "").lower()
        cd = (node.get("content-desc", "") or "").lower()
        for q in queries:
            ql = q.lower()
            if ql == txt or ql == cd or ql in txt or ql in cd:
                # Found matching text — look for nearby clickable
                # Check siblings (nodes at same depth nearby)
                # Or check parent's children for clickable
                bounds = node.get("bounds", "")
                safe_print("  Found text '{}' matching '{}' at {}".format(txt[:50], q, bounds))
                # Look for clickable nodes nearby (within same parent group)
                for j in range(max(0,i-5), min(len(nodes), i+5)):
                    if nodes[j].get("clickable") == "true":
                        cb = nodes[j].get("bounds", "")
                        safe_print("    Nearby clickable: class={} bounds={}".format(nodes[j].get("class","")[-40:], cb))
                        try:
                            coords = cb.replace("[", "").replace("]", ",").rstrip(",").split(",")
                            x1, y1, x2, y2 = map(int, coords)
                            return ((x1+x2)//2, (y1+y2)//2)
                        except:
                            pass
    return None

# Get screen size
size_out = sh("wm size")
import re
m = re.search(r"(\d+)x(\d+)", size_out)
w, h = int(m.group(1)), int(m.group(2)) if m else (720, 1440)
safe_print("Screen: {}x{}".format(w, h))

# First dump current state
safe_print("\n=== BEFORE SCROLL ===")
xml = dump_all("before_scroll")
for node in ET.fromstring(xml).iter("node"):
    txt = (node.get("text", "") or "").strip()
    cd = (node.get("content-desc", "") or "").strip()
    click = node.get("clickable", "")
    bounds = node.get("bounds", "")
    if txt or cd or click == "true":
        safe_print("  text=[{}] desc=[{}] click={} bounds={}".format(txt[:60], cd[:40], click, bounds))

# Scroll down the SetupWizard scroll area
safe_print("\n=== SCROLLING ===")
# ScrollView is at [0,48][720,1344]. Scroll from middle upward.
scroll_x = w // 2
scroll_y_start = int(h * 0.75)
scroll_y_end = int(h * 0.30)
sh("input swipe %d %d %d %d 500" % (scroll_x, scroll_y_start, scroll_x, scroll_y_end))
time.sleep(2)
# Scroll more if needed
sh("input swipe %d %d %d %d 500" % (scroll_x, scroll_y_start, scroll_x, scroll_y_end))
time.sleep(2)
safe_print("Scrolled down")

# Dump after scroll
safe_print("\n=== AFTER SCROLL ===")
xml = dump_all("after_scroll")
for node in ET.fromstring(xml).iter("node"):
    txt = (node.get("text", "") or "").strip()
    cd = (node.get("content-desc", "") or "").strip()
    click = node.get("clickable", "")
    bounds = node.get("bounds", "")
    if txt or cd or click == "true":
        safe_print("  text=[{}] desc=[{}] click={} bounds={}".format(txt[:60], cd[:40], click, bounds))

# Now try to find and tap "Start Using GPS JoyStick"
safe_print("\n=== FINDING BUTTON ===")
pos = find_clickable_by_text(xml, ["Start Using GPS JoyStick", "Start", "GPS JoyStick", "Continue", "Done", "Finish", "Get Started"])
if pos:
    safe_print("Tapping at {}".format(pos))
    tap(pos[0], pos[1])
    time.sleep(3)
else:
    safe_print("No matching button found")
