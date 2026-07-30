#!/usr/bin/env python3
"""Test importing an ephemeral baked GPS flow."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from gps_flow_baker import bake_and_import

client = GeelarKClient()

print("--- Importing ephemeral GPS flow with baked coords ---")
try:
    flow_id = bake_and_import(client, lat="51.5013", lng="-0.0886")
    print(f"SUCCESS: ephemeral flow id = {flow_id}")
except Exception as e:
    print(f"FAIL: {e}")
