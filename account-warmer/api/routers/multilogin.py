"""
Multilogin router — token status, force refresh, profile sync.
"""

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.deps import load_accounts, get_token_status, load_yaml_sync, save_sync_cache, STRATEGIES_FILE

router = APIRouter(prefix="/api/ml", tags=["multilogin"])


@router.get("/status")
def ml_status():
    """Return Multilogin token validity and expiry info."""
    return get_token_status()


@router.post("/refresh")
def ml_refresh():
    """Force a fresh sign-in to Multilogin and update the cached token."""
    try:
        from core.multilogin_auth import get_token
        get_token(force_refresh=True)
        return {**get_token_status(), "refreshed": True}
    except Exception as e:
        raise HTTPException(503, f"Token refresh failed: {e}")


@router.post("/push-proxies")
async def push_proxies_to_ml():
    """
    Push proxy assignments from accounts.yaml to every Multilogin profile.
    Only processes accounts that have both a proxy URL and a Multilogin profile ID.
    Returns per-account results.
    """
    import requests as _req

    try:
        from core.multilogin_auth import auth_headers, CLOUD_API, list_profiles
        from core.profile_manager import _proxy_url_to_ml_dict, _build_profile_update
    except Exception as e:
        raise HTTPException(503, f"Could not load Multilogin modules: {e}")

    # Fetch all ML profiles once so we have their names
    try:
        ml_profiles = list_profiles()
        ml_name_by_id = {p["id"]: p["name"] for p in ml_profiles}
    except Exception as e:
        raise HTTPException(503, f"Could not fetch Multilogin profiles: {e}")

    accounts = await load_accounts()
    results = {"pushed": 0, "skipped": 0, "failed": 0, "details": []}

    for acc in accounts:
        profile_id = acc.get("multilogin_profile_id", "")
        proxy_url  = acc.get("proxy", "")
        acc_id     = acc.get("id", "")

        if not profile_id or not proxy_url:
            results["skipped"] += 1
            continue

        payload = _proxy_url_to_ml_dict(proxy_url)
        if not payload:
            results["failed"] += 1
            results["details"].append({"id": acc_id, "status": "error", "msg": "Could not parse proxy URL"})
            continue

        profile_name = ml_name_by_id.get(profile_id) or acc.get("email", profile_id)

        try:
            resp = _req.post(
                f"{CLOUD_API}/profile/update",
                headers={**auth_headers(), "Content-Type": "application/json"},
                json=_build_profile_update(profile_id, profile_name, payload),
                timeout=15,
            )
            if resp.status_code == 200:
                results["pushed"] += 1
                results["details"].append({"id": acc_id, "status": "ok", "proxy": f"{payload['host']}:{payload['port']}"})
            else:
                results["failed"] += 1
                results["details"].append({"id": acc_id, "status": "error", "msg": f"HTTP {resp.status_code}: {resp.text[:120]}"})
        except Exception as e:
            results["failed"] += 1
            results["details"].append({"id": acc_id, "status": "error", "msg": str(e)[:120]})

    return results


@router.get("/cities")
def list_cities():
    """Return sorted list of city options for the geolocation city picker."""
    from core.geolocation import list_cities as _list_cities
    return _list_cities()


