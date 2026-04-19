import os, yaml, re
from pathlib import Path
_env = Path("warmer.env")
for line in _env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
from activities.google_login_mobile import _shell
with open(r"C:\WarmingData\geelark_accounts.yaml", encoding="utf-8") as f:
    data = yaml.safe_load(f)
acc = next(a for a in data["accounts"] if a.get("id") == "acc_032")
phone_id = acc["geelark_phone_id"]
email = acc["email"]
ok, out = _shell(phone_id, "dumpsys account")
if email.lower() in (out or "").lower():
    print(f"FOUND: {email} IS in AccountManager!")
else:
    print(f"NOT FOUND: {email}")
    accounts = re.findall(r"Account \{[^}]+\}", out or "")
    if accounts:
        for a in accounts[:5]:
            print(" ", a)
    else:
        print("No accounts found at all")
