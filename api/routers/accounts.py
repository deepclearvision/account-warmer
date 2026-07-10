"""
Accounts router — list, update, delete warming accounts from accounts.yaml.
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Ensure account-warmer root is on path for core/ imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.deps import (
    load_accounts, save_accounts, load_proxies, load_yaml_sync, load_sync_cache,
    STRATEGIES_FILE, LOGS_DIR,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

VALID_STRATEGIES = ["standard", "maps_heavy", "light", "pin_prep"]


class BulkProxyAssignRequest(BaseModel):
    account_ids: list[str]
    proxy_ids:   list[str]


class AccountUpdate(BaseModel):
    strategy:           Optional[str]       = None
    active_hours:       Optional[list[int]] = None
    warmup_start_date:  Optional[str]       = None
    target_businesses:  Optional[list[str]] = None
    tags:               Optional[list[str]] = None
    category:           Optional[str]       = None
    geo_city:           Optional[str]       = None
    home_address:       Optional[str]       = None
    home_lat:           Optional[float]     = None
    home_lng:           Optional[float]     = None
    work_address:       Optional[str]       = None
    work_lat:           Optional[float]     = None
    work_lng:           Optional[float]     = None
    work_geo_area:      Optional[str]       = None


# ── Schedule helpers ──────────────────────────────────────────────────────────

def _week_band(weeks_elapsed: int) -> str:
    if weeks_elapsed < 2:   return "weeks_1_2"
    elif weeks_elapsed < 4: return "weeks_3_4"
    elif weeks_elapsed < 6: return "weeks_5_6"
    else:                   return "weeks_7_plus"


def _compute_weeks_elapsed(warmup_start_date: str) -> int:
    try:
        start = date.fromisoformat(warmup_start_date)
        return max(0, (date.today() - start).days // 7)
    except Exception:
        return 0


def _phase_label(strategy: str, weeks_elapsed: int, strategies: dict) -> str:
    band = _week_band(weeks_elapsed)
    strat = strategies.get(strategy) or strategies.get("standard") or {}
    return strat.get(band, {}).get("label", band.replace("_", " ").title())


def _log_mtime(account_id: str) -> datetime | None:
    log_file = LOGS_DIR / f"{account_id}.log"
    if not log_file.exists():
        return None
    try:
        return datetime.fromtimestamp(log_file.stat().st_mtime)
    except Exception:
        return None


def _ml_status_from_cache(profile_id: str, cache: dict) -> str:
    """Return ml_status for a profile ID based on the last sync cache."""
    if not cache or not profile_id:
        return "unknown"
    if profile_id in cache.get("matched", []):
        return "ok"
    if profile_id in cache.get("new_in_ml", []):
        return "new"
    # Cache exists but profile not in matched or new → missing from ML
    if cache:
        return "missing"
    return "unknown"


def _compute_warmed(account_id: str) -> dict:
    """Return time_warmed_24h, time_warmed_7d, time_warmed_total (all in seconds)."""
    from api.deps import load_session_history
    history = load_session_history(account_id)
    now = datetime.now()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d  = now - timedelta(days=7)
    s24 = s7d = stot = 0
    for session in history:
        dur = session.get("duration_s", 0)
        try:
            start = datetime.fromisoformat(session["start"])
        except Exception:
            continue
        stot += dur
        if start >= cutoff_7d:
            s7d += dur
        if start >= cutoff_24h:
            s24 += dur
    return {"time_24h": s24, "time_7d": s7d, "time_total": stot}


def _get_last_action(account_id: str) -> dict | None:
    """Return the most recent session's summary for display."""
    from api.deps import load_session_history
    history = load_session_history(account_id)
    if not history:
        return None
    s = history[0]  # newest first
    acts = s.get("activities", [])
    last_act = acts[-1] if acts else None
    return {
        "when":     s.get("end") or s.get("start"),
        "activity": last_act,
        "success":  s.get("success", True),
    }


