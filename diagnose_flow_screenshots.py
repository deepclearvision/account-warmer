"""
Takes screenshots during RPA flow execution to see what's actually on screen.
Run from: account-warmer/
"""
import os, sys, yaml, time, json
from pathlib import Path

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import _shell
from core.geelark_client import GeelarKClient

client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
acc = data['accounts'][0]
phone_id = acc['geelark_phone_id']
email = acc.get('email', '')
password = acc.get('password', '')
totp_secret = acc.get('totp_secret', '')
flow_id = acc.get('geelark_login_flow_id', '')

print('Phone:', phone_id)
print('Flow:', flow_id)

# Start phone
client.start_phone(phone_id)
for i in range(20):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print('Boot after %ds' % ((i+1)*5))
        break
time.sleep(3)

# Submit flow
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=phone_id,
    param_map={'email': email, 'password': password, 'totp_secret': totp_secret},
    task_name='Screenshot diagnostic',
)
print('Task submitted:', task_id)

# Wait for In Progress
print('Waiting for In Progress...')
for _ in range(30):
    time.sleep(3)
    tasks = client.query_tasks([task_id])
    if tasks and tasks[0].get('status') == 2:  # In Progress
        print('Flow is In Progress!')
        break
    if tasks and tasks[0].get('status') in (3, 4, 7):
        print('Flow ended early:', tasks[0].get('status'))
        break

# Take screenshots every 4 seconds for 50 seconds
print()
print('Taking screenshots during execution...')
shot_dir = Path(r'C:\WarmingData\screenshots\flow_run')
shot_dir.mkdir(parents=True, exist_ok=True)

for i in range(12):
    time.sleep(4)
    tasks = client.query_tasks([task_id])
    status = tasks[0].get('status') if tasks else 0
    status_name = {1:'Waiting', 2:'InProgress', 3:'Completed', 4:'Failed', 7:'Cancelled'}.get(status, str(status))

    # Take screenshot
    shot = client.take_screenshot(phone_id)
    if shot:
        shot_path = shot_dir / ('shot_%02d_%s.png' % (i+1, status_name))
        with open(shot_path, 'wb') as f:
            f.write(shot)
        print('  [%ds] Status=%s Screenshot: %s (%d bytes)' % (
            (i+1)*4, status_name, shot_path.name, len(shot)))
    else:
        print('  [%ds] Status=%s No screenshot' % ((i+1)*4, status_name))

    # Also get focus
    ok, focus = _shell(phone_id, 'dumpsys window | grep mCurrentFocus')
    if ok and focus:
        print('         Focus:', focus.strip()[-80:])

    if status in (3, 4, 7):
        print('  Flow finished, stopping screenshot loop')
        break

# Final state
print()
ok, out = _shell(phone_id, 'dumpsys account | grep -A2 "com.google"')
print('Final AccountManager (google):', out[:300] if out else 'empty')

client.stop_phone(phone_id)
print()
print('Done. Screenshots in:', shot_dir)
