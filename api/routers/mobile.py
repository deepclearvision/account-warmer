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
import csv
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

class BatchRunRequest(BaseModel):
    mode: str                       # "brand-1km", "money-kw", "local-discovery"
    account_ids: list[str]          # e.g. ["gl_004", "gl_005"]

# In-memory job tracking (cleared on restart — intentional)
_login_jobs: dict[str, dict] = {}
_login_lock  = asyncio.Lock()
_jobs: dict[str, dict] = {}   # generic job store for setup / warmup tasks
_jobs_lock   = asyncio.Lock()

# Global semaphores — only one login and one warmup runs at a time.
# Jobs queue up and wait rather than running in parallel.
_login_semaphore  = threading.Semaphore(1)
_warmup_semaphore = threading.Semaphore(1)
_batch_run_semaphore = threading.Semaphore(1)
_active_batch_job_id: Optional[str] = None

# Track the most recent warmup_all job so the dashboard can resume polling after a page refresh.
_active_warmup_all_job_id: Optional[str] = None

# Active hours — must match scheduler.py (7am–10pm)
MOBILE_ACTIVE_HOURS_START = 7
MOBILE_ACTIVE_HOURS_END   = 22

def _check_active_hours():
    """Raise 503 if current local time is outside the allowed warming window."""
    hour = datetime.now().hour
    if not (MOBILE_ACTIVE_HOURS_START <= hour < MOBILE_ACTIVE_HOURS_END):
        raise HTTPException(
            503,
            f"Outside active hours ({MOBILE_ACTIVE_HOURS_START}:00–{MOBILE_ACTIVE_HOURS_END}:00). "
            f"Mobile warmup will resume at {MOBILE_ACTIVE_HOURS_START}:00."
        )


# ── Account data helpers (via AccountStore) ────────────────────────────────────

def _load_gl_accounts() -> list:
    """Return mobile accounts via AccountStore."""
    from core.account_store import get_account_store
    return get_account_store().get_mobile_accounts()


def _save_gl_accounts(accounts: list) -> None:
    """Save mobile accounts via AccountStore."""
    from core.account_store import get_account_store
    get_account_store().save_mobile_accounts(accounts)