def _enrich(account: dict, strategies: dict, cache: dict | None = None,
            trust_checks: dict | None = None) -> dict:
    weeks_elapsed = _compute_weeks_elapsed(
        account.get("warmup_start_date", str(date.today()))
    )
    mtime     = _log_mtime(account["id"])
    ran_today = mtime is not None and mtime.date() == date.today()
    profile_id = account.get("multilogin_profile_id", "")

    warmed      = _compute_warmed(account["id"])
    last_action = _get_last_action(account["id"])
    trust_entry = (trust_checks or {}).get(account["id"]) or {}

    # Pull unified login status from AccountStore (geelark_accounts.yaml)
    from core.account_store import get_account_store
    store = get_account_store()
    mobile = store.find_mobile_by_email(account.get("email", ""))
    desktop_login_status      = mobile.get("desktop_login_status") if mobile else None
    desktop_login_checked_at  = mobile.get("desktop_login_checked_at") if mobile else None
    mobile_login_verified     = mobile.get("login_verified") if mobile else None
    mobile_login_checked_at   = mobile.get("login_checked_at") if mobile else None

    return {
        **account,
        "strategy":          account.get("strategy", "standard"),
        "week":              weeks_elapsed + 1,
        "phase_label":       _phase_label(
                                 account.get("strategy", "standard"),
                                 weeks_elapsed,
                                 strategies,
                             ),
        "ran_today":         ran_today,
        "last_ran":          mtime.isoformat() if mtime else None,
        "ml_status":         _ml_status_from_cache(profile_id, cache or {}),
        "tags":              account.get("tags", []),
        "target_businesses": account.get("target_businesses", []),
        "active_hours":      account.get("active_hours", [7, 23]),
        "geo_city":          account.get("geo_city"),
        "home_address":      account.get("home_address"),
        "home_lat":          account.get("home_lat"),
        "home_lng":          account.get("home_lng"),
        "work_address":      account.get("work_address"),
        "work_lat":          account.get("work_lat"),
        "work_lng":          account.get("work_lng"),
        "work_geo_area":     account.get("work_geo_area"),
        "time_warmed":       warmed,
        "last_action":       last_action,
        "trust_v2":          trust_entry.get("v2"),
        "trust_v2_at":       trust_entry.get("v2_checked_at"),
        "trust_v3":          trust_entry.get("v3"),
        "trust_v3_at":       trust_entry.get("v3_checked_at"),
        # Unified login status (from geelark_accounts.yaml via AccountStore)
        "desktop_login_status":     desktop_login_status,
        "desktop_login_checked_at": desktop_login_checked_at,
        "mobile_login_verified":    mobile_login_verified,
        "mobile_login_checked_at":  mobile_login_checked_at,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_accounts():
    """Return all accounts enriched with week/phase/status info."""
    from api.deps import load_trust_checks
    accounts   = await load_accounts()
    strategies = load_yaml_sync(STRATEGIES_FILE)
    cache      = load_sync_cache()
    trust      = load_trust_checks()
    return [_enrich(a, strategies, cache, trust) for a in accounts]


_PHASE_BANDS = [
    ("weeks_1_2",   "Weeks 1–2"),
    ("weeks_3_4",   "Weeks 3–4"),
    ("weeks_5_6",   "Weeks 5–6"),
    ("weeks_7_plus","Weeks 7+"),
]

_ACTIVITY_LABELS = {
    "search":           "Search",
    "browse":           "Browse",
    "email_read":       "Email Read",
    "email_send":       "Email Send",
    "maps_browse":      "Maps Browse",
    "calendar_setup":   "Calendar Setup",
    "calendar_browse":  "Calendar Browse",
    "calendar_add":     "Calendar Add",
    "drive_setup":      "Drive Setup",
    "drive_browse":     "Drive Browse",
    "drive_edit":       "Drive Edit",
    "drive_create":     "Drive Create",
    "newsletter_signup":"Newsletter Signup",
}


@router.get("/categories")
async def list_account_categories():
    """Return all distinct non-empty account categories."""
    accounts = await load_accounts()
    cats = sorted({a.get("category", "") for a in accounts if a.get("category")})
    return cats


@router.put("/{account_id}")
async def update_account(account_id: str, body: AccountUpdate):
    """Update enrichment fields (strategy, tags, etc.) for one account."""
    accounts = await load_accounts()
    target   = next((a for a in accounts if a["id"] == account_id), None)
    if not target:
        raise HTTPException(404, f"Account {account_id!r} not found")

    if body.strategy is not None:
        if body.strategy not in VALID_STRATEGIES:
            raise HTTPException(400, f"Invalid strategy: {body.strategy!r}. "
                                     f"Valid: {VALID_STRATEGIES}")
        target["strategy"] = body.strategy
    if body.active_hours is not None:
        target["active_hours"] = body.active_hours
    if body.warmup_start_date is not None:
        target["warmup_start_date"] = body.warmup_start_date
    if body.target_businesses is not None:
        target["target_businesses"] = body.target_businesses
    if body.tags is not None:
        target["tags"] = body.tags
    if body.category is not None:
        target["category"] = body.category
    if body.geo_city is not None:
        if body.geo_city:
            target["geo_city"] = body.geo_city
        else:
            target.pop("geo_city", None)
    if body.home_address is not None:
        if body.home_address:
            target["home_address"] = body.home_address
        else:
            target.pop("home_address", None)
    if body.home_lat is not None:
        target["home_lat"] = body.home_lat
    if body.home_lng is not None:
        target["home_lng"] = body.home_lng
    if body.work_address is not None:
        if body.work_address:
            target["work_address"] = body.work_address
        else:
            target.pop("work_address", None)
    if body.work_lat is not None:
        target["work_lat"] = body.work_lat
    if body.work_lng is not None:
        target["work_lng"] = body.work_lng
    if body.work_geo_area is not None:
        if body.work_geo_area:
            target["work_geo_area"] = body.work_geo_area
        else:
            target.pop("work_geo_area", None)

    await save_accounts(accounts)
    strategies = load_yaml_sync(STRATEGIES_FILE)
    cache      = load_sync_cache()
    from api.deps import load_trust_checks
    trust = load_trust_checks()
    return _enrich(target, strategies, cache, trust)


# ── Deletion helpers (platform cleanup) ───────────────────────────────────────

# GeelarK phone run-status codes → readable text (see geelark_client.check_phone_health)
_PHONE_STATUS_TEXT = {0: "Running", 1: "Starting", 2: "Stopped", 3: "Stopped/Expired"}


def _account_in_session(account_id: str) -> bool:
    """
    True if a warming session is currently running for this account, via either
    the background scheduler or a manual runner job. Used to block deletion of
    an account that is mid-session.
    """
    try:
        from core.scheduler import get_scheduler
        entry = get_scheduler().get_status().get("accounts", {}).get(account_id)
        if entry and entry.get("status") == "running":
            return True
    except Exception:
        pass
    try:
        from api.routers import runner as _runner
        job = _runner._jobs.get(account_id)
        if job and job.get("status") == "running":
            proc = job.get("proc")
            if proc is None or proc.poll() is None:
                return True
    except Exception:
        pass
    return False


def _delete_geelark_phone_cloud(phone_id: str) -> dict:
    """Stop then permanently delete a GeelarK cloud phone. Best-effort stop."""
    import time
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


def _cleanup_schedulers(account_id: str) -> None:
    """Remove an account from both the desktop and mobile schedulers if enrolled."""
    try:
        from core.scheduler import get_scheduler
        get_scheduler().unenroll(account_id)
    except Exception:
        pass
    try:
        from core.mobile_scheduler import get_mobile_scheduler
        get_mobile_scheduler().unenroll(account_id)
    except Exception:
        pass


def _delete_account_platforms(acc: dict, delete_ml_profile: bool,
                              delete_geelark_phone: bool) -> dict:
    """
    Perform all cross-platform cleanup for one desktop account.

    Always removes the paired mobile entry (geelark_accounts.yaml), flow data,
    and mobile session records — one logical account spans both data files.
    Optionally destroys the Multilogin cloud profile and/or GeelarK cloud phone.

    Does NOT remove the desktop entry from accounts.yaml — the caller does that
    (so single- vs bulk-delete control the accounts.yaml write themselves).
    """
    from core.account_store import get_account_store
    store      = get_account_store()
    email      = acc.get("email", "")
    account_id = acc.get("id", "")
    ml_id      = acc.get("multilogin_profile_id", "")

    result: dict = {
        "multilogin":           None,   # delete outcome or {"skipped": reason}
        "geelark_phone":        None,   # delete outcome or {"skipped": reason}
        "mobile_entry_removed": False,
        "gl_id":                None,
        "flow_data_removed":    0,
        "sessions_removed":     0,
    }

    # 1. Multilogin cloud profile
    if delete_ml_profile:
        if ml_id:
            from core.profile_manager import delete_profile
            result["multilogin"] = delete_profile(ml_id)
        else:
            result["multilogin"] = {"skipped": "no multilogin_profile_id"}

    # 2. Locate the paired mobile account by email
    mobile   = store.find_mobile_by_email(email) if email else None
    gl_id    = mobile.get("id") if mobile else None
    phone_id = mobile.get("geelark_phone_id") if mobile else None
    result["gl_id"] = gl_id

    # 3. GeelarK cloud phone
    if delete_geelark_phone:
        if phone_id:
            result["geelark_phone"] = _delete_geelark_phone_cloud(phone_id)
        else:
            result["geelark_phone"] = {"skipped": "no geelark_phone_id"}

    # 4. Remove the mobile yaml entry (unified account cleanup)
    if mobile and email:
        gl_accounts = store.get_mobile_accounts()
        kept = [a for a in gl_accounts if (a.get("email") or "").lower() != email.lower()]
        if len(kept) != len(gl_accounts):
            store.save_mobile_accounts(kept)
            result["mobile_entry_removed"] = True

    # 5. Flow data + session records (keyed by desktop id and/or gl id)
    ids = {account_id, gl_id}
    result["flow_data_removed"] = store.remove_flow_data(ids)
    result["sessions_removed"]  = store.remove_mobile_sessions(ids)

    return result


@router.get("/{account_id}/platform-status")
async def platform_status(account_id: str):
    """
    Report which platforms this account has resources on (Multilogin profile,
    GeelarK phone) and their current state — so the dashboard can show exactly
    what a delete will affect before the user confirms.
    """
    accounts = await load_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"Account {account_id!r} not found")

    email = acc.get("email", "")
    ml_id = acc.get("multilogin_profile_id", "")

    # ── Multilogin — best-effort live existence check ─────────────────────────
    ml: dict = {"configured": bool(ml_id), "profile_id": ml_id, "exists": None}
    if ml_id:
        try:
            from core.multilogin_auth import list_profiles
            ml["exists"] = ml_id in {p.get("id") for p in list_profiles()}
        except Exception as e:
            ml["note"] = f"could not verify: {e}"[:150]

    # ── GeelarK — live phone status ───────────────────────────────────────────
    from core.account_store import get_account_store
    mobile = get_account_store().find_mobile_by_email(email) if email else None
    gk: dict = {
        "linked":      bool(mobile),
        "gl_id":       mobile.get("id") if mobile else None,
        "phone_id":    mobile.get("geelark_phone_id") if mobile else None,
        "provisioned": bool(mobile and mobile.get("geelark_phone_id")),
        "status":      None,
        "status_text": "",
    }
    if gk["phone_id"]:
        try:
            from core.geelark_client import GeelarKClient
            statuses = GeelarKClient().get_phone_status([gk["phone_id"]])
            if statuses:
                s = statuses[0].get("status", -1)
                gk["status"]      = s
                gk["status_text"] = _PHONE_STATUS_TEXT.get(s, f"status {s}")
        except Exception as e:
            gk["note"] = f"could not verify: {e}"[:150]

    return {
        "account_id": account_id,
        "email":      email,
        "in_session": _account_in_session(account_id),
        "multilogin": ml,
        "geelark":    gk,
    }


