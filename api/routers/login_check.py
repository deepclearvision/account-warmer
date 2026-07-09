"""
Login check router — check whether Google accounts are currently signed in.

Accounts are queued and run one at a time, respecting the global browser
concurrency limit (same as trust checks and the manual runner).
"""

import subprocess
import sys
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.deps import LOGS_DIR

router = APIRouter(prefix="/api/login-check", tags=["login-check"])

ROOT = Path(__file__).parent.parent.parent

# account_id → {proc, pid, status}
_login_jobs: dict[str, dict] = {}

# Pending: (account_id,)
_login_queue: deque[str] = deque()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _total_running() -> int:
    login_running = sum(
        1 for j in _login_jobs.values()
        if j.get("status") == "running" and j["proc"].poll() is None
    )
    try:
        from api.routers.runner import _jobs
        runner_running = sum(1 for j in _jobs.values() if j["status"] == "running")
    except Exception:
        runner_running = 0
    try:
        from api.routers.trust import _trust_jobs
        trust_running = sum(
            1 for j in _trust_jobs.values()
            if j.get("status") == "running" and j["proc"].poll() is None
        )
    except Exception:
        trust_running = 0
    try:
        from core.scheduler import get_scheduler
        svc = get_scheduler()
        svc._reap()
        scheduler_running = len(svc._procs)
    except Exception:
        scheduler_running = 0
    return login_running + runner_running + trust_running + scheduler_running


def _concurrency_limit() -> int:
    try:
        from core.scheduler import get_scheduler
        return get_scheduler().max_concurrent
    except Exception:
        return 2


def _reap_and_advance() -> None:
    for job in _login_jobs.values():
        if job.get("status") == "running":
            rc = job["proc"].poll()
            if rc is not None:
                job["status"] = "done" if rc == 0 else "error"

    limit = _concurrency_limit()
    while _login_queue and _total_running() < limit:
        acc_id = _login_queue.popleft()
        existing = _login_jobs.get(acc_id)
        if existing and existing.get("status") == "running" and existing["proc"].poll() is None:
            continue
        _spawn_login(acc_id)


def _spawn_login(acc_id: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{acc_id}.log"

    kwargs: dict = {"cwd": str(ROOT)}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP |
            subprocess.DETACHED_PROCESS
        )

    log_fh = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "login_check_run.py"), "--account", acc_id],
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        **kwargs,
    )
    log_fh.close()

    _login_jobs[acc_id] = {"proc": proc, "pid": proc.pid, "status": "running"}


# ── Routes ─────────────────────────────────────────────────────────────────────

class LoginCheckRequest(BaseModel):
    account_ids: list[str]


def _ensure_token() -> None:
    """
    Pre-warm the Multilogin auth token before spawning any subprocesses.

    Without this, every subprocess calls get_token() independently. If the
    token is stale, they all hit the Multilogin sign-in API simultaneously
    and get 429 rate-limited. Calling get_token() here ensures it's cached
    to disk before any subprocess starts, so they all read the file instead.
    """
    try:
        from core.multilogin_auth import get_token
        get_token()
    except Exception:
        pass  # Let the subprocess surface the real error in the account log


@router.post("/check")
def start_login_checks(body: LoginCheckRequest):
    """Queue login checks for the given accounts."""
    if not body.account_ids:
        raise HTTPException(400, "No account IDs provided")

    # Ensure the Multilogin token is fresh and cached before spawning subprocesses.
    # Prevents parallel sign-in 429 errors when multiple accounts check at once.
    _ensure_token()

    _reap_and_advance()

    launched = []
    queued   = []
    skipped  = []

    for acc_id in body.account_ids:
        existing = _login_jobs.get(acc_id)
        if existing and existing.get("status") == "running" and existing["proc"].poll() is None:
            skipped.append(acc_id)
            continue
        if acc_id in _login_queue:
            skipped.append(acc_id)
            continue

        if _total_running() < _concurrency_limit():
            try:
                _spawn_login(acc_id)
                launched.append(acc_id)
            except Exception as e:
                raise HTTPException(500, f"Failed to spawn login check for {acc_id}: {e}")
        else:
            _login_queue.append(acc_id)
            queued.append(acc_id)

    return {"launched": launched, "queued": queued, "skipped": skipped}


@router.get("/status")
def login_check_status():
    """Return running/queued/done status of login check jobs."""
    _reap_and_advance()

    result = {}
    for acc_id, job in _login_jobs.items():
        result[acc_id] = {"status": job["status"], "pid": job["pid"]}

    for i, acc_id in enumerate(_login_queue):
        result[acc_id] = {"status": "queued", "queue_position": i + 1, "pid": None}

    return result
