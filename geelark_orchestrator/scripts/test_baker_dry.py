#!/usr/bin/env python3
"""Dry-run test of the GPS flow baker — exports template, bakes coords, validates JSON."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from gps_flow_baker import bake_and_import, _get_template, HARDCODED_COORDS
import json

client = GeelarKClient()

print("--- 1. Export template ---")
template = _get_template(client)
print(f"Template title: {template.get('title')}")
print(f"Template has {len(str(template))} chars")

print("\n--- 2. Verify hardcoded coords present ---")
gal_str = json.dumps(template, ensure_ascii=False)
assert HARDCODED_COORDS in gal_str, "Hardcoded coords not found!"
print(f"Found '{HARDCODED_COORDS}' in template: OK")

print("\n--- 3. Bake with test coords ---")
test_lat, test_lng = "51.5013", "-0.0886"
baked_coords = f"{test_lat}, {test_lng}"
baked_str = gal_str.replace(HARDCODED_COORDS, baked_coords)
assert HARDCODED_COORDS not in baked_str, "Old coords still present!"
assert baked_coords in baked_str, "New coords not baked in!"
print(f"Replaced with '{baked_coords}': OK")

# Parse and verify title
parsed = json.loads(baked_str)
print(f"Baked title: {parsed.get('title')}")
print(f"Baked desc: {parsed.get('desc')}")

print("\n--- 4. Find inputContent step with baked coords ---")
def find_input_steps(obj, path=""):
    if isinstance(obj, dict):
        if obj.get("type") == "inputContent":
            content = obj.get("config", {}).get("content", [])
            if any(test_lat in str(c) for c in content):
                print(f"  Found at path {path}: {content}")
                return True
        for k, v in obj.items():
            if find_input_steps(v, f"{path}.{k}"):
                return True
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if find_input_steps(item, f"{path}[{i}]"):
                return True
    return False

found = find_input_steps(parsed)
assert found, "Baked coords not found in any inputContent step!"

print("\n--- ALL DRY-RUN CHECKS PASSED ---")
print("Ready to import ephemeral flow and dispatch on phone.")
