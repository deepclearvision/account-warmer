"""
resolver.py — Turn a Profile into a RunPlan.

PURE module: no network, no Geelark. Given a validated Profile, it makes every
random decision for one run, applies the fallback chain, and records exactly
what it did and why. This is the "brain" — and because it's pure and seeded,
every decision is reproducible and unit-testable offline.

This is the module that makes the system transparent: nothing is decided on the
device or hidden in a flow. You can run resolve() a thousand times with logging
and see precisely what would happen.

GPS fallback chain (because phones run a few times, the fallback must stay varied):
    1. random item from nearby_points
    2. random item from nearby_points_backup
    3. the fixed business coordinate          (raises a WARNING — data problem)
    4. abort the run                           (recorded as skipped_data_error)

Search-term fallback chain:
    1. random item from search_keywords
    2. first item in the pool
    3. abort the run

Branded-term (optional, never aborts):
    1. random item from branded_keywords
    2. skip the branded step this run
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Optional

from data_layer import Coordinate, Profile


@dataclass
class RunPlan:
    run_id: str
    profile_key: str
    geelark_profile_id: str
    business_name: str
    business_goal: str

    gps_lat: float
    gps_lng: float

    search_term: Optional[str]            # None only if the run is aborted
    branded_term: Optional[str]           # None = skip branded step this run

    rng_seed: int                          # so this exact plan can be reproduced
    abort: bool = False                    # True => do NOT run; see abort_reason
    abort_reason: Optional[str] = None

    # A plain-language trail of every decision, in order. This is the
    # transparency record — print it, log it, store it.
    decisions: list[str] = field(default_factory=list)
    # Which fallbacks fired (empty = everything came from the primary pool).
    fallbacks_taken: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.abort:
            return f"[ABORT] {self.profile_key}: {self.abort_reason}"
        branded = self.branded_term if self.branded_term else "(skipped)"
        fb = ", ".join(self.fallbacks_taken) if self.fallbacks_taken else "none"
        return (
            f"[PLAN] {self.profile_key} ({self.business_name})\n"
            f"  run_id      : {self.run_id}\n"
            f"  seed        : {self.rng_seed}\n"
            f"  GPS         : {self.gps_lat},{self.gps_lng}\n"
            f"  search_term : {self.search_term}\n"
            f"  branded     : {branded}\n"
            f"  goal        : {self.business_goal}\n"
            f"  fallbacks   : {fb}"
        )


def _pick(rng: random.Random, items: list) -> Optional[object]:
    return rng.choice(items) if items else None


# Default GPS origin split: 70% nearby, 30% onsite. Tunable per-run.
DEFAULT_NEARBY_PCT = 70


def resolve(
    profile: Profile,
    seed: Optional[int] = None,
    avoid_gps: Optional[Coordinate] = None,
    avoid_search: Optional[str] = None,
    nearby_pct: int = DEFAULT_NEARBY_PCT,
) -> RunPlan:
    """
    Build a RunPlan for one run of `profile`.

    seed:        fix the RNG for reproducibility. If None, a random seed is
                 generated AND recorded in the plan, so any run can be replayed.
    avoid_gps:   optional — the GPS point used on this phone's previous run, so
                 a few-run phone doesn't collide on the same point (best-effort).
    avoid_search: same idea for the search term.
    """
    if seed is None:
        seed = random.randrange(2**31)
    rng = random.Random(seed)

    run_id = str(uuid.uuid4())
    decisions: list[str] = []
    fallbacks: list[str] = []

    # ---- GPS origin split (tunable: nearby_pct vs 100-nearby_pct) ----
    def choose_coord(pool: list[Coordinate]) -> Optional[Coordinate]:
        if not pool:
            return None
        if avoid_gps is not None and len(pool) > 1:
            filtered = [c for c in pool if c.as_token() != avoid_gps.as_token()]
            if filtered:
                return _pick(rng, filtered)
        return _pick(rng, pool)

    use_nearby = rng.randint(1, 100) <= nearby_pct
    if use_nearby:
        gps = choose_coord(profile.nearby_points)
        if gps is not None:
            decisions.append(f"GPS: picked {gps.as_token()} from nearby_points (split roll nearby)")
        else:
            gps = choose_coord(profile.nearby_points_backup)
            if gps is not None:
                fallbacks.append("gps->backup_pool")
                decisions.append(
                    f"GPS: nearby_points empty, picked {gps.as_token()} from backup pool"
                )
            else:
                gps = profile.business
                fallbacks.append("gps->business_coord")
                decisions.append(
                    f"GPS: both nearby pools empty, fell back to business coord {gps.as_token()} "
                    f"(WARNING: data problem)"
                )
    else:
        gps = choose_coord(profile.onsite_points)
        if gps is not None:
            decisions.append(f"GPS: picked {gps.as_token()} from onsite_points (split roll onsite)")
        else:
            gps = profile.business
            fallbacks.append("gps->business_coord")
            decisions.append(
                f"GPS: onsite_points empty, fell back to business coord {gps.as_token()} "
                f"(WARNING: data problem)"
            )

    # ---- Search term resolution ----
    # Mode-driven: branded pulls from branded_keywords; discovery from search_keywords.
    search_term: Optional[str] = None
    if profile.search_mode == "branded":
        pool = profile.branded_keywords
        pool_label = "branded_keywords"
    else:
        pool = profile.search_keywords
        pool_label = "search_keywords"

    if pool:
        if avoid_search is not None and len(pool) > 1:
            filtered = [s for s in pool if s != avoid_search]
            search_term = _pick(rng, filtered) if filtered else _pick(rng, pool)
        else:
            search_term = _pick(rng, pool)
        decisions.append(f"search: picked {search_term!r} from {pool_label} (mode={profile.search_mode})")
    else:
        search_term = None  # will trigger abort below
        decisions.append(f"search: {pool_label} empty, no fallback available (mode={profile.search_mode})")

    # ---- Branded term (optional, never aborts) ----
    branded_term = _pick(rng, profile.branded_keywords)
    if branded_term:
        decisions.append(f"branded: picked {branded_term!r}")
    else:
        decisions.append("branded: pool empty, skipping branded step this run")

    # ---- Abort decision ----
    abort = False
    abort_reason: Optional[str] = None
    if search_term is None:
        abort = True
        abort_reason = "no search term available (empty pool, no fallback)"
        decisions.append(f"ABORT: {abort_reason}")

    return RunPlan(
        run_id=run_id,
        profile_key=profile.profile_key,
        geelark_profile_id=profile.geelark_profile_id,
        business_name=profile.business_name,
        business_goal=profile.business_goal,
        gps_lat=gps.lat,
        gps_lng=gps.lng,
        search_term=search_term,
        branded_term=branded_term,
        rng_seed=seed,
        abort=abort,
        abort_reason=abort_reason,
        decisions=decisions,
        fallbacks_taken=fallbacks,
    )