@router.delete("/{account_id}")
async def delete_account(account_id: str,
                         delete_ml_profile: bool = False,
                         delete_geelark_phone: bool = False):
    """
    Remove an account from accounts.yaml, plus its paired mobile entry, flow
    data, and session records. Optionally also delete the Multilogin cloud
    profile (delete_ml_profile) and/or GeelarK cloud phone (delete_geelark_phone).

    Blocked (409) if the account is mid-session.
    """
    accounts = await load_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"Account {account_id!r} not found")
    if _account_in_session(account_id):
        raise HTTPException(409, f"Account {account_id!r} is in an active session — "
                                 f"stop it before deleting.")

    platform = _delete_account_platforms(acc, delete_ml_profile, delete_geelark_phone)
    _cleanup_schedulers(account_id)
    new_list = [a for a in accounts if a["id"] != account_id]
    await save_accounts(new_list)
    return {"deleted": account_id, "email": acc.get("email", ""), "platform": platform}


@router.post("/bulk-delete")
async def bulk_delete(body: dict):
    """
    Delete multiple accounts by ID list, with the same platform-cleanup options
    as single delete (passed in the body: delete_ml_profile, delete_geelark_phone).
    Accounts that are mid-session are skipped and reported, not deleted.
    """
    ids_to_delete = list(dict.fromkeys(body.get("ids", [])))
    delete_ml = bool(body.get("delete_ml_profile", False))
    delete_gk = bool(body.get("delete_geelark_phone", False))
    if not ids_to_delete:
        raise HTTPException(400, "No account IDs provided")

    accounts = await load_accounts()
    by_id    = {a["id"]: a for a in accounts}
    deleted:  list[str]  = []
    skipped:  list[dict] = []
    platform: dict       = {}

    for aid in ids_to_delete:
        acc = by_id.get(aid)
        if not acc:
            skipped.append({"id": aid, "reason": "not found"})
            continue
        if _account_in_session(aid):
            skipped.append({"id": aid, "reason": "active session"})
            continue
        platform[aid] = _delete_account_platforms(acc, delete_ml, delete_gk)
        _cleanup_schedulers(aid)
        deleted.append(aid)

    if deleted:
        deleted_set = set(deleted)
        new_list = [a for a in accounts if a["id"] not in deleted_set]
        await save_accounts(new_list)

    return {"deleted": len(deleted), "ids": deleted,
            "skipped": skipped, "platform": platform}


