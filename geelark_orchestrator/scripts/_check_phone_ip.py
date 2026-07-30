#!/usr/bin/env python3
"""Check phone IP via shell"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import _post

PHONE = sys.argv[1] if len(sys.argv) > 1 else '614216835079864690'

# Try curl first, then wget, then whatismyip
for cmd in ['curl -s --max-time 10 https://ifconfig.me', 'curl -s --max-time 10 https://api.ipify.org']:
    out = _post('/open/v1/shell/execute', {'id': PHONE, 'cmd': cmd})
    result = (out.get('output', '') or '').strip()
    if result and not 'not found' in result.lower():
        print(f'Phone IP ({cmd[:20]}...): {result}')
        break
else:
    print('Could not get phone IP')
