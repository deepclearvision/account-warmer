"""Update the live scheduler state — resets stagger & bumps concurrency to 6."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.scheduler import get_scheduler
from datetime import datetime

svc = get_scheduler()
now = datetime.now().isoformat()

svc.set_max_concurrent(6)

for account_id, entry in svc._state.items():
    entry["next_eligible_after"] = now
    entry.pop("last_run_at", None)
    print(f"  {account_id}: reset stagger")

svc._save_state()
print(f"\nDone — {len(svc._state)} accounts eligible now, max_concurrent={svc.max_concurrent}")
