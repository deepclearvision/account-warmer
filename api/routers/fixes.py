"""
Fixes router — error detection, AI analysis, and fix application.
"""

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json

from api.deps import load_accounts
from core.error_analyser import (
    analyse_all, get_pending_fixes, get_all_fixes, update_fix_status,
)
from core.fix_engine import apply_fix
from core.paths import STATE_DIR

router = APIRouter(prefix="/api/fixes", tags=["fixes"])


@router.get("")
async def list_fixes():
    """Return all pending fixes."""
    return get_pending_fixes()


@router.get("/all")
async def list_all_fixes():
    """Return all fixes including applied and dismissed."""
    return get_all_fixes()


@router.get("/count")
async def fix_count():
    """Return count of pending fixes — used for the dashboard badge."""
    return {"pending": len(get_pending_fixes())}


@router.post("/analyse")
async def trigger_analysis():
    """Manually trigger error analysis across all accounts."""
    accounts = await load_accounts()
    new_count = analyse_all(accounts)

    # Enrich any unknown errors via Claude API (non-blocking best-effort)
    try:
        from core.claude_analyser import enrich_unknown_fixes
        enriched = enrich_unknown_fixes(accounts)
    except Exception:
        enriched = 0

    return {
        "new_fixes":  new_count,
        "ai_enriched": enriched,
        "pending":    len(get_pending_fixes()),
    }


@router.post("/{fix_id}/apply")
async def apply_fix_endpoint(fix_id: str):
    """Apply a pending fix."""
    fixes  = get_all_fixes()
    fix    = next((f for f in fixes if f["id"] == fix_id), None)
    if not fix:
        raise HTTPException(404, f"Fix {fix_id!r} not found")
    if fix["status"] != "pending":
        raise HTTPException(409, f"Fix is already {fix['status']}")
    if not fix.get("fix_action"):
        raise HTTPException(400, "Fix has no action — dismiss it or wait for AI analysis")

    success, message = apply_fix(fix)
    if not success:
        raise HTTPException(500, message)

    update_fix_status(fix_id, "applied")
    return {"applied": fix_id, "message": message}


@router.get("/login-status")
async def login_status():
    """Return the latest Google login status for all accounts."""
    status_file = STATE_DIR / "login_status.json"
    if not status_file.exists():
        return {}
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.post("/{fix_id}/dismiss")
async def dismiss_fix(fix_id: str):
    """Dismiss a pending fix without applying it."""
    if not update_fix_status(fix_id, "dismissed"):
        raise HTTPException(404, f"Fix {fix_id!r} not found")
    return {"dismissed": fix_id}
