#!/usr/bin/env python3
"""Export the Test A2 flow JSON to inspect param names."""
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient
import json

FLOW_ID = "620154902076719475"
client = GeelarKClient()

try:
    gal = client.export_rpa_flow(FLOW_ID)
    data = json.loads(gal)
    print(json.dumps(data, indent=2, ensure_ascii=False)[:5000])
except Exception as e:
    print(f"Export failed: {e}")