@router.post("/bulk-update")
async def bulk_update(body: dict):
    """Update the same field on multiple accounts at once."""
    ids     = set(body.get("ids", []))
    updates = body.get("updates", {})
    allowed = {"strategy", "active_hours", "warmup_start_date", "target_businesses", "tags", "category", "geo_city"}
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not ids or not updates:
        raise HTTPException(400, "Provide ids and at least one allowed field to update")
    if "strategy" in updates and updates["strategy"] not in VALID_STRATEGIES:
        raise HTTPException(400, f"Invalid strategy: {updates['strategy']!r}")

    accounts = await load_accounts()
    count = 0
    for a in accounts:
        if a["id"] in ids:
            a.update(updates)
            count += 1
    await save_accounts(accounts)
    return {"updated": count}


@router.post("/assign-proxies")
async def bulk_assign_proxies(body: BulkProxyAssignRequest):
    """
    Fairly distribute selected proxies across selected accounts.

    Rules:
    - Sort selected proxies by current usage count ascending (unused first).
    - Round-robin assign: account[i] gets sorted_proxy[i % len(proxies)].
    - Each account gets exactly one proxy URL written to accounts.yaml.
    """
    accounts = await load_accounts()
    proxies  = await load_proxies()

    acc_map = {a["id"]: a for a in accounts}
    prx_map = {p["id"]: p for p in proxies}

    sel_accounts = [acc_map[aid] for aid in body.account_ids if aid in acc_map]
    sel_proxies  = [prx_map[pid] for pid in body.proxy_ids  if pid in prx_map]

    if not sel_accounts:
        raise HTTPException(400, "No valid account IDs provided")
    if not sel_proxies:
        raise HTTPException(400, "No valid proxy IDs provided")

    # Count how many accounts currently use each selected proxy
    usage: dict[str, int] = {p["id"]: 0 for p in sel_proxies}
    url_to_prx_id = {p["url"]: p["id"] for p in sel_proxies}
    for a in accounts:
        url = a.get("proxy", "")
        if url and url in url_to_prx_id:
            usage[url_to_prx_id[url]] += 1

    # Sort: unassigned (0) first, then by ascending usage
    sorted_proxies = sorted(sel_proxies, key=lambda p: usage[p["id"]])

    # Round-robin assign
    for i, account in enumerate(sel_accounts):
        account["proxy"] = sorted_proxies[i % len(sorted_proxies)]["url"]

    await save_accounts(accounts)
    return {
        "assigned":     len(sel_accounts),
        "proxies_used": len(sorted_proxies),
    }


class CsvImportRequest(BaseModel):
    csv_text: str          # raw CSV content pasted or uploaded
    provision_phones: bool = True  # auto-create GeelarK phones
    create_ml_profiles: bool = True  # auto-create Multilogin browser profiles


