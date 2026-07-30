#!/usr/bin/env python3
"""Minimal GPS test: deep-link + START button tap. Single phone."""
import json, random, re, sys, time, urllib.parse
from datetime import datetime
from pathlib import Path

_repo = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
sys.path.insert(0, str(_repo / "geelark_orchestrator" / "src"))
sys.path.insert(0, str(_repo / "geelark_orchestrator"))
sys.path.insert(0, str(_repo / "account-warmer"))

from core.geelark_client import GeelarKClient, _post

FAKE_GPS = "com.theappninjas.fakegpsjoystick"
PHONE_ID = "614216903581237315"
BUSINESS = "Southwark Plumbers"
LAT, LNG = "51.48943", "-0.078572"

def log(msg: str):
    print(msg)

client = GeelarKClient()

# 1. Cleanup
running = [p for p in client.list_phones(page_size=100) if p.get("status")==0]
for p in running:
    try: client.stop_phone(p["id"])
    except: pass
time.sleep(5)

# 2. Start phone
log(f"Starting {PHONE_ID}...")
client.start_phone(PHONE_ID)
deadline = time.time()+360
while time.time()<deadline:
    time.sleep(5)
    s = client.get_phone_status([PHONE_ID])[0].get("status")
    if s==0:
        log("Phone running.")
        break
else:
    log("FAILED to start")
    sys.exit(1)

# 3. Minimal GPS: OverlayService + deep-link + START tap
log("Starting OverlayService...")
_post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":f"am startservice -n {FAKE_GPS}/.service.OverlayService"})
time.sleep(3)

log(f"Sending deep-link gpsjoystick://teleport?lat={LAT}&lng={LNG} ...")
_post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":f"am start -a android.intent.action.VIEW -d 'gpsjoystick://teleport?lat={LAT}&lng={LNG}' {FAKE_GPS}"})
time.sleep(5)

# 4. Dismiss any dialog
_post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"uiautomator dump /sdcard/v3_dialog.xml"})
time.sleep(1)
r = _post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"cat /sdcard/v3_dialog.xml"})
xml = r.get("output","")
for text in ("Continue to app","Got it","OK","Allow","Accept","Dismiss"):
    if text.lower() in xml.lower():
        log(f"  Dismissing dialog: {text}")
        _post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"input tap 360 900"})
        time.sleep(2)
        break

# 5. Tap START button (via UI dump)
_post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"uiautomator dump /sdcard/v3_start.xml"})
time.sleep(1)
r = _post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"cat /sdcard/v3_start.xml"})
xml = r.get("output","")

start_pos = None
for text in ("START","Start","start"):
    # find text in xml and extract approximate bounds center
    if f'text="{text}"' in xml or f'text="{text.upper()}"' in xml or f'content-desc="{text}"' in xml:
        # rough center of screen as fallback
        start_pos = (360, 900)
        log(f"  Found START button text '{text}' in XML")
        break

if start_pos:
    log(f"  Tapping START at {start_pos}")
    _post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":f"input tap {start_pos[0]} {start_pos[1]}"})
else:
    log("  START not found in XML, tapping map area fallback")
    _post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"input tap 360 1167"})
time.sleep(5)

# 6. Verify mock
r = _post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"dumpsys location"})
out = r.get("output","")
mock_found = False
mock_coords = ""
for line in out.splitlines():
    if "mock" in line.lower() and "Location[" in line:
        m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
        if m:
            mock_coords = f"{m.group(1)},{m.group(2)}"
            mock_found = True
            break

log(f"Mock verify: found={mock_found} coords={mock_coords}")

# 7. Maps search
enc = urllib.parse.quote(BUSINESS)
geo = f"geo:0,0?q={enc}"
_post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":f"am start -a android.intent.action.VIEW -d '{geo}' com.google.android.apps.maps"})
time.sleep(10)

_post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"uiautomator dump /sdcard/v3_maps.xml"})
time.sleep(2)
r = _post("/open/v1/shell/execute", {"id":PHONE_ID,"cmd":"cat /sdcard/v3_maps.xml"})
maps_xml = r.get("output","")
found = BUSINESS in maps_xml or "Southwark" in maps_xml
log(f"Maps search: {'FOUND' if found else 'NOT_FOUND'}")

# 8. Screenshot
try:
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if data:
        p = _repo/"geelark_orchestrator"/"scripts"/f"v3_screenshot_{PHONE_ID}.png"
        p.write_bytes(data)
        log(f"Screenshot saved: {p}")
except Exception as e:
    log(f"Screenshot failed: {e}")

# 9. Stop
client.stop_phone(PHONE_ID)
log("Phone stopped.")

# 10. Log result
result = {
    "timestamp": datetime.now().isoformat(),
    "phone_id": PHONE_ID,
    "business": BUSINESS,
    "lat": LAT, "lng": LNG,
    "mock_found": mock_found,
    "mock_coords": mock_coords,
    "maps_found": found,
}
log(json.dumps(result, indent=2))
