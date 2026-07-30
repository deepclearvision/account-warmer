import sys, time, json, re
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post
from proxy_control import check_proxy_ip
from run_one_phone import _get_phone_ip

# Already-known proxy-clean from prior surveys
KNOWN_CLEAN = {
    "614216911852404803", "614216903581237315", "614216899286270322",
    "614216895242960963", "614216890796998723", "614216886971793475",
    "614216822245294147", "614216818470420547",
}

expected_ip = check_proxy_ip()
print(f"=== Proxy IP (from control plane): {expected_ip} ===")

client = GeelarKClient()
phones = client.list_phones(page_size=100)
print(f"Total phones: {len(phones)}")

results = []
for p in phones:
    pid = p.get('id')
    name = p.get('serialName', '')
    equip = p.get('equipmentInfo', {})
    model = equip.get('deviceModel', 'unknown')
    android = equip.get('osVersion', 'unknown')
    status = p.get('status', -1)

    if pid in KNOWN_CLEAN:
        print(f"\n=== {pid} | {name} | {model} | {android} | KNOWN CLEAN — skipping ===")
        results.append({
            "phone_id": pid, "name": name, "model": model,
            "android": android, "status": status, "app_path_ip": "(known clean)",
            "verdict": "KNOWN_CLEAN", "was_running": True,
        })
        continue

    print(f"\n=== {pid} | {name} | {model} | {android} | status={status} ===")
    was_running = status == 0

    # If stopped, try to start it briefly
    if status != 0:
        try:
            print("    Phone stopped — attempting start...")
            url = client.start_phone(pid)
            print(f"    Started OK, waiting 15s for boot...")
            time.sleep(15)
            was_running = True
        except Exception as e:
            print(f"    Start failed: {e}")
            was_running = False
            results.append({
                "phone_id": pid, "name": name, "model": model,
                "android": android, "status": status, "app_path_ip": "N/A",
                "verdict": "START_FAILED", "was_running": False,
            })
            continue

    # Run proxy gate
    try:
        detected = _get_phone_ip(pid)
        ok = detected == expected_ip
        verdict = "CLEAN" if ok else "LEAK"
        print(f"    Result: {verdict} | detected={detected}")
        results.append({
            "phone_id": pid, "name": name, "model": model,
            "android": android, "status": status, "app_path_ip": detected,
            "verdict": verdict, "was_running": was_running,
        })
    except Exception as e:
        print(f"    Gate check error: {e}")
        results.append({
            "phone_id": pid, "name": name, "model": model,
            "android": android, "status": status, "app_path_ip": "ERROR",
            "verdict": "GATE_ERROR", "was_running": was_running,
        })

# Save full results
out_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\tmp_fleet_proxy_survey.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved detailed results to {out_path}")

# Print summary table
print("\n=== SURVEY SUMMARY TABLE ===")
print(f"{'Phone ID':<24} | {'Name':<28} | {'Model':<18} | {'Android':<12} | {'IP':<16} | {'Verdict':<12} | {'Running'}")
print("-" * 140)
clean_count = 0
leak_count = 0
other_count = 0
for r in results:
    if r['verdict'] == 'CLEAN':
        clean_count += 1
    elif r['verdict'] == 'LEAK':
        leak_count += 1
    else:
        other_count += 1
    print(f"{r['phone_id']:<24} | {r['name']:<28} | {r['model']:<18} | {r['android']:<12} | {r['app_path_ip']:<16} | {r['verdict']:<12} | {r['was_running']}")

print("-" * 140)
print(f"CLEAN: {clean_count} | LEAK: {leak_count} | OTHER/KNOWN/START_FAILED: {other_count}")
