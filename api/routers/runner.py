"""
Runner router — start, stop, and monitor warming sessions from the dashboard.

Spawns:  python run.py --account <id>
Uses subprocess.Popen (not asyncio subprocess) for reliable Windows support.
Jobs are tracked in-memory; cleared on server restart (that's intentional).
"""

import subprocess
import sys
import threading
import time
from datetime import datetime, date
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from api.deps import load_accounts, LOGS_DIR
from core.account_store import get_account_store

router = APIRouter(prefix="/api/runner", tags=["runner"])
ROOT   = Path(__file__).parent.parent.parent

# account_id → {account_id, pid, proc, status, started_at, ended_at}
_jobs: dict[str, dict] = {}

# ── Business-signal sequential queue ──────────────────────────────────────────
# Accounts waiting to run business signal — processed one at a time as slots open.
_biz_queue: list[str] = []
_biz_queue_lock = threading.Lock()

# Minimum gap between starting consecutive biz-signal accounts (seconds).
# Randomised per-start between these bounds so runs spread naturally across the day.
_BIZ_GAP_MIN = 20 * 60   # 20 minutes
_BIZ_GAP_MAX = 45 * 60   # 45 minutes
_last_biz_start: float = 0.0   # epoch seconds of last spawn
_next_biz_gap:   float = 0.0   # gap chosen after last spawn


def _spawn_biz_signal(account_id: str) -> None:
    """Spawn run.py --activity business_signal for account_id (no HTTP context)."""
    global _last_biz_start, _next_biz_gap
    import random as _random
    cmd = [sys.executable, str(ROOT / "run.py"), "--account", account_id,
           "--activity", "business_signal"]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{account_id}.log"
    kwargs: dict = dict(cwd=str(ROOT))
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP |
            subprocess.DETACHED_PROCESS
        )
    try:
        log_fh = open(log_path, "a", encoding="utf-8")
        proc   = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                  stderr=log_fh, **kwargs)
        log_fh.close()
        _jobs[account_id] = {
            "account_id": account_id,
            "pid":        proc.pid,
            "proc":       proc,
            "status":     "running",
            "job_type":   "biz_signal",
            "started_at": datetime.now().isoformat(),
            "ended_at":   None,
        }
        # Record when this started and pick a random gap before the next one
        _last_biz_start = time.time()
        _next_biz_gap   = _random.uniform(_BIZ_GAP_MIN, _BIZ_GAP_MAX)
    except Exception:
        pass


def _biz_queue_worker() -> None:
    """Background daemon: drain _biz_queue, spacing starts 20–45 min apart."""
    while True:
        time.sleep(30)   # poll every 30s
        _reap()
        with _biz_queue_lock:
            if not _biz_queue:
                continue
            try:
                from core.scheduler import get_scheduler
                limit = get_scheduler().max_concurrent
            except Exception:
                limit = 2
            if _total_running() >= limit:
                continue
            # Enforce minimum gap between consecutive biz-signal starts
            elapsed = time.time() - _last_biz_start
            if _last_biz_start > 0 and elapsed < _next_biz_gap:
                continue   # not yet time for the next one
            account_id = _biz_queue.pop(0)
        _spawn_biz_signal(account_id)


threading.Thread(target=_biz_queue_worker, daemon=True, name="biz-queue").start()


def _reap() -> None:
    """Check for completed processes and update their status."""
    for info in _jobs.values():
        proc = info.get("proc")
        if proc and info["status"] == "running":
            rc = proc.poll()          # non-blocking; None = still running
            if rc is not None:
                info["status"]   = "error" if rc != 0 else "done"
                info["ended_at"] = datetime.now().isoformat()


def _pub(info: dict) -> dict:
    return {k: v for k, v in info.items() if k != "proc"}


# ── Routes ─────────────────────────────────────────────────────────────────────

