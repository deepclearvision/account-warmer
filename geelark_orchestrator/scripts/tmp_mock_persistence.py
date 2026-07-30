import time
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

pid = '614216890796998723'
for i in range(6):
    time.sleep(5)
    r = _post('/open/v1/shell/execute', {'id': pid, 'cmd': 'dumpsys location'})
    out = r.get('output', '')
    mock = [l.strip() for l in out.splitlines() if 'mock' in l.lower() and 'Location[' in l.lower()]
    status = mock[0] if mock else 'NO MOCK'
    print(f'T+{i*5}s: {status}')