@router.post("/push-geo")
async def push_geo_to_ml():
    """
    Push geolocation coordinates from accounts.yaml to every Multilogin profile.
    Uses geo_city if set, otherwise falls back to the account's location field.
    Sets geolocation_popup to 'allow' so Maps auto-grants location permission.
    Returns per-account results.
    """
    import requests as _req

    try:
        from core.multilogin_auth import auth_headers, CLOUD_API, list_profiles
        from core.geolocation import CITY_CENTRES, coords_for_account, coords_accuracy
    except Exception as e:
        raise HTTPException(503, f"Could not load modules: {e}")

    try:
        ml_profiles = list_profiles()
        ml_name_by_id = {p["id"]: p["name"] for p in ml_profiles}
    except Exception as e:
        raise HTTPException(503, f"Could not fetch Multilogin profiles: {e}")

    accounts = await load_accounts()
    results = {"pushed": 0, "skipped": 0, "failed": 0, "details": []}

    for acc in accounts:
        profile_id = acc.get("multilogin_profile_id", "")
        acc_id     = acc.get("id", "")

        if not profile_id:
            results["skipped"] += 1
            continue

        # Resolve coordinates: explicit geo_city key → CITY_CENTRES lookup,
        # then fall back to coords_for_account (parses location string)
        lat = lng = None
        geo_city = acc.get("geo_city", "").lower()
        if geo_city and geo_city in CITY_CENTRES:
            centre = CITY_CENTRES[geo_city]
            lat, lng = centre[0], centre[1]
        else:
            coords = coords_for_account(acc)
            if coords:
                lat, lng = coords

        if lat is None or lng is None:
            results["skipped"] += 1
            results["details"].append({"id": acc_id, "status": "skipped",
                                        "msg": "No city/location configured"})
            continue

        geo = {"latitude": float(lat), "longitude": float(lng),
               "accuracy": coords_accuracy()}

        # Geo-only update — flags + geolocation, no proxy fields changed
        payload = {
            "profile_id": profile_id,
            "name":       ml_name_by_id.get(profile_id) or acc.get("email", profile_id),
            "parameters": {
                "flags": {
                    "geolocation_masking": "mask",
                    "geolocation_popup":   "allow",
                },
                "geolocation": geo,
            },
        }

        try:
            resp = _req.post(
                f"{CLOUD_API}/profile/update",
                headers={**auth_headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
            if resp.status_code == 200:
                results["pushed"] += 1
                city_label = acc.get("geo_city") or acc.get("location", "—")
                results["details"].append({"id": acc_id, "status": "ok",
                                            "city": city_label,
                                            "lat": round(lat, 4),
                                            "lng": round(lng, 4)})
            else:
                results["failed"] += 1
                results["details"].append({"id": acc_id, "status": "error",
                                            "msg": f"HTTP {resp.status_code}: {resp.text[:120]}"})
        except Exception as e:
            results["failed"] += 1
            results["details"].append({"id": acc_id, "status": "error",
                                        "msg": str(e)[:120]})

    return results


@router.get("/sync")
async def ml_sync():
    """
    Fetch all profiles from the Multilogin cloud API and compare with accounts.yaml.

    Returns:
      matched    — profiles present in both ML and our YAML
      new_in_ml  — ML profiles not yet in accounts.yaml (can be imported)
      orphaned   — accounts.yaml entries whose ML profile no longer exists
    """
    try:
        from core.multilogin_auth import list_profiles
        ml_profiles = list_profiles()
    except Exception as e:
        raise HTTPException(503, f"Could not reach Multilogin: {e}")

    accounts = await load_accounts()
    yaml_by_id = {
        a["multilogin_profile_id"]: a
        for a in accounts
        if a.get("multilogin_profile_id")
    }
    ml_by_id = {p["id"]: p for p in ml_profiles}

    matched  = []
    new_only = []
    orphaned = []

    for ml_id, ml_p in ml_by_id.items():
        if ml_id in yaml_by_id:
            matched.append({
                "ml_profile":   ml_p,
                "yaml_account": yaml_by_id[ml_id],
            })
        else:
            new_only.append(ml_p)

    for yaml_id, yaml_a in yaml_by_id.items():
        if yaml_id not in ml_by_id:
            orphaned.append(yaml_a)

    matched_ids  = [m["ml_profile"]["id"] for m in matched]
    new_ids      = [p["id"] for p in new_only]
    orphaned_ids = [a["id"] for a in orphaned]

    # Persist so /api/accounts can show ml_status without a live ML call
    save_sync_cache(matched_ids, new_ids, orphaned_ids)

    return {
        "total_ml":   len(ml_profiles),
        "total_yaml": len(accounts),
        "matched":    len(matched),
        "new_in_ml":  new_only,
        "orphaned":   orphaned,
        "synced_at":  __import__("datetime").datetime.now().isoformat(),
    }
