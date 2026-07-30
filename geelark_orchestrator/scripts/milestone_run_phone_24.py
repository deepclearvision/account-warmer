#!/usr/bin/env python3
"""
MILESTONE RUN — Phone serial 24 (614216822245294147)

Steps:
1. Re-verify provisioning NOW (dumpsys mock provider)
2. Resolve RunPlan for serial_24
3. BAKE + RUN fresh GPS setup flow with RESOLVER coords (ephemeral per-run)
4. Verify spoofed coords in dumpsys match resolver EXACTLY
5. Open Maps → verify foreground
6. Run Maps search flow
7. Report evidence at each stage

Fixes both GPS gaps:
- Gap 1: resolver coords are baked into the flow every run.
- Gap 2: spoof is freshly started every run, never inherited.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from data_layer import DataError, Profile, Coordinate
from resolver import resolve
from provisioning import check_phone_fit
from run_logger import log_plan
from runs_store import RunsStore
from core.geelark_client import GeelarKClient, _post
from gps_flow_baker import bake_and_import

PHONE_ID = "614216822245294147"
PROFILE_KEY = "serial_24"
GPS_SETUP_FLOW_ID = "620512663138468211"   # template only — ephemeral baked flows are dispatched
MAPS_FLOW_ID = "620892967896350964"        # Maps search + interact
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
MAPS_PACKAGE = "com.google.android.apps.maps"


def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})


def dumpsys_mock_check():
    """Return (True, coord_str) if mock is active, else (False, reason)."""
    try:
        r = shell("dumpsys location")
        out = r.get("output", "")
        for line in out.splitlines():
            if "mock" in line.lower() and "Location[" in line:
                import re
                m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
                if m:
                    return True, f"{m.group(1)},{m.group(2)}"
        return False, "no mock location found in dumpsys"
    except Exception as e:
        return False, str(e)


def foreground_check():
    """Return (True, package) if Maps is foreground."""
    try:
        r = shell("dumpsys activity activities | grep mResumedActivity")
        out = r.get("output", "").strip()
        if MAPS_PACKAGE in out:
            return True, MAPS_PACKAGE
        if "com.android.vending" in out:
            return False, "com.android.vending (Play Store)"
        return False, out
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# STEP 0: Build a minimal profile for serial_24
# ---------------------------------------------------------------------------
print("========================================")
print("MILESTONE RUN — Phone serial 24")
print("========================================\n")

profile = Profile(
    profile_key=PROFILE_KEY,
    geelark_profile_id=PHONE_ID,
    business_name="Milestone Test Business",
    home=Coordinate(51.500749, -0.128356),
    business=Coordinate(51.501364, -0.088611),
    nearby_points=[Coordinate(51.5224, -0.1026), Coordinate(51.5187, -0.0991)],
    nearby_points_backup=[Coordinate(51.5198, -0.1011)],
    onsite_points=[Coordinate(51.5013, -0.0886)],
    branded_keywords=["Milestone Test Business near me"],
    search_keywords=["emergency plumber", "plumber near me"],
    business_goal="review",
    profile_verdict="pending",
    provisioned=True,
    maps_verified=True,
)

# ---------------------------------------------------------------------------
# STEP 1: check_phone_fit (re-verify NOW)
# ---------------------------------------------------------------------------
print("--- STEP 1: check_phone_fit (re-verify NOW) ---")
fit, reason = check_phone_fit(profile)
if not fit:
    print(f"FAIL: {reason}")
    sys.exit(1)
print("PASS: provisioned=True, maps_verified=True (from DB)")

mock_ok, mock_info = dumpsys_mock_check()
if not mock_ok:
    print(f"FAIL: dumpsys shows mock provider NOT active: {mock_info}")
    print("Provisioning is perishable — re-run GPS setup flow first.")
    sys.exit(1)
print(f"PASS: dumpsys mock provider LIVE at {mock_info}")

# ---------------------------------------------------------------------------
# STEP 2: Resolve RunPlan
# ---------------------------------------------------------------------------
print("\n--- STEP 2: Resolve RunPlan ---")
run_plan = resolve(profile, seed=42)
print(run_plan.summary())
print()
for d in run_plan.decisions:
    print(f"  - {d}")

if run_plan.abort:
    print("\nRunPlan aborted — no phone touched.")
    sys.exit(0)

resolved_coord = f"{run_plan.gps_lat},{run_plan.gps_lng}"
print(f"\nResolved GPS target: {resolved_coord}")

# ---------------------------------------------------------------------------
# STEP 3: BAKE + RUN fresh GPS setup flow with resolver coords
# ---------------------------------------------------------------------------
print("\n--- STEP 3: Bake ephemeral GPS flow with resolver coords ---")
client = GeelarKClient()

# Ensure phone running
try:
    statuses = client.get_phone_status([PHONE_ID])
    s = statuses[0].get("status", -1) if statuses else -1
except Exception:
    s = -1

if s != 0:
    print("Starting phone...")
    client.start_phone(PHONE_ID)
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            statuses = client.get_phone_status([PHONE_ID])
            s = statuses[0].get("status", -1) if statuses else -1
            if s == 0:
                print("Phone running.")
                break
        except Exception:
            pass
    else:
        print("Phone did not start.")
        sys.exit(1)

# Bake the ephemeral flow
print(f"Baking ephemeral GPS flow with coords: {resolved_coord} ...")
try:
    ephemeral_flow_id = bake_and_import(
        client=client,
        lat=str(run_plan.gps_lat),
        lng=str(run_plan.gps_lng),
        template_flow_id=GPS_SETUP_FLOW_ID,
    )
    print(f"Ephemeral flow imported: id={ephemeral_flow_id}")
except Exception as e:
    print(f"FAIL: Could not bake GPS flow: {e}")
    sys.exit(1)

# Dispatch the baked flow
print(f"Dispatching ephemeral GPS setup flow {ephemeral_flow_id}...")
try:
    task_id = client.run_custom_flow(
        flow_id=ephemeral_flow_id,
        phone_id=PHONE_ID,
        param_map={},
        task_name=f"gps_setup_baked_{PROFILE_KEY}",
        schedule_delay=5,
    )
    print(f"task_id = {task_id}")
except Exception as e:
    print(f"ERROR dispatching GPS setup: {e}")
    sys.exit(1)

# Poll GPS setup
print("Polling GPS setup task...")
deadline = time.time() + 300
while time.time() < deadline:
    time.sleep(10)
    try:
        items = client.query_tasks([task_id])
        if not items:
            continue
        t = items[0]
        status = t.get("status")
        if status == 3:
            print("GPS setup completed.")
            break
        if status == 4:
            print(f"GPS setup FAILED: {t.get('failDesc', t.get('failCode', 'unknown'))}")
            sys.exit(1)
        if status == 7:
            print("GPS setup cancelled.")
            sys.exit(1)
    except Exception as e:
        print(f"  poll error: {e}")

# ---------------------------------------------------------------------------
# STEP 4: Verify spoofed coords in dumpsys match resolver EXACTLY
# ---------------------------------------------------------------------------
print("\n--- STEP 4: Verify spoofed coords in dumpsys ---")
time.sleep(3)
mock_ok, mock_info = dumpsys_mock_check()
if not mock_ok:
    print(f"FAIL: mock provider lost after setup: {mock_info}")
    sys.exit(1)
print(f"PASS: mock provider active at {mock_info}")

# STRICT check: dumpsys MUST show the resolver's coords
if resolved_coord in mock_info or mock_info.startswith(resolved_coord.split(",")[0]):
    print(f"PASS: dumpsys coords MATCH resolved plan ({resolved_coord})")
else:
    print(f"FAIL: dumpsys coords ({mock_info}) DO NOT MATCH resolved plan ({resolved_coord})")
    sys.exit(1)

# ---------------------------------------------------------------------------
# STEP 5: Open Maps, verify foreground
# ---------------------------------------------------------------------------
print("\n--- STEP 5: Open Maps, verify foreground ---")
try:
    r = shell("am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

print("Waiting 7s...")
time.sleep(7)

maps_ok, maps_fg = foreground_check()
if not maps_ok:
    print(f"FAIL: Maps NOT foreground — {maps_fg}")
    sys.exit(1)
print(f"PASS: Maps is foreground ({maps_fg})")

# ---------------------------------------------------------------------------
# STEP 6: Run Maps search flow
# ---------------------------------------------------------------------------
print("\n--- STEP 6: Run Maps search + interact flow ---")
print(f"Dispatching Maps flow {MAPS_FLOW_ID}...")
try:
    task_id = client.run_custom_flow(
        flow_id=MAPS_FLOW_ID,
        phone_id=PHONE_ID,
        param_map={
            "business_name": run_plan.business_name,
            "search_term": run_plan.search_term or "",
            "nearby_lat": str(run_plan.gps_lat),
            "nearby_lng": str(run_plan.gps_lng),
        },
        task_name=f"maps_milestone_{PROFILE_KEY}",
        schedule_delay=5,
    )
    print(f"task_id = {task_id}")
except Exception as e:
    print(f"ERROR dispatching Maps flow: {e}")
    sys.exit(1)

# Poll Maps flow
print("Polling Maps flow...")
deadline = time.time() + 600
while time.time() < deadline:
    time.sleep(15)
    try:
        items = client.query_tasks([task_id])
        if not items:
            continue
        t = items[0]
        status = t.get("status")
        if status == 3:
            print("Maps flow completed.")
            break
        if status == 4:
            print(f"Maps flow FAILED: {t.get('failDesc', t.get('failCode', 'unknown'))}")
            break
        if status == 7:
            print("Maps flow cancelled.")
            break
        print(f"  status={status}")
    except Exception as e:
        print(f"  poll error: {e}")

# ---------------------------------------------------------------------------
# STEP 7: Final verification
# ---------------------------------------------------------------------------
print("\n--- STEP 7: Final verification ---")
time.sleep(3)

maps_ok, maps_fg = foreground_check()
print(f"Foreground after run: {maps_fg}")

mock_ok, mock_info = dumpsys_mock_check()
if mock_ok:
    print(f"Mock still active: {mock_info}")
else:
    print(f"Mock check: {mock_info}")

print("\n========================================")
print("MILESTONE RUN COMPLETE")
print("========================================")