def _total_running() -> int:
    """Count all currently-running browser processes (runner + scheduler + trust + login checks)."""
    runner_count = sum(1 for j in _jobs.values() if j["status"] == "running")
    try:
        from core.scheduler import get_scheduler
        svc = get_scheduler()
        svc._reap()
        scheduler_count = len(svc._procs)
    except Exception:
        scheduler_count = 0
    try:
        from api.routers.trust import _trust_jobs
        trust_count = sum(
            1 for j in _trust_jobs.values()
            if j.get("status") == "running" and j["proc"].poll() is None
        )
    except Exception:
        trust_count = 0
    try:
        from api.routers.login_check import _login_jobs
        login_count = sum(
            1 for j in _login_jobs.values()
            if j.get("status") == "running" and j["proc"].poll() is None
        )
    except Exception:
        login_count = 0
    return runner_count + scheduler_count + trust_count + login_count


@router.post("/{account_id}/start")
async def start_run(account_id: str):
    """Spawn run.py --account <id> as a detached background process."""
    _reap()

    existing = _jobs.get(account_id)
    if existing and existing["status"] == "running":
        raise HTTPException(409, f"Already running (PID {existing['pid']})")

    # Enforce the global browser cap shared with the scheduler
    try:
        from core.scheduler import get_scheduler
        limit = get_scheduler().max_concurrent
    except Exception:
        limit = 2
    if _total_running() >= limit:
        raise HTTPException(429, f"At browser limit ({limit}). Stop a session or raise the Browsers setting.")

    accounts = await load_accounts()
    if not any(a["id"] == account_id for a in accounts):
        raise HTTPException(404, f"Account {account_id!r} not found")

    cmd = [sys.executable, str(ROOT / "run.py"), "--account", account_id]

    # Append subprocess stderr to the account log file so errors/tracebacks
    # are visible in the live log panel. stdout is also captured there
    # (the warmer's logger already writes to the file directly, but any
    # Python tracebacks or other stderr output would otherwise be lost).
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{account_id}.log"

    kwargs: dict = dict(cwd=str(ROOT))
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP |
            subprocess.DETACHED_PROCESS
        )

    try:
        log_fh = open(log_path, "a", encoding="utf-8")
        proc   = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_fh,
            **kwargs,
        )
        log_fh.close()   # parent can close — child has its own fd copy
    except Exception as e:
        raise HTTPException(500, f"Failed to launch: {type(e).__name__}: {e}")

    _jobs[account_id] = {
        "account_id": account_id,
        "pid":        proc.pid,
        "proc":       proc,
        "status":     "running",
        "started_at": datetime.now().isoformat(),
        "ended_at":   None,
    }
    return _pub(_jobs[account_id])


@router.post("/{account_id}/start-biz-signal")
async def start_biz_signal(account_id: str):
    """Queue run.py --activity business_signal for account_id. Starts immediately if a slot is free, otherwise waits in queue."""
    _reap()
    accounts = await load_accounts()
    if not any(a["id"] == account_id for a in accounts):
        raise HTTPException(404, f"Account {account_id!r} not found")

    existing = _jobs.get(account_id)
    if existing and existing["status"] == "running":
        raise HTTPException(409, f"Already running (PID {existing['pid']})")

    with _biz_queue_lock:
        if account_id in _biz_queue:
            raise HTTPException(409, f"{account_id} is already in the biz-signal queue")

        try:
            from core.scheduler import get_scheduler
            limit = get_scheduler().max_concurrent
        except Exception:
            limit = 2

        if _total_running() < limit:
            # Slot free — start immediately
            _spawn_biz_signal(account_id)
            return _pub(_jobs[account_id])
        else:
            # Queue it
            _biz_queue.append(account_id)
            return {
                "account_id": account_id,
                "status":     "queued",
                "queue_position": len(_biz_queue),
            }


@router.post("/bulk-biz-signal")
async def bulk_biz_signal(body: dict):
    """Queue business-signal runs for multiple accounts. Returns started + queued counts."""
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "No account IDs provided")

    accounts = await load_accounts()
    valid_ids = {a["id"] for a in accounts}
    _reap()

    started, queued, skipped = [], [], []
    with _biz_queue_lock:
        try:
            from core.scheduler import get_scheduler
            limit = get_scheduler().max_concurrent
        except Exception:
            limit = 2

        for account_id in ids:
            if account_id not in valid_ids:
                skipped.append(account_id)
                continue
            existing = _jobs.get(account_id)
            if existing and existing["status"] == "running":
                skipped.append(account_id)
                continue
            if account_id in _biz_queue:
                skipped.append(account_id)
                continue

            if _total_running() + len(started) < limit:
                _spawn_biz_signal(account_id)
                started.append(account_id)
            else:
                _biz_queue.append(account_id)
                queued.append(account_id)

    return {"started": started, "queued": queued, "skipped": skipped}


