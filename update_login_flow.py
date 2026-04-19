"""
update_login_flow.py — Update GeelarK login flow to Settings-based account addition.

Replaces the Chrome-based login flow (ID 613514717851287846) with a new flow
that uses Android's Settings Add Account wizard, which:
  1. Uses native Android widgets (not Chrome WebView) — uiautomator can find fields
  2. Registers the account in Android's AccountManager (required for Maps/Gmail/YouTube)

Run from: account-warmer/
Usage: python ../update_login_flow.py
"""

import os
from pathlib import Path

# Load warmer.env
_env = Path("warmer.env")
for line in _env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from core.geelark_flow_builder import flows

EXISTING_FLOW_ID = "613514717851287846"

print("Updating GeelarK login flow to Settings-based account addition ...")
print(f"  Flow ID: {EXISTING_FLOW_ID}")
print()

new_id = flows.google_add_account_via_settings(flow_id=EXISTING_FLOW_ID)
print(f"Flow updated successfully. ID: {new_id}")
print()
print("All accounts with geelark_login_flow_id=613514717851287846 will")
print("automatically use the new flow on next login attempt.")
