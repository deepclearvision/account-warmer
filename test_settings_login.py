"""
Test the new Settings-based login on a single account.
Run from: account-warmer/
"""
import os, sys, logging, yaml
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
    stream=sys.stdout
)

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])

# Pick first account with all required fields
acc = next(
    (a for a in accounts
     if a.get('geelark_phone_id')
     and a.get('geelark_login_flow_id')
     and a.get('password')
     and a.get('totp_secret')),
    None
)
if not acc:
    print('ERROR: No qualifying account found')
    sys.exit(1)

print('=== Testing Settings-based login for: %s ===' % acc.get('email'))
print('  phone_id: %s' % acc.get('geelark_phone_id'))
print('  flow_id:  %s' % acc.get('geelark_login_flow_id'))
print()

from activities.google_login_mobile import run_google_login
result = run_google_login(acc, stop_phone_on_success=True)

print()
print('=== RESULT ===')
for k, v in result.items():
    if k != 'last_screenshot_b64':
        print('  %s: %s' % (k, v))
