#!/usr/bin/env python3
"""
Batch Brand-1km runner. Runs multiple accounts sequentially with 5 min gap.
Collects strict feedback and prints summary.

Usage: python _batch_brand_1km.py acc_004 acc_005 acc_006 acc_007 ...
"""
import sys, subprocess, time, re, csv
from pathlib import Path
from datetime import datetime

SCRIPTS = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts")
CSV_PATH = r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\data\accounts_business_mapping.csv"

def load_accounts(account_ids):
    """Look up account details from CSV."""
    accounts = []
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["account_id"] in account_ids:
                accounts.append({
                    "id": row["account_id"],
                    "phone": row["geelark_phone_id"],
                    "business": row["business_name"],
                    "lat": row["business_lat"],
                    "lng": row["business_lng"],
                })
    # Sort by account_id order as given
    accounts.sort(key=lambda a: account_ids.index(a["id"]) if a["id"] in account_ids else 999)
    return accounts

def run_one(acc):
    """Run Brand-1km on one account. Returns result dict."""
    cmd = [
        sys.executable, "-u",
        str(SCRIPTS / "_brand_1km_run.py"),
        acc["phone"], acc["lat"], acc["lng"],
        acc["id"], acc["business"]
    ]
    print("\n" + "=" * 70)
    print("|  %s - %s" % (acc["id"], acc["business"]))
    print("|  Phone: %s | %.6f, %.6f" % (acc["phone"], float(acc["lat"]), float(acc["lng"])))
    print("|  Started: %s" % datetime.now().strftime("%H:%M:%S"))
    print("=" * 70)

    start = time.time()
    try:
        env = {**__import__("os").environ, "PYTHONUNBUFFERED": "1"}
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(SCRIPTS), env=env)
        stdout = result.stdout
        stderr = result.stderr
        elapsed = int(time.time() - start)
    except subprocess.TimeoutExpired:
        return {**acc, "ok": False, "error": "Timeout (10 min)", "elapsed": 600}
    except Exception as e:
        return {**acc, "ok": False, "error": str(e), "elapsed": int(time.time() - start)}

    # Debug: show stderr if exit code non-zero
    if result.returncode != 0:
        print("  [DEBUG] Subprocess exit=%d, stderr:" % result.returncode)
        for line in (stderr or "").splitlines()[-10:]:
            print("    " + line.encode("ascii", errors="replace").decode())
        # Also show last few lines of stdout
        print("  [DEBUG] stdout tail:")
        for line in (stdout or "").splitlines()[-15:]:
            print("    " + line.encode("ascii", errors="replace").decode())

    # Parse output
    out = {}
    out["elapsed"] = elapsed
    out["exit_code"] = result.returncode

    # GPS status
    gps_ok = re.search(r"GPS MOCK ACTIVE", stdout)
    gps_retry = re.search(r"Step 9.*GPS MOCK ACTIVE", stdout)
    out["gps"] = "OK" if gps_ok else ("RETRY" if gps_retry else "FAIL")

    # IP
    ip_match = re.search(r"Phone IP:\s*([\d.]+)", stdout)
    out["ip"] = ip_match.group(1) if ip_match else "?"

    # Keyword used
    kw_match = re.search(r"Keyword:\s*(.+)$", stdout, re.MULTILINE)
    out["keyword"] = kw_match.group(1).strip() if kw_match else "?"

    # Business found
    biz_match = re.search(r"Business visible:\s*(True|False)", stdout)
    out["biz_found"] = biz_match.group(1) == "True" if biz_match else False

    # Which PASS found it
    pass_match = re.search(r"PASS (\d+).*FOUND.*score=(\d+)", stdout)
    out["pass_num"] = int(pass_match.group(1)) if pass_match else None
    out["pass_score"] = int(pass_match.group(2)) if pass_match else None

    # GPS offset
    offset_match = re.search(r"GPS offset:\s*([\d.]+)\s*km", stdout)
    out["gps_offset"] = float(offset_match.group(1)) if offset_match else None

    # Rating
    rating_match = re.search(r"(\d+\.\d+)\s*stars\s*\((\d+)\)", stdout)
    out["rating"] = rating_match.group(1) if rating_match else "?"
    out["review_count"] = int(rating_match.group(2)) if rating_match else 0

    # Interactions
    out["reviews"] = "OK" if "[OK] Reviews" in stdout else ("SKIP" if "[SKIP] Reviews" in stdout else "?")
    out["directions"] = "OK" if "[OK] Directions" in stdout else ("SKIP" if "[SKIP] Directions" in stdout else "?")
    out["website"] = "OK" if "[OK] Website" in stdout else ("SKIP" if "[SKIP] Website" in stdout else "?")
    out["photos"] = "OK" if "[OK] Photos" in stdout else ("SKIP" if "[SKIP] Photos" in stdout else "?")
    out["call"] = "OK" if "[OK] Phone Call" in stdout else ("SKIP" if "[SKIP] Phone Call" in stdout else "?")

    # Any errors in stderr
    out["stderr_lines"] = [l for l in (stderr or "").splitlines() if l.strip() and "Traceback" not in l]

    # Overall OK
    out["ok"] = out["biz_found"] and out["gps"] in ("OK", "RETRY")

    return {**acc, **out}

