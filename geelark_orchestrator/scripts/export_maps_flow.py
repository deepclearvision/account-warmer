import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
import json
import re

client = GeelarKClient()

FLOW_ID = "620892967896350964"

print(f"--- Exporting flow {FLOW_ID} ---")
try:
    gal_json = client.export_rpa_flow(FLOW_ID)
    data = json.loads(gal_json)

    # Save full JSON for inspection
    out_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\maps_flow_full.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved to: {out_path}")

    # Find all occurrences of {variable} syntax
    gal_str = json.dumps(data, ensure_ascii=False)
    vars_found = re.findall(r"\{[a-zA-Z0-9_]+\}", gal_str)
    print(f"\nUnique param variables in Maps flow:")
    for v in sorted(set(vars_found)):
        print(f"  {v}")

    # Find inputContent steps with context
    print("\n--- inputContent steps ---")
    def find_steps(obj, path=""):
        if isinstance(obj, dict):
            if obj.get("type") in ("inputContent", "inputText"):
                content = obj.get("config", {}).get("content", [])
                print(f"\nstep: {obj.get('name', 'unnamed')} ({obj.get('type')})")
                print(f"  content: {content}")
                remark = obj.get("config", {}).get("remark", "")
                if remark:
                    print(f"  remark: {remark}")
            for k, v in obj.items():
                find_steps(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                find_steps(item, f"{path}[{i}]")

    find_steps(data)

except Exception as e:
    print(f"ERROR: {e}")
