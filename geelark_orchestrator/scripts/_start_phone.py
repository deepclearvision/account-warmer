#!/usr/bin/env python3
"""Start a phone and open live view. Usage: python _start_phone.py <phone_id>"""
import sys, time, subprocess
from pathlib import Path

PHONE = sys.argv[1]

sys.path.insert(0, str(Path(r'C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer')))
from core.geelark_client import GeelarKClient

client = GeelarKClient()
print(f'=== Starting phone {PHONE} ===')
live_url = client.start_phone(PHONE)
print(f'Live URL: {live_url}')

if live_url:
    redirect_path = Path(__file__).parent / '_live_view_redirect.html'
    redirect_path.write_text(f'<meta http-equiv="refresh" content="0; url={live_url}">', encoding='utf-8')
    subprocess.Popen(['cmd', '/c', 'start', '', 'chrome', '--new-window', str(redirect_path)], shell=False)
    print('Live view opened in new window.')
    time.sleep(3)

deadline = time.time() + 120
while time.time() < deadline:
    time.sleep(5)
    try:
        statuses = client.get_phone_status([PHONE])
        s = statuses[0].get('status', -1) if statuses else -1
        remaining = int(deadline - time.time())
        print(f'  status={s} ({remaining}s left)')
        if s == 0:
            print('READY.')
            break
    except Exception as e:
        print(f'  poll error: {e}')
