#!/usr/bin/env python3
"""
Complete fleet audit: provisioning status + IP leak check.
Single-phone-at-a-time. Logs to console and file.
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

_log_date = datetime.now().strftime("%Y%m%d")
_log_file = _repo_root / "geelark_orchestrator" / "scripts" / f"proxy_log_{_log_date}.txt"


def _write_log(line: str) -> None:
    """Append to log file and print."""
    print(line)
    with open(_log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _get_running_phones(client: GeelarKClient) -> list[dict]:
    try:
        phones = client.list_phones(page_size=100)
        return [p for p in phones if p.get("status") == 0]
    except Exception as e:
        _write_log(f"    WARNING: could not list phones: {e}")
        return []


def _global_cleanup(client: GeelarKClient) -> bool:
    _write_log("=== Pre-run global cleanup ===")
    running = _get_running_phones(client)
    if not running:
        _write_log("  No phones running. Clean slate.")
        return True

    _write_log(f"  Found {len(running)} running phones — stopping all...")
    for p in running:
        pid = p["id"]
        name = p.get("serialName", "unknown")
        try:
            client.stop_phone(pid)
            _write_log(f"    STOP sent: {pid} | {name}")
        except Exception as e:
            _write_log(f"    STOP FAILED: {pid} | {name} | {e}")

    for attempt in range(1, 13):
        time.sleep(5)
        still = _get_running_phones(client)
        if len(still) == 0:
            _write_log("  All phones stopped. Clean slate confirmed.")
            return True
        _write_log(f"    Cleanup poll {attempt}/12: {len(still)} still running")

    _write_log("  ERROR: phones still running after cleanup. Proceeding anyway.")
    return False


def _confirm_stop(client: GeelarKClient, phone_id: str, max_retries: int = 3) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            client.stop_phone(phone_id)
        except Exception as e:
            _write_log(f"    Stop command error (attempt {attempt}): {e}")

        for poll in range(1, 4):
            time.sleep(5)
            try:
                statuses = client.get_phone_status([phone_id])
                s = statuses[0].get("status", -1) if statuses else -1
                if s != 0:
                    _write_log(f"    Phone {phone_id} confirmed stopped (attempt {attempt}).")
                    return True
            except Exception:
                pass

    _write_log(f"    ERROR: Phone {phone_id} could NOT be stopped after {max_retries} retries.")
    return False


def _concurrency_guard(client: GeelarKClient, expected_phone_id: str | None = None) -> bool:
    running = _get_running_phones(client)
    if not running:
        return True

    if expected_phone_id and len(running) == 1 and running[0]["id"] == expected_phone_id:
        return True

    _write_log(f"  CONCURRENCY GUARD: {len(running)} unexpected phone(s) running — stopping...")
    for p in running:
        pid = p["id"]
        if expected_phone_id and pid == expected_phone_id:
            continue
        name = p.get("serialName", "unknown")
        try:
            client.stop_phone(pid)
            _write_log(f"    STOP sent: {pid} | {name}")
        except Exception as e:
            _write_log(f"    STOP FAILED: {pid} | {name} | {e}")

    for attempt in range(1, 13):
        time.sleep(5)
        still = _get_running_phones(client)
        if expected_phone_id:
            still = [p for p in still if p["id"] != expected_phone_id]
        if not still:
            _write_log("    Concurrency guard cleared.")
            return True
        _write_log(f"    Guard poll {attempt}/12: {len(still)} still running")

    _write_log("  ERROR: Could not clear concurrent phones.")
    return False


def ensure_running(client: GeelarKClient, phone_id: str, timeout: int = START_TIMEOUT) -> bool:
    try:
        statuses = client.get_phone_status([phone_id])
        s = statuses[0].get("status", -1) if statuses else -1
    except Exception:
        s = -1
    if s == 0:
        return True

    _write_log(f"    Starting phone {phone_id}...")
    try:
        client.start_phone(phone_id)
    except Exception as e:
        _write_log(f"    ERROR: start failed: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            statuses = client.get_phone_status([phone_id])
            s = statuses[0].get("status", -1) if statuses else -1
            if s == 0:
                _write_log("    Phone running.")
                return True
        except Exception:
            pass
    _write_log(f"    ERROR: phone did not start within {timeout}s.")
    return False


def check_provisioning(phone_id: str) -> tuple[bool, str, str]:
    """Returns (dev_ok, mock_app, reason)."""
    dev = "unknown"
    mock = "unknown"
    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get global development_settings_enabled"})
        dev = r.get("output", "").strip()
    except Exception as e:
        return False, f"error:{e}", f"Dev check exception: {e}"

    try:
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "settings get secure mock_location_app"})
        mock = r.get("output", "").strip()
    except Exception as e:
        return False, f"error:{e}", f"Mock check exception: {e}"

    dev_ok = dev == "1"
    mock_ok = mock == FAKE_GPS_PACKAGE
    if dev_ok and mock_ok:
        return True, FAKE_GPS_PACKAGE, ""
    reasons = []
    if not dev_ok:
        reasons.append(f"Dev={dev}")
    if not mock_ok:
        reasons.append(f"Mock={mock}")
    return False, mock, ", ".join(reasons)


def check_proxy_and_ip(phone_id: str) -> dict:
    """Check proxy settings and public IP. Returns dict with findings."""
    result = {
        "proxy_setting": "unknown",
        "public_ip": "unknown",
        "leak_status": "UNKNOWN",
        "notes": "",
    }

    # Try multiple proxy setting keys
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
        result["notes"] += "No proxy configured. "
    else:
        result["proxy_setting"] = proxy_val

    # Get public IP via shell curl
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
            result["notes"] += "No internet connectivity (could not determine IP)."
        else:
            result["notes"] += f"Phone public IP {ip} (no proxy configured)."
    else:
        # Extract proxy IP from setting like "host:port"
        proxy_ip = proxy_val.split(":")[0] if ":" in proxy_val else ""
        if ip and proxy_ip and ip == proxy_ip:
            result["leak_status"] = "OK"
            result["notes"] += f"Public IP {ip} matches proxy IP."
        elif ip and proxy_ip:
            result["leak_status"] = "POTENTIAL_LEAK"
            result["notes"] += f"**LEAK**: Public IP {ip} != proxy IP {proxy_ip}."
        else:
            result["leak_status"] = "POTENTIAL_LEAK"
            result["notes"] += "Could not verify proxy match (missing IP data)."

    return result


def process_phone(client: GeelarKClient, phone: dict, attempt: int) -> dict:
    """Process one phone. Returns result dict."""
    phone_id = phone["phone_id"]
    account = phone["account_email"]
    result = {
        "phone_id": phone_id,
        "account": account,
        "dev_options": "unknown",
        "mock_app": "unknown",
        "prov_status": "unknown",
        "proxy_setting": "unknown",
        "public_ip": "unknown",
        "leak_status": "UNKNOWN",
        "notes": "",
        "started_ok": False,
    }

    _write_log(f"\n=== Attempt {attempt}/{MAX_START_ATTEMPTS} for {account} ({phone_id}) ===")

    # 1. Concurrency guard
    _concurrency_guard(client)

    # 2. Start phone
    start_ok = ensure_running(client, phone_id, timeout=START_TIMEOUT)
    if not start_ok:
        result["prov_status"] = "STILL_UNREACHABLE"
        result["notes"] = f"Did not start within {START_TIMEOUT}s (attempt {attempt})"
        _confirm_stop(client, phone_id, max_retries=2)
        return result

    result["started_ok"] = True

    # 3. Provisioning check
    dev_ok, mock_app, prov_reason = check_provisioning(phone_id)
    result["dev_options"] = "yes" if dev_ok else "no"
    result["mock_app"] = mock_app if mock_app else "not set"
    if dev_ok and mock_app == FAKE_GPS_PACKAGE:
        result["prov_status"] = "ALREADY_PROVISIONED"
    else:
        result["prov_status"] = f"MISSING ({prov_reason})"

    # 4. Proxy / IP check
    ip_result = check_proxy_and_ip(phone_id)
    result["proxy_setting"] = ip_result["proxy_setting"]
    result["public_ip"] = ip_result["public_ip"]
    result["leak_status"] = ip_result["leak_status"]
    result["notes"] += " | " + ip_result["notes"]

    if ip_result["leak_status"] == "POTENTIAL_LEAK":
        _write_log(f"  *** POTENTIAL LEAK on {phone_id}: {ip_result['notes']} ***")

    # 5. Stop phone
    stopped = _confirm_stop(client, phone_id, max_retries=3)
    if not stopped:
        _write_log(f"  *** CRITICAL: Phone {phone_id} could not be stopped ***")
        result["notes"] += " | COULD NOT STOP"

    return result


def main() -> int:
    client = GeelarKClient()

    _write_log("=" * 70)
    _write_log(f"FLEET AUDIT + IP LEAK CHECK — {_log_date}")
    _write_log("=" * 70)

    # Pre-run cleanup
    _global_cleanup(client)

    # Load full fleet
    full_fleet_path = _repo_root / "geelark_orchestrator" / "scripts" / "tmp_full_fleet.json"
    with open(full_fleet_path, encoding="utf-8") as f:
        fleet = json.load(f)

    phones = []
    for p in fleet:
        phones.append({
            "phone_id": p["id"],
            "account_email": p.get("serialName", "unknown"),
        })

    _write_log(f"\nTotal phones to audit: {len(phones)}")

    results: list[dict] = []
    abort_run = False

    for idx, phone in enumerate(phones, 1):
        if abort_run:
            _write_log(f"\n*** RUN ABORTED — skipping remaining phones ***")
            for remaining in phones[idx - 1:]:
                results.append({
                    "phone_id": remaining["phone_id"],
                    "account": remaining["account_email"],
                    "dev_options": "unknown",
                    "mock_app": "unknown",
                    "prov_status": "SKIPPED (run aborted)",
                    "proxy_setting": "unknown",
                    "public_ip": "unknown",
                    "leak_status": "SKIPPED",
                    "notes": "Run aborted due to unstoppable phone",
                })
            break

        # Retry loop (up to MAX_START_ATTEMPTS)
        phone_result = None
        for attempt in range(1, MAX_START_ATTEMPTS + 1):
            phone_result = process_phone(client, phone, attempt)
            if phone_result["started_ok"]:
                break
            if "COULD NOT STOP" in phone_result.get("notes", ""):
                abort_run = True
                break
            if attempt < MAX_START_ATTEMPTS:
                _write_log(f"  -> Retrying {phone['account_email']} (attempt {attempt + 1})")
                _concurrency_guard(client)
                time.sleep(INTER_PHONE_DELAY)

        if phone_result:
            results.append(phone_result)
            cat = phone_result["prov_status"]
            _write_log(f"  -> FINAL: {cat} | Leak: {phone_result['leak_status']}")

        # Inter-phone delay (skip after last phone)
        if idx < len(phones):
            _concurrency_guard(client)
            _write_log(f"  Waiting {INTER_PHONE_DELAY}s before next phone...")
            time.sleep(INTER_PHONE_DELAY)

    # =======================
    # DELIVERABLE 1: Provisioning Status Table
    # =======================
    _write_log("\n" + "=" * 70)
    _write_log("DELIVERABLE 1: PROVISIONING STATUS TABLE")
    _write_log("=" * 70)
    _write_log(f"{'phone_id':<22} | {'account':<35} | {'DevOpts':<7} | {'mock_app':<40} | {'status'}")
    _write_log("-" * 130)
    for r in results:
        mock_display = r["mock_app"][:38]
        _write_log(f"{r['phone_id']:<22} | {r['account']:<35} | {r['dev_options']:<7} | {mock_display:<40} | {r['prov_status']}")

    # =======================
    # DELIVERABLE 2: IP Leak Report
    # =======================
    _write_log("\n" + "=" * 70)
    _write_log("DELIVERABLE 2: IP LEAK REPORT")
    _write_log("=" * 70)
    _write_log(f"{'phone_id':<22} | {'account':<35} | {'proxy':<25} | {'public_ip':<16} | {'leak_status'}")
    _write_log("-" * 130)
    leak_count = 0
    for r in results:
        proxy_disp = (r["proxy_setting"] if r["proxy_setting"] != "unknown" else "—")[:23]
        ip_disp = (r["public_ip"] if r["public_ip"] != "unknown" else "—")[:14]
        _write_log(f"{r['phone_id']:<22} | {r['account']:<35} | {proxy_disp:<25} | {ip_disp:<16} | {r['leak_status']}")
        if r["leak_status"] == "POTENTIAL_LEAK":
            leak_count += 1
            _write_log(f"    NOTE: {r['notes']}")

    _write_log("\n" + "=" * 70)
    _write_log(f"SUMMARY: {len(results)} phones processed, {leak_count} potential leaks found.")
    _write_log(f"Log saved to: {_log_file}")
    _write_log("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
