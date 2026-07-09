"""
Scheduler router — enrol/unenrol accounts in the continuous warming scheduler.

Enrolled accounts run automatically between 7am–10pm, with the account that
has waited longest always scheduled next. Sessions are spaced at least 3 hours
apart per account. Non-zero exits auto-unenrol the account and set status=error.
"""

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from api.deps import load_accounts

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


def _svc():
    from core.scheduler import get_scheduler
    return get_scheduler()


@router.post("/{account_id}/enroll")
async def enroll(account_id: str):
    """Enrol an account in the continuous scheduler."""
    accounts = await load_accounts()
    if not any(a["id"] == account_id for a in accounts):
        raise HTTPException(404, f"Account {account_id!r} not found")
    return _svc().enroll(account_id)


@router.post("/{account_id}/unenroll")
def unenroll(account_id: str):
    """Remove an account from the scheduler (kills any active session)."""
    if not _svc().unenroll(account_id):
        raise HTTPException(404, f"{account_id!r} is not scheduled")
    return {"unenrolled": account_id}


@router.get("/status")
def scheduler_status():
    """Return scheduler state for all enrolled accounts."""
    return _svc().get_status()


@router.post("/pause")
def pause_scheduler():
    """Pause the scheduler — no new sessions will start until resumed."""
    _svc().pause()
    return {"paused": True}


@router.post("/resume")
def resume_scheduler():
    """Resume the scheduler — eligible accounts will start running again."""
    _svc().resume()
    return {"paused": False}


@router.post("/concurrency")
def set_concurrency(body: dict):
    """Set maximum simultaneous browser sessions (1–5)."""
    n = body.get("max_concurrent")
    if n is None:
        raise HTTPException(400, "Provide max_concurrent")
    _svc().set_max_concurrent(n)
    return {"max_concurrent": _svc().max_concurrent}


@router.post("/bulk-enroll")
async def bulk_enroll(body: dict):
    """Enrol multiple accounts at once."""
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "Provide ids list")
    accounts = await load_accounts()
    valid = {a["id"] for a in accounts}
    svc   = _svc()
    enrolled = [aid for aid in ids if aid in valid and not svc.enroll(aid) or aid in valid]
    # Enrol all valid IDs
    enrolled = []
    for aid in ids:
        if aid in valid:
            svc.enroll(aid)
            enrolled.append(aid)
    return {"enrolled": len(enrolled), "ids": enrolled}


@router.post("/bulk-unenroll")
def bulk_unenroll(body: dict):
    """Unenrol multiple accounts at once."""
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "Provide ids list")
    svc     = _svc()
    removed = [aid for aid in ids if svc.unenroll(aid)]
    return {"unenrolled": len(removed), "ids": removed}
