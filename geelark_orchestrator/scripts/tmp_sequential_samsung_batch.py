import sys, json, re, time
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

RUNS = [
    ("goal_a1_s23_1.csv", "sandeephassamatta", "614216835079864690", "Galaxy S23 Ultra"),
    ("goal_a1_s22.csv", "jacobsiesta38", "614216798086103107", "Galaxy S22 Ultra"),
    ("goal_a1_s20_reconfirm.csv", "williambrown314809", "614216773910135154", "Galaxy S20 Ultra"),
    ("goal_a1_s23_2.csv", "bradyjazeel", "614216786023284803", "Galaxy S23 Ultra"),
]

results = []

for idx, (csv, account, pid, model) in enumerate(RUNS, 1):
    print(f"\n{'='*70}")
    print(f"[{idx}/4] Running: {model} | {account} ({pid})")
    print(f"{'='*70}")

    # Run the orchestrator
    import subprocess
    cmd = [
        sys.executable,
        r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\run_one_phone.py",
        csv,
        account,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    out, _ = proc.communicate(timeout=300)

    # Extract key fields from feedback packets
    run_id = re.search(r'run_id\s*:\s*([\w-]+)', out)
    status = re.search(r'"status":\s*"(\w+)"', out)
    duration = re.search(r'"duration_seconds":\s*(\d+)', out)
    gps_verified = re.search(r'"gps_verified":\s*(\d)', out)
    gps_coords = re.search(r'"gps_dumpsys_coords":\s*"([^"]+)"', out)
    search = re.search(r'"search_submitted":\s*(true|false)', out)
    business = re.search(r'"business_found":\s*(true|false)', out)
    card = re.search(r'"card_opened":\s*(true|false)', out)
    interactions = re.search(r'"interactions_present":\s*(\[[^\]]*\])', out)
    ip = re.search(r'"interaction_ip":\s*"([^"]+)"', out)

    result = {
        "idx": idx,
        "model": model,
        "account": account,
        "phone_id": pid,
        "run_id": run_id.group(1) if run_id else "N/A",
        "status": status.group(1) if status else "unknown",
        "duration": int(duration.group(1)) if duration else 0,
        "gps_verified": int(gps_verified.group(1)) if gps_verified else 0,
        "gps_coords": gps_coords.group(1) if gps_coords else "N/A",
        "search_submitted": search.group(1) == "true" if search else False,
        "business_found": business.group(1) == "true" if business else False,
        "card_opened": card.group(1) == "true" if card else False,
        "interactions": interactions.group(1) if interactions else "[]",
        "interaction_ip": ip.group(1) if ip else "N/A",
    }
    results.append(result)

    # Print summary
    print(f"\n  Result: {result['status']} | duration={result['duration']}s")
    print(f"  GPS: verified={result['gps_verified']} | coords={result['gps_coords']}")
    print(f"  Maps: search={result['search_submitted']} | business={result['business_found']} | card={result['card_opened']}")
    print(f"  Interactions: {result['interactions']}")
    print(f"  IP: {result['interaction_ip']}")

    # Final dumpsys mock check
    print(f"\n  Final dumpsys mock check:")
    try:
        r = _post('/open/v1/shell/execute', {'id': pid, 'cmd': 'dumpsys location | grep mock'})
        d_out = r.get('output', '').strip()
        # Extract coords from mock line
        m = re.search(r'Location\[gps\s+(-?\d+\.\d+),(-?\d+\.\d+)', d_out)
        if m:
            print(f"    Mock still active: {m.group(1)},{m.group(2)}")
            result["final_mock"] = f"{m.group(1)},{m.group(2)}"
        else:
            print(f"    (mock inactive or no output)")
            result["final_mock"] = "inactive"
    except Exception as e:
        print(f"    Error: {e}")
        result["final_mock"] = f"error: {e}"

    # Respect pacing between phones
    if idx < 4:
        print(f"\n  --- Waiting 30s before next phone ---")
        time.sleep(30)

# Print overall report
print(f"\n{'='*70}")
print("SEQUENTIAL BATCH COMPLETE — ALL 4 PHONES")
print(f"{'='*70}")
for r in results:
    print(f"\n[{r['idx']}] {r['model']} | {r['account']}")
    print(f"    Status: {r['status']} | {r['duration']}s | IP={r['interaction_ip']}")
    print(f"    GPS: {r['gps_coords']} | Mock end: {r['final_mock']}")
    print(f"    Maps: search={r['search_submitted']} business={r['business_found']} card={r['card_opened']}")
    print(f"    Interactions: {r['interactions']}")

# Save JSON
out_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\tmp_samsung_batch_results.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved results to {out_path}")
