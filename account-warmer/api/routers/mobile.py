"""
Mobile Phones router — GeelarK cloud phone management from the dashboard.

Endpoints:
  GET  /api/mobile/phones              — list GeelarK accounts + phone status
  POST /api/mobile/phones/{id}/provision — create a GeelarK phone for this account
  POST /api/mobile/phones/{id}/start    — start phone, return viewer URL
  POST /api/mobile/phones/{id}/stop     — stop phone
  POST /api/mobile/phones/{id}/login    — trigger Google login (async, returns job id)
  POST /api/mobile/phones/{id}/setup    — run one-time account setup (async, returns job id)
  POST /api/mobile/phones/{id}/warmup   — run a single warm-up session (async, returns job id)
  POST /api/mobile/warmup/all           — run warm-up for ALL enabled accounts (async, returns job id)
  PATCH /api/mobile/phones/{id}         — update account fields (warming toggle, setup flag, geo_city)
  GET  /api/mobile/phones/{id}/screenshot — take & return screenshot as base64
  GET  /api/mobile/login/{job_id}       — poll login job status
  GET  /api/mobile/job/{job_id}         — poll any async job status
"""

import asyncio
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.paths import DATA_DIR, LOGS_DIR

router = APIRouter(prefix="/api/mobile", tags=["mobile"])

GEELARK_ACCOUNTS_FILE = DATA_DIR / "geelark_accounts.yaml"


class PhoneUpdate(BaseModel):
    mobile_warming_enabled: Optional[bool] = None
    mobile_setup_done:      Optional[bool] = None
    geo_city:               Optional[str]  = None


class ProvisionRequest(BaseModel):
    brand:   Optional[str] = None   # e.g. "Samsung" — if omitted, random fast model chosen
    model:   Optional[str] = None   # e.g. "Galaxy S9+"
    android: Optional[str] = None   # e.g. "Android 10" — if omitted, follows brand/model choice

# In-memory job tracking (cleared on restart — intentional)
_login_jobs: dict[str, dict] = {}
_login_lock  = asyncio.Lock()
_jobs: dict[str, dict] = {}   # generic job store for setup / warmup tasks
_jobs_lock   = asyncio.Lock()

# Global semaphores — only one login and one warmup runs at a time.
# Jobs queue up and wait rather than running in parallel.
_login_semaphore  = threading.Semaphore(1)
_warmup_semaphore = threading.Semaphore(1)


# ── YAML helpers ──────────────────────────────────────────────────────────────