@router.post("/import-csv")
async def import_from_csv(body: CsvImportRequest):
    """
    Import accounts from CSV text.

    Required columns : email
    Optional columns : password, totp_secret, multilogin_profile_id, proxy,
                       location, timezone, language, warmup_start_date,
                       active_hours_start, active_hours_end,
                       geo_city, neighbourhood, category, strategy,
                       target_businesses, geo_area, home_address, home_lat, home_lng

    Creates entries in both accounts.yaml (for desktop warmer) and
    geelark_accounts.yaml (for mobile warmer).  Optionally provisions
    a GeelarK cloud phone for each new account (provision_phones=True).
    """
    import csv
    import io
    import os
    from core.account_store import get_account_store

    store = get_account_store()

    # ── Parse CSV ─────────────────────────────────────────────────────────────
    reader   = csv.DictReader(io.StringIO(body.csv_text.strip()))
    if reader.fieldnames is None:
        raise HTTPException(400, "CSV appears to be empty or has no header row")
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

    rows = []
    errors = []
    for i, row in enumerate(reader, start=2):
        row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
        if not any(row.values()):
            continue
        email = row.get("email", "").strip()
        if not email:
            errors.append(f"Row {i}: missing email — skipped")
            continue
        rows.append((i, row))

    if not rows and not errors:
        return {"imported": 0, "skipped": 0, "errors": [], "phones_created": [], "phones_failed": []}

    # ── Load existing data ─────────────────────────────────────────────────────
    accounts = await load_accounts()
    existing_emails = {a.get("email", "").lower() for a in accounts}
    existing_ids = {a["id"] for a in accounts}
    n_acc = max(
        (int(a["id"].split("_")[-1]) for a in accounts if a["id"].startswith("acc_")),
        default=0,
    )

    gl_accounts = store.get_mobile_accounts()
    gl_emails   = {a.get("email", "").lower() for a in gl_accounts}

    added          = []
    updated        = []
    phones_created = []
    phones_failed  = []
    ml_created     = []
    ml_failed      = []

    # Multilogin folder to create profiles in — fetched once, reused for all rows.
    ml_folder_id = None
    if body.create_ml_profiles:
        from core.profile_manager import get_default_folder_id
        ml_folder_id = get_default_folder_id()

    # Fields that can be updated on existing accounts via CSV re-import
    UPDATABLE = {
        "category", "location", "geo_city", "geo_area", "neighbourhood",
        "home_address", "home_lat", "home_lng",
        "work_address", "work_lat", "work_lng", "work_geo_area",
        "strategy", "warmup_start_date", "target_businesses",
        "password", "totp_secret", "proxy",
    }

    VALID_STRATEGIES = {"standard", "maps_heavy", "light", "pin_prep"}

    for row_num, row in rows:
        email = row["email"]

        # ── UPDATE existing account ───────────────────────────────────────────
        if email.lower() in existing_emails:
            acc = next((a for a in accounts if a.get("email", "").lower() == email.lower()), None)
            if acc:
                changed = False
                for field in UPDATABLE:
                    val = row.get(field, "").strip()
                    if not val:
                        continue
                    if field == "target_businesses":
                        val = [v.strip() for v in val.replace(",", ";").split(";") if v.strip()]
                    elif field in ("home_lat", "home_lng"):
                        try:
                            val = float(val)
                        except ValueError:
                            continue
                    acc[field] = val
                    changed = True
                if changed:
                    updated.append(acc["id"])
            continue

        # ── CREATE new account ────────────────────────────────────────────────
        # Auto-generate ID
        account_id = row.get("id", "").strip()
        if not account_id:
            n_acc += 1
            account_id = f"acc_{n_acc:03d}"
            while account_id in existing_ids:
                n_acc += 1
                account_id = f"acc_{n_acc:03d}"
        existing_ids.add(account_id)

        location = row.get("location", "London, UK") or "London, UK"
        # Auto-detect timezone from location
        tz_map = {
            "london": "Europe/London", "manchester": "Europe/London",
            "new york": "America/New_York", "los angeles": "America/Los_Angeles",
            "chicago": "America/Chicago", "sydney": "Australia/Sydney",
            "toronto": "America/Toronto", "paris": "Europe/Paris",
            "berlin": "Europe/Berlin", "dubai": "Asia/Dubai",
        }
        tz = row.get("timezone", "").strip()
        if not tz:
            loc_lower = location.lower()
            for key, val in tz_map.items():
                if key in loc_lower:
                    tz = val
                    break
            if not tz:
                tz = "Europe/London"

        acc_entry = {
            "id":                    account_id,
            "email":                 email,
            "multilogin_profile_id": row.get("multilogin_profile_id", ""),
            "multilogin_folder_id":  "",
            "proxy":                 row.get("proxy", ""),
            "timezone":              tz,
            "location":              location,
            "language":              row.get("language", "en-GB") or "en-GB",
            "warmup_start_date":     row.get("warmup_start_date", str(date.today())) or str(date.today()),
            "active_hours":          [
                int(row.get("active_hours_start", "8") or "8"),
                int(row.get("active_hours_end", "22") or "22"),
            ],
            "strategy":              row.get("strategy", "standard") or "standard",
        }
        # Optional shared fields
        for field in ("password", "totp_secret", "category", "geo_area", "geo_city",
                       "neighbourhood", "home_address", "work_address"):
            if row.get(field):
                acc_entry[field] = row[field]
        for field in ("home_lat", "home_lng", "work_lat", "work_lng"):
            if row.get(field):
                try:
                    acc_entry[field] = float(row[field])
                except ValueError:
                    pass
        # target_businesses
        biz_raw = row.get("target_businesses", "").strip()
        if biz_raw:
            acc_entry["target_businesses"] = [
                b.strip() for b in biz_raw.replace(",", ";").split(";") if b.strip()
            ]

        accounts.append(acc_entry)
        existing_emails.add(email.lower())
        added.append(account_id)

        # ── Create a Multilogin browser profile (best-effort) ─────────────────
        # Only when requested, the account has no profile id already, and we
        # resolved a target folder. The proxy (if any) is applied at launch.
        if body.create_ml_profiles and not acc_entry["multilogin_profile_id"] and ml_folder_id:
            from core.profile_manager import create_profile
            res = create_profile(email, ml_folder_id, proxy_url=acc_entry.get("proxy") or None)
            if res.get("success"):
                acc_entry["multilogin_profile_id"] = res["profile_id"]
                acc_entry["multilogin_folder_id"]  = res["folder_id"]
                ml_created.append({"acc_id": account_id, "profile_id": res["profile_id"]})
            else:
                ml_failed.append({"acc_id": account_id, "error": res.get("error", "unknown")})

        # ── geelark_accounts.yaml entry (mobile warmer) ───────────────────────
        # Same ID as desktop — one account, two platform entries
        if email.lower() not in gl_emails:
            geo_city = row.get("geo_city", "").strip()
            if not geo_city:
                loc_lower = location.lower()
                for key in tz_map:
                    if key in loc_lower:
                        geo_city = key
                        break
            if not geo_city:
                geo_city = "london"

            gl_entry: dict = {
                "id":                     account_id,
                "email":                  email,
                "password":               row.get("password", ""),
                "totp_secret":            row.get("totp_secret", "") or row.get("totp", ""),
                "geelark_phone_id":       None,
                "mobile_warming_enabled": True,
                "mobile_setup_done":      False,
                "geo_city":               geo_city,
                "strategy":               row.get("strategy", "maps_heavy") or "maps_heavy",
                "login_verified":         None,
            }
            for field in ("geo_area", "neighbourhood", "category", "home_address",
                           "work_address", "home_lat", "home_lng", "work_lat", "work_lng"):
                if row.get(field):
                    gl_entry[field] = row[field]
            if row.get("proxy"):
                gl_entry["proxy"] = row["proxy"]
            biz_raw = row.get("target_businesses", "").strip()
            if biz_raw:
                gl_entry["target_businesses"] = [
                    b.strip() for b in biz_raw.replace(",", ";").split(";") if b.strip()
                ]

            # Auto-provision phone if requested
            if body.provision_phones:
                mobile_proxy = os.environ.get("GEELARK_PROXY", row.get("proxy", "") or "")
                try:
                    from core.geelark_client import GeelarKClient
                    phone_id, equip = GeelarKClient().create_phone(
                        profile_name=email,
                        proxy=mobile_proxy or None,
                    )
                    gl_entry["geelark_phone_id"] = phone_id
                    gl_entry["device_brand"]     = equip.get("deviceBrand", "")
                    gl_entry["device_model"]     = equip.get("deviceModel", "")
                    gl_entry["os_version"]       = equip.get("osVersion", "")
                    phones_created.append({"acc_id": acc_entry["id"], "gl_id": gl_entry["id"], "phone_id": phone_id})
                except Exception as exc:
                    phones_failed.append({"acc_id": acc_entry["id"], "gl_id": gl_entry["id"], "error": str(exc)})

            gl_accounts.append(gl_entry)
            gl_emails.add(email.lower())

    # ── Save ──────────────────────────────────────────────────────────────────
    await save_accounts(accounts)
    store.save_mobile_accounts(gl_accounts)

    return {
        "imported":       len(added),
        "updated":        len(updated),
        "account_ids":    added,
        "updated_ids":    updated,
        "errors":         errors,
        "phones_created": phones_created,
        "phones_failed":  phones_failed,
        "ml_created":     ml_created,
        "ml_failed":      ml_failed,
    }