@router.get("/biz-signal-pending")
def biz_signal_pending():
    """
    Return account IDs that have at least one target business with an incomplete phase.
    Used by the dashboard 'Select Biz Pending' button.
    """
    import json as _json
    from core.paths import STATE_DIR

    accounts = get_account_store().get_desktop_accounts()

    pending = []
    for acc in accounts:
        acc_id = acc.get("id", "")
        for biz_id in acc.get("target_businesses", []):
            state_file = STATE_DIR / f"{acc_id}_biz_{biz_id}.json"
            if not state_file.exists():
                # Never run — definitely pending
                pending.append(acc_id)
                break
            try:
                st = _json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                pending.append(acc_id)
                break
            # Pending if any incomplete phase (phase 3 needs appointment date logic but
            # if not complete it's still pending)
            if not st.get("phase_1_complete") or not st.get("phase_2_complete") or not st.get("phase_3_complete"):
                pending.append(acc_id)
                break

    return {"pending_ids": pending, "count": len(pending)}


@router.get("/biz-queue")
def biz_queue_status():
    """Return current business-signal queue, running jobs, and next-start timing."""
    _reap()
    with _biz_queue_lock:
        queue = list(_biz_queue)
    running = [
        _pub(j) for j in _jobs.values()
        if j.get("job_type") == "biz_signal" and j["status"] == "running"
    ]
    # How long until the next queued account is eligible to start
    wait_secs = None
    if queue and _last_biz_start > 0:
        remaining = (_last_biz_start + _next_biz_gap) - time.time()
        wait_secs = max(0, round(remaining))
    return {
        "running":          running,
        "queued":           queue,
        "queued_count":     len(queue),
        "next_start_secs":  wait_secs,
        "gap_min_mins":     _BIZ_GAP_MIN // 60,
        "gap_max_mins":     _BIZ_GAP_MAX // 60,
    }


@router.get("/status")
def runner_status():
    """Return current status of all tracked jobs."""
    _reap()
    return [_pub(i) for i in _jobs.values()]


@router.post("/stop-all")
def stop_all():
    """Kill every running job, clear the biz queue, and pause the scheduler."""
    global _biz_queue

    killed = []
    errors = []

    # 1. Kill all manual / biz-signal jobs tracked in _jobs
    _reap()
    for account_id, job in list(_jobs.items()):
        if job.get("status") == "running":
            proc = job.get("proc")
            try:
                if proc and proc.poll() is None:
                    if sys.platform == "win32":
                        import signal
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        proc.terminate()
                job["status"]   = "stopped"
                job["ended_at"] = datetime.now().isoformat()
                killed.append(account_id)
            except Exception as e:
                errors.append(f"{account_id}: {e}")

    # 2. Clear the biz-signal queue
    with _biz_queue_lock:
        cleared_queue = list(_biz_queue)
        _biz_queue.clear()

    # 3. Kill all scheduler-managed processes and pause the scheduler
    try:
        from core.scheduler import get_scheduler
        svc = get_scheduler()
        for acc_id in list(svc._procs.keys()):
            svc._kill(acc_id)
            if acc_id not in killed:
                killed.append(acc_id)
        svc.pause()
    except Exception as e:
        errors.append(f"scheduler: {e}")

    # 4. Kill any orphaned run.py processes the server doesn't know about
    orphans_killed = 0
    try:
        import subprocess as _sp
        if sys.platform == "win32":
            result = _sp.run(
                ["wmic", "process", "where", "CommandLine like '%run.py%--account%'",
                 "call", "terminate"],
                capture_output=True, text=True,
            )
            orphans_killed = result.stdout.count("successful")
        else:
            _sp.run(["pkill", "-f", "run.py.*--account"], capture_output=True)
    except Exception as e:
        errors.append(f"orphan kill: {e}")

    return {
        "killed":          killed,
        "killed_count":    len(killed),
        "orphans_killed":  orphans_killed,
        "queue_cleared":   cleared_queue,
        "errors":          errors,
    }


