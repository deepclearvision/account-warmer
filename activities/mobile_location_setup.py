"""
mobile_location_setup.py — Reusable Mobile Location + Permission Helper

Provides a single entry point for every mobile app session to:
  1. Enable location services on the phone
  2. Set GPS to the account's preferred location (home/work → geo_area → city fallback)
  3. Dismiss permission dialogs that Google apps show on first launch
  4. Optionally set GPS near a specific business for targeted activity

Usage:
    from activities.mobile_location_setup import (
        ensure_location_and_permissions,
        set_gps_near_business,
        choose_business_location,
    )

    # Before a Maps or YouTube session:
    ensure_location_and_permissions(phone_id, account, acc_id)

    # For a Maps session targeting a specific business:
    biz = choose_business_location(account)
    if biz:
        set_gps_near_business(phone_id, account, biz, acc_id)
"""

import logging
import math
import random

import yaml

log = logging.getLogger("mobile_location_setup")

# ── Reuse existing ADB helpers ──────────────────────────────────────────────────
from activities.google_login_mobile import (
    _shell,
    _find_and_tap,
    _get_screen_size,
    _scale,
    _CITY_CONFIG,
)

from set_phone_area import set_phone_gps, AREAS, area_coords
from core.paths import DATA_DIR

# ── Reference screen size for scaling dialog-tap coordinates ────────────────────
_REF_W = 720
_REF_H = 1440

# Permission dialog dismissal labels — in priority order
_PERMISSION_LABELS = [
    "Allow",
    "Allow all the time",
    "While using the app",
    "Only this time",
    "OK",
    "Got it",
    "Turn on",
    "Enable",
]

# Common dialog button positions on 720×1440, scaled to actual resolution at runtime
_DIALOG_TAP_POSITIONS = [
    (360, 1020),   # centre, ~70% down (typical "Allow" button)
    (540, 1250),   # bottom-right (typical "OK" / "Got it" button)
]


# ═══════════════════════════════════════════════════════════════════════════════════
# Primary helper: ensure GPS is set and permission dialogs are dismissed
# ═══════════════════════════════════════════════════════════════════════════════════

def ensure_location_and_permissions(phone_id: str, account: dict, acc_id: str) -> bool:
    """
    Enable location services, set GPS to the account's preferred location,
    and dismiss any permission dialogs that Google apps show on first launch.

    GPS priority order (same as mobile_warmup._refresh_gps):
      1. home_lat / home_lng from account (65% home, 35% work when both available)
      2. geo_area looked up in set_phone_area.AREAS with area-radius jitter
      3. geo_city fallback via _CITY_CONFIG from google_login_mobile

    A small jitter (±0.0005 lat, ±0.0007 lon) is always applied so coordinates
    vary each session.

    Returns True if GPS was set successfully, False otherwise.
    """
    # ── Step 1: Enable location services ─────────────────────────────────────
    _shell(phone_id, "settings put secure location_mode 3")
    log.debug("[%s] Location mode set to high accuracy (3)", acc_id)

    # ── Step 2: Determine GPS coordinates ────────────────────────────────────
    gps_ok = _set_account_gps(phone_id, account, acc_id)

    # ── Step 3: Dismiss any permission dialogs ───────────────────────────────
    _dismiss_permission_dialogs(phone_id, acc_id)

    return gps_ok


# ═══════════════════════════════════════════════════════════════════════════════════
# GPS near a specific business
# ═══════════════════════════════════════════════════════════════════════════════════

def set_gps_near_business(
    phone_id: str,
    account: dict,
    business: dict,
    acc_id: str,
    jitter_meters: float = 200,
) -> bool:
    """
    Set the phone GPS to a random point within `jitter_meters` of a business.

    Uses a simple meters-to-degrees conversion:
        1 deg latitude  ≈ 111,000 m
        1 deg longitude ≈ 111,000 × cos(lat) m

    Args:
        phone_id:       GeelarK phone ID.
        account:        Account dict (unused; reserved for future use).
        business:       Dict with "lat" and "lng" keys, plus optional "name".
        acc_id:         Account ID for logging.
        jitter_meters:  Maximum distance in metres from the business centre.

    Returns:
        True if GPS was set successfully.
    """
    biz_lat = business.get("lat")
    biz_lng = business.get("lng")
    biz_name = business.get("name", "unknown")

    if not biz_lat or not biz_lng:
        log.warning("[%s] Business %r has no lat/lng — cannot set GPS", acc_id, biz_name)
        return False

    # Convert jitter distance to degrees
    lat_jitter_deg = jitter_meters / 111_000.0
    lon_jitter_deg = jitter_meters / (111_000.0 * math.cos(math.radians(biz_lat)))

    # Pick a random offset within the jitter radius (uniform in a square;
    # circular would use random angle + random radius, but the difference is
    # negligible for 200 m).
    lat = biz_lat + random.uniform(-lat_jitter_deg, lat_jitter_deg)
    lon = biz_lng + random.uniform(-lon_jitter_deg, lon_jitter_deg)

    lat = round(lat, 6)
    lon = round(lon, 6)

    log.info(
        "[%s] GPS near business %r: (%.5f, %.5f) — jittered from (%.5f, %.5f) by ≤%dm",
        acc_id, biz_name, lat, lon, biz_lat, biz_lng, jitter_meters,
    )

    return set_phone_gps(phone_id, lat, lon)


# ═══════════════════════════════════════════════════════════════════════════════════
# Business location chooser
# ═══════════════════════════════════════════════════════════════════════════════════