@router.get("/csv-template")
async def csv_template():
    """Return a sample CSV template matching the full import format."""
    from fastapi.responses import PlainTextResponse
    template = (
        "id,email,password,totp_secret,multilogin_profile_id,proxy,location,timezone,language,"
        "warmup_start_date,active_hours_start,active_hours_end,"
        "geo_city,neighbourhood,category,strategy,target_businesses\n"
        "acc_001,john.smith1985@gmail.com,MyPassword123!,BASE32TOTPSECRETHERE,,"
        "user:pass@host:port,London UK,Europe/London,en-GB,"
        "2026-07-03,8,22,london,greenwich,batch_1,standard,biz_001\n"
        "acc_002,jane.doe1990@gmail.com,AnotherPass456!,ANOTHERBASE32SECRET,,"
        "user:pass@host:port,London UK,Europe/London,en-GB,"
        "2026-07-03,9,21,london,westminster,batch_1,maps_heavy,biz_001\n"
    )
    return PlainTextResponse(template, media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="accounts_template.csv"'})


@router.post("/generate-home-addresses")
async def generate_home_addresses(body: dict):
    """
    Generate real UK addresses for selected accounts using their geo_area.
    Generates both a home address (from geo_area) and a work address (from a
    different randomly-chosen area). Calls Nominatim (1 req/sec rate limit).
    Stores home_address/lat/lng and work_address/lat/lng/work_geo_area.
    """
    import time
    import random
    import requests
    from core.account_store import get_account_store

    store = get_account_store()

    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "No account IDs provided")

    # Import AREAS / helpers from set_phone_area (already on sys.path via account-warmer root)
    import importlib
    spa = importlib.import_module("set_phone_area")
    AREAS = spa.AREAS
    area_coords = spa.area_coords

    accounts = await load_accounts()

    gl_accounts = store.get_mobile_accounts()
    gl_by_email = {a.get("email", "").lower(): a for a in gl_accounts}

    id_set   = set(ids)
    results  = []
    errors   = []

    for acc in accounts:
        if acc["id"] not in id_set:
            continue

        email  = acc.get("email", "").lower()
        gl_acc = gl_by_email.get(email)

        # geo_area comes from geelark_accounts.yaml entry (or fall back to accounts.yaml)
        raw_geo_area = (gl_acc or {}).get("geo_area") or acc.get("geo_area")
        geo_area = spa.resolve_area_key(raw_geo_area) if raw_geo_area else None
        if not geo_area:
            errors.append({"id": acc["id"], "error": f"No geo_area set or unrecognised: {raw_geo_area!r}"})
            continue

        # Pick work area — different from home area
        raw_work_area = acc.get("work_geo_area") or (gl_acc or {}).get("work_geo_area")
        existing_work_area = spa.resolve_area_key(raw_work_area) if raw_work_area else None
        if existing_work_area and existing_work_area != geo_area:
            work_area = existing_work_area
        else:
            other_areas = [k for k in AREAS if k != geo_area]
            work_area = random.choice(other_areas)

        def _nominatim_address(lat: float, lon: float) -> tuple[str, float, float]:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json"},
                headers={"User-Agent": "AccountWarmer/1.0 (+https://localhost)"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            addr = data.get("address", {})
            parts = []
            house = addr.get("house_number", "")
            road  = addr.get("road", "")
            if house and road:
                parts.append(f"{house} {road}")
            elif road:
                parts.append(road)
            for field in ("suburb", "neighbourhood", "quarter"):
                if addr.get(field):
                    parts.append(addr[field])
                    break
            city = addr.get("city") or addr.get("town") or addr.get("county") or "London"
            parts.append(city)
            if addr.get("postcode"):
                parts.append(addr["postcode"])
            address = ", ".join(p for p in parts if p)
            return address, round(lat, 6), round(lon, 6)

        try:
            # Home address
            h_raw_lat, h_raw_lon = area_coords(geo_area, jitter=True)
            home_address, h_lat, h_lng = _nominatim_address(h_raw_lat, h_raw_lon)
            acc["home_address"] = home_address
            acc["home_lat"]     = h_lat
            acc["home_lng"]     = h_lng
            if gl_acc:
                gl_acc["home_address"] = home_address
                gl_acc["home_lat"]     = h_lat
                gl_acc["home_lng"]     = h_lng
            time.sleep(1.1)

            # Work address
            w_raw_lat, w_raw_lon = area_coords(work_area, jitter=True)
            work_address, w_lat, w_lng = _nominatim_address(w_raw_lat, w_raw_lon)
            acc["work_address"]  = work_address
            acc["work_lat"]      = w_lat
            acc["work_lng"]      = w_lng
            acc["work_geo_area"] = work_area
            if gl_acc:
                gl_acc["work_address"]  = work_address
                gl_acc["work_lat"]      = w_lat
                gl_acc["work_lng"]      = w_lng
                gl_acc["work_geo_area"] = work_area

            results.append({
                "id": acc["id"],
                "home_address": home_address, "home_lat": h_lat, "home_lng": h_lng,
                "work_address": work_address, "work_lat": w_lat, "work_lng": w_lng,
                "work_geo_area": work_area,
            })
        except Exception as exc:
            errors.append({"id": acc["id"], "error": str(exc)})

        time.sleep(1.1)  # Nominatim: max 1 req/sec

    await save_accounts(accounts)

    if results:
        store.save_mobile_accounts(gl_accounts)

    return {"generated": len(results), "errors": errors, "results": results}


@router.post("/import-ml")
async def import_from_ml(profiles: list[dict]):
    """
    Write new Multilogin profiles into accounts.yaml.
    Expects a list of ML profile dicts (from /api/ml/sync new_in_ml).
    Also creates a GeelarK cloud phone for each new account (best-effort).
    """
    import os
    from core.account_store import get_account_store

    store      = get_account_store()
    accounts   = await load_accounts()
    existing   = {a["multilogin_profile_id"] for a in accounts}
    added      = []
    n          = max((int(a["id"].split("_")[-1]) for a in accounts
                      if a["id"].startswith("acc_")), default=0)

    # Load geelark accounts via AccountStore to avoid duplicate phone creation
    gl_accounts = store.get_mobile_accounts()
    gl_emails   = {a.get("email", "").lower() for a in gl_accounts}
    gl_max_n    = max(
        (int(a["id"].split("_")[-1]) for a in gl_accounts if a["id"].startswith("gl_")),
        default=0,
    )

    phones_created = []
    phones_failed  = []

    for p in profiles:
        ml_id = p.get("id", "")
        if not ml_id or ml_id in existing:
            continue
        n += 1
        email       = p.get("name", "")
        folder_name = p.get("folder_name", "").strip()   # ML folder → use as PC category
        pc_id       = os.environ.get("PC_ID", "").strip()
        # Category priority: ML folder name > PC_ID env var > blank
        category = folder_name or pc_id or ""

        new_acc = {
            "id":                    f"acc_{n:03d}",
            "email":                 email,
            "multilogin_profile_id": ml_id,
            "multilogin_folder_id":  p.get("folder_id", ""),
            "proxy":                 "",
            "timezone":              "Europe/London",
            "location":              "UK",
            "language":              "en-GB",
            "warmup_start_date":     str(date.today()),
            "active_hours":          [8, 22],
            "strategy":              "standard",
        }
        if category:
            new_acc["category"] = category
        accounts.append(new_acc)
        added.append(new_acc["id"])
        existing.add(ml_id)

        # ── Auto-provision GeelarK phone ───────────────────────────────────────
        if email.lower() not in gl_emails:
            gl_max_n += 1
            gl_id = f"gl_{gl_max_n:03d}"
            mobile_proxy = os.environ.get("GEELARK_PROXY", "")
            try:
                from core.geelark_client import GeelarKClient
                client   = GeelarKClient()
                phone_id, equip = client.create_phone(
                    profile_name=email or gl_id,
                    proxy=mobile_proxy or None,
                )
                new_gl_acc = {
                    "id":                     gl_id,
                    "email":                  email,
                    "password":               "",
                    "totp_secret":            "",
                    "geelark_phone_id":       phone_id,
                    "mobile_warming_enabled": True,
                    "mobile_setup_done":      False,
                    "geo_city":               "london",
                    "strategy":               "maps_heavy",
                    "login_verified":         None,
                    "device_brand":           equip.get("deviceBrand", ""),
                    "device_model":           equip.get("deviceModel", ""),
                    "os_version":             equip.get("osVersion", ""),
                }
                if category:
                    new_gl_acc["category"] = category
                gl_accounts.append(new_gl_acc)
                gl_emails.add(email.lower())
                phones_created.append({"account_id": new_acc["id"], "gl_id": gl_id, "phone_id": phone_id})
            except Exception as exc:
                phones_failed.append({"account_id": new_acc["id"], "error": str(exc)})

    await save_accounts(accounts)

    if phones_created or phones_failed:
        store.save_mobile_accounts(gl_accounts)

    return {
        "imported":       len(added),
        "account_ids":    added,
        "phones_created": phones_created,
        "phones_failed":  phones_failed,
    }


@router.post("/{account_id}/run-ml-setup")
async def run_ml_setup(account_id: str):
    """
    Trigger the one-time Multilogin account setup for this account.
    Spawns ml_setup_run.py as a background subprocess.
    Returns a job id — poll GET /api/runner/{account_id}/status for progress.
    """
    accounts = await load_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(404, f"Account {account_id!r} not found")
    if not acc.get("multilogin_profile_id"):
        raise HTTPException(400, "Account has no multilogin_profile_id — cannot start ML setup")

    import subprocess
    script = Path(__file__).parent.parent.parent / "ml_setup_run.py"
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--account", account_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(script.parent),
        )
        return {"account_id": account_id, "pid": proc.pid, "status": "started"}
    except Exception as e:
        raise HTTPException(500, f"Failed to start ml_setup_run.py: {e}")