@router.post("/{account_id}/stop")
def stop_run(account_id: str):
    """Terminate a running account process."""
    _reap()
    job = _jobs.get(account_id)
    if not job or job["status"] != "running":
        raise HTTPException(409, f"{account_id} is not currently running")
    proc = job["proc"]
    try:
        if sys.platform == "win32":
            import signal
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except Exception:
        pass
    job["status"]   = "stopped"
    job["ended_at"] = datetime.now().isoformat()
    return {"stopped": account_id}


@router.post("/{account_id}/open-browser")
async def open_browser(account_id: str):
    """
    Open the Multilogin browser profile for manual use — no automation runs.
    The profile starts in normal (non-headless) mode so the user can interact freely.
    """
    import requests as req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    accounts = await load_accounts()
    account  = next((a for a in accounts if a["id"] == account_id), None)
    if not account:
        raise HTTPException(404, f"Account {account_id!r} not found")

    profile_id = account.get("multilogin_profile_id", "")
    folder_id  = account.get("multilogin_folder_id", "")
    if not profile_id or not folder_id:
        raise HTTPException(400, "Account is missing multilogin_profile_id or multilogin_folder_id")

    sys.path.insert(0, str(ROOT))
    from core.multilogin_auth import LOCAL_API, auth_headers

    # Start without ?automation_type=playwright so the browser opens in normal
    # interactive mode rather than Playwright-automated mode.
    start_url = f"{LOCAL_API}/api/v2/profile/f/{folder_id}/p/{profile_id}/start"
    try:
        resp = req.get(start_url, headers=auth_headers(), timeout=30, verify=False)
        try:
            body = resp.json()
        except Exception:
            body = {}

        error_code = (body.get("status") or {}).get("error_code", "")
        if resp.status_code == 200 or error_code == "PROFILE_ALREADY_RUNNING":
            note = "already open" if error_code == "PROFILE_ALREADY_RUNNING" else "opened"
            return {"opened": True, "account_id": account_id, "note": note}

        raise HTTPException(500, f"Multilogin start failed ({resp.status_code}): {resp.text[:300]}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"open-browser error: {e}")


@router.get("/errors")
def scan_errors(date: str = None):
    """
    Scan all acc_*.log files for ERROR lines on a given date.
    date param: ISO date string e.g. "2026-03-12" (default: yesterday).
    Returns {account_id: {count, last_error, last_error_time, errors_with_context}}.
    """
    from datetime import timedelta
    if date:
        target = date
    else:
        target = (datetime.now() - timedelta(days=1)).date().isoformat()

    result = {}
    if not LOGS_DIR.exists():
        return result

    for log_file in sorted(LOGS_DIR.glob("acc_*.log")):
        acc_id = log_file.stem
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Find all ERROR line indices for the target date
            error_indices = [
                i for i, l in enumerate(lines)
                if l.startswith(target) and " ERROR " in l
            ]
            if not error_indices:
                continue

            # Build each error with surrounding context (catches tracebacks above)
            errors_with_context = []
            seen = set()
            for idx in error_indices:
                start = max(0, idx - 12)
                end   = min(len(lines), idx + 3)
                key   = (start, end)
                if key in seen:
                    continue
                seen.add(key)
                errors_with_context.append({
                    "error_line":  lines[idx].rstrip(),
                    "context":     [l.rstrip() for l in lines[start:end]],
                    "line_number": idx + 1,
                })

            errs = [lines[i].rstrip() for i in error_indices]
            result[acc_id] = {
                "count":                len(errs),
                "last_error":           errs[-1],
                "last_error_time":      errs[-1][:19] if len(errs[-1]) >= 19 else "",
                "all_errors":           errs[-20:],
                "errors_with_context":  errors_with_context[-30:],
                "date":                 target,
            }
        except Exception:
            continue

    return result