def _get_account(account_id: str) -> dict:
    acc = next((a for a in _load_gl_accounts() if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"GeelarK account {account_id!r} not found")
    return acc


def _mobile_job_running(account_id: str) -> bool:
    """True if a login/setup/warmup job is currently running for this account."""
    for store in (_jobs, _login_jobs):
        for job in store.values():
            if job.get("account_id") == account_id and job.get("status") == "running":
                return True
    return False


def _delete_geelark_phone_cloud(phone_id: str) -> dict:
    """Stop then permanently delete a GeelarK cloud phone. Best-effort stop."""
    from core.geelark_client import GeelarKClient
    client = GeelarKClient()
    try:
        client.stop_phone(phone_id)
        time.sleep(3)  # let GeelarK release the env before delete
    except Exception:
        pass  # may already be stopped
    try:
        client.delete_phone(phone_id)
        return {"success": True, "phone_id": phone_id, "status": "deleted"}
    except Exception as e:
        return {"success": False, "phone_id": phone_id, "status": "error",
                "detail": str(e)[:200]}


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

    # Build time-warmed (seconds) and last-session info per account from mobile_sessions.json
    import json as _json
    from datetime import date as _date
    time_warmed_total: dict[str, float] = {}
    time_warmed_today: dict[str, float] = {}
    last_session_map: dict[str, dict]   = {}
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
                # Last session = last appended record per account
                last_session_map[aid] = {
                    "date":       ts[:10] if ts else "",
                    "steps":      s.get("steps_done", []),
                    "screenshot": s.get("screenshot", ""),
                    "success":    s.get("success", False),
                }
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
            "last_session":            last_session_map.get(acc["id"]),
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


@router.delete("/phones/{account_id}")
def delete_mobile_account(account_id: str,
                          delete_phone: bool = False,
                          delete_desktop: bool = False):
    """
    Delete a mobile account from geelark_accounts.yaml, plus its flow data and
    session records. Optionally destroy the GeelarK cloud phone (delete_phone)
    and/or remove the paired desktop account from accounts.yaml (delete_desktop).

    Blocked (409) if a mobile job (login/setup/warmup) is running for this account.
    """
    from core.account_store import get_account_store
    store = get_account_store()

    accounts = _load_gl_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"GeelarK account {account_id!r} not found")
    if _mobile_job_running(account_id):
        raise HTTPException(409, f"A mobile job is running for {account_id!r} — "
                                 f"wait for it to finish before deleting.")

    email    = acc.get("email", "")
    phone_id = acc.get("geelark_phone_id")

    result: dict = {
        "account_id":            account_id,
        "email":                 email,
        "geelark_phone":         None,
        "mobile_entry_removed":  False,
        "desktop_entry_removed": False,
        "flow_data_removed":     0,
        "sessions_removed":      0,
    }

    # 1. GeelarK cloud phone
    if delete_phone:
        if phone_id:
            result["geelark_phone"] = _delete_geelark_phone_cloud(phone_id)
        else:
            result["geelark_phone"] = {"skipped": "no geelark_phone_id"}

    # 2. Remove the mobile yaml entry
    kept = [a for a in accounts if a["id"] != account_id]
    if len(kept) != len(accounts):
        _save_gl_accounts(kept)
        result["mobile_entry_removed"] = True

    # 3. Optionally remove the paired desktop account by email
    desktop_id = None
    if delete_desktop and email:
        desktop = store.get_desktop_accounts()
        removed = [a for a in desktop if (a.get("email") or "").lower() == email.lower()]
        if removed:
            desktop_id = removed[0].get("id")
            store.save_desktop_accounts(
                [a for a in desktop if (a.get("email") or "").lower() != email.lower()]
            )
            result["desktop_entry_removed"] = True

    # 4. Flow data + session records (keyed by mobile id and/or desktop id)
    ids = {account_id, desktop_id}
    result["flow_data_removed"] = store.remove_flow_data(ids)
    result["sessions_removed"]  = store.remove_mobile_sessions(ids)

    return result