@router.post("/run-desktop-login")
async def run_desktop_login(body: dict):
    """
    Run Google desktop login for selected accounts via google_login_desktop.py.
    Spawns the script as a background subprocess; logs to WarmingData/logs/desktop_login.log.
    """
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "No account IDs provided")

    accounts = await load_accounts()
    id_set = set(ids)
    pending = [a for a in accounts if a["id"] in id_set and a.get("multilogin_profile_id")]
    if not pending:
        raise HTTPException(400, "No matching accounts with a multilogin_profile_id found")

    import subprocess
    from core.paths import LOGS_DIR
    script = Path(__file__).parent.parent.parent / "google_login_desktop.py"
    if not script.exists():
        raise HTTPException(500, "google_login_desktop.py not found")
    acc_ids = [a["id"] for a in pending]
    log_file = LOGS_DIR / "desktop_login.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--accounts"] + acc_ids,
            stdout=lf,
            stderr=lf,
            cwd=str(script.parent),
        )
    return {"started": len(pending), "pid": proc.pid, "accounts": acc_ids, "log": str(log_file)}


@router.post("/run-maps-setup")
async def run_maps_setup_bulk(body: dict):
    """
    Set Home/Work labeled places in Google Maps for selected accounts.
    Spawns one ml_setup_run.py --maps-only process per account (sequentially).
    """
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "No account IDs provided")

    accounts = await load_accounts()
    id_set = set(ids)
    pending = [a for a in accounts if a["id"] in id_set and a.get("multilogin_profile_id")]
    if not pending:
        raise HTTPException(400, "No matching accounts with a multilogin_profile_id found")

    import subprocess
    from core.paths import LOGS_DIR
    script = Path(__file__).parent.parent.parent / "ml_setup_run.py"
    acc_ids = [a["id"] for a in pending]
    log_file = LOGS_DIR / "maps_setup.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--accounts"] + acc_ids + ["--maps-only", "--concurrency", "1"],
            stdout=lf,
            stderr=lf,
            cwd=str(script.parent),
        )
    return {"started": len(pending), "pid": proc.pid, "accounts": acc_ids, "log": str(log_file)}