def _load_gl_accounts() -> list:
    if not GEELARK_ACCOUNTS_FILE.exists():
        return []
    try:
        data = yaml.safe_load(GEELARK_ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
        return data.get("accounts", [])
    except Exception:
        return []


def _save_gl_accounts(accounts: list) -> None:
    GEELARK_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GEELARK_ACCOUNTS_FILE.write_text(
        yaml.dump({"accounts": accounts}, default_flow_style=False,
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _get_account(account_id: str) -> dict:
    acc = next((a for a in _load_gl_accounts() if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"GeelarK account {account_id!r} not found")
    return acc


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/phones")
def list_phones():
    """
    Return all GeelarK accounts with their provisioning status and current
    phone run-status (if a phone_id is assigned).
    """
    from core.geelark_client import GeelarKClient
    accounts = _load_gl_accounts()

    # Batch-query status for any provisioned phones
    phone_ids = [a["geelark_phone_id"] for a in accounts if a.get("geelark_phone_id")]
    status_map: dict = {}
    detail_map: dict = {}
    if phone_ids:
        try:
            client   = GeelarKClient()
            statuses = client.get_phone_status(phone_ids)
            status_map = {s["id"]: s for s in statuses}
            # Fetch full details for model info
            all_phones = client.list_phones(page_size=50)
            detail_map = {p["id"]: p for p in all_phones}
        except Exception:
            pass  # status just stays empty

    # Build time-warmed (seconds) per account from mobile_sessions.json
    # Split into today's total and all-time total
    import json as _json
    from datetime import date as _date
    time_warmed_total: dict[str, float] = {}
    time_warmed_today: dict[str, float] = {}
    _today_str = str(_date.today())
    mobile_log = LOGS_DIR / "mobile_sessions.json"
    if mobile_log.exists():
        try:
            sessions = _json.loads(mobile_log.read_text(encoding="utf-8"))
            for s in sessions:
                aid = s.get("account_id")
                if not aid:
                    continue
                dur = float(s.get("duration_s", 0))
                time_warmed_total[aid] = time_warmed_total.get(aid, 0.0) + dur
                ts = s.get("timestamp", "")
                if ts.startswith(_today_str):
                    time_warmed_today[aid] = time_warmed_today.get(aid, 0.0) + dur
        except Exception:
            pass

    result = []
    for acc in accounts:
        pid    = acc.get("geelark_phone_id")
        detail = detail_map.get(pid, {}) if pid else {}
        equip  = detail.get("equipmentInfo", {})
        result.append({
            "id":                      acc["id"],
            "email":                   acc.get("email", ""),
            "phone_id":                pid,
            "provisioned":             bool(pid),
            "phone_status":            status_map.get(pid, {}) if pid else {},
            "device_brand":            equip.get("deviceBrand", acc.get("device_brand", "")),
            "device_model":            equip.get("deviceModel", acc.get("device_model", "")),
            "os_version":              equip.get("osVersion",   acc.get("os_version", "")),
            "mobile_warming_enabled":  acc.get("mobile_warming_enabled", True),
            "mobile_setup_done":       acc.get("mobile_setup_done", False),
            "geo_city":                acc.get("geo_city", ""),
            "login_verified":          acc.get("login_verified"),        # True/False/None
            "login_checked_at":        acc.get("login_checked_at", ""),
            "time_warmed_s":           time_warmed_total.get(acc["id"], 0.0),
            "time_warmed_today_s":     time_warmed_today.get(acc["id"], 0.0),
        })
    return result


@router.get("/fast-models")
def get_fast_models():
    """Return the curated list of fast phone models available for provisioning."""
    from core.geelark_client import FAST_MODELS
    return FAST_MODELS


@router.post("/phones/{account_id}/provision")
def provision_phone(account_id: str, body: ProvisionRequest = None):
    """
    Create a new GeelarK cloud phone profile for this account.
    Optionally specify brand/model/android — if omitted, a random fast model is chosen.
    Stores the returned phone ID and model info into geelark_accounts.yaml.
    """
    from core.geelark_client import GeelarKClient
    accounts = _load_gl_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"Account {account_id!r} not found")
    if acc.get("geelark_phone_id"):
        raise HTTPException(409, f"Account already provisioned: phone_id={acc['geelark_phone_id']}")

    import os
    client = GeelarKClient()

    mobile_proxy = os.environ.get(
        "GEELARK_PROXY",
        "socks5://19866_1:s1jlR529hE@mobile-proxy-140-166.streamvia.io:7002",
    )

    req = body or ProvisionRequest()
    try:
        phone_id, equip = client.create_phone(
            profile_name=acc.get("email", account_id),
            proxy=mobile_proxy,
            android_version=req.android or None,
            brand=req.brand or None,
            model=req.model or None,
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to create phone: {e}")

    acc["geelark_phone_id"] = phone_id
    acc["device_brand"]     = equip.get("deviceBrand", req.brand or "")
    acc["device_model"]     = equip.get("deviceModel", req.model or "")
    acc["os_version"]       = equip.get("osVersion", req.android or "")
    acc.setdefault("mobile_warming_enabled", True)
    acc.setdefault("mobile_setup_done", False)
    _save_gl_accounts(accounts)
    return {
        "account_id":   account_id,
        "phone_id":     phone_id,
        "device_brand": acc["device_brand"],
        "device_model": acc["device_model"],
        "os_version":   acc["os_version"],
    }


@router.post("/phones/{account_id}/start")
def start_phone(account_id: str):
    """
    Start the cloud phone. Returns the viewer URL — open in a browser tab
    to watch and control the phone in real time.
    """
    from core.geelark_client import GeelarKClient
    acc    = _get_account(account_id)
    pid    = acc.get("geelark_phone_id")
    if not pid:
        raise HTTPException(400, "Phone not provisioned — call /provision first")

    client = GeelarKClient()
    try:
        viewer_url = client.start_phone(pid)
    except Exception as e:
        raise HTTPException(500, f"Failed to start phone: {e}")
    return {"account_id": account_id, "phone_id": pid, "viewer_url": viewer_url}


@router.post("/phones/{account_id}/stop")
def stop_phone(account_id: str):
    """Stop the cloud phone."""
    from core.geelark_client import GeelarKClient
    acc = _get_account(account_id)
    pid = acc.get("geelark_phone_id")
    if not pid:
        raise HTTPException(400, "Phone not provisioned")
    client = GeelarKClient()
    try:
        client.stop_phone(pid)
    except Exception as e:
        raise HTTPException(500, f"Failed to stop phone: {e}")
    return {"account_id": account_id, "stopped": True}


@router.patch("/phones/{account_id}")
def update_phone_account(account_id: str, body: PhoneUpdate):
    """
    Update per-account settings: mobile_warming_enabled, mobile_setup_done, geo_city.
    Used by the dashboard to toggle warming on/off and track setup progress.
    """
    accounts = _load_gl_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"Account {account_id!r} not found")

    if body.mobile_warming_enabled is not None:
        acc["mobile_warming_enabled"] = body.mobile_warming_enabled
    if body.mobile_setup_done is not None:
        acc["mobile_setup_done"] = body.mobile_setup_done
    if body.geo_city is not None:
        acc["geo_city"] = body.geo_city

    _save_gl_accounts(accounts)
    pid = acc.get("geelark_phone_id")
    return {
        "id":                     acc["id"],
        "email":                  acc.get("email", ""),
        "phone_id":               pid,
        "provisioned":            bool(pid),
        "mobile_warming_enabled": acc.get("mobile_warming_enabled", True),
        "mobile_setup_done":      acc.get("mobile_setup_done", False),
        "geo_city":               acc.get("geo_city", ""),
    }


@router.post("/phones/{account_id}/login")
async def trigger_login(account_id: str):
    """
    Start the Google login flow for this account on its cloud phone.
    Runs asynchronously in a background thread.
    Returns a job_id — poll GET /api/mobile/login/{job_id} for status.
    """
    acc = _get_account(account_id)
    if not acc.get("geelark_phone_id"):
        raise HTTPException(400, "Phone not provisioned — call /provision first")

    job_id = str(uuid.uuid4())[:8]
    async with _login_lock:
        _login_jobs[job_id] = {
            "job_id":     job_id,
            "account_id": account_id,
            "email":      acc.get("email", ""),
            "status":     "running",
            "phase":      "starting",
            "viewer_url": "",
            "started_at": time.time(),
            "result":     None,
        }

    # Run in background thread so the HTTP response returns immediately
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_login_sync, job_id, acc)

    return {"job_id": job_id, "account_id": account_id, "status": "running"}


def _run_login_sync(job_id: str, account: dict) -> None:
    """Background worker — calls the login loop and stores result in _login_jobs.
    Acquires _login_semaphore so only one login runs at a time across all accounts."""
    job = _login_jobs.get(job_id)

    # Mark as queued while waiting for the semaphore
    if job:
        job["phase"] = "queued"

    _login_semaphore.acquire()

    # Re-fetch job in case it was cleared while waiting
    job = _login_jobs.get(job_id)
    if job:
        job["phase"] = "starting"

    def _on_progress(update: dict) -> None:
        j = _login_jobs.get(job_id)
        if not j:
            return
        if "phase" in update:
            j["phase"] = update["phase"]
        if update.get("viewer_url"):
            j["viewer_url"] = update["viewer_url"]

    try:
        from activities.google_login_mobile import run_google_login
        result = run_google_login(account, stop_phone_on_success=True,
                                  progress_callback=_on_progress)
        _login_jobs[job_id]["result"] = result
        _login_jobs[job_id]["status"] = "completed" if result["success"] else (
            "needs_input" if result.get("needs_user_input") else "failed"
        )
        _login_jobs[job_id]["phase"] = "complete" if result["success"] else "failed"

        # Persist login_verified to YAML so the dashboard can show status without re-checking
        if result["success"]:
            _persist_login_status(account["id"], verified=True)
    except Exception as e:
        _login_jobs[job_id]["status"] = "error"
        _login_jobs[job_id]["phase"] = "error"
        _login_jobs[job_id]["result"] = {"error": str(e)}
    finally:
        _login_semaphore.release()


def _persist_login_status(account_id: str, verified: bool) -> None:
    """Write login_verified + login_checked_at into geelark_accounts.yaml."""
    try:
        accounts = _load_gl_accounts()
        for acc in accounts:
            if acc["id"] == account_id:
                acc["login_verified"]   = verified
                acc["login_checked_at"] = datetime.now(timezone.utc).isoformat()
                break
        _save_gl_accounts(accounts)
    except Exception as e:
        import logging as _log
        _log.getLogger("mobile").warning("Failed to persist login status for %s: %s", account_id, e)


@router.get("/login/{job_id}")
def poll_login(job_id: str):
    """Poll the status of a login job started by POST /phones/{id}/login."""
    job = _login_jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Login job {job_id!r} not found")
    return job


@router.post("/phones/{account_id}/recreate")
def recreate_phone(account_id: str, body: ProvisionRequest = None):
    """
    Stop, delete and recreate the phone for this account in one call.
    Use this when a phone shows 'service is bad' or is stuck.
    Picks a random fast model unless brand/model/android are specified.
    Requires the phone viewer to be closed in the GeelarK portal first.
    """
    import os
    from core.geelark_client import GeelarKClient
    acc = _get_account(account_id)
    old_phone_id = acc.get("geelark_phone_id")
    if not old_phone_id:
        raise HTTPException(400, "No phone provisioned for this account")

    client       = GeelarKClient()
    mobile_proxy = os.environ.get("GEELARK_PROXY", "")
    req          = body or ProvisionRequest()

    try:
        new_phone_id, equip = client.recreate_phone(
            old_phone_id=old_phone_id,
            profile_name=acc.get("email", account_id),
            proxy=mobile_proxy,
            brand=req.brand or None,
            model=req.model or None,
            android_version=req.android or None,
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    accounts = _load_gl_accounts()
    for a in accounts:
        if a["id"] == account_id:
            a["geelark_phone_id"] = new_phone_id
            a["device_brand"]     = equip.get("deviceBrand", "")
            a["device_model"]     = equip.get("deviceModel", "")
            a["os_version"]       = equip.get("osVersion", "")
            a["mobile_setup_done"]  = False
            a["login_verified"]     = None
            a["login_checked_at"]   = ""
            break
    _save_gl_accounts(accounts)

    return {
        "account_id":    account_id,
        "old_phone_id":  old_phone_id,
        "new_phone_id":  new_phone_id,
        "device_brand":  equip.get("deviceBrand", ""),
        "device_model":  equip.get("deviceModel", ""),
        "os_version":    equip.get("osVersion", ""),
    }


@router.post("/phones/{account_id}/check-login")
async def check_login(account_id: str):
    """
    Strong login check — starts the phone, runs 3 independent ADB checks
    to verify the Google account is registered, then stops the phone.
    Returns a job_id — poll GET /api/mobile/job/{job_id} for status.
    """
    acc = _get_account(account_id)
    if not acc.get("geelark_phone_id"):
        raise HTTPException(400, "Phone not provisioned")

    job_id = str(uuid.uuid4())[:8]
    async with _jobs_lock:
        _jobs[job_id] = {
            "job_id":     job_id,
            "type":       "check_login",
            "account_id": account_id,
            "email":      acc.get("email", ""),
            "status":     "running",
            "started_at": time.time(),
            "result":     None,
        }

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_check_login_sync, job_id, acc)
    return {"job_id": job_id, "account_id": account_id, "status": "running"}


def _run_check_login_sync(job_id: str, account: dict) -> None:
    try:
        from activities.google_login_mobile import run_login_check
        result = run_login_check(account)
        _jobs[job_id]["result"] = result
        _jobs[job_id]["status"] = "completed"
        # Persist result so the dashboard badge stays up to date
        _persist_login_status(account["id"], verified=result["verified"])
    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["result"] = {"error": str(e)}


@router.get("/phones/{account_id}/screenshot")
def take_screenshot(account_id: str):
    """
    Take a screenshot of the phone right now.
    Returns {"screenshot_b64": "<base64 PNG>"} on success.
    """
    import base64
    from core.geelark_client import GeelarKClient
    acc = _get_account(account_id)
    pid = acc.get("geelark_phone_id")
    if not pid:
        raise HTTPException(400, "Phone not provisioned")

    client = GeelarKClient()
    img    = client.take_screenshot(pid)
    if not img:
        raise HTTPException(500, "Screenshot failed or phone is not running")
    return {"screenshot_b64": base64.standard_b64encode(img).decode()}


# ── One-time setup job ────────────────────────────────────────────────────────

@router.post("/phones/{account_id}/setup")
async def trigger_setup(account_id: str):
    """
    Run the one-time mobile account setup on this phone (async).
    Returns a job_id — poll GET /api/mobile/job/{job_id} for status.
    Skips if mobile_setup_done is already true.
    """
    acc = _get_account(account_id)
    if not acc.get("geelark_phone_id"):
        raise HTTPException(400, "Phone not provisioned — call /provision first")

    job_id = str(uuid.uuid4())[:8]
    async with _jobs_lock:
        _jobs[job_id] = {
            "job_id":     job_id,
            "type":       "setup",
            "account_id": account_id,
            "status":     "running",
            "started_at": time.time(),
            "result":     None,
        }

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_setup_sync, job_id, acc)
    return {"job_id": job_id, "account_id": account_id, "status": "running"}


def _run_setup_sync(job_id: str, account: dict) -> None:
    try:
        from activities.mobile_account_setup import run_account_setup
        result = run_account_setup(account)
        _jobs[job_id]["result"] = result
        _jobs[job_id]["status"] = "completed" if result["success"] else "failed"

        if result["success"]:
            # Persist mobile_setup_done = true
            accounts = _load_gl_accounts()
            for acc in accounts:
                if acc["id"] == account["id"]:
                    acc["mobile_setup_done"] = True
                    break
            _save_gl_accounts(accounts)
    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["result"] = {"error": str(e)}


# ── Warm-up jobs ──────────────────────────────────────────────────────────────

@router.post("/phones/{account_id}/warmup")
async def trigger_warmup(account_id: str):
    """
    Run a single warm-up session for this phone (async).
    Returns a job_id — poll GET /api/mobile/job/{job_id} for status.
    """
    acc = _get_account(account_id)
    if not acc.get("geelark_phone_id"):
        raise HTTPException(400, "Phone not provisioned — call /provision first")
    if not acc.get("mobile_warming_enabled", True):
        raise HTTPException(400, "mobile_warming_enabled is false for this account")

    job_id = str(uuid.uuid4())[:8]
    async with _jobs_lock:
        _jobs[job_id] = {
            "job_id":     job_id,
            "type":       "warmup",
            "account_id": account_id,
            "status":     "running",
            "phase":      "queued",
            "viewer_url": "",
            "started_at": time.time(),
            "result":     None,
        }

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_warmup_sync, job_id, acc)
    return {"job_id": job_id, "account_id": account_id, "status": "running"}


@router.post("/warmup/all")
async def trigger_warmup_all():
    """
    Run warm-up sessions for ALL mobile_warming_enabled accounts, sequentially.
    Returns a job_id — poll GET /api/mobile/job/{job_id} for status.
    """
    job_id = str(uuid.uuid4())[:8]
    async with _jobs_lock:
        _jobs[job_id] = {
            "job_id":     job_id,
            "type":       "warmup_all",
            "status":     "running",
            "phase":      "queued",
            "viewer_url": "",
            "started_at": time.time(),
            "result":     None,
        }

    accounts = _load_gl_accounts()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_warmup_all_sync, job_id, accounts)
    return {"job_id": job_id, "status": "running"}


def _run_warmup_sync(job_id: str, account: dict) -> None:
    # Mark as queued while waiting for the semaphore (another phone may be running)
    job = _jobs.get(job_id)
    if job:
        job["status"] = "running"
        job["phase"]  = "queued"

    _warmup_semaphore.acquire()

    job = _jobs.get(job_id)
    if job:
        job["phase"] = "starting"

    def _progress(update: dict) -> None:
        j = _jobs.get(job_id)
        if not j:
            return
        if "phase" in update:
            j["phase"] = update["phase"]
        if update.get("viewer_url"):
            j["viewer_url"] = update["viewer_url"]

    try:
        from activities.mobile_warmup import run_warmup_session
        from core.paths import DATA_DIR
        log_file = DATA_DIR / "logs" / "mobile_sessions.json"
        result = run_warmup_session(account, log_file, progress_callback=_progress)
        _jobs[job_id]["result"] = result
        _jobs[job_id]["status"] = "completed" if result["success"] else "failed"
        _jobs[job_id]["phase"]  = "complete" if result["success"] else "failed"
        _jobs[job_id]["viewer_url"] = ""   # clear after done so window can close
    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["phase"]  = "error"
        _jobs[job_id]["result"] = {"error": str(e)}
        _jobs[job_id]["viewer_url"] = ""
    finally:
        _warmup_semaphore.release()


