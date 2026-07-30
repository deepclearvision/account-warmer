"""
daily_mobile_batch_runner.py — Standalone Daily Mobile Warm-Up Batch Runner

Runs one warm-up session per account, per day, on GeelarK cloud phones
via the geelark_orchestrator/scripts/_run_with_youtube.py subprocess.

Default behaviour (no --script flag):
  Follows the monthly schedule (_day_to_script):
    • Most days: local-discovery (Maps "near me" search)
    • Day 3: money-kw (commercial keyword searches)
    • Days 11, 21: brand-1km (named business navigation)

YouTube is mixed into every run via _run_with_youtube.py:
  Default --youtube random: 25% before, 35% after, 10% both, 30% none

After all accounts complete, the runner computes the next run time (random
minute within 08:00–18:00 tomorrow) and sleeps until then.

Usage:
  python daily_mobile_batch_runner.py                              # daemon mode
  python daily_mobile_batch_runner.py --once                       # one cycle
  python daily_mobile_batch_runner.py --status                     # show status
  python daily_mobile_batch_runner.py --account acc_042 --once     # single account
  python daily_mobile_batch_runner.py --account acc_042 --once --script brand-1km --youtube both
"""

import argparse
import csv
import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
PROJECT_ROOT       = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
RUNNER_SCRIPT      = PROJECT_ROOT / "geelark_orchestrator" / "scripts" / "_run_with_youtube.py"
CSV_PATH           = PROJECT_ROOT / "data" / "accounts_business_mapping.csv"
GEELARK_ACCOUNTS   = Path(os.environ.get("WARMER_DATA_DIR", r"C:\WarmingData")) / "geelark_accounts.yaml"
SESSION_LOG        = Path(os.environ.get("WARMER_DATA_DIR", r"C:\WarmingData")) / "logs" / "mobile_sessions.json"
BATCH_LOG          = Path(os.environ.get("WARMER_DATA_DIR", r"C:\WarmingData")) / "logs" / "daily_mobile_batch.log"
ACTIVE_HOUR_START  = 8
ACTIVE_HOUR_END    = 18
SUBPROCESS_TIMEOUT = 1200  # 20 minutes per account


# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    BATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)
    fh = logging.FileHandler(str(BATCH_LOG), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"))
    logger.addHandler(fh)

log = logging.getLogger("daily_batch")


# ── Account loading ───────────────────────────────────────────────────────────

def load_accounts(pc_filter: str = "") -> list[dict]:
    """Load enabled accounts from the CSV mapping file."""
    if not CSV_PATH.exists():
        log.error("CSV mapping not found at %s", CSV_PATH)
        return []
    accounts = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            acc_id = row.get("account_id", "")
            phone  = row.get("geelark_phone_id", "")
            if not phone:
                continue
            # Check geelark_accounts.yaml for warming-enabled flag
            accounts.append({
                "id": acc_id,
                "phone_id": phone,
                "business_name": row.get("business_name", ""),
                "business_lat":  row.get("business_lat", ""),
                "business_lng":  row.get("business_lng", ""),
                "email": row.get("email", ""),
            })
    # Filter by PC category if provided
    if pc_filter and GEELARK_ACCOUNTS.exists():
        import yaml
        try:
            data = yaml.safe_load(GEELARK_ACCOUNTS.read_text(encoding="utf-8")) or {}
            cat_map = {}
            for a in data.get("accounts", []):
                cat_map[a["id"]] = a.get("category", "")
            accounts = [a for a in accounts if cat_map.get(a["id"], "") == pc_filter]
            log.info("PC filter %r: %d account(s)", pc_filter, len(accounts))
        except Exception:
            pass

    # Filter out accounts with mobile_warming_enabled=False
    if GEELARK_ACCOUNTS.exists():
        import yaml
        try:
            data = yaml.safe_load(GEELARK_ACCOUNTS.read_text(encoding="utf-8")) or {}
            disabled = set()
            for a in data.get("accounts", []):
                if a.get("mobile_warming_enabled") is False:
                    disabled.add(a["id"])
            accounts = [a for a in accounts if a["id"] not in disabled]
        except Exception:
            pass

    log.info("Loaded %d account(s) with phone_id and warming enabled.", len(accounts))
    return accounts


# ── Schedule ──────────────────────────────────────────────────────────────────

def _day_to_script(day: int) -> str:
    """Monthly schedule — same as mobile_warmup._day_to_script()."""
    cycle = ((day - 1) % 30) + 1
    if day <= 30:
        if cycle == 3:
            return "money-kw"
        if cycle in (11, 21):
            return "brand-1km"
        return "local-discovery"
    if cycle % 10 == 0:
        return "brand-1km"
    return "local-discovery"


# ── Session logger ────────────────────────────────────────────────────────────

def _log_session(acc_id: str, mode: str, youtube_timing: str, success: bool,
                 duration_s: float, stdout: str = "", stderr: str = "") -> None:
    """Append a session record to mobile_sessions.json."""
    SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
    records: list = []
    if SESSION_LOG.exists():
        try:
            records = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
        except Exception:
            records = []
    records.append({
        "account_id":      acc_id,
        "timestamp":       datetime.utcnow().isoformat(),
        "mode":            mode,
        "youtube_timing":  youtube_timing,
        "success":         success,
        "duration_s":      round(duration_s, 1),
    })
    SESSION_LOG.write_text(json.dumps(records, indent=2), encoding="utf-8")


# ── Subprocess runner ─────────────────────────────────────────────────────────

def run_one_subprocess(acc: dict, mode: str, youtube_timing: str) -> dict:
    """Run _run_with_youtube.py as a subprocess for one account."""
    acc_id = acc["id"]
    cmd = [
        sys.executable, str(RUNNER_SCRIPT),
        mode, acc_id,
        "--youtube", youtube_timing,
    ]
    log.info("[%s] Subprocess: %s", acc_id, " ".join(cmd))
    t_start = time.time()
    try:
        result = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
        )
        duration = time.time() - t_start
        success = result.returncode == 0
        # Log stdout/stderr
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                log.debug("[%s] %s", acc_id, line)
        if result.stderr and not success:
            for line in result.stderr.strip().splitlines():
                log.warning("[%s] STDERR: %s", acc_id, line)
        _log_session(acc_id, mode, youtube_timing, success, duration,
                     stdout=result.stdout, stderr=result.stderr)
        status = "OK" if success else "FAILED (rc=%d)" % result.returncode
        log.info("[%s] %s | mode=%s | youtube=%s | duration=%.0fs",
                 acc_id, status, mode, youtube_timing, duration)
        return {"account_id": acc_id, "success": success, "mode": mode,
                "youtube_timing": youtube_timing, "duration_s": duration}
    except subprocess.TimeoutExpired:
        duration = time.time() - t_start
        log.error("[%s] TIMEOUT after %.0fs", acc_id, duration)
        _log_session(acc_id, mode, youtube_timing, False, duration)
        return {"account_id": acc_id, "success": False, "mode": mode,
                "youtube_timing": youtube_timing, "duration_s": duration,
                "error": "timeout"}
    except Exception as e:
        duration = time.time() - t_start
        log.error("[%s] Subprocess crashed: %s", acc_id, e)
        _log_session(acc_id, mode, youtube_timing, False, duration)
        return {"account_id": acc_id, "success": False, "mode": mode,
                "youtube_timing": youtube_timing, "duration_s": duration,
                "error": str(e)}


# ── Full batch cycle ──────────────────────────────────────────────────────────

def run_batch_cycle(accounts: list[dict], force_script: str | None = None,
                    youtube_timing: str = "random") -> list[dict]:
    """Run one warm-up session for every enabled account, sequentially."""
    results = []
    if not accounts:
        log.info("No accounts to run.")
        return results

    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    done_today: set[str] = set()
    if SESSION_LOG.exists():
        try:
            records = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
            for r in records:
                if r.get("timestamp", "").startswith(today_str) and r.get("success"):
                    done_today.add(r.get("account_id", ""))
        except Exception:
            pass

    to_run  = [a for a in accounts if a["id"] not in done_today]
    skipped = [a for a in accounts if a["id"] in done_today]

    if skipped:
        log.info("Skipping %d account(s) already warmed today: %s",
                 len(skipped), [a["id"] for a in skipped])
        for acc in skipped:
            results.append({"account_id": acc["id"], "success": True,
                            "skipped": True, "reason": "already_done_today"})

    if not to_run:
        log.info("All accounts already warmed today — nothing to do.")
        return results

    # Compute schedule day once for this run
    schedule_day = 1
    try:
        warmup_start = datetime(2025, 1, 1)  # fallback
        schedule_day = max(1, (datetime.now() - warmup_start).days + 1)
    except Exception:
        pass

    log.info("Starting batch cycle for %d account(s) (%d done). Schedule day ~%d.",
             len(to_run), len(skipped), schedule_day)

    for i, acc in enumerate(to_run):
        log.info("── Account %d/%d: %s ──", i + 1, len(to_run), acc["id"])
        mode = force_script or _day_to_script(schedule_day)
        r = run_one_subprocess(acc, mode, youtube_timing)
        results.append(r)
        time.sleep(3)

    ok = sum(1 for r in results if r.get("success") and not r.get("skipped"))
    failed = sum(1 for r in results if not r.get("success") and not r.get("skipped"))
    log.info("Batch cycle complete. OK: %d  Failed: %d  Skipped: %d",
             ok, failed, len(skipped))
    return results


