"""
quick_phone_status.py — Check GeelarK phone status for all accounts.
"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
_env = Path(__file__).parent / "warmer.env"
if _env.exists():
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from core.geelark_client import GeelarKClient
from core.account_store import get_account_store

accounts = get_account_store().get_mobile_accounts()
phone_ids = [a["geelark_phone_id"] for a in accounts if a.get("geelark_phone_id")]

client = GeelarKClient()
print(f"Checking {len(phone_ids)} phones …\n")

for pid in phone_ids:
    try:
        statuses = client.get_phone_status([pid])
        s = statuses[0] if statuses else {}
        st = s.get("status", -1)
        status_map = {0: "Running", 1: "Starting", 2: "Stopped", 3: "Expired/Stopping"}
        print(f"  {pid}: {status_map.get(st, f'status={st}')}  name={s.get('name','')}")
    except Exception as e:
        print(f"  {pid}: ERROR — {e}")

# Also check GeelarK balance
print("\nChecking GeelarK balance …")
try:
    from core.geelark_client import _post
    r = _post("/open/v1/balance", {})
    print(f"  Balance response: {r}")
except Exception as e:
    print(f"  Balance check failed: {e}")
