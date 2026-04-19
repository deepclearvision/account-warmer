"""
Test whether openApp works at all on this phone.
Creates a minimal flow: just openApp(Chrome) + wait + screenshot.
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

# Build a minimal test flow: open Chrome, wait 5s, open Settings, wait 5s
fb = FlowBuilder("openApp Test", "Test if openApp works", timeout_minutes=2, error_type="skip")
fb.open_app("com.android.chrome", remark="Open Chrome")
fb.wait(5000, "Wait after Chrome open")
fb.open_app("com.android.settings", remark="Open Settings")
fb.wait(5000, "Wait after Settings open")

flow_id = fb.save()
print('Test flow created:', flow_id)

# Start phone
print('Starting phone...')
client.start_phone(phone_id)
for i in range(20):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print('Boot after %ds' % ((i+1)*5))
        break
time.sleep(3)

# Submit test flow
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=phone_id,
    param_map={},
    task_name='openApp test',
)
print('Task:', task_id)

# Wait for In Progress, then take screenshots
print('Waiting for In Progress...')
for _ in range(30):
    time.sleep(3)
    tasks = client.query_tasks([task_id])
    if tasks and tasks[0].get('status') == 2:
        print('In Progress!')
        break
    if tasks and tasks[0].get('status') in (3,4,7):
        print('Ended:', tasks[0].get('status'))
        break

shot_dir = Path(r'C:\WarmingData\screenshots\openapp_test')
shot_dir.mkdir(parents=True, exist_ok=True)

for i in range(6):
    time.sleep(2)
    tasks = client.query_tasks([task_id])
    status = {1:'Waiting', 2:'InProgress', 3:'Completed', 4:'Failed', 7:'Cancelled'}.get(
        tasks[0].get('status') if tasks else 0, 'Unknown')

    shot = client.take_screenshot(phone_id)
    ok, focus = _shell(phone_id, 'dumpsys window | grep mCurrentFocus')
    focus_str = focus.strip()[-60:] if focus else '?'

    if shot:
        p = shot_dir / ('shot_%02d_%s.png' % (i+1, status))
        with open(p, 'wb') as f:
            f.write(shot)
        print('[%ds] %s | Focus: %s | %d bytes' % ((i+1)*2, status, focus_str, len(shot)))
    else:
        print('[%ds] %s | Focus: %s | no screenshot' % ((i+1)*2, status, focus_str))

    if status in ('Completed', 'Failed', 'Cancelled'):
        break

client.stop_phone(phone_id)
print('Done. Screenshots in:', shot_dir)
