"""
Account Warmer Dashboard — FastAPI Server

Run from the account-warmer/ directory:
    python api/main.py

Then open http://localhost:8000 in your browser.
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add account-warmer root to sys.path so core/ and other local imports work
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import accounts, multilogin, proxies, businesses, logs, runner, trust, fixes, login_check, mobile
from api.routers import scheduler as scheduler_router

STATIC_DIR = ROOT / "static"

# ── Token auto-refresh loop ───────────────────────────────────────────────────

async def _token_refresh_loop():
    """
    Keep the Multilogin JWT token fresh so subprocesses never need to sign in.

    Tokens expire after ~1 hour.  We refresh ~10 minutes before expiry so
    there is always a valid cached token in ml_token.json when a subprocess
    reads it.  On startup we check how much time is left on the current token
    and refresh immediately if it expires within 10 minutes.
    Subprocesses that attempt their own sign-in risk 429 rate-limits when
    several run simultaneously — this loop prevents that entirely.
    """
    import logging
    import time
    log = logging.getLogger("token-refresh")

    REFRESH_BEFORE = 10 * 60  # refresh when < 10 min remaining

    while True:
        # How long until the current token needs refreshing?
        sleep_secs = 0.0
        try:
            from api.deps import get_token_status
            status = get_token_status()
            if status.get("valid"):
                remaining = status.get("expires_in_hours", 0) * 3600
                sleep_secs = max(0.0, remaining - REFRESH_BEFORE)
        except Exception:
            pass  # sleep_secs stays 0 → refresh immediately

        if sleep_secs > 0:
            await asyncio.sleep(sleep_secs)

        try:
            from core.multilogin_auth import get_token
            get_token(force_refresh=True)
            log.info("Multilogin token auto-refreshed.")
        except Exception as exc:
            log.warning(f"Token auto-refresh failed: {exc}")
            # On failure wait 2 min before retrying so we don't spam the API
            await asyncio.sleep(2 * 60)


# ── Startup cleanup — stop any GeelarK phones left running from a crashed session

def _stop_orphaned_phones():
    """
    On server startup, stop any GeelarK cloud phones that are still running.

    If the server crashed mid-warmup, the phone was never stopped — it keeps
    running and burning proxy/billing credits indefinitely.  This runs once at
    startup to clean that up before any new sessions begin.
    """
    import logging
    log = logging.getLogger("startup-cleanup")
    try:
        from core.account_store import get_account_store
        from core.geelark_client import GeelarKClient

        accounts = get_account_store().get_mobile_accounts()
        phone_ids = [a["geelark_phone_id"] for a in accounts if a.get("geelark_phone_id")]
        if not phone_ids:
            return

        client = GeelarKClient()
        # Query in batches of 50 (API limit)
        running = []
        for i in range(0, len(phone_ids), 50):
            batch = phone_ids[i:i+50]
            statuses = client.get_phone_status(batch)
            for s in statuses:
                if s.get("status") in (0, 1):  # 0=Running, 1=Starting
                    running.append(s.get("id") or s.get("phoneId"))

        if not running:
            log.info("Startup cleanup: no orphaned phones found.")
            return

        log.warning("Startup cleanup: stopping %d orphaned phone(s): %s", len(running), running)
        for phone_id in running:
            try:
                client.stop_phone(phone_id)
                log.info("Startup cleanup: stopped phone %s", phone_id)
            except Exception as e:
                log.warning("Startup cleanup: failed to stop %s: %s", phone_id, e)

    except Exception as exc:
        # Never block startup due to cleanup failure
        logging.getLogger("startup-cleanup").warning("Startup cleanup failed: %s", exc)


def _resume_mobile_warmup_if_needed():
    """
    After a crash, re-trigger mobile warmup for any accounts that didn't
    complete a session today.

    Only fires between 07:00–22:00 local time so a late-night restart doesn't
    kick off phones at 3am.  Accounts that already have a session logged today
    are skipped — they don't get a second run.
    """
    import logging
    import threading
    log = logging.getLogger("startup-resume")
    try:
        import json
        from datetime import datetime
        from core.account_store import get_account_store
        from core.paths import LOGS_DIR

        # Only resume during reasonable hours
        hour = datetime.now().hour
        if not (7 <= hour < 22):
            log.info("Mobile resume: outside active hours (%02d:xx) — skipping", hour)
            return

        accounts = [a for a in get_account_store().get_mobile_accounts()
                    if a.get("geelark_phone_id") and a.get("mobile_warming_enabled", True)]
        if not accounts:
            return

        # Find accounts that already completed a session today
        sessions_file = LOGS_DIR / "mobile_sessions.json"
        completed_today: set = set()
        if sessions_file.exists():
            try:
                records = json.loads(sessions_file.read_text(encoding="utf-8"))
                today = datetime.now().date().isoformat()
                for r in records:
                    ts = r.get("timestamp", "")
                    if ts.startswith(today):
                        completed_today.add(r.get("account_id"))
            except Exception:
                pass

        pending = [a for a in accounts if a["id"] not in completed_today]
        if not pending:
            log.info("Mobile resume: all accounts already warmed today — nothing to do.")
            return

        log.warning("Mobile resume: %d account(s) didn't complete warmup today — re-triggering: %s",
                    len(pending), [a["id"] for a in pending])

        def _run():
            from activities.mobile_warmup import run_all_warmup_sessions
            run_all_warmup_sessions(pending, sessions_file)

        threading.Thread(target=_run, daemon=True).start()

    except Exception as exc:
        logging.getLogger("startup-resume").warning("Mobile resume check failed: %s", exc)


# ── Lifespan (start scheduler + token refresh background loops) ───────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Clean up any phones left running from a previous crashed session
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _stop_orphaned_phones)

    # Mobile warmup auto-resume DISABLED — use Batch Runner tab manually instead.
    # await loop.run_in_executor(None, _resume_mobile_warmup_if_needed)

    from core.scheduler import get_scheduler
    svc          = get_scheduler()
    sched_task   = asyncio.create_task(svc.run_loop())
    refresh_task = asyncio.create_task(_token_refresh_loop())

    from core.mobile_scheduler import get_mobile_scheduler
    mobile_svc   = get_mobile_scheduler()
    mob_task     = asyncio.create_task(mobile_svc.run_loop())

    yield
    sched_task.cancel()
    refresh_task.cancel()
    mob_task.cancel()
    for t in (sched_task, refresh_task, mob_task):
        try:
            await t
        except asyncio.CancelledError:
            pass

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Account Warmer Dashboard",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(accounts.router)
app.include_router(multilogin.router)
app.include_router(proxies.router)
app.include_router(businesses.router)
app.include_router(logs.router)
app.include_router(runner.router)
app.include_router(scheduler_router.router)
app.include_router(trust.router)
app.include_router(fixes.router)
app.include_router(login_check.router)
app.include_router(mobile.router)

# ── Static files ──────────────────────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/screenshots/{path:path}", include_in_schema=False)
def serve_screenshot(path: str):
    """Serve proof screenshots stored in WarmingData/logs/screenshots/."""
    from core.paths import LOGS_DIR
    screenshot_path = LOGS_DIR / path
    if not screenshot_path.exists() or not screenshot_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        return JSONResponse(status_code=404, content={"error": "Screenshot not found"})
    return FileResponse(str(screenshot_path))


@app.get("/api/system/version")
def system_version():
    """Return the running code version + which instance this is (PC ID / port)."""
    version_file = ROOT / "version.txt"
    # utf-8-sig strips a BOM if the file was saved by a Windows editor/PowerShell.
    version = version_file.read_text(encoding="utf-8-sig").strip() if version_file.exists() else "unknown"
    return {
        "version":  version,
        "pc_id":    os.environ.get("PC_ID", ""),
        "port":     os.environ.get("SERVER_PORT", "8000"),
        "data_dir": os.environ.get("WARMER_DATA_DIR", ""),
    }


@app.get("/", include_in_schema=False)
def serve_dashboard(v: str = ""):
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return JSONResponse(
            status_code=500,
            content={"error": "Dashboard not found", "expected": str(index)},
        )
    # Redirect bare / to /?v={mtime} so the URL changes every time the file
    # changes.  Firefox (and other browsers) apply heuristic long-lived caching
    # when no Cache-Control header was present on a previous response, meaning
    # a cached old page can survive server restarts and hard-refreshes.
    # A URL the browser has never seen before bypasses that cache entirely.
    mtime = str(int(index.stat().st_mtime))
    if v != mtime:
        from fastapi.responses import RedirectResponse
        r = RedirectResponse(url=f"/?v={mtime}", status_code=302)
        r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        r.headers["Pragma"] = "no-cache"
        return r
    return HTMLResponse(
        content=index.read_bytes(),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SERVER_PORT", "8000"))
    print(f"\n  Account Warmer Dashboard")
    print(f"  http://localhost:{port}\n")
    uvicorn.run(
        "api.main:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )
