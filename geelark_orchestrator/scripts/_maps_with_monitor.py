#!/usr/bin/env python3
"""
Run new-maps2 with a shell monitor that detects when the business page opens
and cancels the flow task — preventing random clicking after success.

Usage: python _maps_with_monitor.py <phone_id> <business_name> <label>
"""
import sys, time, json, re
from pathlib import Path

PHONE = sys.argv[1]
BUSINESS = sys.argv[2]
LABEL = sys.argv[3] if len(sys.argv) > 3 else "unknown"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient, _post

client = GeelarKClient()

def sh(cmd):
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": cmd})
    return (r.get("output", "") or "")

def dump_texts():
    """Dump UI and return all visible text strings."""
    _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "uiautomator dump /sdcard/monitor_dump.xml"})
    time.sleep(1.5)
    r = _post("/open/v1/shell/execute", {"id": PHONE, "cmd": "cat /sdcard/monitor_dump.xml"})
    xml = (r.get("output", "") or "")
    texts = re.findall(r'text="([^"]*)"', xml)
    return texts

# ── Business detail page indicators ──
# These text elements appear when we're on a business's detail page in Google Maps
BUSINESS_DETAIL_MARKERS = [
    "Directions",
    "Call",
    "Website",
    "Reviews",
    "Overview",
    "Save",
    "Share",
]

def is_business_page(texts):
    """Return True if the visible texts look like a business detail page."""
    if not texts:
        return False
    joined = " ".join(texts)
    # Must have the business name somewhere
    has_business = BUSINESS.lower() in joined.lower()
    if not has_business:
        # Also check partial (first 2 words of business name)
        words = BUSINESS.split()
        if len(words) >= 2:
            partial = " ".join(words[:2]).lower()
            has_business = partial in joined.lower()
    if not has_business:
        return False
    # Must have at least 2 of the business-detail markers
    markers_found = sum(1 for m in BUSINESS_DETAIL_MARKERS if m.lower() in joined.lower())
    return markers_found >= 2

# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("MAPS WITH MONITOR: %s" % LABEL)
print("Phone: %s | Business: %s" % (PHONE, BUSINESS))
print("=" * 60)

# ── Step 1: Build fresh new-maps2 flow ──
print("\n--- Building flow ---")
gal = client.export_rpa_flow('624431313889263826')
data = json.loads(gal)

# Inject business name into click step
for step in data['content']['contents']:
    if step['type'] == 'forTimes':
        for child in step['config']['children']:
            if child['type'] == 'click':
                for fc in child['config'].get('filterCollection', []):
                    for f in fc:
                        if f.get('type') == 'text':
                            f['content'] = BUSINESS
                            f['filterType'] = 'contains'
                            print("  Injected business name into click filter")

# Reduce loop from 8 to 4 (less chance of random clicking)
for step in data['content']['contents']:
    if step['type'] == 'forTimes':
        step['config']['times'] = '4'
        print("  Loop reduced to 4 iterations")

new_id = client.import_rpa_flow(json.dumps(data))
print("  Flow ID: %s" % new_id)

# ── Step 2: Start the flow ──
print("\n--- Starting flow ---")
task_id = client.run_custom_flow(
    flow_id=new_id,
    phone_id=PHONE,
    param_map={},
    task_name="Maps monitor — %s" % LABEL
)
print("  Task ID: %s" % task_id)

# ── Step 3: Monitor loop ──
print("\n--- Monitoring (max 180s) ---")
monitor_deadline = time.time() + 180
found_business = False

while time.time() < monitor_deadline:
    # Check flow status
    tasks = client.query_tasks([task_id])
    flow_status = tasks[0].get('status') if tasks else -1
    sm = {1: 'Waiting', 2: 'InProgress', 3: 'Completed', 4: 'Failed', 7: 'Cancelled'}
    flow_st = sm.get(flow_status, str(flow_status))

    if flow_st in ('Completed', 'Failed', 'Cancelled'):
        print("  Flow ended: %s" % flow_st)
        break

    # Dump screen and check
    try:
        texts = dump_texts()
        elapsed = int(time.time() - (monitor_deadline - 180))
        if is_business_page(texts):
            found_business = True
            print("  [t+%ds] BUSINESS PAGE DETECTED!" % elapsed)
            # Show what we see
            relevant = [t for t in texts if t.strip()]
            print("  Visible: %s" % (", ".join(relevant[:12])))
            break
        else:
            # Show progress
            top_texts = [t for t in texts if t.strip()][:8]
            print("  [t+%ds] Scanning... flow=%s screen=%s" % (
                elapsed, flow_st, ", ".join(top_texts[:5])
            ))
    except Exception as e:
        print("  [t+%ds] Poll error: %s" % (elapsed, e))

    time.sleep(3)

# ── Step 4: Cancel the flow if business found ──
if found_business:
    print("\n--- Business found — cancelling flow ---")
    try:
        cancel_r = _post("/open/v1/task/cancel", {"taskId": task_id})
        print("  Cancelled: %s" % cancel_r.get("msg", "ok"))
    except Exception as e:
        print("  Cancel error: %s" % e)
    time.sleep(1)
    # Clean up: press Back to leave business page, then Home
    print("  Pressing Back...")
    sh("input keyevent KEYCODE_BACK")
    time.sleep(1.5)
    sh("input keyevent KEYCODE_BACK")
    time.sleep(0.5)
    sh("input keyevent KEYCODE_HOME")
    print("[OK] Cleaned up")
else:
    print("\n--- Business not detected in time ---")
    print("  Flow may have completed or timed out without finding business")

print("=" * 60)
print("DONE")
