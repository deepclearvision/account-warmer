import sys, subprocess, re, json, time, os
sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator"

RUNS = [
    (rf"{SCRIPT_DIR}\goal_a2_galaxya51.csv", "marcinbagerman", "614216843351031875", "Galaxy A51"),
    (rf"{SCRIPT_DIR}\goal_a2_s17pro.csv", "azariahtrevino9987", "614216867258564675", "S17 Pro"),
    (rf"{SCRIPT_DIR}\goal_a2_galaxya22.csv", "priscillagiles9987", "614216871117324355", "Galaxy A22"),
    (rf"{SCRIPT_DIR}\goal_a2_y77.csv", "crystalwiggins9533", "614216863500468594", "Y77"),
]

results = []

for idx, (csv, account, pid, model) in enumerate(RUNS, 1):
    print(f"\n{'='*70}")
    print(f"[{idx}/4] {model} | {account} ({pid})")
    print(f"{'='*70}")

    cmd = [
        sys.executable,
        rf"{SCRIPT_DIR}\scripts\run_one_phone.py",
        csv,
        account,
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=env, cwd=SCRIPT_DIR)
    out, _ = proc.communicate(timeout=900)

    # Parse
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
    }

    print(f"\n  Status: {result['status']} | {result['duration']}s | IP={result['interaction_ip']}")
    print(f"  GPS: verified={result['gps_verified']} | coords={result['gps_coords']}")
    print(f"  Maps: search={result['search_submitted']} | business={result['business_found']} | card={result['card_opened']}")
    print(f"  Interactions: {result['interactions']}")
    if result['abort_reason']:
        print(f"  Abort: {result['abort_reason']}")

    results.append(result)

    if idx < 4:
        print(f"\n  --- Waiting 30s before next phone ---")
        time.sleep(30)

# Final report
print(f"\n{'='*70}")
print("BATCH COMPLETE — 4 PHONES")
print(f"{'='*70}")
for r in results:
    print(f"\n[{r['idx']}] {r['model']} | {r['account']}")
    print(f"    Status: {r['status']} | {r['duration']}s | IP={r['interaction_ip']}")
    print(f"    GPS: {r['gps_coords']}")
    print(f"    Maps: search={r['search_submitted']} business={r['business_found']} card={r['card_opened']}")
    print(f"    Interactions: {r['interactions']}")
    if r.get('abort_reason'):
        print(f"    Abort: {r['abort_reason']}")

out_path = rf"{SCRIPT_DIR}\scripts\tmp_batch_4_phones_results.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {out_path}")
