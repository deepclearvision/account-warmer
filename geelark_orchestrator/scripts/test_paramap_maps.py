#!/usr/bin/env python3
"""Test if paramMap substitution works in Maps flow inputContent and click filters."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
import json

client = GeelarKClient()

# Export the Maps flow
FLOW_ID = "620892967896350964"
print(f"--- Exporting Maps flow {FLOW_ID} ---")
gal = client.export_rpa_flow(FLOW_ID)
data = json.loads(gal)

# Check if there are any {variable} references
gal_str = json.dumps(data, ensure_ascii=False)
import re
vars_found = re.findall(r"\{[a-zA-Z0-9_]+\}", gal_str)
print(f"\nExisting param variables: {sorted(set(vars_found))}")

# Check step types for param support
print("\n--- Step types that might use params ---")
for i, step in enumerate(data.get("content", {}).get("contents", [])):
    stype = step.get("type")
    if stype in ("inputContent", "inputText", "click"):
        config = step.get("config", {})
        content = config.get("content", [])
        filters = config.get("filters", [])
        filter_collection = config.get("filterCollection", [])
        print(f"\nStep {i}: {step.get('name', 'unnamed')} ({stype})")
        if content:
            print(f"  content: {content}")
        if filters:
            print(f"  filters: {filters}")
        if filter_collection:
            print(f"  filterCollection: {filter_collection[:2]}...")  # first 2
