"""
Concurrent account warming pipeline - runs up to 3 accounts at a time.

Each account has its own unique ISP proxy port, so concurrent sessions
do not share IP addresses. Proxy guard runs before every session.

Usage:
  python run_pipeline.py --all                  Run all eligible accounts
  python run_pipeline.py --start-from acc_012   Resume from a specific account
  python run_pipeline.py --account acc_006      Run a single account (same as run.py)

Safety:
  - Proxy guard runs before every session (IP verified each time)
  - Each account has a unique ISP proxy port - no IP sharing
  - Continues on failure - one bad account doesn't block the rest
  - Progress saved to pipeline_state.json for resumability
"""

import argparse
import subprocess
import sys
import json
import re
import time
from datetime import datetime, time as dt_time
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from core.paths import DATA_DIR, STATE_DIR, ACCOUNTS_FILE
from core.account_store import get_account_store

PIPELINE_STATE_FILE = STATE_DIR / "pipeline_state.json"
RUN_PY = SCRIPT_DIR / "run.py"
PYTHON_EXE = sys.executable

ACTIVE_HOURS_START = 7
ACTIVE_HOURS_END = 22
SESSION_TIMEOUT_SEC = 7200  # 120 minutes
MAX_CONCURRENT = 6  # each account has unique ISP proxy port, safe to parallelize


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat()


def _load_accounts() -> list[dict]:
    store = get_account_store()
    return store.get_desktop_accounts()


def _within_active_hours(account: dict) -> bool:
    hours = account.get("active_hours", [ACTIVE_HOURS_START, ACTIVE_HOURS_END])
    if not hours or len(hours) < 2:
        hours = [ACTIVE_HOURS_START, ACTIVE_HOURS_END]
    now = datetime.now().time()
    start = dt_time(hours[0], 0)
    end = dt_time(hours[1], 0)
    if start <= end:
        return start <= now <= end
    else:
        return now >= start or now <= end


