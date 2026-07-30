#!/usr/bin/env python3
"""Timed batch runner for brand-1km. Tracks minutes per account and total."""
import sys, subprocess, time
from datetime import datetime

ACCOUNTS = sys.argv[1].split(",") if len(sys.argv) > 1 else []
SCRIPT = "_brand_1km_run.py"  # or _local_discovery_run.py

if not ACCOUNTS:
    print("Usage: python _batch_timed.py acc_004,acc_005,acc_006")
    sys.exit(1)

print("=" * 70)
print("TIMED BATCH: %s on %d accounts" % (SCRIPT, len(ACCOUNTS)))
print("Started: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 70)

batch_start = time.time()
results = []

for i, acc in enumerate(ACCOUNTS):
    acc_start = time.time()
    print("\n[%d/%d] %s — %s" % (i+1, len(ACCOUNTS), acc, datetime.now().strftime("%H:%M:%S")))

    result = subprocess.run(
        [sys.executable, SCRIPT, acc],
        capture_output=True, text=True, timeout=900,
        cwd="C:/Users/Administrator/Desktop/AccountWarmer-Deploy-Enhanced/geelark_orchestrator/scripts"
    )

    elapsed = int(time.time() - acc_start)
    total_elapsed = int(time.time() - batch_start)

    # Parse key results
    stdout = result.stdout
    found = "Business visible: True" in stdout
    markers = ""
    for m in ["Directions", "Reviews", "Photos", "Call"]:
        if "[OK] %s" % m in stdout:
            markers += m[0]
        elif "[SKIP] %s" % m in stdout:
            markers += "-"
        else:
            markers += "?"

    import re
    ip = re.search(r"Phone IP:\s*([\d.]+)", stdout)
    ip_str = ip.group(1) if ip else "?"
    rating = re.search(r"(\d+\.\d+)\s*stars\s*\((\d+)\)", stdout)
    rating_str = "%s*(%s)" % (rating.group(1), rating.group(2)) if rating else "?"
    kw = re.search(r"Keyword:\s*(.+)$", stdout, re.MULTILINE)
    kw_str = kw.group(1).strip()[:40] if kw else "?"

    line = "%s | %s | %s | %s | %dm | %s | %s" % (
        acc, "OK" if found else "NO", ip_str, rating_str,
        elapsed//60, markers, kw_str
    )

    # Also print the key lines
    for l in stdout.splitlines():
        if any(x in l for x in ["Business visible", "Phone IP", "GPS offset", "Keyword:", "--- Stopping"]):
            print("  " + l.strip()[:120])

    print("  >> %s (elapsed: %dm, total: %dm)" % (
        "FOUND" if found else "NOT FOUND",
        elapsed//60, total_elapsed//60
    ))
    results.append(line)

# Summary
total_time = int(time.time() - batch_start)
print("\n" + "=" * 70)
print("BATCH COMPLETE — %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print("Total time: %dm (%.1f hrs)" % (total_time//60, total_time/60))
print("=" * 70)
print("Acc | OK | IP | Rating | Time | Acts | Keyword")
for r in results:
    print(r)
found_count = sum(1 for r in results if "| OK |" in r)
print("\nFound: %d/%d" % (found_count, len(ACCOUNTS)))
print("Avg per account: %.1f min" % (total_time / len(ACCOUNTS) / 60))
