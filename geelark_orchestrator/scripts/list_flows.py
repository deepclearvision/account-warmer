import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
import json

client = GeelarKClient()

print("--- Listing RPA flows ---")
flows = client.list_rpa_flows(page=1, page_size=50)
print(f"Total flows: {len(flows)}\n")

for f in flows:
    fid = f.get("id", "")
    title = f.get("title", "")
    params = f.get("params", [])
    print(f"ID: {fid}")
    print(f"  Title: {title}")
    print(f"  Params: {params}")
    print()
