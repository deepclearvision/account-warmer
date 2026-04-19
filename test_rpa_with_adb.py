"""
Test: Open Settings via ADB first, then submit a minimal RPA flow to click elements.
This tests whether RPA element interaction works when an app is open via ADB.
Run from: account-warmer/
"""
import os, sys, yaml, time
from pathlib import Path

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import _shell
from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
acc = data['accounts'][0]
phone_id = acc['geelark_phone_id']

# Build a test flow: no openApp, just click elements
# If Settings is already open (via ADB), these should work
fb = FlowBuilder("RPA Click Test", "Test click on ADB-opened Settings", timeout_minutes=2, error_type="skip")
fb.wait(2000, "Brief initial wait")
fb.click("text", "Network & internet", 5000, remark="Click Network section in Settings")
fb.wait(3000, "Wait after Network click")
fb.go_back("Go back to Settings main")
fb.wait(2000, "Wait after back")
fb.click("text", "Display", 5000, remark="Click Display in Settings")
fb.wait(3000, "Final wait")

flow_id = fb.save()
print('Test flow:', flow_id)

# Start phone
client.start_phone(phone_id)
for i in range(20):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print('Boot after %ds' % ((i+1)*5))
        break
time.sleep(3)

# Open Settings via ADB
print()
print('=== Opening Settings via ADB ===')
ok, out = _shell(phone_id, 'am start -n com.android.settings/.Settings')
print('Result:', ok, out[:100])
time.sleep(2)

# Take screenshot of Settings
shot_dir = Path(r'C:\WarmingData\screenshots\rpa_adb_test')
shot_dir.mkdir(parents=True, exist_ok=True)
shot = client.take_screenshot(phone_id)
if shot:
    p = shot_dir / 'settings_before_rpa.png'
    with open(p, 'wb') as f:
        f.write(shot)
    print('Before-RPA screenshot saved')

ok, focus = _shell(phone_id, 'dumpsys window | grep mCurrentFocus')
print('Focus before RPA:', focus.strip()[-60:] if focus else '?')

# Submit RPA flow immediately (Settings should still be showing)
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=phone_id,
    param_map={},
    task_name='RPA+ADB test',
)
print()
print('=== RPA flow submitted:', task_id)

# Wait for In Progress
print('Waiting for In Progress...')
for _ in range(30):
    time.sleep(3)
    tasks = client.query_tasks([task_id])
    if tasks and tasks[0].get('status') == 2:
        print('In Progress! Checking if Settings navigated...')
        break
    if tasks and tasks[0].get('status') in (3,4,7):
        print('Ended:', tasks[0].get('status'))
        break

# Take screenshots during execution
for i in range(6):
    time.sleep(2)
    tasks = client.query_tasks([task_id])
    status_code = tasks[0].get('status') if tasks else 0
    status = {1:'Waiting', 2:'InProgress', 3:'Completed', 4:'Failed', 7:'Cancelled'}.get(status_code, str(status_code))

    shot = client.take_screenshot(phone_id)
    ok, focus = _shell(phone_id, 'dumpsys window | grep mCurrentFocus')
    focus_str = focus.strip()[-70:] if focus else '?'

    if shot:
        p = shot_dir / ('shot_%02d_%s.png' % (i+1, status))
        with open(p, 'wb') as f:
            f.write(shot)
        print('[%ds] %s | Focus: %s' % ((i+1)*2, status, focus_str))
    else:
        print('[%ds] %s | No screenshot | Focus: %s' % ((i+1)*2, status, focus_str))

    if status in ('Completed', 'Failed', 'Cancelled'):
        break

client.stop_phone(phone_id)
print()
print('Screenshots:', shot_dir)
print()
print('INTERPRETATION:')
print('  If focus changed to Settings activity -> RPA click worked!')
print('  If focus stayed on launcher -> RPA cannot interact with ADB-opened apps')
