#!/usr/bin/env python3
"""Dry-run test of Maps flow baker."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from maps_flow_baker import bake_and_import, _get_template, HARDCODED_GPS, HARDCODED_SEARCH, HARDCODED_BUSINESS
import json

client = GeelarKClient()

print("--- 1. Export template ---")
template = _get_template(client)
print(f"Template title: {template.get('title')}")

gal_str = json.dumps(template, ensure_ascii=False)
assert HARDCODED_GPS in gal_str
assert HARDCODED_SEARCH in gal_str
assert HARDCODED_BUSINESS in gal_str
print("All hardcoded literals found: OK")

print("\n--- 2. Bake with test values ---")
test_lat, test_lng = "51.5013", "-0.0886"
test_search = "emergency plumber"
test_biz = "Milestone Test Business"

flow_id = bake_and_import(
    client=client,
    lat=test_lat,
    lng=test_lng,
    search_term=test_search,
    business_name=test_biz,
)
print(f"SUCCESS: ephemeral flow id = {flow_id}")

print("\n--- 3. Verify imported flow ---")
gal = client.export_rpa_flow(flow_id)
data = json.loads(gal)
print(f"Title: {data.get('title')}")

gal_str2 = json.dumps(data, ensure_ascii=False)
assert HARDCODED_GPS not in gal_str2, "Old GPS coords still present!"
assert HARDCODED_SEARCH not in gal_str2, "Old search term still present!"
assert HARDCODED_BUSINESS not in gal_str2, "Old business name still present!"
assert f"{test_lat}, {test_lng}" in gal_str2, "New GPS coords not baked!"
assert test_search in gal_str2, "New search term not baked!"
assert test_biz in gal_str2, "New business name not baked!"
print("All replacements verified: OK")

# Count business name occurrences
biz_count = gal_str2.count(test_biz)
print(f"Business name appears {biz_count} times (expect 16 for click attempts)")

print("\n--- ALL CHECKS PASSED ---")
