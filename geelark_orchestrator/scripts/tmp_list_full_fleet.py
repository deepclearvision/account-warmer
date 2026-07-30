import sys, json
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import GeelarKClient

print("=== Fetching full Geelark fleet ===")
client = GeelarKClient()
phones = client.list_phones(page_size=100)
print(f"Total phones in account: {len(phones)}")

# Save full list to file for reference
out_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\tmp_full_fleet.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(phones, f, indent=2)
print(f"Saved full fleet to {out_path}")

# Print summary table
print("\n=== Fleet Summary ===")
for p in phones:
    equip = p.get('equipmentInfo', {})
    print(f"{p.get('id')} | {p.get('serialName','')} | model={equip.get('deviceModel','')} | android={equip.get('osVersion','')} | status={p.get('status','')}")
