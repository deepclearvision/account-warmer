#!/usr/bin/env python3
"""Run GPS flow on any phone — text-based finding across devices."""
import sys, time, re, csv, subprocess, xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(r'C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced') / 'account-warmer'))
from core.geelark_client import GeelarKClient, _post

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'acc_010'

csv_path = Path(r'C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\data\accounts_business_mapping.csv')
with open(csv_path, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
row = next(r for r in rows if r['account_id'] == ACCOUNT)

PHONE = row['geelark_phone_id']
EMAIL = row['account_email']
BIZ = row['business_name']
lat = float(row['business_lat'])
lng = float(row['business_lng'])
GPS = 'com.theappninjas.fakegpsjoystick'
WAIT = 8

print('=== %s: %s ===' % (ACCOUNT, EMAIL))
print('Phone: %s' % PHONE)
print('Business: %s' % BIZ)
print('Target: %s, %s' % (lat, lng))

def sh(cmd):
    r = _post('/open/v1/shell/execute', {'id': PHONE, 'cmd': cmd})
    return r.get('output','') or ''

def tap(x, y):
    _post('/open/v1/shell/execute', {'id': PHONE, 'cmd': 'input tap %d %d' % (x, y)})

def get_focus():
    return sh('dumpsys window | grep mCurrentFocus').strip()

def dump_ui(tag):
    path = '/sdcard/%s_dump.xml' % tag
    _post('/open/v1/shell/execute', {'id': PHONE, 'cmd': 'uiautomator dump %s' % path})
    time.sleep(1.5)
    r = _post('/open/v1/shell/execute', {'id': PHONE, 'cmd': 'cat %s' % path})
    return r.get('output','')

def find_and_tap(xml_str, query, step_label):
    q = query.lower()
    best = None
    best_score = 999
    if not xml_str.startswith('<'): return False
    root = ET.fromstring(xml_str)
    for node in root.iter('node'):
        t = node.get('text','').strip().lower()
        cd = node.get('content-desc','').strip().lower()
        if t == q or cd == q: score = 0
        elif t.startswith(q) or cd.startswith(q): score = 1
        elif q in t or q in cd: score = 2
        else: continue
        if score < best_score:
            best_score = score
            bounds = node.get('bounds','')
            try:
                x1,y1,x2,y2 = map(int, bounds.replace('[','').replace(']',',').rstrip(',').split(','))
                best = ((x1+x2)//2, (y1+y2)//2)
            except: pass
    if best:
        print('  Tapped "%s" at %s' % (query, best))
        tap(best[0], best[1])
        return True
    else:
        print('  "%s" NOT FOUND' % query)
        root2 = ET.fromstring(xml_str)
        vis = []
        for node in root2.iter('node'):
            t = node.get('text','').strip()
            if t: vis.append(t)
            cd = node.get('content-desc','').strip()
            if cd and cd != t: vis.append(cd)
        print('    Visible: %s' % (vis[:20],))
        return False

# Stop acc_005 and acc_006 if running (keep things clean)
print()
print('=== Stopping other phones ===')
client = GeelarKClient()
for old_id in ['614216769833271363', '614216773910135154']:
    try:
        client.stop_phone(old_id)
        print('  Stopped %s' % old_id)
    except:
        pass

# Start phone
print()
print('=== Starting phone ===')
live_url = client.start_phone(PHONE)
print('Live URL: %s' % (live_url or 'none',))

if live_url:
    redirect_path = Path(__file__).parent / '_live_view_redirect.html'
    redirect_path.write_text('<meta http-equiv="refresh" content="0; url=%s">' % live_url, encoding='utf-8')
    subprocess.Popen(['cmd', '/c', 'start', '', 'chrome', '--new-window', str(redirect_path)], shell=False)
    print('Browser opened.')
    time.sleep(5)

deadline = time.time() + 120
while time.time() < deadline:
    time.sleep(5)
    try:
        statuses = client.get_phone_status([PHONE])
        s = statuses[0].get('status', -1) if statuses else -1
        remaining = int(deadline - time.time())
        print('  Poll: status=%s (%ss remaining)' % (s, remaining))
        if s == 0:
            print('Phone running!')
            break
    except Exception as e:
        print('  Poll error: %s' % e)

# ============================================================
print()
print('=== STEP 1: Close all apps ===')
for p in ['com.google.android.apps.maps', GPS, 'com.android.vending', 'com.google.android.gms', 'com.android.chrome']:
    sh('am force-stop %s' % p)
    time.sleep(0.3)
time.sleep(1)
sh('input keyevent KEYCODE_HOME')
time.sleep(0.5)
sh('input keyevent KEYCODE_APP_SWITCH')
time.sleep(0.5)
sh('input swipe 360 600 360 1200 300')
time.sleep(0.5)
sh('input keyevent KEYCODE_HOME')
time.sleep(WAIT)
print('[OK] Step 1')

print()
print('=== STEP 2: Clear GPS app storage ===')
out = sh('pm clear %s' % GPS)
print('  pm clear: %s' % out.strip())
time.sleep(1)
sh('am force-stop %s' % GPS)
time.sleep(WAIT)
print('[OK] Step 2')

print()
print('=== STEP 3: Grant permissions ===')
sh('pm grant %s android.permission.ACCESS_FINE_LOCATION' % GPS)
sh('pm grant %s android.permission.ACCESS_COARSE_LOCATION' % GPS)
sh('pm grant %s android.permission.POST_NOTIFICATIONS' % GPS)
sh('appops set %s MOCK_LOCATION allow' % GPS)
sh('appops set %s SYSTEM_ALERT_WINDOW allow' % GPS)
sh('settings put secure mock_location_app %s' % GPS)
sh('settings put secure mock_location 1')
sh('settings put secure location_mode 3')
time.sleep(WAIT)
print('[OK] Step 3')

# STEP 4: Open App - CRITICAL: force-stop first to kill stale process, then monkey
print()
print('=== STEP 4: Open App ===')
sh('am force-stop %s' % GPS)
time.sleep(2)
sh('monkey -p %s -c android.intent.category.LAUNCHER 1' % GPS)
time.sleep(5)
focus = get_focus()
print('  Focus: %s' % focus[:150])
print('[OK] Step 4')

print()
print('=== STEP 5: Privacy screen ===')
if 'PrivacyActivity' in focus:
    xml = dump_ui('privacy')
    if not find_and_tap(xml, 'ACCEPT', 'Privacy-ACCEPT'):
        find_and_tap(xml, 'Accept', 'Privacy-Accept')
    time.sleep(WAIT)
    print('[OK] Step 5')
else:
    print('[OK] Step 5 - No privacy')

print()
print('=== STEP 6: Start Using GPS JoyStick ===')
xml = dump_ui('step6')
find_and_tap(xml, 'Start Using GPS JoyStick', 'Step 6')
time.sleep(WAIT)

print()
print('=== STEP 6b: Update dialog ===')
xml = dump_ui('update')
vis = []
if xml.startswith('<'):
    for node in ET.fromstring(xml).iter('node'):
        t = node.get('text','').strip()
        if t: vis.append(t)
if 'CANCEL' in vis or 'DOWNLOAD' in vis:
    print('  Update dialog - tapping CANCEL')
    find_and_tap(xml, 'CANCEL', 'Cancel')
    time.sleep(WAIT)
else:
    print('  No update. Visible: %s' % (vis[:15],))

print()
print("=== STEP 6c: What's New ===")
xml = dump_ui('whatsnew')
vis = []
if xml.startswith('<'):
    for node in ET.fromstring(xml).iter('node'):
        t = node.get('text','').strip()
        if t: vis.append(t)
if 'Done' in vis:
    print("  What's New - tapping Done")
    find_and_tap(xml, 'Done', 'Done')
    time.sleep(WAIT)
else:
    print("  No What's New. Visible: %s" % (vis[:15],))

print()
print('=== STEP 7: Deep-links ===')
url = 'gpsjoystick://teleport?lat=%s&lng=%s' % (lat, lng)
print('  %s' % url)
deep_link_sent_at = datetime.now().isoformat()
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
time.sleep(5)
sh('input keyevent KEYCODE_HOME')
time.sleep(3)
print('  Re-sending from home...')
sh("am start -a android.intent.action.VIEW -d '%s' %s" % (url, GPS))
time.sleep(8)
sh('input keyevent KEYCODE_HOME')
time.sleep(WAIT)
print('[OK] Step 7')

print()
print('=== STEP 8: Home ===')
sh('input keyevent KEYCODE_HOME')
time.sleep(WAIT)
print('[OK] Step 8')

print()
print('=== STEP 9: Bulletproof GPS Check (fresh + persistent + coords) ===')
out = sh('dumpsys location')

# Parse mock events and coordinates
add_events = []
remove_events = []
found_coords = ''
found_age = ''
for line in out.splitlines():
    m_ts = re.match(r'^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)', line)
    ts = m_ts.group(1) if m_ts else ''
    if 'added mock provider override' in line.lower():
        add_events.append(ts)
    if 'removed mock provider override' in line.lower():
        remove_events.append(ts)
    if 'mock' in line.lower() and 'Location[' in line:
        m = re.search(r'(-?\d+\.\d+),(-?\d+\.\d+).*?et=([^\s,\]]+)', line)
        if m:
            found_coords = '%s,%s' % (m.group(1), m.group(2))
            found_age = m.group(3)

# Check 1: Coordinates match
coords_ok = False
if found_coords:
    try:
        clat, clng = found_coords.split(',')
        coords_ok = abs(float(clat) - lat) <= 0.0002 and abs(float(clng) - lng) <= 0.0002
    except:
        pass

# Check 2: Persistent (last ADD after last REMOVE)
persistent = False
if add_events and not remove_events:
    persistent = True
elif add_events and remove_events:
    persistent = add_events[-1] > remove_events[-1]

# Check 3: Fresh (ADD event after our deep link timestamp)
fresh = False
if add_events and deep_link_sent_at:
    try:
        after_dt = deep_link_sent_at.split('T')
        after_mmdd = after_dt[0][5:]  # '06-12'
        after_time = after_dt[1][:8]  # '19:41:00'
        for add_ts in add_events:
            if add_ts >= '%s %s' % (after_mmdd, after_time):
                fresh = True
                break
    except:
        pass

print('  Coords:    %s (match=%s, age=%s)' % (found_coords, coords_ok, found_age))
print('  Adds: %d  Removes: %d' % (len(add_events), len(remove_events)))
print('  Last ADD:  %s' % (add_events[-1] if add_events else 'NONE'))
print('  Last REM:  %s' % (remove_events[-1] if remove_events else 'NONE'))
print('  Persistent: %s' % persistent)
print('  Fresh:     %s' % fresh)

all_ok = coords_ok and persistent and fresh
if all_ok:
    print('\n[OK] GPS MOCK ACTIVE, FRESH, and PERSISTENT on %s!' % ACCOUNT)
elif not fresh:
    print('\n[FAIL] GPS data is STALE (not activated this session)')
elif not persistent:
    print('\n[FAIL] Mock NOT persistent (added then removed)')
elif not coords_ok:
    print('\n[FAIL] Wrong coordinates')
print('DONE - phone left running')
