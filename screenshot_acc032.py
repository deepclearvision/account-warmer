import os, yaml, pathlib
from pathlib import Path
_env = Path("warmer.env")
for line in _env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
from core.geelark_client import GeelarKClient
client = GeelarKClient()
with open(r"C:\WarmingData\geelark_accounts.yaml", encoding="utf-8") as f:
    data = yaml.safe_load(f)
acc = next(a for a in data["accounts"] if a.get("id") == "acc_032")
phone_id = acc["geelark_phone_id"]
s = client.take_screenshot(phone_id)
if s:
    p = pathlib.Path(r"C:\WarmingData\screenshots\live_now.png")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(s)
    print("OK", len(s))
else:
    print("NO SCREENSHOT")