def print_summary(results):
    """Print a clean summary table."""
    print("\n" + "=" * 100)
    print("BATCH BRAND-1KM SUMMARY -- %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 100)

    # Header
    header = f"{'Account':<8} {'Business':<35} {'GPS':<6} {'IP':<14} {'Found':<6} {'PASS':<6} {'Rating':<12} {'R':<3} {'D':<3} {'W':<3} {'P':<3} {'C':<3} {'Time':<5}"
    print(header)
    print("-" * 100)

    ok_count = 0
    for r in results:
        bid = r["id"]
        biz = r["business"][:33]
        gps = r.get("gps", "?")
        ip = r.get("ip", "?")
        found = "YES" if r.get("biz_found") else "NO"
        pn = r.get("pass_num")
        ps = f"P{pn}(s{ r.get('pass_score','?')})" if pn is not None else "-"
        rating = f"{r.get('rating','?')}* ({r.get('review_count',0)})"
        rev = "V" if r.get("reviews") == "OK" else ("-" if r.get("reviews") == "SKIP" else "?")
        dirs = "V" if r.get("directions") == "OK" else ("-" if r.get("directions") == "SKIP" else "?")
        web = "V" if r.get("website") == "OK" else ("-" if r.get("website") == "SKIP" else "?")
        pho = "V" if r.get("photos") == "OK" else ("-" if r.get("photos") == "SKIP" else "?")
        cal = "V" if r.get("call") == "OK" else ("-" if r.get("call") == "SKIP" else "?")
        el = f"{r.get('elapsed',0)}s"
        if r.get("ok"): ok_count += 1

        row = f"{bid:<8} {biz:<35} {gps:<6} {ip:<14} {found:<6} {ps:<6} {rating:<12} {rev:<3} {dirs:<3} {web:<3} {pho:<3} {cal:<3} {el:<5}"
        print(row)

        # Print keyword used
        kw = r.get("keyword", "?")
        print(f"        ↳ KW: {kw}")
        # Print GPS offset
        off = r.get("gps_offset")
        if off:
            print(f"        ↳ Offset: {off:.2f} km")

    print("-" * 100)
    print(f"Total: {len(results)} | OK: {ok_count} | Failed: {len(results) - ok_count}")

    # Detail on failures
    failures = [r for r in results if not r.get("ok")]
    if failures:
        print("\nFAILURES:")
        for f in failures:
            reason = []
            if f.get("gps") == "FAIL": reason.append("GPS")
            if not f.get("biz_found"): reason.append("Biz not found")
            if not reason: reason.append(f.get("error", "unknown"))
            print(f"  {f['id']}: {', '.join(reason)}")

    print("=" * 100)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python _batch_brand_1km.py acc_004 acc_005 acc_006 ...")
        sys.exit(1)

    account_ids = sys.argv[1:]
    accounts = load_accounts(account_ids)

    not_found = [aid for aid in account_ids if aid not in [a["id"] for a in accounts]]
    if not_found:
        print("WARNING: These accounts not in CSV: %s" % ", ".join(not_found))

    if not accounts:
        print("No valid accounts found.")
        sys.exit(1)

    print("Running Brand-1km on %d accounts: %s" % (len(accounts), ", ".join(a["id"] for a in accounts)))
    print("Gap between runs: 5 minutes")
    print()

    results = []
    for i, acc in enumerate(accounts):
        if i > 0:
            gap = 300  # 5 minutes
            print("\n--- Waiting %d seconds before next account ---" % gap)
            # Show countdown every 60s
            remaining = gap
            while remaining > 0:
                print("  %ds remaining..." % remaining)
                wait = min(60, remaining)
                time.sleep(wait)
                remaining -= wait

        result = run_one(acc)
        results.append(result)

        # Stop phone after run
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")))
            from core.geelark_client import GeelarKClient
            c = GeelarKClient()
            c.stop_phone(acc["phone"])
            print("Phone stopped.")
        except Exception as e:
            print("Stop phone: %s" % e)

        # Quick per-account feedback
        print("  RESULT: %s | GPS=%s | Biz=%s | IP=%s" % (
            "OK" if result.get("ok") else "FAIL",
            result.get("gps", "?"),
            "YES" if result.get("biz_found") else "NO",
            result.get("ip", "?")
        ))

    print_summary(results)
