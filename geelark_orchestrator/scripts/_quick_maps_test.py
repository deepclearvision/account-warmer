#!/usr/bin/env python3
"""Quick Maps search test - re-center button, search, leave phone running."""
import sys, time, re, xml.etree.ElementTree as ET
from pathlib import Path

PHONE = "614216875076747634"
LAT, LNG = 52.545329, -1.375792
KEYWORD = "plumber near me"
BUSINESS = "Collins Plumbing & Heating Specialists"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient, _post
c = GeelarKClient()

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return (r.get("output", "") or "")

def tap(x, y):
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "input tap %d %d" % (x, y)})

def dump_ui(tag):
    path = "/sdcard/quick_%s.xml" % tag
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump %s" % path})
    time.sleep(1.5)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat %s" % path})
    return r.get("output", "")

print("Starting phone...")
c.start_phone(PHONE)
time.sleep(40)

GPS = "com.theappninjas.fakegpsjoystick"
sh("settings put secure mock_location 1")
sh("am start -a android.intent.action.VIEW -d gpsjoystick://teleport?lat=%.6f&lng=%.6f %s" % (LAT, LNG, GPS))
time.sleep(5)
sh("input keyevent KEYCODE_HOME")
time.sleep(3)

print("Opening Maps...")
sh("am force-stop com.google.android.apps.maps")
time.sleep(1)
sh("monkey -p com.google.android.apps.maps -c android.intent.category.LAUNCHER 1")
time.sleep(8)

print("Looking for re-center button...")
xml = dump_ui("maps_start")
found = False
w, h = 720, 1440
try:
    xs = xml.find("<?xml")
    if xs >= 0:
        root = ET.fromstring(xml[xs:])
        for node in root.iter("node"):
            cd = (node.get("content-desc", "") or "").strip().lower()
            if any(w in cd for w in ["current location", "my location", "re-center", "recenter"]):
                bounds = node.get("bounds", "")
                try:
                    x1, y1, x2, y2 = map(int, bounds.replace("[", "").replace("]", ",").rstrip(",").split(","))
                    cx, cy = (x1+x2)//2, (y1+y2)//2
                    print("  Found: %s at (%d, %d)" % (cd, cx, cy))
                    tap(cx, cy)
                    found = True
                    time.sleep(2)
                    break
                except: pass
except: pass
if not found:
    print("  Not found by text, trying proportional...")
    for yp in [0.78, 0.82, 0.75]:
        tap(int(w*0.88), int(h*yp))
        time.sleep(0.5)

print("Tapping search bar...")
time.sleep(2)
xml = dump_ui("pre")
sc = None
for q in ["Search here", "Search Google Maps", "Search"]:
    xs = xml.find("<?xml") if xml else -1
    if xs < 0: continue
    try: root = ET.fromstring(xml[xs:])
    except: continue
    for node in root.iter("node"):
        t = (node.get("text", "") or "").strip().lower()
        cd = (node.get("content-desc", "") or "").strip().lower()
        if q.lower() in t or q.lower() in cd:
            bounds = node.get("bounds", "")
            try:
                x1, y1, x2, y2 = map(int, bounds.replace("[", "").replace("]", ",").rstrip(",").split(","))
                sc = ((x1+x2)//2, (y1+y2)//2)
                break
            except: pass
    if sc: break
if sc:
    tap(sc[0], sc[1])
    print("  Tapped at %s" % (sc,))
else:
    tap(360, 72)
time.sleep(2)

sh("i=0; while [ $i -lt 60 ]; do input keyevent 67; i=$((i+1)); done")
time.sleep(1)

sh("input text '%s'" % KEYWORD.replace("'", "\\'"))
time.sleep(3)
sh("input keyevent 66")
time.sleep(6)

print("Searching...")
for i in range(10):
    xml = dump_ui("s%d" % i)
    texts = re.findall(r"text=\"([^\"]*)\"", xml)
    joined = " ".join(texts)
    query = " ".join(BUSINESS.split()[:2]).lower()
    if query in joined.lower():
        print("  PASS %d: FOUND!" % i)
        xs = xml.find("<?xml")
        if xs >= 0:
            root = ET.fromstring(xml[xs:])
            for node in root.iter("node"):
                t = (node.get("text", "") or "").strip().lower()
                if query in t:
                    bounds = node.get("bounds", "")
                    try:
                        x1, y1, x2, y2 = map(int, bounds.replace("[", "").replace("]", ",").rstrip(",").split(","))
                        tap((x1+x2)//2, (y1+y2)//2)
                        print("  Tapped at %s" % (((x1+x2)//2, (y1+y2)//2),))
                        break
                    except: pass
        break
    else:
        rel = [t for t in texts if t.strip()][:5]
        print("  PASS %d: %s" % (i, rel))
        sh("input swipe %d %d %d %d 500" % (360, 500, 360, 250))
        time.sleep(3)

print("Done - phone left running")
