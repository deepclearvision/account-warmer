import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient

client = GeelarKClient()

print("--- Listing RPA flows ---")
try:
    flows = client.list_rpa_flows(page=1, page_size=50)
    for f in flows:
        fid = f.get("id", "?")
        title = f.get("title", "untitled")
        desc = f.get("desc", "")
        print(f"  {fid}: {title}")
        if desc:
            print(f"    desc: {desc[:80]}")
except Exception as e:
    print(f"ERROR: {e}")
