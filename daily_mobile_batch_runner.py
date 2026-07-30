"""
daily_mobile_batch_runner.py — Standalone Daily Mobile Warm-Up Batch Runner

Runs one warm-up session per account, per day, on GeelarK cloud phones.
All sessions are strictly sequential (one phone at a time) because all
phones share a single StreamVia mobile proxy.

Default behaviour (no --activity flag):
  Follows the monthly schedule from mobile_warmup._day_to_script():
    • Most days: local_discovery (Maps "near me" search + optional YouTube)
    • Day 3: money_kw (commercial keyword searches + optional YouTube)
    • Days 11, 21: brand_1km (named business navigation + GPS variation)

Testing behaviour (--activity flag):
  Runs the specified activity on every account instead of the schedule.

After all accounts complete, the runner computes the next run time (random
minute within 08:00–18:00 tomorrow) and sleeps until then.  Designed to
run as a background daemon or a one-shot tester.

Usage:
  python daily_mobile_batch_runner.py                # daemon mode (runs daily schedule)
  python daily_mobile_batch_runner.py --once         # one schedule cycle, then exit
  python daily_mobile_batch_runner.py --status       # show last session per account
  python daily_mobile_batch_runner.py --account gl_001            # single account, then exit
  python daily_mobile_batch_runner.py --account gl_001 --activity youtube  # test single activity
"""

import argparse
import json
import logging
import os
import random
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import yaml

# ── Project imports ───────────────────────────────────────────────────────────
from activities.google_login_mobile import _rotate_proxy_ip
from activities.mobile_warmup import (
    _wait_for_phone_ready,
    run_selected_warmup,
    run_warmup_session,       # schedule-driven session runner (alias for run_mobile_schedule_session)
)
from activities.mobile_location_setup import (
    ensure_location_and_permissions,
    set_gps_near_business,
    choose_business_location,
)
from core.geelark_client import GeelarKClient
from core.paths import DATA_DIR

# ── Constants ─────────────────────────────────────────────────────────────────
ACCOUNTS_FILE      = DATA_DIR / "geelark_accounts.yaml"
SESSION_LOG        = DATA_DIR / "logs" / "mobile_sessions.json"
BATCH_LOG          = DATA_DIR / "logs" / "daily_mobile_batch.log"
ACTIVE_HOUR_START  = 8    # 08:00
ACTIVE_HOUR_END    = 18   # 18:00
SESSION_TIMEOUT_S  = 20 * 60   # 20 minutes per account
ACCOUNT_GAP_S      = 5         # seconds between accounts


# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    """Configure logging to both console and the daily batch log file."""
    BATCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(str(BATCH_LOG), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"))
    logger.addHandler(fh)

log = logging.getLogger("daily_batch")


# ── Account loading ───────────────────────────────────────────────────────────

def load_accounts(pc_filter: str = "") -> list[dict]:
    """
    Load GeelarK accounts from DATA_DIR / geelark_accounts.yaml.

    Returns only accounts that have a ``geelark_phone_id`` and
    ``mobile_warming_enabled`` is not explicitly False.
    """
    if not ACCOUNTS_FILE.exists():
        log.error("geelark_accounts.yaml not found at %s", ACCOUNTS_FILE)
        return []

    try:
        data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.error("Failed to parse geelark_accounts.yaml: %s", e)
        return []

    accounts = data.get("accounts", [])
    if pc_filter:
        accounts = [a for a in accounts if a.get("category", "") == pc_filter]

    enabled = [
        a for a in accounts
        if a.get("geelark_phone_id") and a.get("mobile_warming_enabled", True)
    ]
    log.info("Loaded %d account(s) (%d enabled with phone_id).",
             len(accounts), len(enabled))
    return enabled


# ── Phone helpers ─────────────────────────────────────────────────────────────

def _is_phone_occupied(client: GeelarKClient, phone_id: str) -> bool:
    """
    Check if the phone is already running / starting / occupied.

    Returns True if the phone is busy and should be skipped.

    Reasons a phone is considered occupied:
      - GeelarK status 0 (Running) or 1 (Starting)
      - API error containing code 43021 (phone in use by another operation)
    """
    try:
        statuses = client.get_phone_status([phone_id])
        st = statuses[0].get("status") if statuses else -1
        if st in (0, 1):
            return True
    except Exception as e:
        msg = str(e)
        if "43021" in msg:
            log.debug("Phone %s: API reports in use (43021).", phone_id)
            return True
        # If we can't query status at all, err on the safe side and don't block
        log.debug("Could not query phone status: %s", e)
    return False


def _stop_phone_verified(client: GeelarKClient, phone_id: str, acc_id: str) -> bool:
    """
    Stop a GeelarK phone and verify it actually stopped (up to 3 attempts).

    Returns True if confirmed stopped, False otherwise.
    """
    for attempt in range(1, 4):
        try:
            client.stop_phone(phone_id)
        except Exception as e:
            log.warning("[%s] stop_phone attempt %d failed: %s", acc_id, attempt, e)

        time.sleep(5)

        try:
            statuses = client.get_phone_status([phone_id])
            st = statuses[0].get("status") if statuses else -1
            if st in (2, 3):  # 2=Stopped, 3=Stopping/Expired
                log.info("[%s] Phone stopped (attempt %d, status=%d).", acc_id, attempt, st)
                return True
            log.warning("[%s] Phone still running after stop attempt %d (status=%d) — retrying",
                        acc_id, attempt, st)
        except Exception as e:
            log.warning("[%s] Could not verify phone status after stop: %s", acc_id, e)
            break

    log.error("[%s] Phone %s could NOT be confirmed stopped after 3 attempts.", acc_id, phone_id)
    return False


# ── Testing-only: single-activity runner ──────────────────────────────────────
# Used when --activity is specified.  For normal operation, the schedule-driven
# run_warmup_session() handles the full lifecycle (proxy, phone, GPS, activity).

def run_one_account_activity(account: dict, activity: str) -> dict:
    """
    Run a single specified activity for one GeelarK account (testing only).

    Handles the full phone lifecycle: proxy rotation, start, boot, GPS,
    activity, stop, log.  Uses a random GPS location near the account's
    business or fallback.

    Args:
        account:  Account dict from geelark_accounts.yaml.
        activity: Activity name (maps_browse, maps_directions, youtube, etc.).

    Returns:
        dict with keys: account_id, success, activity, steps_done, duration_s,
                        ip, location_label, error
    """
    acc_id   = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id")
    result   = {
        "account_id": acc_id, "success": False, "activity": activity,
        "steps_done": [], "duration_s": 0.0, "ip": "",
        "location_label": "", "error": "",
    }

    if not phone_id:
        result["error"] = "No phone_id"
        return result

    t_start  = time.time()
    client   = GeelarKClient()
    deadline = t_start + SESSION_TIMEOUT_S

    # Resolve desktop account ID for logging
    log_acc_id = acc_id
    email = account.get("email", "").lower()
    if email:
        try:
            from core.paths import ACCOUNTS_FILE as DESKTOP_ACCOUNTS_FILE
            if DESKTOP_ACCOUNTS_FILE.exists():
                data = yaml.safe_load(DESKTOP_ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
                for a in data.get("accounts", []):
                    if a.get("email", "").lower() == email:
                        log_acc_id = a["id"]
                        break
        except Exception:
            pass

    # 0. Skip if phone is occupied
    if _is_phone_occupied(client, phone_id):
        result["error"] = "Phone already occupied (running/starting)"
        log.warning("[%s] Skipping — phone %s is already in use.", acc_id, phone_id)
        return result

    # 1. Rotate proxy IP
    log.info("[%s] Rotating proxy IP …", acc_id)
    try:
        ip = _rotate_proxy_ip()
    except Exception as e:
        log.warning("[%s] Proxy rotation failed: %s — proceeding anyway", acc_id, e)
        ip = ""
    result["ip"] = ip

    if ip:
        settle = random.uniform(5, 15)
        log.info("[%s] IP %s — waiting %.0fs before phone start.", acc_id, ip, settle)
        time.sleep(settle)

    if time.time() > deadline:
        result["error"] = "Session timed out before phone start"
        result["duration_s"] = round(time.time() - t_start, 1)
        return result

    # 2. Start phone
    log.info("[%s] Starting phone %s …", acc_id, phone_id)
    try:
        client.start_phone(phone_id)
    except Exception as e:
        result["error"] = f"Failed to start phone: {e}"
        log.error("[%s] Phone start failed: %s", acc_id, e)
        return result

    # 3. Wait for boot
    log.info("[%s] Waiting for phone to boot …", acc_id)
    if not _wait_for_phone_ready(phone_id, acc_id, timeout=90):
        log.warning("[%s] Phone did not boot in 90s — proceeding anyway", acc_id)

    if time.time() > deadline:
        log.warning("[%s] Session timeout after boot — stopping phone.", acc_id)
        _stop_phone_verified(client, phone_id, acc_id)
        result["error"] = "Session timed out after boot"
        result["duration_s"] = round(time.time() - t_start, 1)
        return result

    # 4. Set GPS location
    location_label = "unknown"
    try:
        biz = choose_business_location(account)
        if biz:
            set_gps_near_business(phone_id, account, biz, acc_id, jitter_meters=200)
            location_label = f"business: {biz.get('name', biz.get('id', '?'))}"
        else:
            ensure_location_and_permissions(phone_id, account, acc_id)
            if account.get("home_lat"):
                location_label = "home"
            elif account.get("geo_area"):
                location_label = f"area: {account['geo_area']}"
            else:
                location_label = account.get("geo_city", "london")
    except Exception as e:
        log.warning("[%s] GPS setup failed: %s — continuing without GPS", acc_id, e)
        location_label = "gps_failed"
    result["location_label"] = location_label
    log.info("[%s] Location: %s", acc_id, location_label)

    # 5. Run activity
    log.info("[%s] Running activity: %s", acc_id, activity)
    try:
        ok, label, steps = run_selected_warmup(
            phone_id, acc_id, account, activity=activity, log_acc_id=log_acc_id,
        )
        result["success"]    = ok
        result["activity"]   = label
        result["steps_done"] = steps
    except Exception as e:
        log.error("[%s] Activity %s crashed: %s", acc_id, activity, e)
        traceback.print_exc()
        result["success"]  = False
        result["activity"] = activity
        result["error"]    = str(e)

    # 6. Stop phone
    log.info("[%s] Stopping phone …", acc_id)
    _stop_phone_verified(client, phone_id, acc_id)

    # 7. Log session
    result["duration_s"] = round(time.time() - t_start, 1)
    try:
        _log_session(
            acc_id=acc_id,
            ip=result["ip"],
            activity=result["activity"],
            duration_s=result["duration_s"],
            steps_done=result["steps_done"],
            success=result["success"],
            location_label=result["location_label"],
        )
    except Exception as e:
        log.warning("[%s] Session log write failed: %s", acc_id, e)

    log.info("[%s] Session complete.  %s  duration=%.0fs  ip=%s  location=%s",
             acc_id,
             "OK" if result["success"] else "FAILED",
             result["duration_s"],
             result["ip"] or "?",
             result["location_label"])
    return result


# ── Session logger ────────────────────────────────────────────────────────────

def _log_session(acc_id: str, ip: str, activity: str, duration_s: float,
                 steps_done: list, success: bool, location_label: str) -> None:
    """Append a session record to mobile_sessions.json."""
    SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)

    records: list = []
    if SESSION_LOG.exists():
        try:
            records = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
        except Exception:
            records = []

    records.append({
        "account_id":     acc_id,
        "timestamp":      datetime.utcnow().isoformat(),
        "ip":             ip,
        "activity":       activity,
        "duration_s":     round(duration_s, 1),
        "steps_done":     steps_done,
        "success":        success,
        "location_label": location_label,
    })
    SESSION_LOG.write_text(json.dumps(records, indent=2), encoding="utf-8")


# ── Full batch cycle ──────────────────────────────────────────────────────────

def run_batch_cycle(accounts: list[dict], activity: str | None = None) -> list[dict]:
    """
    Run one warm-up session for every enabled account, sequentially.

    In schedule mode (activity=None): delegates to run_warmup_session()
    which follows the monthly _day_to_script() schedule and handles the
    full phone lifecycle internally.

    In testing mode (activity set): uses run_one_account_activity() to
    force a single activity on every account.

    Args:
        accounts: List of account dicts from geelark_accounts.yaml.
        activity: Optional forced activity (testing only).  When None,
                  the monthly schedule is followed.

    Returns:
        List of per-account result dicts.
    """
    results = []
    if not accounts:
        log.info("No accounts to run.")
        return results

    # Skip accounts already warmed today
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
            results.append({
                "account_id": acc["id"], "success": True, "skipped": True,
                "reason": "already_done_today", "steps_done": [],
                "duration_s": 0.0, "activity": "", "ip": "",
                "location_label": "", "error": "",
            })

    if not to_run:
        log.info("All accounts already warmed today — nothing to do.")
        return results

    if activity:
        mode_label = f"testing (forced activity: {activity})"
    else:
        mode_label = "schedule (monthly _day_to_script)"
    log.info("Starting batch cycle for %d account(s) (%d already done today). Mode: %s",
             len(to_run), len(skipped), mode_label)

    for i, acc in enumerate(to_run):
        log.info("── Account %d/%d: %s ──", i + 1, len(to_run), acc["id"])
        try:
            if activity:
                # ── Testing mode: force a single activity ───────────────────
                r = run_one_account_activity(acc, activity)
            else:
                # ── Schedule mode: delegate to monthly schedule ─────────────
                session_result = run_warmup_session(acc, SESSION_LOG)
                r = {
                    "account_id": acc["id"],
                    "success":    session_result.get("success", False),
                    "activity":   session_result.get("script", "schedule"),
                    "steps_done": session_result.get("steps_done", []),
                    "duration_s": session_result.get("duration_s", 0.0),
                    "ip":         session_result.get("ip", ""),
                    "location_label": "",
                    "error":      session_result.get("error", ""),
                }
        except Exception as e:
            log.error("[%s] Unhandled exception: %s", acc["id"], e)
            traceback.print_exc()
            r = {
                "account_id": acc["id"], "success": False, "activity": "",
                "steps_done": [], "duration_s": 0.0, "ip": "",
                "location_label": "", "error": str(e),
            }
        results.append(r)

        # Brief gap between accounts
        time.sleep(ACCOUNT_GAP_S)

    ok     = sum(1 for r in results if r.get("success") and not r.get("skipped"))
    failed = sum(1 for r in results if not r.get("success") and not r.get("skipped"))
    log.info("Batch cycle complete.  OK: %d  Failed: %d  Skipped (done today): %d",
             ok, failed, len(skipped))
    return results


# ── Next-run scheduler ────────────────────────────────────────────────────────

def _compute_next_run() -> float:
    """
    Compute the next run time: a random minute within 08:00–18:00 tomorrow
    (local time).  Returns the Unix timestamp for that moment.
    """
    now    = datetime.now()
    tomorrow = now.date() + timedelta(days=1)

    # Pick a random minute within the active window
    active_minutes = (ACTIVE_HOUR_END - ACTIVE_HOUR_START) * 60
    offset_minutes = random.randint(0, active_minutes - 1)
    run_time = datetime(
        tomorrow.year, tomorrow.month, tomorrow.day,
        ACTIVE_HOUR_START, 0, 0,
    ) + timedelta(minutes=offset_minutes)

    return run_time.timestamp()


def _sleep_until(target_ts: float) -> None:
    """Sleep until the given Unix timestamp, logging progress periodically."""
    while True:
        remaining = target_ts - time.time()
        if remaining <= 0:
            break
        if remaining > 3600:
            log.info("Next run in %.1f hours (%s).",
                     remaining / 3600,
                     datetime.fromtimestamp(target_ts).strftime("%Y-%m-%d %H:%M"))
            sleep_chunk = min(remaining, 3600)
        elif remaining > 60:
            log.info("Next run in %.0f minutes.", remaining / 60)
            sleep_chunk = min(remaining, 300)
        else:
            sleep_chunk = remaining
        time.sleep(sleep_chunk)


# ── Status reporter ───────────────────────────────────────────────────────────

def show_status() -> None:
    """Print the last session for each account from mobile_sessions.json."""
    if not SESSION_LOG.exists():
        print("No session log found — no sessions have run yet.")
        return

    try:
        records = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to read session log: {e}")
        return

    last: dict[str, dict] = {}
    for r in records:
        last[r["account_id"]] = r

    header = f"{'Account':<12} {'Timestamp':<22} {'Activity':<18} {'IP':<16} {'Dur':>6}  {'OK':>5}  Location"
    print(f"\n{header}")
    print("-" * len(header))
    for acc_id, r in sorted(last.items()):
        ts   = r.get("timestamp", "")[:19].replace("T", " ")
        ip   = r.get("ip", "—")
        act  = r.get("activity", "?")
        dur  = f"{r.get('duration_s', 0):.0f}s"
        ok   = "✓" if r.get("success") else "✗"
        loc  = r.get("location_label", "—")
        print(f"{acc_id:<12} {ts:<22} {act:<18} {ip:<16} {dur:>6}  {ok:>5}  {loc}")
    print()


# ── Signal handling for clean daemon shutdown ─────────────────────────────────

_shutdown_requested = False


def _on_shutdown(signum, frame) -> None:
    global _shutdown_requested
    log.info("Shutdown signal received (signal %d).  Exiting after current cycle …", signum)
    _shutdown_requested = True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Daily mobile warm-up batch runner for GeelarK cloud phones.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="Run one batch cycle and exit.")
    mode.add_argument("--daemon", action="store_true",
                      help="Run continuously (default behaviour).")
    parser.add_argument("--account", metavar="ID",
                        help="Run a single account and exit.")
    parser.add_argument("--activity", type=str,
                        help="Force a single activity for testing "
                             "(maps_browse, maps_directions, youtube, maps+youtube, "
                             "gmail, google_search). Without this flag, the monthly "
                             "schedule is followed.")
    parser.add_argument("--status", action="store_true",
                        help="Print last session per account and exit.")
    parser.add_argument("--pc", type=str,
                        help="Only run accounts whose category matches this PC ID.")
    args = parser.parse_args()

    # ── Status mode ────────────────────────────────────────────────────────
    if args.status:
        show_status()
        return

    # ── Load accounts ──────────────────────────────────────────────────────
    pc_filter = args.pc or os.environ.get("PC_ID", "").strip()
    all_accounts = load_accounts(pc_filter)

    if not all_accounts:
        log.error("No enabled accounts with phone_id found.  Exiting.")
        sys.exit(1)

    # Validate activity argument
    valid_activities = {"maps_browse", "maps_directions", "youtube",
                        "maps+youtube", "gmail", "google_search"}
    if args.activity and args.activity not in valid_activities:
        log.error("Invalid activity %r.  Choose from: %s",
                  args.activity, ", ".join(sorted(valid_activities)))
        sys.exit(1)

    # ── Single-account mode ────────────────────────────────────────────────
    if args.account:
        acc = next((a for a in all_accounts if a["id"] == args.account), None)
        if not acc:
            log.error("Account %r not found in geelark_accounts.yaml.", args.account)
            sys.exit(1)
        log.info("Single-account mode: %s", acc["id"])
        if args.activity:
            result = run_one_account_activity(acc, args.activity)
            status = "OK" if result["success"] else "FAILED"
            log.info("Result: %s | Activity: %s | Duration: %.0fs | IP: %s | Location: %s",
                     status, result["activity"], result["duration_s"],
                     result["ip"] or "?", result["location_label"])
        else:
            result = run_warmup_session(acc, SESSION_LOG)
            status = "OK" if result.get("success") else "FAILED"
            log.info("Result: %s | Script: %s | Steps: %s | Duration: %.0fs | IP: %s",
                     status, result.get("script", "?"), result.get("steps_done", []),
                     result.get("duration_s", 0), result.get("ip", "?"))
        return

    # ── Once mode ──────────────────────────────────────────────────────────
    if args.once:
        log.info("Once mode — running one batch cycle, then exiting.")
        run_batch_cycle(all_accounts, activity=args.activity)
        return

    # ── Daemon mode (default) ──────────────────────────────────────────────
    if args.activity:
        log.info("Daemon mode (testing) — forced activity: %s. Will run daily between %02d:00–%02d:00.",
                 args.activity, ACTIVE_HOUR_START, ACTIVE_HOUR_END)
    else:
        log.info("Daemon mode (schedule) — following monthly _day_to_script(). Will run daily between %02d:00–%02d:00.",
                 ACTIVE_HOUR_START, ACTIVE_HOUR_END)
    signal.signal(signal.SIGINT, _on_shutdown)
    signal.signal(signal.SIGTERM, _on_shutdown)

    while not _shutdown_requested:
        log.info("══════════ Starting daily batch cycle ══════════")
        run_batch_cycle(all_accounts, activity=args.activity)

        if _shutdown_requested:
            break

        next_run_ts = _compute_next_run()
        friendly = datetime.fromtimestamp(next_run_ts).strftime("%Y-%m-%d %H:%M")
        log.info("Next run: %s (%.1f hours from now).", friendly,
                 (next_run_ts - time.time()) / 3600)
        _sleep_until(next_run_ts)

    log.info("Daily batch runner exiting.")


if __name__ == "__main__":
    main()
