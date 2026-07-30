#!/usr/bin/env python3
"""Find and inspect the 'Google Maps - Find and click' flow."""
import sys
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_orchestrator_src = _repo_root / "geelark_orchestrator" / "src"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient

client = GeelarKClient()

print("=== Listing all RPA flows ===")
flows = client.list_rpa_flows(page_size=100)
for f in flows:
    fid = f.get("id", "")
    title = f.get("title", "")
    desc = f.get("desc", "")
    params = f.get("params", [])
    print(f"  ID={fid} | Title='{title}' | Params={params}")
    if "Find and click" in title or "find and click" in title.lower():
        print(f"\n>>> FOUND: {title}")
        print(f"    Exporting flow {fid}...")
        gal = client.export_rpa_flow(fid)
        # Save to file for inspection
        out_path = Path(__file__).parent / f"flow_{fid}_exported.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(gal)
        print(f"    Saved to: {out_path}")
        # Show first 2000 chars
        print(f"    Preview (first 2000 chars):")
        print(gal[:2000])
        break
else:
    print("\nFlow 'Google Maps - Find and click' NOT FOUND in list.")
    print(f"Total flows: {len(flows)}")
