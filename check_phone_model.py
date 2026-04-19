"""
Check the actual phone model for our accounts.
Run from: account-warmer/
"""
import os, yaml
from pathlib import Path

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import _shell
from core.geelark_client import GeelarKClient, _post

client = GeelarKClient()

with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])

# Get phone IDs for first 5 accounts
phone_ids = [a.get('geelark_phone_id') for a in accounts[:5] if a.get('geelark_phone_id')]
print('Checking phone models for:', phone_ids)
print()

# Use get_phone_status to get model info
status = client.get_phone_status(phone_ids)
for s in status:
    print('Phone:', s.get('id'))
    print('  Status:', s)
    print()

# Also try the phone list/detail API
print()
print('=== Phone list (first 5 accounts) ===')
phones = client.list_phones(page_no=1, page_size=50)
for p in phones[:10]:
    print('  ID:', p.get('id'), 'Brand:', p.get('brand'), 'Model:', p.get('model'),
          'Android:', p.get('androidVer'), 'Name:', p.get('name', '')[:40])

# Get ADB properties for acc_004's phone model
print()
print('=== ADB properties for acc_004 ===')
acc004 = next((a for a in accounts if a.get('id') == 'acc_004' or
               'james' in a.get('email', '').lower()), None)
if acc004:
    phone_id = acc004.get('geelark_phone_id')
    print('Phone ID:', phone_id)

    # Start phone briefly to check properties
    try:
        client.start_phone(phone_id)
        import time
        for i in range(20):
            time.sleep(5)
            ok, out = _shell(phone_id, 'wm size')
            if ok and out.strip():
                print('Boot after', (i+1)*5, 's')
                break

        props = [
            ('Model', 'getprop ro.product.model'),
            ('Brand', 'getprop ro.product.brand'),
            ('Manufacturer', 'getprop ro.product.manufacturer'),
            ('Android', 'getprop ro.build.version.release'),
            ('Build', 'getprop ro.build.id'),
            ('SDK', 'getprop ro.build.version.sdk'),
        ]
        for label, cmd in props:
            ok, val = _shell(phone_id, cmd)
            print('  %s: %s' % (label, val.strip() if ok else 'ERROR'))

        client.stop_phone(phone_id)
    except Exception as e:
        print('Error:', e)