def _run_warmup_all_sync(job_id: str, accounts: list) -> None:
    job = _jobs.get(job_id)
    if job:
        job["phase"] = "queued"

    _warmup_semaphore.acquire()

    job = _jobs.get(job_id)
    if job:
        job["phase"] = "starting"

    def _progress(update: dict) -> None:
        j = _jobs.get(job_id)
        if not j:
            return
        if "phase" in update:
            j["phase"] = update["phase"]
        if update.get("viewer_url") is not None:
            j["viewer_url"] = update["viewer_url"]

    try:
        from activities.mobile_warmup import run_all_warmup_sessions
        from core.paths import DATA_DIR
        log_file = DATA_DIR / "logs" / "mobile_sessions.json"
        results = run_all_warmup_sessions(accounts, log_file, progress_callback=_progress)
        _jobs[job_id]["result"] = results
        ok = sum(1 for r in results if r.get("success"))
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["phase"]  = "complete"
        _jobs[job_id]["viewer_url"] = ""   # clear after done
        _jobs[job_id]["summary"] = f"{ok}/{len(results)} accounts OK"
    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["phase"]  = "error"
        _jobs[job_id]["result"] = {"error": str(e)}
        _jobs[job_id]["viewer_url"] = ""
    finally:
        _warmup_semaphore.release()


@router.get("/job/{job_id}")
def poll_job(job_id: str):
    """Poll any async job (setup, warmup, warmup_all) by job_id."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    return job
