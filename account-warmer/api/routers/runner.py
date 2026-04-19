"""
Runner router — start, stop, and monitor warming sessions from the dashboard.

Spawns:  python run.py --account <id>
Uses subprocess.Popen (not asyncio subprocess) for reliable Windows support.
Jobs are tracked in-memory; cleared on server restart (that's intentional).
"""

import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from api.deps import load_accounts, LOGS_DIR

router = APIRouter(prefix="/api/runner", tags=["runner"])
ROOT   = Path(__file__).parent.parent.parent

# account_id → {account_id, pid, proc, status, started_at, ended_at}
_jobs: dict[str, dict] = {}


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


@router.get("/status")
def runner_status():
    """Return current status of all tracked jobs."""
    _reap()
    return [_pub(i) for i in _jobs.values()]


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