@router.post("/run-security-clear")
async def run_security_clear(body: dict):
    """
    Dismiss Google security alerts/prompts for selected accounts via Multilogin desktop browser.
    Navigates to myaccount.google.com/security and confirms any pending sign-in events.
    """
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "No account IDs provided")

    accounts = await load_accounts()
    id_set = set(ids)
    pending = [a for a in accounts if a["id"] in id_set and a.get("multilogin_profile_id")]
    if not pending:
        raise HTTPException(400, "No matching accounts with a multilogin_profile_id found")

    import subprocess
    from core.paths import LOGS_DIR
    script = Path(__file__).parent.parent.parent / "ml_setup_run.py"
    acc_ids = [a["id"] for a in pending]
    log_file = LOGS_DIR / "security_clear.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--accounts"] + acc_ids + ["--security-only", "--concurrency", "1"],
            stdout=lf,
            stderr=lf,
            cwd=str(script.parent),
        )
    return {"started": len(pending), "pid": proc.pid, "accounts": acc_ids, "log": str(log_file)}


@router.get("/{account_id}/business-interactions")
async def get_business_interactions(account_id: str):
    """
    Return all logged business signal interactions (with proof screenshot paths)
    for a given account.  Reads *_biz_*_interactions.json files from STATE_DIR.
    """
    from core.paths import STATE_DIR as _state_dir
    import glob as _glob

    pattern  = str(_state_dir / f"{account_id}_biz_*_interactions.json")
    all_records: list[dict] = []
    for fpath in _glob.glob(pattern):
        try:
            with open(fpath, encoding="utf-8") as f:
                records = json.load(f)
            all_records.extend(records)
        except Exception:
            pass

    # Sort by timestamp descending (newest first)
    all_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return {"account_id": account_id, "interactions": all_records}


@router.get("/{account_id}/activity-log")
async def get_activity_log(account_id: str, limit: int = 200):
    """
    Return the unified activity log for one account — all desktop and mobile
    activities with search terms, business names, and screenshot links.

    Reads WarmingData/logs/{account_id}_activity_log.json (written by
    core/activity_log.py).  Entries are sorted newest-first.
    """
    log_file = LOGS_DIR / f"{account_id}_activity_log.json"
    records: list[dict] = []
    if log_file.exists():
        try:
            with open(log_file, encoding="utf-8") as f:
                records = json.load(f)
        except Exception:
            records = []

    # Sort newest first, honour limit
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return {"account_id": account_id, "events": records[:limit]}


@router.get("/{account_id}/log")
async def get_account_log(account_id: str, lines: int = 100):
    """Return the last N lines of this account's log file."""
    log_file = LOGS_DIR / f"{account_id}.log"
    if not log_file.exists():
        return {"lines": [], "exists": False}
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return {
            "lines":       [ln.rstrip() for ln in all_lines[-lines:]],
            "exists":      True,
            "total_lines": len(all_lines),
        }
    except Exception as e:
        raise HTTPException(500, str(e))
