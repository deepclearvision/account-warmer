"""
Bulk Google login runner — logs all GeelarK accounts in sequentially.

Usage (from account-warmer/):
    python ..\bulk_login_run.py                  # all accounts not yet logged in
    python ..\bulk_login_run.py --all            # force-retry even already-logged-in accounts
    python ..\bulk_login_run.py --account acc_033  # single account

Skips:
  - accounts with no geelark_phone_id (not provisioned)
  - accounts with no totp_secret unless --all is set (would likely fail)
  - acc_008 (dennislambartt@gmail.com) — no TOTP secret

Results are written back to geelark_accounts.yaml as login_status: "success" / "failed".
A summary table is printed at the end.
"""
import argparse
import os
import sys
import time
import yaml
from pathlib import Path

# ── Bootstrap environment ────────────────────────────────────────────────────
# Must run from account-warmer/ so that core/ and activities/ are importable.
_here = Path(__file__).parent
_warmer = _here / "account-warmer"
if _warmer.exists():
    os.chdir(_warmer)
    sys.path.insert(0, str(_warmer))
else:
    # Already inside account-warmer/
    sys.path.insert(0, str(_here))

_env_file = Path("warmer.env")
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

import logging

# Write to a file directly from Python so output isn't lost to OS buffering
_LOG_FILE = Path(r"C:\Windows\Temp\bulk_login.log")
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s", datefmt="%H:%M:%S")

_fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)

logging.root.setLevel(logging.INFO)
logging.root.addHandler(_fh)

log = logging.getLogger("bulk_login")

ACCOUNTS_FILE = Path(r"C:\WarmingData\geelark_accounts.yaml")

# Accounts with no TOTP — skipped always
NO_TOTP_IDS = {"acc_008"}   # dennislambartt@gmail.com

# Accounts with uncertain/wrong TOTP secrets — NOT from AYCD CSV.
# These will always fail at the 2FA step until correct secrets are provided.
# Skip them by default; pass --include-uncertain to attempt anyway.
UNCERTAIN_TOTP_IDS = {
    "acc_004", "acc_005", "acc_006", "acc_007",
    "acc_010", "acc_012", "acc_013", "acc_014", "acc_015",
    "acc_018", "acc_020", "acc_022", "acc_023", "acc_024",
    "acc_026", "acc_028", "acc_029", "acc_030", "acc_031",
}

# ── Argument parsing ─────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Bulk Google login for GeelarK phones")
parser.add_argument("--all", action="store_true",
                    help="Retry accounts already marked login_status=success")
parser.add_argument("--account", metavar="ID",
                    help="Run a single account by ID (e.g. acc_033)")
parser.add_argument("--include-uncertain", action="store_true",
                    help="Also attempt acc_004-031 (TOTP secrets now confirmed from CSV)")
args = parser.parse_args()

# ── Load accounts ────────────────────────────────────────────────────────────
with open(ACCOUNTS_FILE, encoding="utf-8") as f:
    data = yaml.safe_load(f)

all_accounts = data.get("accounts", [])

# ── Filter to candidates ─────────────────────────────────────────────────────
if args.account:
    candidates = [a for a in all_accounts if a.get("id") == args.account]
    if not candidates:
        log.error("Account %r not found in geelark_accounts.yaml", args.account)
        sys.exit(1)
else:
    candidates = []
    for a in all_accounts:
        acc_id   = a.get("id", "?")
        phone_id = a.get("geelark_phone_id", "")
        status   = a.get("login_status", "unknown")
        totp     = (a.get("totp_secret") or "").strip()

        if not phone_id:
            log.info("SKIP %-12s — no phone provisioned", acc_id)
            continue

        if acc_id in NO_TOTP_IDS:
            log.info("SKIP %-12s — no TOTP secret (account excluded)", acc_id)
            continue

        if not totp:
            log.info("SKIP %-12s — no TOTP secret", acc_id)
            continue

        if acc_id in UNCERTAIN_TOTP_IDS and not getattr(args, "include_uncertain", False):
            log.info("SKIP %-12s — uncertain TOTP secret (use --include-uncertain to attempt)",
                     acc_id)
            continue

        if status == "success" and not args.all:
            log.info("SKIP %-12s — already logged in (pass --all to retry)", acc_id)
            continue

        candidates.append(a)

if not candidates:
    log.info("No accounts to process.")
    sys.exit(0)

log.info("Processing %d account(s): %s",
         len(candidates),
         ", ".join(a.get("id", "?") for a in candidates))

# ── Import login module ──────────────────────────────────────────────────────
from activities.google_login_mobile import run_google_login

# ── Run logins sequentially ──────────────────────────────────────────────────
results = []   # list of (acc_id, email, success, diagnosis)

for idx, account in enumerate(candidates, 1):
    acc_id = account.get("id", "?")
    email  = account.get("email", "?")
    log.info("")
    log.info("═" * 70)
    log.info("ACCOUNT %d/%d  %s  (%s)", idx, len(candidates), acc_id, email)
    log.info("═" * 70)

    try:
        result = run_google_login(account, stop_phone_on_success=True)
    except Exception as exc:
        log.exception("[%s] run_google_login raised: %s", acc_id, exc)
        result = {"success": False, "diagnosis": str(exc), "attempts": 0, "error": str(exc)}

    success   = result.get("success", False)
    diagnosis = result.get("diagnosis") or result.get("error", "")
    attempts  = result.get("attempts", 0)

    log.info("[%s] Result: success=%s  attempts=%d  %s",
             acc_id, success, attempts, diagnosis[:120])

    results.append((acc_id, email, success, diagnosis))

    # ── Write login_status back to YAML ─────────────────────────────────────
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        live = yaml.safe_load(f)
    for a in live.get("accounts", []):
        if a.get("id") == acc_id:
            a["login_status"] = "success" if success else "failed"
            break
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(live, f, allow_unicode=True, sort_keys=False)
    log.info("[%s] login_status written to YAML.", acc_id)

    # Brief cooldown between accounts (proxy rate limit is 180s per rotation;
    # each run already spends time booting + logging in, so this is just a buffer)
    if idx < len(candidates):
        log.info("Waiting 10s before next account …")
        time.sleep(10)

# ── Summary ──────────────────────────────────────────────────────────────────
log.info("")
log.info("═" * 70)
log.info("BULK LOGIN SUMMARY")
log.info("═" * 70)
succeeded = [r for r in results if r[2]]
failed    = [r for r in results if not r[2]]

log.info("SUCCESS (%d):", len(succeeded))
for acc_id, email, _, diag in succeeded:
    log.info("  ✓ %-12s  %s", acc_id, email)

if failed:
    log.info("FAILED (%d):", len(failed))
    for acc_id, email, _, diag in failed:
        log.info("  ✗ %-12s  %s — %s", acc_id, email, diag[:80])

log.info("Total: %d/%d succeeded", len(succeeded), len(results))
