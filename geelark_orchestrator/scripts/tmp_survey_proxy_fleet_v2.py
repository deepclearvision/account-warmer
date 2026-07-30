import sys, time, json, re
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post
from proxy_control import change_proxy_ip, check_proxy_ip
from run_one_phone import _get_phone_ip

# Already-known proxy-clean from prior surveys (including the 3 we just proved)
KNOWN_CLEAN = {
    "614216911852404803", "614216903581237315", "614216899286270322",
    "614216895242960963", "614216890796998723", "614216886971793475",
    "614216822245294147", "614216818470420547",
    "614216915744719218", "614216908010422642",
    "614216883146588530",  # Samsung Galaxy S20 Ultra just proven
}

def _shell_is_responsive(phone_id: str) -> bool:
    """Return True if the phone accepts shell commands and returns output."""
    try:
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "echo PONG"})
        out = r.get("output", "").strip()
        return out == "PONG"
    except Exception:
        return False

def _ensure_running_with_retry(client: GeelarKClient, phone_id: str) -> tuple[bool, bool]:
    """
    Ensure phone is running. If stopped, start it and wait up to 45s for boot.
    Then retry shell responsiveness 3 times (15s apart).
    Returns (success, was_already_running).
    """
    try:
        statuses = client.get_phone_status([phone_id])
        s = statuses[0] if statuses else {}
        st = s.get("status", -1)
    except Exception:
        st = -1

    was_running = st == 0
    if st == 0:
        # Already running — still need to verify shell responsiveness
        pass
    else:
        print("    Phone stopped — attempting start...")
        try:
            client.start_phone(phone_id)
            print("    Start command sent.")
        except Exception as e:
            print(f"    Start failed: {e}")
            return False, False
        print("    Waiting 45s for boot...")
        time.sleep(45)
        was_running = True

    # Retry shell responsiveness up to 3 times
    for attempt in range(1, 4):
        if _shell_is_responsive(phone_id):
            print(f"    Shell responsive (attempt {attempt}/3).")
            return True, was_running
        print(f"    Shell not responsive (attempt {attempt}/3), waiting 15s...")
        time.sleep(15)

    print("    Shell never became responsive after retries.")
    return False, was_running


print("=== PROXY SURVEY V2 — Sequential, per-phone proxy rotation ===")
client = GeelarKClient()
phones = client.list_phones(page_size=100)
print(f"Total phones in account: {len(phones)}")

# Filter to UNKNOWN phones only (not in KNOWN_CLEAN)
unknown_phones = []
for p in phones:
    pid = p.get('id')
    if pid not in KNOWN_CLEAN:
        equip = p.get('equipmentInfo', {})
        unknown_phones.append({
            "phone_id": pid,
            "name": p.get('serialName', ''),
            "model": equip.get('deviceModel', 'unknown'),
            "android": equip.get('osVersion', 'unknown'),
            "status": p.get('status', -1),
        })

print(f"Phones to survey: {len(unknown_phones)}")
print()

results = []
clean_count = 0
leak_count = 0
unreachable_count = 0

for idx, phone in enumerate(unknown_phones, 1):
    pid = phone["phone_id"]
    name = phone["name"]
    model = phone["model"]
    android = phone["android"]

    print(f"\n{'='*60}")
    print(f"[{idx}/{len(unknown_phones)}] {pid}")
    print(f"  Account: {name}")
    print(f"  Model:   {model} | {android}")

    # Step 1: Rotate proxy and get new expected IP
    print("  --- Rotating proxy...")
    try:
        expected_ip = change_proxy_ip()
        print(f"  New proxy IP: {expected_ip}")
    except Exception as e:
        print(f"  Proxy rotation FAILED: {e}")
        results.append({**phone, "app_path_ip": "PROXY_ROTATION_FAILED", "verdict": "PROXY_ERROR", "was_running": False})
        unreachable_count += 1
        continue

    # Step 2: Ensure phone running with retry logic
    running_ok, was_running = _ensure_running_with_retry(client, pid)
    if not running_ok:
        results.append({**phone, "app_path_ip": "SHELL_UNRESPONSIVE", "verdict": "UNREACHABLE", "was_running": was_running})
        unreachable_count += 1
        continue

    # Step 3: IP convergence — confirm phone shell IP matches proxy IP
    print("  --- Checking IP convergence (phone shell -> proxy)...")
    phone_ip = _get_phone_ip(pid)
    if phone_ip and phone_ip == expected_ip:
        print(f"  IP converged: {phone_ip}")
    else:
        print(f"  IP mismatch or timeout: phone_ip={phone_ip}, expected={expected_ip}")
        # Not fatal — proceed to app-path check which is the real gate

    # Step 4: Run proxy gate (curl verification)
    print("  --- Running proxy gate (curl)...")
    try:
        detected = _get_phone_ip(pid)
        ok = detected == expected_ip
        if ok:
            print(f"  Result: CLEAN | detected={detected}")
            results.append({**phone, "app_path_ip": detected, "verdict": "CLEAN", "was_running": True})
            clean_count += 1
        else:
            print(f"  Result: LEAK | detected={detected}")
            results.append({**phone, "app_path_ip": detected, "verdict": "LEAK", "was_running": True})
            leak_count += 1
    except Exception as e:
        print(f"  Gate error: {e}")
        results.append({**phone, "app_path_ip": "ERROR", "verdict": "GATE_ERROR", "was_running": True})
        unreachable_count += 1

# Save results
out_path = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\tmp_fleet_proxy_survey_v2.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2)

print(f"\n{'='*60}")
print("SURVEY COMPLETE")
print(f"{'='*60}")
print(f"CLEAN:      {clean_count}")
print(f"LEAK:       {leak_count}")
print(f"UNREACHABLE: {unreachable_count}")
print(f"Total:      {len(results)}")
print(f"\nSaved to: {out_path}")
