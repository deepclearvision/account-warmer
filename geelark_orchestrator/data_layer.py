"""
data_layer.py — Load and validate profile data.

PURE module: no network, no Geelark, no randomness. Just turns rows of stored
data into clean, validated Profile objects (or rejects them loudly).

This is half of where correctness lives. The other half is resolver.py.
Everything here is unit-testable offline.

The Two-Delimiter Rule (the single most important correctness rule):
    - PIPE  '|'  separates items in a pool.
    - COMMA ','  stays INSIDE a single "lat,lng" coordinate and never separates items.
A coordinate token is therefore "51.5224,-0.1026". A pool of them is
"51.5224,-0.1026|51.5187,-0.0991". If a comma is ever allowed to separate
items, a coordinate gets torn in half and the bot navigates to garbage.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Optional


# A coordinate token: "lat,lng" with optional space after the comma.
# Lat in [-90, 90], lng in [-180, 180] is checked separately (regex only shapes it).
_COORD_RE = re.compile(r"^-?\d{1,3}(?:\.\d+)?,\s*-?\d{1,3}(?:\.\d+)?$")


class DataError(ValueError):
    """Raised when a profile row is malformed enough that it must not run."""


@dataclass
class Coordinate:
    lat: float
    lng: float

    def as_token(self) -> str:
        return f"{self.lat},{self.lng}"


@dataclass
class Profile:
    profile_key: str
    geelark_profile_id: str
    business_name: str
    home: Coordinate
    business: Coordinate
    nearby_points: list[Coordinate]          # the 1:many pool (70% default)
    nearby_points_backup: list[Coordinate]   # secondary pool (varied fallback)
    onsite_points: list[Coordinate]          # onsite pool (30% default)
    branded_keywords: list[str]
    search_keywords: list[str]
    search_mode: str = "discovery"
    business_goal: str = "review"
    profile_verdict: str = "pending"
    # Per-phone provisioning state (Section 9.10). Both must be True before a run.
    provisioned: bool = False   # dumpsys verified: Fake GPS is active mock provider
    maps_verified: bool = False  # BEHAVIOUR-based: Maps opens cleanly, NO Play Store interstitial
    # Anything that looked off but wasn't fatal is recorded here, not hidden.
    warnings: list[str] = field(default_factory=list)


def _split_pool(raw: str) -> list[str]:
    """Split a pipe-delimited pool into trimmed, non-empty items."""
    if raw is None:
        return []
    return [item.strip() for item in str(raw).split("|") if item.strip()]


def _parse_coord(token: str) -> Coordinate:
    """Parse one 'lat,lng' token into a Coordinate, validating ranges."""
    token = token.strip()
    if not _COORD_RE.match(token):
        raise DataError(f"Coordinate token is not 'lat,lng': {token!r}")
    lat_str, lng_str = token.split(",")
    lat, lng = float(lat_str.strip()), float(lng_str.strip())
    if not (-90.0 <= lat <= 90.0):
        raise DataError(f"Latitude out of range in {token!r}: {lat}")
    if not (-180.0 <= lng <= 180.0):
        raise DataError(f"Longitude out of range in {token!r}: {lng}")
    return Coordinate(lat, lng)


def _parse_coord_pool(raw: str, label: str, warnings: list[str]) -> list[Coordinate]:
    """
    Parse a pipe-delimited pool of coordinate tokens.
    Bad individual tokens are dropped with a warning rather than killing the row,
    EXCEPT the caller decides whether an empty result is fatal.
    """
    coords: list[Coordinate] = []
    for item in _split_pool(raw):
        try:
            coords.append(_parse_coord(item))
        except DataError as e:
            warnings.append(f"{label}: dropped bad coordinate ({e})")
    return coords


def profile_from_row(row: dict[str, str]) -> Profile:
    """
    Build a validated Profile from a dict of raw string columns.
    Raises DataError if the row is too broken to ever run safely.
    Non-fatal issues are attached to Profile.warnings instead of being hidden.
    """
    warnings: list[str] = []

    def required(col: str) -> str:
        val = (row.get(col) or "").strip()
        if not val:
            raise DataError(f"Missing required column: {col!r}")
        return val

    profile_key = required("profile_key")
    geelark_profile_id = required("geelark_profile_id")
    business_name = required("business_name")

    # 1:1 coordinates are required and must be valid — these anchor the fallbacks.
    home = _parse_coord(f"{required('home_lat')},{required('home_lng')}")
    business = _parse_coord(f"{required('business_lat')},{required('business_lng')}")

    nearby = _parse_coord_pool(row.get("nearby_points", ""), "nearby_points", warnings)
    nearby_backup = _parse_coord_pool(
        row.get("nearby_points_backup", ""), "nearby_points_backup", warnings
    )
    onsite = _parse_coord_pool(row.get("onsite_points", ""), "onsite_points", warnings)
    branded = _split_pool(row.get("branded_keywords", ""))
    search = _split_pool(row.get("search_keywords", ""))

    search_mode = (row.get("search_mode") or "discovery").strip().lower()
    if search_mode not in ("branded", "discovery"):
        warnings.append(f"Unknown search_mode {search_mode!r}, defaulting to 'discovery'")
        search_mode = "discovery"

    # Pools being empty is not fatal here — resolver.py has a fallback chain.
    # But we surface it so it's never silent.
    if not nearby:
        warnings.append("nearby_points pool is empty — GPS will use backup/business fallback")
    if not search:
        warnings.append("search_keywords pool is empty — run will be skipped if no fallback")

    goal = (row.get("business_goal") or "review").strip().lower()
    if goal not in ("review", "edit"):
        warnings.append(f"Unknown business_goal {goal!r}, defaulting to 'review'")
        goal = "review"

    verdict = (row.get("profile_verdict") or "pending").strip().lower()

    def _bool_field(raw: str | None) -> bool:
        return (raw or "").strip().lower() in ("1", "true", "yes")

    provisioned = _bool_field(row.get("provisioned"))
    maps_verified = _bool_field(row.get("maps_verified"))

    return Profile(
        profile_key=profile_key,
        geelark_profile_id=geelark_profile_id,
        business_name=business_name,
        home=home,
        business=business,
        nearby_points=nearby,
        nearby_points_backup=nearby_backup,
        onsite_points=onsite,
        branded_keywords=branded,
        search_keywords=search,
        search_mode=search_mode,
        business_goal=goal,
        profile_verdict=verdict,
        provisioned=provisioned,
        maps_verified=maps_verified,
        warnings=warnings,
    )


def assert_unique_profile_keys(profiles: list[Profile], path: str = "") -> None:
    """
    Load-time guard: every profile_key must appear exactly once.
    Raises DataError on first duplicate, naming the offending key and rows.
    """
    seen: dict[str, int] = {}
    for i, p in enumerate(profiles, start=2):  # row 1 is header
        key = p.profile_key
        if key in seen:
            raise DataError(
                f"Duplicate profile_key {key!r} at rows {seen[key]} and {i}"
                f"{(' in ' + path) if path else ''}. "
                f"Every profile_key must be unique."
            )
        seen[key] = i


def load_profiles_csv(path: str) -> list[Profile]:
    """
    Load all profiles from a CSV. Rows that fail validation are NOT silently
    dropped — they raise, so you find out at load time, not mid-run.
    """
    profiles: list[Profile] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            try:
                profiles.append(profile_from_row(row))
            except DataError as e:
                raise DataError(f"Row {i} ({row.get('profile_key', '?')}): {e}") from e
    assert_unique_profile_keys(profiles, path)
    return profiles


def load_profile_csv_by_key(path: str, profile_key: str) -> Profile:
    """Load a single profile by key (for debugging one phone at a time)."""
    matches = [p for p in load_profiles_csv(path) if p.profile_key == profile_key]
    if len(matches) == 0:
        raise DataError(f"profile_key {profile_key!r} not found in {path}")
    if len(matches) > 1:
        raise DataError(
            f"profile_key {profile_key!r} matched {len(matches)} rows in {path} — "
            f"expected exactly one. Data has duplicates."
        )
    return matches[0]
