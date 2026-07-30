import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
import json

client = GeelarKClient()

# Export the GPS setup flow
FLOW_ID = "620512663138468211"

print(f"--- Exporting flow {FLOW_ID} ---")
try:
    gal_json = client.export_rpa_flow(FLOW_ID)
    data = json.loads(gal_json)

    # Save full pretty-printed JSON
    out_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\gps_flow_full.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved full flow to: {out_path}")

    # Search for the hardcoded coords
    gal_str = json.dumps(data)
    print(f"\nTotal chars: {len(gal_str)}")

    # Find coord-like strings
    import re
    coords = re.findall(r"-?\d+\.\d{4,}", gal_str)
    print(f"\nCandidate coordinates in flow:")
    for c in set(coords):
        print(f"  {c}")

except Exception as e:
    print(f"ERROR: {e}")