# ── Next-run scheduler ────────────────────────────────────────────────────────

def _compute_next_run() -> float:
    now = datetime.now()
    tomorrow = now.date() + timedelta(days=1)
    active_minutes = (ACTIVE_HOUR_END - ACTIVE_HOUR_START) * 60
    offset_minutes = random.randint(0, active_minutes - 1)
    run_time = datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                        ACTIVE_HOUR_START, 0, 0) + timedelta(minutes=offset_minutes)
    return run_time.timestamp()


def _sleep_until(target_ts: float) -> None:
    while True:
        remaining = target_ts - time.time()
        if remaining <= 0:
            break
        if remaining > 3600:
            log.info("Next run in %.1f hours.", remaining / 3600)
            time.sleep(min(remaining, 3600))
        elif remaining > 60:
            log.info("Next run in %.0f minutes.", remaining / 60)
            time.sleep(min(remaining, 300))
        else:
            time.sleep(remaining)


# ── Status reporter ───────────────────────────────────────────────────────────

def show_status() -> None:
    if not SESSION_LOG.exists():
        print("No session log found.")
        return
    try:
        records = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed: {e}")
        return
    last: dict = {}
    for r in records:
        last[r["account_id"]] = r
    header = f"{'Account':<12} {'Timestamp':<22} {'Mode':<16} {'YT':<9} {'Dur':>6}  {'OK':>5}"
    print(f"\n{header}")
    print("-" * len(header))
    for acc_id, r in sorted(last.items()):
        ts  = r.get("timestamp", "")[:19].replace("T", " ")
        md  = r.get("mode", "?")
        yt  = r.get("youtube_timing", "?")
        dur = f"{r.get('duration_s', 0):.0f}s"
        ok  = "✓" if r.get("success") else "✗"
        print(f"{acc_id:<12} {ts:<22} {md:<16} {yt:<9} {dur:>6}  {ok:>5}")
    print()


# ── Signal handling ───────────────────────────────────────────────────────────

_shutdown_requested = False

def _on_shutdown(signum, frame) -> None:
    global _shutdown_requested
    log.info("Shutdown signal %d. Exiting after cycle.", signum)
    _shutdown_requested = True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Daily mobile warm-up batch runner — subprocess orchestration.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="Run one batch cycle and exit.")
    mode.add_argument("--daemon", action="store_true",
                      help="Run continuously (default).")
    parser.add_argument("--account", metavar="ID",
                        help="Run a single account and exit.")
    parser.add_argument("--script", type=str,
                        choices=["local-discovery", "money-kw", "brand-1km"],
                        help="Force a specific schedule script.")
    parser.add_argument("--youtube", type=str,
                        choices=["before", "after", "both", "none", "random"],
                        default="random",
                        help="YouTube timing (default: random).")
    parser.add_argument("--status", action="store_true",
                        help="Print last session per account and exit.")
    parser.add_argument("--pc", type=str,
                        help="Only run accounts matching this PC category.")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    accounts = load_accounts(args.pc)
    if not accounts:
        log.error("No enabled accounts found. Exiting.")
        sys.exit(1)

    # ── Single-account mode ────────────────────────────────────────────────
    if args.account:
        acc = next((a for a in accounts if a["id"] == args.account), None)
        if not acc:
            log.error("Account %r not found.", args.account)
            sys.exit(1)
        mode = args.script or _day_to_script(max(1, (datetime.now() - datetime(2025,1,1)).days + 1))
        log.info("Single-account: %s | mode=%s | youtube=%s", acc["id"], mode, args.youtube)
        r = run_one_subprocess(acc, mode, args.youtube)
        log.info("Result: %s", "OK" if r["success"] else "FAILED")
        return

    # ── Once mode ──────────────────────────────────────────────────────────
    if args.once:
        log.info("Once mode.")
        run_batch_cycle(accounts, force_script=args.script, youtube_timing=args.youtube)
        return

    # ── Daemon mode ────────────────────────────────────────────────────────
    log.info("Daemon mode — daily between %02d:00–%02d:00.", ACTIVE_HOUR_START, ACTIVE_HOUR_END)
    signal.signal(signal.SIGINT, _on_shutdown)
    signal.signal(signal.SIGTERM, _on_shutdown)

    while not _shutdown_requested:
        log.info("══════════ Starting daily batch cycle ══════════")
        run_batch_cycle(accounts, force_script=args.script, youtube_timing=args.youtube)
        if _shutdown_requested:
            break
        next_ts = _compute_next_run()
        log.info("Next run: %s", datetime.fromtimestamp(next_ts).strftime("%Y-%m-%d %H:%M"))
        _sleep_until(next_ts)

    log.info("Daily batch runner exiting.")


if __name__ == "__main__":
    main()
