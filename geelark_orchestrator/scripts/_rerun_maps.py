#!/usr/bin/env python3
"""Re-run new-maps1 workflow on a phone that's already running."""
import sys, time
from pathlib import Path

PHONE = sys.argv[1]
LABEL = sys.argv[2] if len(sys.argv) > 2 else "unknown"

sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
from core.geelark_client import GeelarKClient

c = GeelarKClient()

task_id = c.run_custom_flow(
    flow_id='623992235725160754',
    phone_id=PHONE,
    param_map={},
    task_name=f'new-maps1 — {LABEL}'
)
print(f'Task ID: {task_id}')

for i in range(18):
    time.sleep(10)
    tasks = c.query_tasks([task_id])
    for t in tasks:
        sm = {1:'Waiting', 2:'InProgress', 3:'Completed', 4:'Failed', 7:'Cancelled'}
        st = sm.get(t.get('status'), str(t.get('status')))
        print(f't+{(i+1)*10}s: {st}')
        if st in ('Completed','Failed','Cancelled'):
            import sys
            sys.exit(0)