@router.post("/phones/bulk-delete")
def bulk_delete_mobile(body: dict):
    """
    Delete multiple mobile accounts at once.
    body: {"ids": [...], "delete_phone": bool, "delete_desktop": bool}

    Cloud phones are stopped+deleted only for provisioned accounts. Accounts
    with a running mobile job are skipped. geelark_accounts.yaml and
    accounts.yaml are each rewritten once; flow data + sessions are cleaned up
    for every removed id.
    """
    from core.account_store import get_account_store
    store = get_account_store()

    ids            = list(dict.fromkeys(body.get("ids", [])))
    delete_phone   = bool(body.get("delete_phone", False))
    delete_desktop = bool(body.get("delete_desktop", False))
    if not ids:
        raise HTTPException(400, "No account IDs provided")

    accounts = _load_gl_accounts()
    by_id    = {a["id"]: a for a in accounts}

    deleted:  list[str]  = []
    skipped:  list[dict] = []
    results:  dict       = {}
    remove_ids:      set = set()
    emails_desktop:  set = set()

    for aid in ids:
        acc = by_id.get(aid)
        if not acc:
            skipped.append({"id": aid, "reason": "not found"}); continue
        if _mobile_job_running(aid):
            skipped.append({"id": aid, "reason": "job running"}); continue

        phone_id = acc.get("geelark_phone_id")
        email    = acc.get("email", "")
        res: dict = {"geelark_phone": None}
        if delete_phone:
            res["geelark_phone"] = (_delete_geelark_phone_cloud(phone_id)
                                    if phone_id else {"skipped": "no geelark_phone_id"})
        if delete_desktop and email:
            emails_desktop.add(email.lower())
        remove_ids.add(aid)
        results[aid] = res
        deleted.append(aid)

    # Rewrite geelark_accounts.yaml once
    if remove_ids:
        _save_gl_accounts([a for a in accounts if a["id"] not in remove_ids])

    # Rewrite accounts.yaml once (only the paired desktop accounts we were asked to remove)
    desktop_removed: list[str] = []
    if emails_desktop:
        desktop = store.get_desktop_accounts()
        desktop_removed = [a["id"] for a in desktop
                           if (a.get("email") or "").lower() in emails_desktop]
        if desktop_removed:
            store.save_desktop_accounts(
                [a for a in desktop if (a.get("email") or "").lower() not in emails_desktop]
            )

    all_ids = remove_ids | set(desktop_removed)
    flow_removed     = store.remove_flow_data(all_ids)
    sessions_removed = store.remove_mobile_sessions(all_ids)

    return {
        "deleted":           len(deleted),
        "ids":               deleted,
        "skipped":           skipped,
        "desktop_removed":   desktop_removed,
        "flow_data_removed": flow_removed,
        "sessions_removed":  sessions_removed,
        "results":           results,
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


@router.post("/phones/{account_id}/install-gps-app")
def install_gps_app(account_id: str):
    """
    Install the mock GPS spoofing app on an already-provisioned phone.
    Returns immediately; does not wait for install to complete.
    Use /screenshot to verify afterwards.
    """
    from core.gps_spoofing import ensure_installed
    from core.geelark_client import GeelarKClient

    acc = _get_account(account_id)
    pid = acc.get("geelark_phone_id")
    if not pid:
        raise HTTPException(400, "Phone not provisioned")

    client = GeelarKClient()
    try:
        client.start_phone(pid)
    except Exception:
        pass  # may already be running

    ok = ensure_installed(pid)
    return {
        "account_id": account_id,
        "phone_id": pid,
        "installed": ok,
        "note": "Install started. Verify with /screenshot after 30s." if ok else "APK not found — place fake-gps.apk in data/",
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
    _check_active_hours()
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


@router.get("/warmup/status")
def get_warmup_status():
    """Return the currently running (or most recent) warmup_all job for dashboard polling."""
    global _active_warmup_all_job_id
    if _active_warmup_all_job_id:
        job = _jobs.get(_active_warmup_all_job_id)
        if job:
            return job
    return {"status": "idle"}


@router.post("/warmup/all")
async def trigger_warmup_all():
    """
    Run warm-up sessions for ALL mobile_warming_enabled accounts, sequentially.
    Returns a job_id — poll GET /api/mobile/job/{job_id} for status.
    """
    global _active_warmup_all_job_id
    _check_active_hours()
    job_id = str(uuid.uuid4())[:8]
    _active_warmup_all_job_id = job_id
    async with _jobs_lock:
        _jobs[job_id] = {
            "job_id":          job_id,
            "type":            "warmup_all",
            "status":          "running",
            "phase":           "queued",
            "viewer_url":      "",
            "started_at":      time.time(),
            "current_account": "",
            "progress":        "",
            "result":          None,
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
        from activities.mobile_warmup import run_mobile_schedule_session
        from core.paths import DATA_DIR
        log_file = DATA_DIR / "logs" / "mobile_sessions.json"
        result = run_mobile_schedule_session(account, log_file, progress_callback=_progress, schedule_state=None)
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
        if "current_account" in update:
            j["current_account"] = update["current_account"]
        if "progress" in update:
            j["progress"] = update["progress"]

    try:
        from activities.mobile_warmup import run_all_warmup_sessions  # kept for backward compat
        from core.paths import DATA_DIR
        log_file = DATA_DIR / "logs" / "mobile_sessions.json"
        results = run_all_warmup_sessions(accounts, log_file, progress_callback=_progress)
        _jobs[job_id]["result"] = results
        ok = sum(1 for r in results if r.get("success") and not r.get("skipped"))
        skipped = sum(1 for r in results if r.get("skipped"))
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["phase"]  = "complete"
        _jobs[job_id]["viewer_url"] = ""   # clear after done
        _jobs[job_id]["summary"] = f"{ok}/{len(results) - skipped} ran OK" + (f", {skipped} already done today" if skipped else "")
    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["phase"]  = "error"
        _jobs[job_id]["result"] = {"error": str(e)}
        _jobs[job_id]["viewer_url"] = ""
    finally:
        _warmup_semaphore.release()


@router.post("/warmup/bulk-by-accounts")
async def trigger_warmup_bulk_by_accounts(body: dict):
    """
    Queue mobile warmup sessions for a list of desktop account IDs (acc_xxx).
    Resolves each to its GeelarK phone via email match, then queues warmup.
    Sessions run one at a time via the existing warmup semaphore.
    """
    _check_active_hours()
    acc_ids = body.get("ids", [])
    if not acc_ids:
        raise HTTPException(400, "No account IDs provided")

    # Build email → desktop account map via AccountStore
    from core.account_store import get_account_store
    store = get_account_store()
    desktop_email: dict[str, str] = {}
    for a in store.get_desktop_accounts():
        if a.get("email"):
            desktop_email[a["id"]] = a["email"].lower()

    # Build email → GeelarK account map
    gl_accounts = _load_gl_accounts()
    email_to_gl: dict[str, dict] = {
        a.get("email", "").lower(): a for a in gl_accounts if a.get("email")
    }

    queued, skipped = [], []
    loop = asyncio.get_event_loop()

    for acc_id in acc_ids:
        email = desktop_email.get(acc_id, "")
        if not email:
            skipped.append({"id": acc_id, "reason": "desktop account not found"})
            continue
        gl_acc = email_to_gl.get(email)
        if not gl_acc:
            skipped.append({"id": acc_id, "reason": "no mobile phone linked"})
            continue
        if not gl_acc.get("geelark_phone_id"):
            skipped.append({"id": acc_id, "reason": "phone not provisioned"})
            continue
        if not gl_acc.get("mobile_warming_enabled", True):
            skipped.append({"id": acc_id, "reason": "mobile warming disabled"})
            continue

        job_id = str(uuid.uuid4())[:8]
        async with _jobs_lock:
            _jobs[job_id] = {
                "job_id":     job_id,
                "type":       "warmup",
                "account_id": gl_acc["id"],
                "desktop_id": acc_id,
                "status":     "running",
                "phase":      "queued",
                "viewer_url": "",
                "started_at": time.time(),
                "result":     None,
            }

        loop.run_in_executor(None, _run_warmup_sync, job_id, gl_acc)
        queued.append({"id": acc_id, "gl_id": gl_acc["id"], "job_id": job_id})

    return {"queued": queued, "skipped": skipped}


# ── Batch Runner ──────────────────────────────────────────────────────────────

@router.post("/batch/run")
async def trigger_batch_run(body: BatchRunRequest):
    """Run _run.py for each selected account sequentially. Returns job_id for polling."""
    global _active_batch_job_id
    if body.mode not in ("brand-1km", "money-kw", "local-discovery"):
        raise HTTPException(400, f"Invalid mode: {body.mode}")
    if not body.account_ids:
        raise HTTPException(400, "No account IDs provided")

    # Validate all IDs exist and have phone_ids
    accounts = _load_gl_accounts()
    gl_map = {a["id"]: a for a in accounts}
    invalid = [aid for aid in body.account_ids if aid not in gl_map]
    if invalid:
        raise HTTPException(400, f"Unknown account IDs: {invalid}")
    unprovisioned = [aid for aid in body.account_ids if not gl_map[aid].get("geelark_phone_id")]
    if unprovisioned:
        raise HTTPException(400, f"Phones not provisioned: {unprovisioned}")

    job_id = str(uuid.uuid4())[:8]
    _active_batch_job_id = job_id
    async with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id, "type": "batch_run", "mode": body.mode,
            "status": "running", "phase": "queued", "started_at": time.time(),
            "current_account": "", "progress": "", "result": None, "summary": "", "result_detail": [],
        }

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_batch_sync, job_id, body.account_ids, body.mode)
    return {"job_id": job_id, "status": "running"}


@router.get("/batch/status")
def get_batch_status():
    """Return currently running batch job (for dashboard page-refresh resume)."""
    global _active_batch_job_id
    if _active_batch_job_id:
        job = _jobs.get(_active_batch_job_id)
        if job:
            return job
    return {"status": "idle"}


def _run_batch_sync(job_id: str, account_ids: list[str], mode: str) -> None:
    """Sequentially run _run.py <mode> <acc_id> for each account."""
    job = _jobs.get(job_id)
    if job:
        job["phase"] = "queued"
    _batch_run_semaphore.acquire()
    job = _jobs.get(job_id)
    if job:
        job["phase"] = "running"

    CSV_PATH = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\data\accounts_business_mapping.csv")
    gl_accounts = _load_gl_accounts()
    gl_by_id = {a["id"]: a for a in gl_accounts}

    # Build email → acc_id map from CSV
    email_to_acc = {}
    try:
        with open(CSV_PATH) as f:
            for row in csv.DictReader(f):
                email = (row.get("account_email") or "").lower().strip()
                if email:
                    email_to_acc[email] = row["account_id"]
    except Exception:
        pass

    RUN_SCRIPT = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\scripts\_run.py")
    total = len(account_ids)
    results = []
    detail = []

    for idx, gl_id in enumerate(account_ids):
        gl_acc = gl_by_id.get(gl_id)
        if not gl_acc:
            detail.append({"account": gl_id, "status": "error", "info": "not found"})
            results.append({"account_id": gl_id, "success": False, "error": "not found"})
            continue

        email = (gl_acc.get("email") or "").lower().strip()
        acc_id = email_to_acc.get(email)
        if not acc_id:
            detail.append({"account": gl_id, "status": "error", "info": "no CSV mapping for %s" % email})
            results.append({"account_id": gl_id, "success": False, "error": "no CSV mapping"})
            continue

        progress = f"{idx + 1}/{total}"
        if job:
            job["current_account"] = gl_id
            job["progress"] = progress
        detail.append({"account": gl_id, "status": "running", "info": "starting %s..." % mode})
        if job:
            job["result_detail"] = list(detail)

        try:
            completed = subprocess.run(
                [sys.executable, str(RUN_SCRIPT), mode, acc_id],
                capture_output=True, text=True, timeout=900,
                cwd=str(RUN_SCRIPT.parent)
            )
            ok = completed.returncode == 0
            last_lines = (completed.stdout or "").splitlines()[-3:]
            info = " ".join(last_lines) if last_lines else ("exit " + str(completed.returncode))
            detail[-1] = {"account": gl_id, "status": "done" if ok else "error", "info": info[:200]}
            results.append({"account_id": gl_id, "acc_id": acc_id, "success": ok, "returncode": completed.returncode})
        except subprocess.TimeoutExpired:
            detail[-1] = {"account": gl_id, "status": "error", "info": "timeout (15 min)"}
            results.append({"account_id": gl_id, "success": False, "error": "timeout"})
        except Exception as e:
            detail[-1] = {"account": gl_id, "status": "error", "info": str(e)[:100]}
            results.append({"account_id": gl_id, "success": False, "error": str(e)})

        if job:
            job["result_detail"] = list(detail)

    ok_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - ok_count
    if job:
        job["result"] = results
        job["summary"] = f"{ok_count}/{total} OK" + (f", {fail_count} failed" if fail_count else "")
        job["status"] = "completed"
        job["phase"] = "complete"
        job["current_account"] = ""
        job["result_detail"] = detail

    _batch_run_semaphore.release()


# ═══════════════════════════════════════════════════════════════════════════════
# ── Mobile Schedule endpoints ─────────────────────────────────────────────────

@router.post("/schedule/run-forever")
async def schedule_run_forever(body: dict | None = None):
    """
    Enroll selected accounts in BOTH the desktop scheduler and the new mobile
    schedule scheduler — one button to start continuous dual-platform warming.

    Body (optional): {"account_ids": ["acc_001", "acc_002", ...]}

    If no account_ids provided, ALL desktop accounts with a paired provisioned
    GeelarK phone are enrolled.
    """
    _check_active_hours()
    acc_ids = (body or {}).get("account_ids", [])

    from core.account_store import get_account_store
    store = get_account_store()
    desktop_accounts = store.get_desktop_accounts()

    if acc_ids:
        id_set = set(acc_ids)
        desktop_accounts = [a for a in desktop_accounts if a["id"] in id_set]

    # ── Desktop — resume scheduler + bulk-enroll ─────────────────────────────
    from core.scheduler import get_scheduler as get_desktop_scheduler
    desktop_svc = get_desktop_scheduler()
    desktop_svc.resume()
    desktop_enrolled = 0
    for acc in desktop_accounts:
        desktop_svc.enroll(acc["id"])
        desktop_enrolled += 1

    # ── Mobile — resolve GL accounts by email, enroll in mobile scheduler ───
    from core.mobile_scheduler import get_mobile_scheduler
    mobile_svc = get_mobile_scheduler()
    mobile_svc.resume()

    gl_accounts  = store.get_mobile_accounts()
    email_to_gl  = {a.get("email", "").lower(): a for a in gl_accounts if a.get("email")}

    mobile_enrolled = 0
    errors = []
    for acc in desktop_accounts:
        email = (acc.get("email") or "").lower()
        gl_acc = email_to_gl.get(email)
        if not gl_acc:
            errors.append({"account": acc["id"], "error": "no mobile phone linked"})
            continue
        if not gl_acc.get("geelark_phone_id"):
            errors.append({"account": acc["id"], "error": "phone not provisioned"})
            continue
        if not gl_acc.get("mobile_warming_enabled", True):
            errors.append({"account": acc["id"], "error": "mobile warming disabled"})
            continue
        mobile_svc.enroll(gl_acc["id"])
        mobile_enrolled += 1

    return {
        "desktop": {"enrolled": desktop_enrolled,
                    "scheduler_resumed": not desktop_svc.paused},
        "mobile":  {"enrolled": mobile_enrolled,
                     "schedule_paused": mobile_svc.paused},
        "errors":  errors,
    }


@router.get("/schedule/status")
def schedule_status():
    """Return the mobile schedule state for all enrolled accounts."""
    from core.mobile_scheduler import get_mobile_scheduler
    return get_mobile_scheduler().get_status()


@router.post("/schedule/pause")
def schedule_pause():
    """Pause the mobile schedule — no new phone sessions will start."""
    from core.mobile_scheduler import get_mobile_scheduler
    get_mobile_scheduler().pause()
    return {"paused": True}


@router.post("/schedule/resume")
def schedule_resume():
    """Resume the mobile schedule."""
    from core.mobile_scheduler import get_mobile_scheduler
    get_mobile_scheduler().resume()
    return {"paused": False}


@router.post("/schedule/{account_id}/enroll")
def schedule_enroll(account_id: str):
    """Add a single account to the mobile schedule."""
    from core.mobile_scheduler import get_mobile_scheduler
    return get_mobile_scheduler().enroll(account_id)


@router.post("/schedule/{account_id}/unenroll")
def schedule_unenroll(account_id: str):
    """Remove a single account from the mobile schedule."""
    from core.mobile_scheduler import get_mobile_scheduler
    ok = get_mobile_scheduler().unenroll(account_id)
    if not ok:
        raise HTTPException(404, f"{account_id!r} is not in the mobile schedule")
    return {"unenrolled": account_id}


@router.get("/job/{job_id}")
def poll_job(job_id: str):
    """Poll any async job (setup, warmup, warmup_all) by job_id."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    return job


@router.get("/screenshots/{path:path}")
def serve_screenshot(path: str):
    """Serve a saved proof screenshot from the logs/screenshots directory."""
    from fastapi.responses import FileResponse
    shot_path = LOGS_DIR / "screenshots" / path
    if not shot_path.exists():
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(str(shot_path), media_type="image/png")
