import yaml
with open(r"C:\WarmingData\geelark_accounts.yaml", encoding="utf-8") as f:
    data = yaml.safe_load(f)
accs = [a for a in data["accounts"] if a.get("geelark_phone_id")]
for a in sorted(accs, key=lambda x: x.get("id", "")):
    login = a.get("login_status", "unknown")
    has_totp = bool(a.get("totp_secret", "").strip())
    print(f"{a.get('id','?'):10} {a.get('email','?'):45} status={login:12} totp={'yes' if has_totp else 'NO'}")
print(f"\nTotal: {len(accs)} accounts with phone IDs")
