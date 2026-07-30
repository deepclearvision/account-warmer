import sys, subprocess, re, json, time
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post
from proxy_control import change_proxy_ip

SCRIPT_DIR = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator"

RUNS = [
    (rf"{SCRIPT_DIR}\goal_a1_s23_1.csv", "sandeephassamatta", "614216835079864690", "Galaxy S23 Ultra"),
    (rf"{SCRIPT_DIR}\goal_a1_s22.csv", "jacobsiesta38", "614216798086103107", "Galaxy S22 Ultra"),
    (rf"{SCRIPT_DIR}\goal_a1_s20_reconfirm.csv", "williambrown314809", "614216773910135154", "Galaxy S20 Ultra"),
    (rf"{SCRIPT_DIR}\goal_a1_s23_2.csv", "bradyjazeel", "614216786023284803", "Galaxy S23 Ultra"),
]

client = GeelarKClient()

# Quick balance check — try to start first phone briefly
print("=== BALANCE CHECK: attempting to start first phone ===")
_, _, pid_test, _ = RUNS[0]
try:
    client.start_phone(pid_test)
    print(f"  Phone {pid_test} started OK — balance appears sufficient.")
except Exception as e:
    print(f"  START FAILED: {e}")
    print("\n*** ABORTING BATCH — Geelark balance still insufficient ***")
    sys.exit(1)

# Stop the test-start phone immediately to keep state clean
try:
    client.stop_phone(pid_test)
    print("  Test-start phone stopped (state reset).")
    time.sleep(5)
except Exception:
    pass

results = []

for idx, (csv, account, pid, model) in enumerate(RUNS, 1):
    print(f"\n{'='*70}")
    print(f"[{idx}/4] {model} | {account} ({pid})")
    print(f"{'='*70}")

    # Step 1: Rotate proxy
    print("\n[Step 1] Rotating proxy...")
    try:
        expected_ip = change_proxy_ip()
        print(f"  New proxy IP: {expected_ip}")
    except Exception as e:
        print(f"  PROXY ROTATION FAILED: {e}")
        results.append({"idx": idx, "model": model, "account": account, "phone_id": pid,
                        "status": "ABORTED", "reason": f"proxy rotation failed: {e}"})
        break

    # Step 2: Ensure phone running
    print("\n[Step 2] Starting phone...")
    try:
        client.start_phone(pid)
        print("  Start command sent.")
    except Exception as e:
        print(f"  START FAILED: {e}")
        results.append({"idx": idx, "model": model, "account": account, "phone_id": pid,
                        "status": "ABORTED", "reason": f"phone start failed: {e}"})
        break
    print("  Waiting 60s for boot...")
    time.sleep(60)

    # Step 3: Run full end-to-end via run_one_phone.py
    print("\n[Step 3] Running full Maps interaction...")
    cmd = [
        sys.executable,
        rf"{SCRIPT_DIR}\scripts\run_one_phone.py",
        csv,
        account,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    out, _ = proc.communicate(timeout=600)

    # Parse key fields
    run_id = re.search(r'run_id\s*:\s*([\w-]+)', out)
    status_match = re.search(r'"status":\s*"(\w+)"', out)
    duration = re.search(r'"duration_seconds":\s*(\d+)', out)
    gps_verified = re.search(r'"gps_verified":\s*(\d)', out)
    gps_coords = re.search(r'"gps_dumpsys_coords":\s*"([^"]+)"', out)
    search = re.search(r'"search_submitted":\s*(true|false)', out)
    business = re.search(r'"business_found":\s*(true|false)', out)
    card = re.search(r'"card_opened":\s*(true|false)', out)
    interactions = re.search(r'"interactions_present":\s*(\[[^\]]*\])', out)
    ip = re.search(r'"interaction_ip":\s*"([^"]+)"', out)
    abort_reason = re.search(r'"abort_reason":\s*"([^"]*)"', out)

    result = {
        "idx": idx,
        "model": model,
        "account": account,
        "phone_id": pid,
        "run_id": run_id.group(1) if run_id else "N/A",
        "status": status_match.group(1) if status_match else "unknown",
        "duration": int(duration.group(1)) if duration else 0,
        "gps_verified": int(gps_verified.group(1)) if gps_verified else 0,
        "gps_coords": gps_coords.group(1) if gps_coords else "N/A",
        "search_submitted": search.group(1) == "true" if search else False,
        "business_found": business.group(1) == "true" if business else False,
        "card_opened": card.group(1) == "true" if card else False,
        "interactions": interactions.group(1) if interactions else "[]",
        "interaction_ip": ip.group(1) if ip else "N/A",
        "abort_reason": abort_reason.group(1) if abort_reason else None,
        "raw_output": out,
    }

    print(f"\n  Orchestrator status: {result['status']} | duration={result['duration']}s")
    print(f"  GPS: verified={result['gps_verified']} | coords={result['gps_coords']}")
    print(f"  Maps: search={result['search_submitted']} | business={result['business_found']} | card={result['card_opened']}")
    print(f"  Interactions: {result['interactions']}")
    print(f"  IP: {result['interaction_ip']}")
    if result['abort_reason']:
        print(f"  Abort reason: {result['abort_reason']}")

    # Step 4: Final dumpsys mock check
    print("\n[Step 4] Final dumpsys mock check:")
    try:
        r = _post('/open/v1/shell/execute', {'id': pid, 'cmd': 'dumpsys location | grep mock'})
        d_out = r.get('output', '').strip()
        m = re.search(r'Location\[gps\s+(-?\d+\.\d+),(-?\d+\.\d+)', d_out)
        if m:
            mock_str = f"{m.group(1)},{m.group(2)}"
            print(f"  Mock still active: {mock_str}")
            result["final_mock"] = mock_str
        else:
            print("  Mock INACTIVE (no Location[gps] in dumpsys)")
            result["final_mock"] = "inactive"
    except Exception as e:
        print(f"  Error checking mock: {e}")
        result["final_mock"] = f"error: {e}"

    results.append(result)

    # Step 5: Stop phone
    print("\n[Step 5] Stopping phone...")
    try:
        client.stop_phone(pid)
        print("  Phone stopped.")
    except Exception as e:
        print(f"  Stop warning: {e}")

    # Pacing between phones (30s) — respects ~15 min window across 4 phones
    if idx < 4:
        print(f"\n  --- Waiting 30s before next phone ---")
        time.sleep(30)

# Final report
print(f"\n{'='*70}")
print("SEQUENTIAL BATCH COMPLETE")
print(f"{'='*70}")
for r in results:
    print(f"\n[{r['idx']}] {r['model']} | {r['account']}")
    print(f"    Status: {r['status']} | {r['duration']}s | IP={r['interaction_ip']}")
    print(f"    GPS: {r['gps_coords']} | Mock end: {r['final_mock']}")
    print(f"    Maps: search={r['search_submitted']} business={r['business_found']} card={r['card_opened']}")
    print(f"    Interactions: {r['interactions']}")
    if r.get('abort_reason'):
        print(f"    Abort: {r['abort_reason']}")

out_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\tmp_samsung_batch_v2_results.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved results to {out_path}")