def _load_state() -> dict:
    if PIPELINE_STATE_FILE.exists():
        try:
            return json.loads(PIPELINE_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": [], "last_account": None, "started_at": None, "rounds": 0}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PIPELINE_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _parse_proxy_ip(output: str) -> str | None:
    """Extract exit IP from proxy guard line."""
    m = re.search(r"exit IP\s*=\s*([\d.]+)", output)
    return m.group(1) if m else None


def _parse_login(output: str) -> str | None:
    """Extract login status."""
    m = re.search(r"Login check:\s*(\S+)", output)
    return m.group(1) if m else None


def _parse_activities(output: str) -> list[str]:
    """Extract planned activity list."""
    m = re.search(r"Planned activities:\s*(\[.*?\])", output)
    if m:
        try:
            return json.loads(m.group(1).replace("'", '"'))
        except Exception:
            pass
    return []


def _parse_errors(output: str) -> list[str]:
    """Extract ERROR and WARNING lines."""
    issues = []
    for line in output.splitlines():
        if "ERROR" in line or "WARNING" in line:
            issues.append(line.strip())
    return issues


def _parse_duration(output: str) -> str | None:
    """Estimate session duration from first and last timestamp lines."""
    timestamps = re.findall(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", output, re.MULTILINE)
    if len(timestamps) >= 2:
        try:
            fmt = "%Y-%m-%d %H:%M:%S"
            t1 = datetime.strptime(timestamps[0], fmt)
            t2 = datetime.strptime(timestamps[-1], fmt)
            secs = (t2 - t1).total_seconds()
            mins = int(secs // 60)
            secs = int(secs % 60)
            return f"{mins}m{secs}s"
        except Exception:
            pass
    return None


def _print_summary(account_id: str, ip: str | None, login: str | None,
                   num_acts: int, duration: str | None, issues: list[str]) -> None:
    """Print a clean one-line summary."""
    ip_str = ip or "NO_IP"
    login_str = login or "?"
    issues_str = f" | {len(issues)} issues" if issues else " | clean"
    dur_str = f" | {duration}" if duration else ""
    print(f"\n{'='*90}")
    print(f"  {account_id}  |  IP {ip_str}  |  login={login_str}  |  {num_acts} acts{issues_str}{dur_str}")
    for issue in issues:
        print(f"    !! {issue}")
    print(f"{'='*90}\n")


# ── Concurrent runner ──────────────────────────────────────────────────────────

def _launch_one(account_id: str, dry_run: bool = False) -> subprocess.Popen:
    """Launch a single account via run.py. Non-blocking — returns the Popen process."""
    cmd = [PYTHON_EXE, str(RUN_PY), "--account", account_id]
    if dry_run:
        cmd.append("--dry-run")

    print(f"\n[{_now_iso()}] Starting {account_id} ...")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(SCRIPT_DIR),
    )


def _run_with_pool(account_ids: list[str], max_concurrent: int,
                    dry_run: bool = False) -> tuple[int, int]:
    """
    Run accounts concurrently, up to max_concurrent at a time.
    Each account has a unique ISP proxy port — no IP sharing risk.
    Returns (ok_count, fail_count).
    """
    running: dict[str, tuple[subprocess.Popen, float]] = {}  # aid -> (proc, started_at)
    ok_count = 0
    fail_count = 0
    HUNG_SESSION_SEC = 2400  # 40 min — kill hung sessions that are stuck in retry loops

    for i, account_id in enumerate(account_ids):
        # Check active hours before launching — don't start new sessions past 22:00
        if not _within_active_hours({"active_hours": [ACTIVE_HOURS_START, ACTIVE_HOURS_END]}):
            print("Outside active hours — not launching more accounts.")
            break

        # Wait for a slot to open if we're at max concurrent
        while len(running) >= max_concurrent:
            # Check for hung sessions (stuck in Playwright retry loops, etc.)
            now = time.time()
            for aid, (p, started) in list(running.items()):
                if now - started > HUNG_SESSION_SEC and p.poll() is None:
                    print(f"  !! {aid}: hung for {int((now-started)/60)}m — killing")
                    p.kill()
                    time.sleep(0.5)

            # Check all running procs — handle any that finished or were killed
            finished = [aid for aid, (p, _) in running.items() if p.poll() is not None]
            if finished:
                aid = finished[0]
                p, _ = running.pop(aid)
                output = (p.stdout.read() or "") + (p.stderr.read() or "")
                ok = p.returncode in (0, -9)  # -9 = killed by watchdog

                ip = _parse_proxy_ip(output)
                login = _parse_login(output)
                acts = _parse_activities(output)
                issues = _parse_errors(output)
                duration = _parse_duration(output)
                _print_summary(aid, ip, login, len(acts), duration, issues)

                if ok:
                    ok_count += 1
                else:
                    fail_count += 1
            else:
                time.sleep(2)  # brief pause before re-checking

        # Launch the next account
        running[account_id] = (_launch_one(account_id, dry_run=dry_run), time.time())
        time.sleep(3)  # stagger launches slightly (ML API rate limit)

    # Wait for all remaining processes to finish
    while running:
        # Check for hung sessions
        now = time.time()
        for aid, (p, started) in list(running.items()):
            if now - started > HUNG_SESSION_SEC and p.poll() is None:
                print(f"  !! {aid}: hung for {int((now-started)/60)}m — killing")
                p.kill()
                time.sleep(0.5)

        finished = [aid for aid, (p, _) in running.items() if p.poll() is not None]
        if finished:
            for aid in finished:
                p, _ = running.pop(aid)
                output = (p.stdout.read() or "") + (p.stderr.read() or "")
                ok = p.returncode in (0, -9)

                ip = _parse_proxy_ip(output)
                login = _parse_login(output)
                acts = _parse_activities(output)
                issues = _parse_errors(output)
                duration = _parse_duration(output)
                _print_summary(aid, ip, login, len(acts), duration, issues)

                if ok:
                    ok_count += 1
                else:
                    fail_count += 1
        else:
            time.sleep(2)

    return ok_count, fail_count


def _load_schedule() -> dict:
    """Load the schedule YAML to read sessions_per_day and session_gap_hours."""
    schedule_path = SCRIPT_DIR / "config" / "schedule.yaml"
    try:
        import yaml
        with open(schedule_path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


def _get_max_sessions_per_day(schedule: dict) -> int:
    """Return the highest sessions_per_day across all week bands."""
    max_sessions = 1
    for key in schedule:
        if key.startswith("weeks_"):
            spd = schedule[key].get("sessions_per_day", 1)
            if spd > max_sessions:
                max_sessions = spd
    return max_sessions


def _sleep_until_active_hours() -> None:
    """Sleep until the next active-hours window (07:00)."""
    from datetime import timedelta
    now = datetime.now()
    tomorrow_start = now.replace(hour=ACTIVE_HOURS_START, minute=0, second=0, microsecond=0)
    if now >= tomorrow_start:
        # Past 07:00 today — sleep until 07:00 tomorrow
        tomorrow_start = tomorrow_start + timedelta(days=1)
    wait_secs = (tomorrow_start - now).total_seconds()
    wait_mins = int(wait_secs / 60)
    print(f"Outside active hours — sleeping {wait_mins} min until {tomorrow_start.strftime('%H:%M')} ...")
    time.sleep(wait_secs)


def _get_min_gap_hours(schedule: dict) -> int:
    """Return the shortest session_gap_hours[0] across all week bands."""
    min_gap = 2  # default
    for key in schedule:
        if key.startswith("weeks_"):
            gap_range = schedule[key].get("session_gap_hours", [2, 6])
            if gap_range[0] < min_gap:
                min_gap = gap_range[0]
    return min_gap


def main():
    parser = argparse.ArgumentParser(description="Sequential account warming pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run all eligible accounts")
    group.add_argument("--start-from", type=str, metavar="ACC_ID", help="Resume from a specific account")
    group.add_argument("--account", type=str, metavar="ACC_ID", help="Run a single account")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without opening browser")
    parser.add_argument("--rounds", type=int, default=0, help="Override number of rounds (0 = auto from schedule)")
    args = parser.parse_args()

    # ── Load accounts ────────────────────────────────────────────────────────
    all_accounts = _load_accounts()
    if not all_accounts:
        print("No accounts found.")
        return

    # Filter by active hours
    eligible = [a for a in all_accounts if _within_active_hours(a)]
    skipped = len(all_accounts) - len(eligible)
    if skipped:
        outside = [a["id"] for a in all_accounts if not _within_active_hours(a)]
        print(f"Outside active hours ({skipped}): {', '.join(outside)}")
    if not eligible:
        print("No accounts within active hours.")
        return

    # ── Load schedule for multi-round config ─────────────────────────────────
    schedule = _load_schedule()
    max_rounds = args.rounds if args.rounds > 0 else _get_max_sessions_per_day(schedule)
    min_gap_hours = _get_min_gap_hours(schedule)
    min_gap_secs = min_gap_hours * 3600

    # ── Determine run list ───────────────────────────────────────────────────
    state = _load_state()
    eligible_ids = sorted([a["id"] for a in eligible])

    # ── Single-account mode: run once and exit ──────────────────────────────
    if args.account:
        if args.account not in [a["id"] for a in all_accounts]:
            print(f"Account {args.account} not found.")
            return
        ok, output = run_one(args.account, dry_run=args.dry_run)
        ip = _parse_proxy_ip(output)
        login = _parse_login(output)
        acts = _parse_activities(output)
        issues = _parse_errors(output)
        duration = _parse_duration(output)
        _print_summary(args.account, ip, login, len(acts), duration, issues)
        return

    # ── Continuous daily loop (--all / --start-from) ────────────────────────
    print(f"Max {max_rounds} rounds/day, min gap {min_gap_hours}h between rounds")

    if args.dry_run:
        print(f"DRY RUN - would run: {', '.join(eligible_ids)}")

    while True:
        # If outside active hours, sleep until morning before anything else
        if not _within_active_hours({"active_hours": [ACTIVE_HOURS_START, ACTIVE_HOURS_END]}):
            _sleep_until_active_hours()

        # Recompute eligible — accounts may have entered active hours
        eligible = [a for a in all_accounts if _within_active_hours(a)]
        if not eligible:
            print("No accounts in active hours.")
            _sleep_until_active_hours()
            continue

        eligible_ids = sorted([a["id"] for a in eligible])
        run_list = eligible_ids

        # On first day, trim to --start-from point (one-time)
        if args.start_from and args.start_from in eligible_ids:
            idx = eligible_ids.index(args.start_from)
            run_list = eligible_ids[idx:]
            print(f"Resuming from {args.start_from} ({len(run_list)} accounts)")
            args.start_from = None  # don't trim on subsequent days

        # Reset per-day state
        state["current_round"] = 1
        state["completed"] = []
        _save_state(state)

        current_round = state.get("current_round", 1)
        daily_ok = 0
        daily_fail = 0

        for round_num in range(current_round, max_rounds + 1):
            if not _within_active_hours({"active_hours": [ACTIVE_HOURS_START, ACTIVE_HOURS_END]}):
                _sleep_until_active_hours()
                # Recompute eligible after waking
                eligible = [a for a in all_accounts if _within_active_hours(a)]
                eligible_ids = sorted([a["id"] for a in eligible])
                run_list = eligible_ids  # reset for new round after overnight sleep

            # Reset per-round tracking
            state["current_round"] = round_num
            state["completed"] = []
            _save_state(state)

            # Recompute run_list from full eligible set each round
            # (only the first round gets trimmed by --start-from)
            if round_num > 1 or not args.start_from:
                eligible_now = [a for a in all_accounts if _within_active_hours(a)]
                run_list = sorted([a["id"] for a in eligible_now])

            print(f"\n{'#'*80}")
            print(f"  ROUND {round_num}/{max_rounds} — {len(run_list)} accounts")
            print(f"  Max concurrent: {MAX_CONCURRENT}")
            print(f"{'#'*80}")

            round_ok, round_fail = _run_with_pool(
                run_list, MAX_CONCURRENT, dry_run=args.dry_run
            )

            # Update state with all completed accounts
            for account_id in run_list:
                state["last_account"] = account_id
                state["completed"].append(account_id)
            state["started_at"] = state["started_at"] or _now_iso()
            _save_state(state)

            daily_ok += round_ok
            daily_fail += round_fail

            print(f"\nRound {round_num} complete: {round_ok} OK, {round_fail} FAILED")

            # Wait between rounds (if more rounds remain)
            if round_num < max_rounds:
                gap_mins = round(min_gap_secs / 60)
                print(f"Waiting {gap_mins} minutes before round {round_num + 1}...")
                # Sleep in 60s chunks so we re-check active hours
                for _ in range(int(min_gap_secs / 60)):
                    if not _within_active_hours({"active_hours": [ACTIVE_HOURS_START, ACTIVE_HOURS_END]}):
                        _sleep_until_active_hours()
                    time.sleep(60)

        # ── Day complete ──────────────────────────────────────────────────────
        print(f"\n{'='*90}")
        print(f"  Day complete: {daily_ok} OK, {daily_fail} FAILED across {max_rounds} rounds")
        print(f"  Sleeping until tomorrow 07:00 ...")
        print(f"{'='*90}")
        _sleep_until_active_hours()


if __name__ == "__main__":
    main()
