#!/usr/bin/env python3
"""
Batch test: 5 phones with 2-GPS reorder (production sequence).
Warm-up GPS failure -> abort that phone and continue to next.
"""
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
SCRIPT = REPO / "scripts" / "run_one_phone.py"
SEED = 200001

PHONES = [
    ("X50",       "helenscott9987",    "goal_a3_x50.csv"),
    ("S15e",      "vincehawthorne92",  "goal_a4_s15e.csv"),
    ("Q5",        "billyhavenstock",   "goal_a4_q5.csv"),
    ("edge40neo", "andrewjocobyte",    "goal_a4_edge40.csv"),
    ("8i",        "waylonswanson92387","goal_a3_8i.csv"),
]

results = []

for model, profile_key, csv_name in PHONES:
    csv_path = REPO / csv_name
    print(f"\n{'='*60}")
    print(f"[{model}] {profile_key}")
    print(f"CSV: {csv_path}")
    print(f"{'='*60}\n")

    cmd = [
        sys.executable, str(SCRIPT), str(csv_path), profile_key,
        "--seed", str(SEED),
        "--log-dir", str(REPO / "logs" / f"batch_5phone_{SEED}"),
    ]

    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        stdout = proc.stdout
        stderr = proc.stderr
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        stdout = ""
        stderr = "TIMEOUT after 15 minutes"
        rc = -1
    except Exception as e:
        stdout = ""
        stderr = f"Exception: {e}"
        rc = -2

    elapsed = time.time() - start

    # Parse status from output
    status = "unknown"
    failed_step = "n/a"
    for line in stdout.splitlines():
        if "Run aborted" in line:
            status = "aborted"
        if "failed_step:" in line:
            failed_step = line.split("failed_step:", 1)[1].strip()
        if "=== RUN STATUS:" in line:
            status = line.split("=== RUN STATUS:", 1)[1].strip().split()[0]

    if rc == 0 and status == "unknown":
        status = "success"
    elif rc != 0 and status == "unknown":
        status = "failed"

    results.append({
        "model": model,
        "profile_key": profile_key,
        "status": status,
        "failed_step": failed_step,
        "rc": rc,
        "elapsed": round(elapsed, 1),
        "stdout": stdout,
        "stderr": stderr,
    })

    # Save individual output
    out_dir = REPO / "logs" / f"batch_5phone_{SEED}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{model}_{profile_key}.txt").write_text(
        f"=== STDOUT ===\n{stdout}\n\n=== STDERR ===\n{stderr}\n\nRC={rc} elapsed={elapsed:.1f}s\n",
        encoding="utf-8",
    )

    print(f"  -> {status} | rc={rc} | {elapsed:.1f}s | failed_step={failed_step}")

    # Brief pause before next phone
    time.sleep(5)

# Final summary
print("\n" + "="*60)
print("BATCH SUMMARY")
print("="*60)
print(f"{'Phone':<12} {'Profile':<20} {'Status':<12} {'Failed Step'}")
print("-"*60)
for r in results:
    print(f"{r['model']:<12} {r['profile_key']:<20} {r['status']:<12} {r['failed_step']}")
print("="*60)
