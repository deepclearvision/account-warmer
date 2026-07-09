"""
Trust check router — trigger per-account trust checks and retrieve results.

Trust checks are queued and run one-at-a-time (respecting the global browser
concurrency limit shared with the scheduler and manual runner).  Spawning all
accounts at once used to overwhelm Multilogin, causing most to fail silently.

Queue behaviour:
  - POST /check  → adds accounts to _trust_queue; immediately tries to fill
                   available slots up to the global concurrency limit.
  - GET  /status → reaps finished processes, advances the queue, returns state.
  - Accounts already running or queued are skipped on duplicate submission.
"""

import subprocess
import sys
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.deps import load_trust_checks, save_trust_checks, STATE_DIR, LOGS_DIR

router = APIRouter(prefix="/api/trust", tags=["trust"])

ROOT = Path(__file__).parent.parent.parent

# account_id → {proc, pid, status, check_type}
_trust_jobs: dict[str, dict] = {}

# Pending accounts not yet spawned: (account_id, check_type)
_trust_queue: deque[tuple[str, str]] = deque()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _total_running() -> int:
    """Count all browser sessions currently open (trust + runner + scheduler + login checks)."""
    trust_running = sum(
        1 for j in _trust_jobs.values()
        if j.get("status") == "running" and j["proc"].poll() is None
    )
    try:
        from api.routers.runner import _jobs
        runner_running = sum(1 for j in _jobs.values() if j["status"] == "running")
    except Exception:
        runner_running = 0
    try:
        from core.scheduler import get_scheduler
        svc = get_scheduler()
        svc._reap()
        scheduler_running = len(svc._procs)
    except Exception:
        scheduler_running = 0
    try:
        from api.routers.login_check import _login_jobs
        login_running = sum(
            1 for j in _login_jobs.values()
            if j.get("status") == "running" and j["proc"].poll() is None
        )
    except Exception:
        login_running = 0
    return trust_running + runner_running + scheduler_running + login_running


def _concurrency_limit() -> int:
    try:
        from core.scheduler import get_scheduler
        return get_scheduler().max_concurrent
    except Exception:
        return 2


def _reap_and_advance() -> None:
    """Reap finished trust processes then start queued ones if slots are free."""
    # Reap
    for job in _trust_jobs.values():
        if job.get("status") == "running":
            rc = job["proc"].poll()
            if rc is not None:
                job["status"] = "done" if rc == 0 else "error"

    # Advance queue
    limit = _concurrency_limit()
    while _trust_queue and _total_running() < limit:
        acc_id, check_type = _trust_queue.popleft()

        # Skip if it was already launched (duplicate in queue)
        existing = _trust_jobs.get(acc_id)
        if existing and existing.get("status") == "running" and existing["proc"].poll() is None:
            continue

        _spawn_trust(acc_id, check_type)


def _spawn_trust(acc_id: str, check_type: str) -> None:
    """Launch trust_run.py for one account, logging stderr to its log file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{acc_id}.log"

    kwargs: dict = {"cwd": str(ROOT)}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP |
            subprocess.DETACHED_PROCESS
        )

    try:
        log_fh = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "trust_run.py"),
             "--account", acc_id, "--check-type", check_type],
            stdout=subprocess.DEVNULL,
            stderr=log_fh,
            **kwargs,
        )
        log_fh.close()
    except Exception as e:
        raise RuntimeError(f"Failed to spawn trust check for {acc_id}: {e}")

    _trust_jobs[acc_id] = {
        "proc":       proc,
        "pid":        proc.pid,
        "status":     "running",
        "check_type": check_type,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

class TrustCheckRequest(BaseModel):
    account_ids: list[str]
    check_type:  str = "both"


@router.get("/results")
def get_trust_results():
    """Return all stored trust check results."""
    return load_trust_checks()


@router.post("/check")
def start_trust_checks(body: TrustCheckRequest):
    """
    Queue trust check processes for the given accounts.
    Accounts run one at a time up to the global browser concurrency limit.
    """
    if body.check_type not in ("v2", "v3", "both"):
        raise HTTPException(400, "check_type must be 'v2', 'v3', or 'both'")
    if not body.account_ids:
        raise HTTPException(400, "No account IDs provided")

    _reap_and_advance()

    queued   = []
    launched = []
    skipped  = []

    for acc_id in body.account_ids:
        # Skip if already running
        existing = _trust_jobs.get(acc_id)
        if existing and existing.get("status") == "running" and existing["proc"].poll() is None:
            skipped.append(acc_id)
            continue

        # Skip if already in queue
        if any(a == acc_id for a, _ in _trust_queue):
            skipped.append(acc_id)
            continue

        limit = _concurrency_limit()
        if _total_running() < limit:
            # Slot available — spawn immediately
            try:
                _spawn_trust(acc_id, body.check_type)
                launched.append(acc_id)
            except Exception as e:
                raise HTTPException(500, str(e))
        else:
            # No slot — add to queue
            _trust_queue.append((acc_id, body.check_type))
            queued.append(acc_id)

    return {"launched": launched, "queued": queued, "skipped": skipped}


@router.get("/status")
def trust_check_status():
    """Return running/queued status of all trust check jobs."""
    _reap_and_advance()

    result = {}

    # Running / done / error jobs
    for acc_id, job in _trust_jobs.items():
        result[acc_id] = {
            "status":     job["status"],
            "pid":        job["pid"],
            "check_type": job["check_type"],
        }

    # Queued jobs not yet spawned
    queue_position = 1
    for acc_id, check_type in _trust_queue:
        result[acc_id] = {
            "status":         "queued",
            "queue_position": queue_position,
            "check_type":     check_type,
            "pid":            None,
        }
        queue_position += 1

    return result


@router.delete("/results/{account_id}")
def clear_trust_result(account_id: str):
    """Clear the trust check result for one account."""
    data = load_trust_checks()
    if account_id in data:
        del data[account_id]
        save_trust_checks(data)
    return {"cleared": account_id}