def choose_business_location(account: dict) -> dict | None:
    """
    Pick a random business from the account's ``target_businesses`` list.

    Loads DATA_DIR / "businesses.yaml" and resolves the account's target_businesses
    IDs to full business dicts.  Returns the chosen dict or None if no matching
    business with lat/lng is found.
    """
    biz_file = DATA_DIR / "businesses.yaml"
    if not biz_file.exists():
        log.debug("businesses.yaml not found at %s", biz_file)
        return None

    target_ids = account.get("target_businesses") or []
    if not target_ids:
        return None

    try:
        data = yaml.safe_load(biz_file.read_text(encoding="utf-8")) or {}
        all_businesses = data.get("businesses", [])
    except Exception as e:
        log.warning("Failed to load businesses.yaml: %s", e)
        return None

    # Build lookup by ID
    biz_map = {b["id"]: b for b in all_businesses if b.get("id")}

    # Filter to routable candidates (must have lat + lng)
    candidates = [
        biz_map[bid]
        for bid in target_ids
        if bid in biz_map and biz_map[bid].get("lat") and biz_map[bid].get("lng")
    ]

    if not candidates:
        log.debug("No routable businesses found for account's target_businesses")
        return None

    chosen = random.choice(candidates)
    log.debug(
        "Chose business %r (%.5f, %.5f) from %d candidate(s)",
        chosen.get("name", chosen.get("id")),
        chosen.get("lat"),
        chosen.get("lng"),
        len(candidates),
    )
    return chosen


# ═══════════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════════

def _set_account_gps(phone_id: str, account: dict, acc_id: str) -> bool:
    """
    Set GPS to the account's preferred location using the standard priority order.

    Priority:
      1. home_lat / home_lng  (65%)  or  work_lat / work_lng  (35%)
      2. geo_area looked up in AREAS with area-radius jitter
      3. geo_city fallback via _CITY_CONFIG

    A small jitter (±0.0005 lat, ±0.0007 lon) is applied at every tier so
    exact coordinates are never repeated.
    """
    # ── Tier 1: home or work coordinates (65% / 35%) ─────────────────────────
    home_lat = account.get("home_lat")
    home_lng = account.get("home_lng")
    work_lat = account.get("work_lat")
    work_lng = account.get("work_lng")

    use_work = work_lat and work_lng and random.random() < 0.35
    if use_work:
        base_lat, base_lng, loc_label = work_lat, work_lng, "work"
    elif home_lat and home_lng:
        base_lat, base_lng, loc_label = home_lat, home_lng, "home"
    else:
        base_lat = base_lng = None

    if base_lat and base_lng:
        lat = base_lat + random.uniform(-0.0005, 0.0005)
        lon = base_lng + random.uniform(-0.0007, 0.0007)
        ok = set_phone_gps(phone_id, lat, lon)
        if ok:
            log.info("[%s] GPS set to %s location (%.4f, %.4f)", acc_id, loc_label, lat, lon)
            return True
        log.warning("[%s] GPS set failed for %s coords, trying geo_area", acc_id, loc_label)

    # ── Tier 2: geo_area with area-radius jitter ─────────────────────────────
    area_key = account.get("geo_area")
    if area_key:
        resolved = None
        try:
            from set_phone_area import resolve_area_key
            resolved = resolve_area_key(area_key)
        except Exception:
            resolved = None
        if not resolved and area_key in AREAS:
            resolved = area_key
        if resolved and resolved in AREAS:
            lat, lon = area_coords(resolved, jitter=True)
            name = AREAS[resolved][0]
            ok = set_phone_gps(phone_id, lat, lon)
            if ok:
                log.info("[%s] GPS set via area: %s (%.4f, %.4f)", acc_id, name, lat, lon)
                return True
            log.warning("[%s] GPS set failed for area %s, falling back to city", acc_id, name)

    # ── Tier 3: city-level fallback ──────────────────────────────────────────
    geo_city = (account.get("geo_city") or "london").lower()
    cfg = _CITY_CONFIG.get(geo_city, _CITY_CONFIG["london"])
    _, city_lat, city_lon = cfg
    ok = set_phone_gps(phone_id, city_lat, city_lon)
    if ok:
        log.info("[%s] GPS set via city fallback: lat=%.4f lon=%.4f (city=%s)",
                 acc_id, city_lat, city_lon, geo_city)
    else:
        log.warning("[%s] GPS set completely failed (city=%s)", acc_id, geo_city)
    return ok


def _dismiss_permission_dialogs(phone_id: str, acc_id: str) -> None:
    """
    Dismiss common permission / first-run dialogs that Google apps show.

    Tries text-based taps via _find_and_tap first (works on Android 10 where
    uiautomator can see dialog text), then falls back to scaled coordinate taps
    for Android 14+ where dialogs are rendered in WebViews invisible to uiautomator.
    """
    # ── Text-based dismissal (preferred) ─────────────────────────────────────
    any_text_tapped = False
    for label in _PERMISSION_LABELS:
        if _find_and_tap(phone_id, [label]):
            any_text_tapped = True
            log.debug("[%s] Dismissed permission dialog via label %r", acc_id, label)
            import time as _time
            _time.sleep(0.5)

    # ── Coordinate-based fallback ────────────────────────────────────────────
    if not any_text_tapped:
        try:
            screen_w, screen_h = _get_screen_size(phone_id)
        except Exception:
            screen_w, screen_h = _REF_W, _REF_H

        for ref_x, ref_y in _DIALOG_TAP_POSITIONS:
            fx = ref_x / _REF_W
            fy = ref_y / _REF_H
            tx, ty = _scale(fx, fy, screen_w, screen_h)
            _shell(phone_id, f"input tap {tx} {ty}")
            log.debug("[%s] Coordinate dialog tap at (%d, %d)", acc_id, tx, ty)
            import time as _time
            _time.sleep(0.3)
