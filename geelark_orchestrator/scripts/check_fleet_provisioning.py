#!/usr/bin/env python3
"""
Full fleet provisioning check — 7 gates + IP leak + JSON state logging.
Single-phone-at-a-time. Retry queue (max 3 attempts).
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_orchestrator_src = _repo_root / "geelark_orchestrator" / "src"
_orchestrator_root = _repo_root / "geelark_orchestrator"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient, _post

FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
MAX_START_ATTEMPTS = 3
START_TIMEOUT = 360
INTER_PHONE_DELAY = 30

_log_date = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file = _repo_root / "geelark_orchestrator" / "scripts" / f"provisioning_full_state_{_log_date}.json"
_text_log = _repo_root / "geelark_orchestrator" / "scripts" / f"provisioning_run_{_log_date}.txt"
_jsonl_file = _repo_root / "logs" / "run_history.jsonl"

# In-memory state collection
_all_states: list[dict] = []


def write_jsonl(state: dict, script_name: str = "check_fleet_provisioning.py") -> None:
    """Append a single run record to the cumulative JSONL log."""
    record = {
        "timestamp": state.get("timestamp", datetime.now().isoformat()),
        "script_name": script_name,
        "run_id": _log_date,
        "phone_id": state.get("phone_id", ""),
        "account_email": state.get("account", ""),
        "attempt": state.get("attempt", 1),
        "started_ok": state.get("started_ok", False),
        "gates": {
            k: v for k, v in state.get("gates", {}).items()
        },
        "overall_status": state.get("overall_status", "UNKNOWN"),
        "missing_gates": state.get("missing_gates", []),
        "warnings": state.get("warnings", []),
        "ip_leak": state.get("ip_leak", {}),
        "errors": [w for w in state.get("warnings", []) if "error" in w.lower()],
        "retries": state.get("attempt", 1) - 1 if not state.get("started_ok", False) else 0,
    }
    _jsonl_file.parent.mkdir(parents=True, exist_ok=True)
    with open(_jsonl_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write(line: str) -> None:
    print(line)
    with open(_text_log, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _get_running_phones(client: GeelarKClient) -> list[dict]:
    try:
        phones = client.list_phones(page_size=100)
        return [p for p in phones if p.get("status") == 0]
    except Exception as e:
        _write(f"    WARNING: could not list phones: {e}")
        return []


def _global_cleanup(client: GeelarKClient) -> bool:
    _write("=== Pre-run global cleanup ===")
    running = _get_running_phones(client)
    if not running:
        _write("  No phones running. Clean slate.")
        return True

    _write(f"  Found {len(running)} running phones — stopping all...")
    for p in running:
        pid = p["id"]
        name = p.get("serialName", "unknown")
        try:
            client.stop_phone(pid)
            _write(f"    STOP sent: {pid} | {name}")
        except Exception as e:
            _write(f"    STOP FAILED: {pid} | {name} | {e}")

    for attempt in range(1, 13):
        time.sleep(5)
        still = _get_running_phones(client)
        if len(still) == 0:
            _write("  All phones stopped. Clean slate confirmed.")
            return True
        _write(f"    Cleanup poll {attempt}/12: {len(still)} still running")

    _write("  ERROR: phones still running after cleanup. Proceeding anyway.")
    return False


def _confirm_stop(client: GeelarKClient, phone_id: str, max_retries: int = 3) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            client.stop_phone(phone_id)
        except Exception as e:
            _write(f"    Stop command error (attempt {attempt}): {e}")

        for poll in range(1, 4):
            time.sleep(5)
            try:
                statuses = client.get_phone_status([phone_id])
                s = statuses[0].get("status", -1) if statuses else -1
                if s != 0:
                    _write(f"    Phone {phone_id} confirmed stopped (attempt {attempt}).")
                    return True
            except Exception:
                pass

    _write(f"    ERROR: Phone {phone_id} could NOT be stopped after {max_retries} retries.")
    return False


def _concurrency_guard(client: GeelarKClient, expected_phone_id: str | None = None) -> bool:
    running = _get_running_phones(client)
    if not running:
        return True

    if expected_phone_id and len(running) == 1 and running[0]["id"] == expected_phone_id:
        return True

    _write(f"  CONCURRENCY GUARD: {len(running)} unexpected phone(s) running — stopping...")
    for p in running:
        pid = p["id"]
        if expected_phone_id and pid == expected_phone_id:
            continue
        name = p.get("serialName", "unknown")
        try:
            client.stop_phone(pid)
            _write(f"    STOP sent: {pid} | {name}")
        except Exception as e:
            _write(f"    STOP FAILED: {pid} | {name} | {e}")

    for attempt in range(1, 13):
        time.sleep(5)
        still = _get_running_phones(client)
        if expected_phone_id:
            still = [p for p in still if p["id"] != expected_phone_id]
        if not still:
            _write("    Concurrency guard cleared.")
            return True
        _write(f"    Guard poll {attempt}/12: {len(still)} still running")

    _write("  ERROR: Could not clear concurrent phones.")
    return False


def ensure_running(client: GeelarKClient, phone_id: str, timeout: int = START_TIMEOUT) -> bool:
    try:
        statuses = client.get_phone_status([phone_id])
        s = statuses[0].get("status", -1) if statuses else -1
    except Exception:
        s = -1
    if s == 0:
        return True

    _write(f"    Starting phone {phone_id}...")
    try:
        client.start_phone(phone_id)
    except Exception as e:
        _write(f"    ERROR: start failed: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            statuses = client.get_phone_status([phone_id])
            s = statuses[0].get("status", -1) if statuses else -1
            if s == 0:
                _write("    Phone running.")
                return True
        except Exception:
            pass
    _write(f"    ERROR: phone did not start within {timeout}s.")
    return False


# =====================================================================
# GATE CHECKS
# =====================================================================

def gate1_dev_options(phone_id: str) -> tuple[bool, str]:
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get global development_settings_enabled"})
        val = r.get("output", "").strip()
        return val == "1", val
    except Exception as e:
        return False, f"error:{e}"


def gate2_usb_debug(phone_id: str) -> tuple[bool, str]:
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get global adb_enabled"})
        val = r.get("output", "").strip()
        return val == "1", val
    except Exception as e:
        return False, f"error:{e}"


def gate3_mock_app_installed(phone_id: str) -> tuple[bool, str]:
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": f"pm list packages {FAKE_GPS_PACKAGE}"})
        out = r.get("output", "").strip()
        return FAKE_GPS_PACKAGE in out, out
    except Exception as e:
        return False, f"error:{e}"


def gate4_mock_app_set(phone_id: str) -> tuple[bool, str]:
    # Check appops mock_location allow
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": f"appops get {FAKE_GPS_PACKAGE} android:mock_location"})
        out = r.get("output", "").strip()
        if "ALLOW" in out.upper() or "allow" in out:
            return True, out
    except Exception:
        pass

    # Fallback: secure mock_location setting
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get secure mock_location"})
        val = r.get("output", "").strip()
        if FAKE_GPS_PACKAGE in val:
            return True, val
    except Exception:
        pass

    return False, "not set"


def gate5_google_account(phone_id: str) -> tuple[bool, str]:
    methods = [
        # Method 1: dumpsys account grep
        {"cmd": "dumpsys account | grep -A 5 'Account {name=' | grep 'type=com.google'"},
        # Method 2: cmd account get-accounts (Android 8+)
        {"cmd": "cmd account list"},
        # Method 3: pm list users then dumpsys per user
        {"cmd": "dumpsys account | grep 'type=com.google'"},
    ]
    for i, m in enumerate(methods, 1):
        try:
            r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": m["cmd"]})
            out = r.get("output", "").strip()
            if "com.google" in out or "google" in out.lower():
                return True, f"method{i}: found"
        except Exception:
            pass
    return False, "No Google account found"


def gate6_screen_lock(phone_id: str) -> tuple[bool, str]:
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get secure lockscreen.disabled"})
        val = r.get("output", "").strip()
        if val == "1":
            return True, "disabled"
        return False, f"enabled ({val})"
    except Exception as e:
        return False, f"error:{e}"


def gate7_stay_awake(phone_id: str) -> tuple[bool, str]:
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get global stay_on_while_plugged_in"})
        val = r.get("output", "").strip()
        if val and val != "0" and val != "null":
            return True, f"enabled ({val})"
        return False, f"disabled ({val})"
    except Exception as e:
        return False, f"error:{e}"


# =====================================================================
# AUTO-FIX
# =====================================================================

def auto_fix_gates(phone_id: str, gates: dict) -> dict:
    """Attempt to fix gates 1, 2, 3, 4. Returns updated gate results."""
    _write("    Auto-fix: attempting to repair missing gates...")

    # Fix gate 1: Developer Options
    if not gates["gate1_dev_options"][0]:
        _write("      Fixing Developer Options...")
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "settings put global development_settings_enabled 1"})
        time.sleep(1)
        gates["gate1_dev_options"] = gate1_dev_options(phone_id)
        _write(f"      DevOps after fix: {gates['gate1_dev_options']}")

    # Fix gate 2: USB Debugging (attempt)
    if not gates["gate2_usb_debug"][0]:
        _write("      Fixing USB Debugging...")
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "settings put global adb_enabled 1"})
        time.sleep(1)
        gates["gate2_usb_debug"] = gate2_usb_debug(phone_id)
        _write(f"      USB Debug after fix: {gates['gate2_usb_debug']}")

    # Fix gate 3: Install/check mock app (we can't install, just verify)
    if not gates["gate3_mock_installed"][0]:
        _write("      Mock app not installed — cannot auto-install via ADB.")

    # Fix gate 4: Set mock location app
    if not gates["gate4_mock_set"][0]:
        _write("      Fixing mock location app setting...")
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": f"appops set {FAKE_GPS_PACKAGE} android:mock_location allow"})
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": f"settings put secure mock_location_app {FAKE_GPS_PACKAGE}"})
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "settings put secure mock_location 1"})
        time.sleep(1)
        gates["gate4_mock_set"] = gate4_mock_app_set(phone_id)
        _write(f"      Mock set after fix: {gates['gate4_mock_set']}")

    return gates


# =====================================================================
# IP / PROXY CHECK (reused from audit)
# =====================================================================

def check_proxy_and_ip(phone_id: str) -> dict:
    result = {
        "proxy_setting": "unknown",
        "public_ip": "unknown",
        "leak_status": "UNKNOWN",
        "notes": "",
    }

    proxy_cmds = [
        "settings get global http_proxy",
        "settings get global global_http_proxy",
    ]
    proxy_val = ""
    for cmd in proxy_cmds:
        try:
            r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
            out = r.get("output", "").strip()
            if out and out != "null" and out != "0" and ":" in out:
                proxy_val = out
                break
        except Exception:
            pass

    if not proxy_val:
        result["proxy_setting"] = "NONE"
    else:
        result["proxy_setting"] = proxy_val

    ip = ""
    for url in ("http://ifconfig.me", "https://icanhazip.com"):
        try:
            r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"curl -s {url}"})
            out = r.get("output", "").strip()
            if out and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", out):
                ip = out
                break
        except Exception:
            pass

    result["public_ip"] = ip or "COULD_NOT_DETERMINE"

    if not proxy_val:
        result["leak_status"] = "NO_PROXY_SET"
        if not ip:
            result["notes"] = "No internet connectivity (could not determine IP)."
        else:
            result["notes"] = f"Phone public IP {ip} (no proxy configured)."
    else:
        proxy_ip = proxy_val.split(":")[0] if ":" in proxy_val else ""
        if ip and proxy_ip and ip == proxy_ip:
            result["leak_status"] = "OK"
            result["notes"] = f"Public IP {ip} matches proxy IP."
        elif ip and proxy_ip:
            result["leak_status"] = "POTENTIAL_LEAK"
            result["notes"] = f"**LEAK**: Public IP {ip} != proxy IP {proxy_ip}."
        else:
            result["leak_status"] = "POTENTIAL_LEAK"
            result["notes"] = "Could not verify proxy match (missing IP data)."

    return result


# =====================================================================
# PROCESS ONE PHONE
# =====================================================================

def process_phone(client: GeelarKClient, phone: dict, attempt: int) -> dict:
    phone_id = phone["phone_id"]
    account = phone["account_email"]
    timestamp = datetime.now().isoformat()

    state = {
        "phone_id": phone_id,
        "account": account,
        "timestamp": timestamp,
        "attempt": attempt,
        "started_ok": False,
        "unreachable": False,
        "gates": {},
        "ip_leak": {},
        "overall_status": "UNKNOWN",
        "missing_gates": [],
        "warnings": [],
    }

    _write(f"\n=== Attempt {attempt}/{MAX_START_ATTEMPTS} for {account} ({phone_id}) ===")

    _concurrency_guard(client)

    start_ok = ensure_running(client, phone_id, timeout=START_TIMEOUT)
    if not start_ok:
        state["started_ok"] = False
        state["unreachable"] = True
        state["overall_status"] = "UNREACHABLE"
        _confirm_stop(client, phone_id, max_retries=2)
        return state

    state["started_ok"] = True

    # Run all 7 gates
    gates = {
        "gate1_dev_options": gate1_dev_options(phone_id),
        "gate2_usb_debug": gate2_usb_debug(phone_id),
        "gate3_mock_installed": gate3_mock_app_installed(phone_id),
        "gate4_mock_set": gate4_mock_app_set(phone_id),
        "gate5_google_account": gate5_google_account(phone_id),
        "gate6_screen_lock": gate6_screen_lock(phone_id),
        "gate7_stay_awake": gate7_stay_awake(phone_id),
    }

    # Auto-fix hard gates if any fail
    hard_fail = any(not gates[g][0] for g in [
        "gate1_dev_options", "gate2_usb_debug",
        "gate3_mock_installed", "gate4_mock_set", "gate5_google_account"
    ])
    if hard_fail:
        gates = auto_fix_gates(phone_id, gates)

    # Store gate results
    for gname, (passed, detail) in gates.items():
        state["gates"][gname] = {"passed": passed, "detail": detail}

    # Determine missing hard gates
    missing = []
    if not gates["gate1_dev_options"][0]:
        missing.append("DevOptions")
    if not gates["gate2_usb_debug"][0]:
        missing.append("USBDebug")
    if not gates["gate3_mock_installed"][0]:
        missing.append("MockAppInstalled")
    if not gates["gate4_mock_set"][0]:
        missing.append("MockAppSet")
    if not gates["gate5_google_account"][0]:
        missing.append("GoogleAccount")
    state["missing_gates"] = missing

    # Warnings
    warnings = []
    if not gates["gate6_screen_lock"][0]:
        warnings.append(f"LOCKED ({gates['gate6_screen_lock'][1]})")
    if not gates["gate7_stay_awake"][0]:
        warnings.append(f"MaySleep ({gates['gate7_stay_awake'][1]})")
    state["warnings"] = warnings

    # Overall status
    if not missing:
        state["overall_status"] = "FULLY_PROVISIONED"
    elif missing == ["GoogleAccount"]:
        state["overall_status"] = "PARTIALLY_PROVISIONED (No Google Account)"
    else:
        state["overall_status"] = f"PARTIALLY_PROVISIONED ({', '.join(missing)})"

    # If Google account missing, mark as FAILED for Maps readiness
    if "GoogleAccount" in missing:
        state["overall_status"] = "FAILED (No Google Account)"

    # IP leak check
    ip_result = check_proxy_and_ip(phone_id)
    state["ip_leak"] = ip_result
    if ip_result["leak_status"] == "POTENTIAL_LEAK":
        _write(f"  *** POTENTIAL LEAK: {ip_result['notes']} ***")

    _write(f"  -> Gates: Dev={gates['gate1_dev_options'][0]} USB={gates['gate2_usb_debug'][0]} "
           f"MockInst={gates['gate3_mock_installed'][0]} MockSet={gates['gate4_mock_set'][0]} "
           f"Google={gates['gate5_google_account'][0]} Lock={gates['gate6_screen_lock'][0]} StayAwake={gates['gate7_stay_awake'][0]}")
    _write(f"  -> Overall: {state['overall_status']}")

    # Stop phone
    stopped = _confirm_stop(client, phone_id, max_retries=3)
    if not stopped:
        _write(f"  *** CRITICAL: Phone {phone_id} could not be stopped ***")
        state["warnings"].append("COULD_NOT_STOP")

    return state


# =====================================================================
# MAIN
# =====================================================================

def load_fleet() -> list[dict]:
    full_fleet_path = _repo_root / "geelark_orchestrator" / "scripts" / "tmp_full_fleet.json"
    with open(full_fleet_path, encoding="utf-8") as f:
        fleet = json.load(f)
    phones = []
    for p in fleet:
        phones.append({
            "phone_id": p["id"],
            "account_email": p.get("serialName", "unknown"),
        })
    return phones


def main() -> int:
    client = GeelarKClient()
    _write("=" * 70)
    _write(f"FULL FLEET PROVISIONING CHECK — {_log_date}")
    _write("=" * 70)

    _global_cleanup(client)

    phones = load_fleet()
    _write(f"\nTotal phones to check: {len(phones)}")

    global _all_states
    abort_run = False

    for idx, phone in enumerate(phones, 1):
        if abort_run:
            _write("\n*** RUN ABORTED — marking remaining as SKIPPED ***")
            for remaining in phones[idx - 1:]:
                _all_states.append({
                    "phone_id": remaining["phone_id"],
                    "account": remaining["account_email"],
                    "timestamp": datetime.now().isoformat(),
                    "attempt": 0,
                    "started_ok": False,
                    "unreachable": False,
                    "overall_status": "SKIPPED (run aborted)",
                    "gates": {},
                    "ip_leak": {},
                    "missing_gates": [],
                    "warnings": [],
                })
            break

        state = None
        for attempt in range(1, MAX_START_ATTEMPTS + 1):
            state = process_phone(client, phone, attempt)
            if state["started_ok"]:
                break
            if "COULD_NOT_STOP" in state.get("warnings", []):
                abort_run = True
                break
            if attempt < MAX_START_ATTEMPTS:
                _write(f"  -> Retrying {phone['account_email']} (attempt {attempt + 1})")
                _concurrency_guard(client)
                time.sleep(INTER_PHONE_DELAY)

        if state:
            _all_states.append(state)
            _write(f"  -> FINAL STATUS: {state['overall_status']}")
            write_jsonl(state)

        if idx < len(phones):
            _concurrency_guard(client)
            time.sleep(INTER_PHONE_DELAY)

    # Save JSON
    with open(_log_file, "w", encoding="utf-8") as f:
        json.dump({
            "run_timestamp": _log_date,
            "total_phones": len(phones),
            "phones": _all_states,
        }, f, indent=2)

    # Reports
    _print_reports()

    _write(f"\nJSON state log saved to: {_log_file}")
    _write(f"Text log saved to: {_text_log}")
    return 0


def _print_reports() -> None:
    # DELIVERABLE 1: Full table
    _write("\n" + "=" * 110)
    _write("DELIVERABLE 1: FULL PROVISIONING STATE TABLE")
    _write("=" * 110)
    hdr = f"{'phone_id':<22} | {'account':<35} | {'Dev':<3} | {'USB':<3} | {'MInst':<5} | {'MSet':<4} | {'Goog':<4} | {'Lock':<4} | {'Awake':<5} | {'Status'}"
    _write(hdr)
    _write("-" * 110)

    for s in _all_states:
        g = s["gates"]
        dev = "Y" if g.get("gate1_dev_options", {}).get("passed") else "N"
        usb = "Y" if g.get("gate2_usb_debug", {}).get("passed") else "N"
        minst = "Y" if g.get("gate3_mock_installed", {}).get("passed") else "N"
        mset = "Y" if g.get("gate4_mock_set", {}).get("passed") else "N"
        goog = "Y" if g.get("gate5_google_account", {}).get("passed") else "N"
        lock = "Y" if g.get("gate6_screen_lock", {}).get("passed") else "N"
        awake = "Y" if g.get("gate7_stay_awake", {}).get("passed") else "N"
        status = s["overall_status"]
        _write(f"{s['phone_id']:<22} | {s['account']:<35} | {dev:<3} | {usb:<3} | {minst:<5} | {mset:<4} | {goog:<4} | {lock:<4} | {awake:<5} | {status}")

    # Summary
    fully = [s for s in _all_states if s["overall_status"] == "FULLY_PROVISIONED"]
    partial = [s for s in _all_states if "PARTIALLY" in s["overall_status"]]
    failed = [s for s in _all_states if "FAILED" in s["overall_status"]]
    unreachable = [s for s in _all_states if s["overall_status"] == "UNREACHABLE"]
    skipped = [s for s in _all_states if "SKIPPED" in s["overall_status"]]

    _write("\n" + "=" * 70)
    _write("SUMMARY")
    _write("=" * 70)
    _write(f"FULLY PROVISIONED:     {len(fully)}")
    _write(f"PARTIALLY PROVISIONED: {len(partial)}")
    for s in partial:
        _write(f"    {s['phone_id']} | {s['account']} | missing: {', '.join(s['missing_gates'])}")
    _write(f"FAILED:                {len(failed)}")
    for s in failed:
        _write(f"    {s['phone_id']} | {s['account']} | {s['overall_status']}")
    _write(f"UNREACHABLE:           {len(unreachable)}")
    for s in unreachable:
        _write(f"    {s['phone_id']} | {s['account']}")
    _write(f"SKIPPED:               {len(skipped)}")

    # DELIVERABLE 2: IP Leak
    _write("\n" + "=" * 90)
    _write("DELIVERABLE 2: IP LEAK REPORT")
    _write("=" * 90)
    _write(f"{'phone_id':<22} | {'account':<35} | {'proxy':<25} | {'public_ip':<16} | {'leak_status'}")
    _write("-" * 90)
    leak_count = 0
    for s in _all_states:
        ip = s.get("ip_leak", {})
        proxy = (ip.get("proxy_setting", "—") if ip else "—")[:23]
        pub = (ip.get("public_ip", "—") if ip else "—")[:14]
        status = ip.get("leak_status", "—") if ip else "—"
        _write(f"{s['phone_id']:<22} | {s['account']:<35} | {proxy:<25} | {pub:<16} | {status}")
        if status == "POTENTIAL_LEAK":
            leak_count += 1
            note = ip.get("notes", "") if ip else ""
            _write(f"    NOTE: {note}")
    _write(f"\nTotal potential leaks: {leak_count}")


if __name__ == "__main__":
    raise SystemExit(main())
