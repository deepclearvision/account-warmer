#!/usr/bin/env python3
"""Verify the imported ephemeral flow contains the baked coords."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
import json

EPHEMERAL_FLOW_ID = "621086866879283336"

client = GeelarKClient()
print(f"--- Exporting ephemeral flow {EPHEMERAL_FLOW_ID} ---")
gal = client.export_rpa_flow(EPHEMERAL_FLOW_ID)
data = json.loads(gal)

print(f"Title: {data.get('title')}")
print(f"Desc: {data.get('desc')}")

# Find inputContent steps
def find_inputs(obj, path=""):
    if isinstance(obj, dict):
        if obj.get("type") == "inputContent":
            content = obj.get("config", {}).get("content", [])
            print(f"\n  inputContent at {path}: {content}")
        for k, v in obj.items():
            find_inputs(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            find_inputs(item, f"{path}[{i}]")

find_inputs(data)

# Check coords
gal_str = json.dumps(data, ensure_ascii=False)
if "51.5013, -0.0886" in gal_str:
    print("\nPASS: Baked coords found in imported flow.")
else:
    print("\nFAIL: Baked coords NOT found in imported flow.")
