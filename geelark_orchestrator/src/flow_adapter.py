"""
flow_adapter.py — Hide Path 1 vs Path 2 behind one function.

Since Path 1 (paramMap injection) is confirmed working on Geelark, this adapter
simply maps the resolved RunPlan + Profile into the flat scalar dict that
run_custom_flow() accepts as param_map.

Guardrail #2: print_adapter_audit() shows the mapping side-by-side before any
phone is touched, so typos in field names are caught by eye.
"""
from __future__ import annotations

from data_layer import Profile
from resolver import RunPlan


def build_param_map(run_plan: RunPlan, profile: Profile) -> dict[str, str]:
    """
    Turn a resolved RunPlan into the param_map the Test A2 flow expects.

    The Test A2 flow uses these paramMap keys:
      {business_name}  — matched in the Maps result scroll loop
      {search_term}    — typed into the Maps search bar
      {nearby_lat} / {nearby_lng}
                       — the resolved GPS spoof point (RunPlan.gps_lat/gps_lng)
      {home_lat} / {home_lng}
                       — the profile's home location (Profile.home.lat/lng)

    Path 1 confirmed: Geelark accepts paramMap at POST /open/v1/task/rpa/add
    and substitutes {key} in flow steps at runtime.
    """
    if run_plan.abort:
        raise ValueError(
            f"Cannot build param_map for aborted plan: {run_plan.abort_reason}"
        )

    return {
        "business_name": run_plan.business_name,
        "search_term": run_plan.search_term or "",
        "nearby_lat": str(run_plan.gps_lat),
        "nearby_lng": str(run_plan.gps_lng),
        "home_lat": str(profile.home.lat),
        "home_lng": str(profile.home.lng),
        "business_lat": str(profile.business.lat),
        "business_lng": str(profile.business.lng),
    }


def print_adapter_audit(run_plan: RunPlan, profile: Profile) -> None:
    """Guardrail #2 — print RunPlan + resulting param_map side by side."""
    param_map = build_param_map(run_plan, profile)
    print("\n=== ADAPTER AUDIT (RunPlan -> param_map) ===")
    print(f"  business_name  -> {param_map['business_name']!r}")
    print(f"  search_term    -> {param_map['search_term']!r}")
    print(f"  nearby_lat     -> {param_map['nearby_lat']!r}  (RunPlan.gps_lat)")
    print(f"  nearby_lng     -> {param_map['nearby_lng']!r}  (RunPlan.gps_lng)")
    print(f"  home_lat       -> {param_map['home_lat']!r}  (Profile.home.lat)")
    print(f"  home_lng       -> {param_map['home_lng']!r}  (Profile.home.lng)")
    print(f"  business_lat   -> {param_map['business_lat']!r}  (Profile.business.lat)")
    print(f"  business_lng   -> {param_map['business_lng']!r}  (Profile.business.lng)")
    print("=== END ADAPTER AUDIT ===\n")
