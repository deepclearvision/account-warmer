"""
data_adapter.py — Bridge the real production CSV to the reference Profile format.

The real CSV (geelark_flow_data_2026-05-13.csv) uses different column names and
stores nearby points as separate lat/lng columns rather than pipe-delimited pools.
This adapter converts rows to the format expected by data_layer.py/profile_from_row(),
so the tested validation logic (two-delimiter rule, coordinate parsing, etc.) is
reused untouched.
"""
from __future__ import annotations

import csv
from pathlib import Path

from data_layer import DataError, Profile, profile_from_row, assert_unique_profile_keys


def _build_pool_from_columns(row: dict, prefix: str, max_n: int) -> str:
    """
    Read nearby_1_lat, nearby_1_lng ... nearby_N_lat, nearby_N_lng from the row
    and produce a pipe-delimited 'lat,lng|lat,lng' string.
    """
    parts: list[str] = []
    for i in range(1, max_n + 1):
        lat = row.get(f"{prefix}_{i}_lat", "").strip()
        lng = row.get(f"{prefix}_{i}_lng", "").strip()
        if lat and lng:
            parts.append(f"{lat},{lng}")
    return "|".join(parts)


def profile_from_real_csv_row(row: dict[str, str]) -> Profile:
    """
    Convert a raw dict from the real CSV into a validated Profile.
    """
    profile_key = row.get("account_id", "").strip()
    if not profile_key:
        raise DataError("Missing account_id (used as profile_key)")

    geelark_profile_id = row.get("geelark_phone_id", "").strip()
    if not geelark_profile_id:
        raise DataError("Missing geelark_phone_id")

    business_name = row.get("business_name", "").strip()
    if not business_name:
        raise DataError("Missing business_name")

    # Coordinate columns are required (these anchor fallbacks).
    home_lat = row.get("home_lat", "").strip()
    home_lng = row.get("home_lng", "").strip()
    business_lat = row.get("business_lat", "").strip()
    business_lng = row.get("business_lng", "").strip()
    if not all((home_lat, home_lng, business_lat, business_lng)):
        raise DataError("Missing one or more required lat/lng columns")

    # Build pools from the wide-column layout.
    nearby_points = _build_pool_from_columns(row, "nearby", 20)
    onsite_points = _build_pool_from_columns(row, "onsite", 10)

    # Branded and search pools already arrive pipe-delimited in the CSV.
    branded_keywords = row.get("branded_searches", "").strip()
    search_keywords = row.get("search_terms", "").strip()

    search_mode = (row.get("search_mode") or "discovery").strip().lower()
    if search_mode not in ("branded", "discovery"):
        search_mode = "discovery"

    goal = (row.get("business_goal") or "review").strip().lower()

    def _bool_str(raw: str | None) -> str:
        return "1" if (raw or "").strip().lower() in ("1", "true", "yes") else "0"

    # Package into the reference row format so profile_from_row() validates it.
    ref_row: dict[str, str] = {
        "profile_key": profile_key,
        "geelark_profile_id": geelark_profile_id,
        "business_name": business_name,
        "home_lat": home_lat,
        "home_lng": home_lng,
        "business_lat": business_lat,
        "business_lng": business_lng,
        "nearby_points": nearby_points,
        "onsite_points": onsite_points,
        "branded_keywords": branded_keywords,
        "search_keywords": search_keywords,
        "search_mode": search_mode,
        "business_goal": goal,
        "profile_verdict": "pending",
        "provisioned": _bool_str(row.get("provisioned")),
        "maps_verified": _bool_str(row.get("maps_verified")),
    }
    return profile_from_row(ref_row)


def load_real_profiles_csv(path: str) -> list[Profile]:
    """Load all profiles from the real CSV."""
    profiles: list[Profile] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            try:
                profiles.append(profile_from_real_csv_row(row))
            except DataError as e:
                raise DataError(f"Row {i} ({row.get('account_id', '?')}): {e}") from e
    assert_unique_profile_keys(profiles, path)
    return profiles


def load_real_profile_by_key(path: str, profile_key: str) -> Profile:
    """Load a single profile by account_id / profile_key."""
    matches = [p for p in load_real_profiles_csv(path) if p.profile_key == profile_key]
    if len(matches) == 0:
        raise DataError(f"profile_key {profile_key!r} not found in {path}")
    if len(matches) > 1:
        raise DataError(
            f"profile_key {profile_key!r} matched {len(matches)} rows in {path} — "
            f"expected exactly one. Data has duplicates."
        )
    return matches[0]
