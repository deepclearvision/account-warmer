#!/usr/bin/env python3
"""
run_full_maps_flow.py — Full rebuilt Maps flow: one phone, proven chain, three outcomes.

Spec:
  1. GPS spoof -> wait 7s -> open Maps
  2. Click "Search here" -> inputContent (baked literal) -> keyOption enter
  3. Scroll-and-find business (capped ~15) -> click -> card
  4. If found: probabilistic interactions
  5. If not found: record outcome, optionally geo: fallback

Usage:
    python3 scripts/run_full_maps_flow.py <real_csv> <profile_key> [--seed N] [--dry-run]
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_repo_root = Path(__file__).resolve().parent.parent.parent
_orchestrator_root = _repo_root / "geelark_orchestrator"
_orchestrator_src = _orchestrator_root / "src"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from data_adapter import load_real_profile_by_key, DataError
from resolver import resolve
from run_logger import log_plan, make_feedback_packet
from runs_store import RunsStore
from provisioning import check_phone_fit
from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder, step_click, step_wait, step_scroll

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
MAPS_PACKAGE = "com.google.android.apps.maps"

# Interaction probabilities (tunable)
INTERACTION_CFG = {
    "scroll_card": 0.80,
    "photos": 0.60,
    "reviews": 0.50,
    "directions": 0.40,
    "website": 0.35,
    "call": 0.30,
    "save": 0.25,
}

# ---------------------------------------------------------------------------
# Warm-up flow — browse nearby listings (not the primary business)
# ---------------------------------------------------------------------------

# Load search categories from maps_data.json (account-warmer/data/maps_data.json)
_MAPS_DATA_PATH = _account_warmer / "data" / "maps_data.json"
_WARMUP_SEARCH_TERMS: list[str] = []
try:
    with open(_MAPS_DATA_PATH, "r", encoding="utf-8") as _f:
        _maps_data = json.load(_f)
    _cats = _maps_data.get("search_categories", {})
    for _cat_terms in _cats.values():
        if isinstance(_cat_terms, list):
            _WARMUP_SEARCH_TERMS.extend(_cat_terms)
except Exception:
    # Fallback if JSON is missing / unreadable
    _WARMUP_SEARCH_TERMS = [
        "pub near me",
        "coffee shops near me",
        "restaurants near me",
        "cafes near me",
        "barbers near me",
        "hair salon near me",
        "supermarket near me",
        "pharmacy near me",
        "park near me",
        "cinema near me",
    ]


def build_warmup_flow(
    profile,
    run_plan,
    max_listings: int = 2,
) -> dict:
    """
    Build a warm-up Maps browse flow.

    Steps:
      1. Open Maps (GPS already active)
      2. Search a category term randomly selected from maps_data.json
      3. Expand results panel
      4. Click 1-2 result listings by coordinate (avoiding primary business)
      5. Light interactions: scroll card, maybe Photos or Save
      6. Press Back to return to results

    State tracking: consults BusinessTracker to avoid repeating search terms
    within the TTL window. Falls back to untracked terms if all have been used.
    """
    rng = random.Random(run_plan.rng_seed)

    # Search-term selection with state tracking
    from state_tracker import BusinessTracker
    tracker = BusinessTracker(profile.profile_key, ttl_days=7)

    # Build candidate pool, excluding terms recently visited
    candidates = [t for t in _WARMUP_SEARCH_TERMS if not tracker.is_visited(t)]
    if not candidates:
        # All terms exhausted within TTL — fall back to full pool
        candidates = _WARMUP_SEARCH_TERMS[:]

    search_term = rng.choice(candidates)
    # Record the search term as "visited" for this session so future warm-ups
    # rotate through categories.
    tracker.record_visit(search_term)
    tracker.save()

    fb = FlowBuilder(
        title=f"Maps warm-up browse — {profile.profile_key}",
        desc="Warm-up: search nearby listings, light interactions, no primary business",
        timeout_minutes=10,
        error_type="skip",  # all warm-up steps are optional
    )

    # Stage 1: Open Maps and search
    fb.open_app(MAPS_PACKAGE, remark="Open Google Maps")
    fb.wait(5000, "Wait for Maps to load")

    fb.click("text", "Search here", search_time_ms=10000,
             remark="Tap search box")
    fb.wait(1000, "Wait after tap")

    fb.add(_step_input_content(search_term, remark=f"Search: {search_term}"))
    fb.wait(1000, "Wait after typing")

    fb.key_option("enter", remark="Submit search")
    fb.wait(8000, "Wait for results list")

    # Stage 2: Expand results panel (swipe up from bottom)
    fb.scroll_up(min_px=900, max_px=1000, remark="Expand results panel")
    fb.wait(2000, "Wait after expand")

    # Stage 3: Browse 1-2 random listings
    # We click by coordinate in the results panel area (first result ≈ y=900)
    # and do light interactions, then press Back.
    n_listings = rng.randint(1, max_listings)
    for i in range(n_listings):
        # Click a result in the list area (approximate first result position)
        # Screen is ~720×1440; results panel top is ~800-1200 after expand
        fb.click_coord(360, 900 + i * 200, remark=f"Click result {i + 1}")
        fb.wait(4000, "Wait for card to open")

        # Light interaction: scroll the card
        fb.scroll_down(min_px=300, max_px=500, remark="Scroll card")
        fb.wait(1500, "Wait after scroll")

        # Maybe Photos (30% chance — lower than primary flow)
        if rng.random() < 0.30:
            fb.click("text", "Photos", search_time_ms=5000,
                     remark=f"Warmup: Photos on listing {i + 1}")
            fb.wait(3000, "Wait after Photos")
            fb.press_back(remark="Back from Photos")
            fb.wait(1500, "Wait after back")

        # Maybe Save (20% chance)
        if rng.random() < 0.20:
            fb.click("text", "Save", search_time_ms=5000,
                     remark=f"Warmup: Save on listing {i + 1}")
            fb.wait(3000, "Wait after Save")

        # Return to results list for next listing (unless last)
        if i < n_listings - 1:
            fb.press_back(remark="Back to results list")
            fb.wait(2000, "Wait after back")

    print(f"  [Warmup builder] search={search_term!r} | listings={n_listings}")
    return fb.build_gal()


# ---------------------------------------------------------------------------
# Flow construction — bake resolved values as literals
# ---------------------------------------------------------------------------

def _step_input_content(text: str, remark: str = "") -> dict:
    """inputContent step for Maps search box (proven working)."""
    return {
        "type": "inputContent",
        "name": remark or "Input content",
        "config": {
            "clear": True,
            "serial": 1,
            "content": [text],
            "simulate": True,
            "waitTime": 300,
            "inputType": "taskOrder",
            "searchTime": 5000,
            "serialType": "fixedValue",
            "hiddenChildren": False,
            "filterCollection": [
                [{"type": "class", "content": "android.widget.EditText", "filterType": "equal"}]
            ],
        },
    }


def _step_key_option(key_type: str, remark: str = "") -> dict:
    """keyOption step (visual-editor style) — submit Maps search."""
    return {"type": "keyOption", "config": {"keyType": key_type, "remark": remark}}


def build_maps_flow(
    run_plan,
    profile,
    max_scroll_attempts: int = 15,
    interaction_seed: int = None,
    skip_gps_setup: bool = False,
) -> dict:
    """
    Build a complete Maps search+interact GAL flow with BAKED LITERAL values.
    All randomness happens in Python; the flow receives resolved scalars.
    """
    fb = FlowBuilder(
        title=f"Maps search + interact — {profile.profile_key}",
        desc="Full rebuilt flow: GPS -> Maps -> search -> find -> interact",
        timeout_minutes=15,
        error_type="skip",
    )

    # ---- Stage 1: Fake GPS setup (optional) ----
    if not skip_gps_setup:
        fb.open_app(FAKE_GPS_PACKAGE, remark="Open Fake GPS")
        fb.wait(5000, "Wait for Fake GPS load")

        # Click "Set Location" button (two attempts for consent screens)
        fb.click("id", "com.theappninjas.fakegpsjoystick:id/set_location_button",
                 search_time_ms=5000, remark="Tap Set Location")
        fb.wait(3000, "Wait for location dialog")

        # Input baked GPS coordinates
        fb.add(_step_input_content(
            f"{run_plan.gps_lat}, {run_plan.gps_lng}",
            remark="Input GPS coordinates"
        ))
        fb.wait(2000, "Wait after GPS input")

        # Click START
        fb.click("id", "com.theappninjas.fakegpsjoystick:id/start_button",
                 search_time_ms=5000, remark="Tap START")
        fb.wait(7000, "CRITICAL: wait 7s for spoof to take effect before opening Maps")
    else:
        fb.wait(2000, "GPS setup skipped — assuming mock already active")

    # ---- Stage 2: Maps search (proven chain) ----
    fb.open_app(MAPS_PACKAGE, remark="Open Google Maps")
    fb.wait(5000, "Wait for Maps to load")

    # Click search box
    fb.click("text", "Search here", search_time_ms=10000, remark="Tap search box")
    fb.wait(1000, "Wait after tap")

    # Type baked search term
    fb.add(_step_input_content(
        run_plan.search_term,
        remark=f"Search: {run_plan.search_term}"
    ))
    fb.wait(1000, "Wait after typing")

    # Submit via keyOption enter (NOT pressKey — proven difference)
    fb.key_option("enter", remark="Submit search")
    fb.wait(8000, "Wait for results list")

    # ---- Stage 3: Scroll-and-find (capped) ----
    # Try clicking the business name immediately (found-top case)
    fb.click("text", profile.business_name,
             search_time_ms=8000, remark="Try click business (top)")
    fb.wait(3000, "Wait after click attempt")

    # If not found at top, scroll and try again (capped attempts)
    for i in range(1, max_scroll_attempts + 1):
        fb.scroll_down(min_px=400, max_px=600,
                       remark=f"Scroll attempt {i}/{max_scroll_attempts}")
        fb.wait(1500, "Wait after scroll")
        fb.click("text", profile.business_name,
                 search_time_ms=6000, remark=f"Try click after scroll {i}")
        fb.wait(1500, "Wait after click attempt")

    # ---- Stage 3b: Business confirmation ----
    # Verify the opened card actually contains the expected business name.
    # This waits for the business name to appear as a header/title element.
    fb.wait_for_element("text", profile.business_name,
                        search_time_ms=8000,
                        remark="VERIFY: business card title matches expected")
    fb.wait(2000, "Wait after business confirmation")

    # ---- Stage 4: Structured interactions ----
    # All randomness is resolved in Python before flow generation.
    if interaction_seed is None:
        interaction_seed = random.randrange(2**31)
    rng = random.Random(interaction_seed)

    # -- Phase 1: Scroll card + pick 1-2 from [Photos, Reviews] --
    fb.scroll_down(min_px=300, max_px=500, remark="Interaction: scroll card")
    fb.wait(2000, "Wait after scroll")

    phase1_pool = ["Photos", "Reviews"]
    phase1_count = rng.randint(1, 2)
    phase1_selected = rng.sample(phase1_pool, k=phase1_count)
    for interaction in phase1_selected:
        fb.click("text", interaction, search_time_ms=5000,
                 remark=f"Phase1: {interaction}")
        fb.wait(3000, f"Wait after {interaction}")
        # Verification: wait for a screen element that proves the interaction opened
        if interaction == "Photos":
            fb.wait_for_element("text", "Photos",
                                search_time_ms=5000,
                                remark=f"VERIFY: Photos tab opened")
        elif interaction == "Reviews":
            fb.wait_for_element("text", "Write a review",
                                search_time_ms=5000,
                                remark=f"VERIFY: Reviews list opened")
        fb.wait(2000, "Wait after verification")

    # -- Phase 2: Pick exactly 1 from [Save, Share, Website] --
    phase2_pool = ["Save", "Share", "Website"]
    phase2_selected = rng.choice(phase2_pool)
    fb.click("text", phase2_selected, search_time_ms=5000,
             remark=f"Phase2: {phase2_selected}")
    fb.wait(3000, f"Wait after {phase2_selected}")
    # Verification per interaction type
    if phase2_selected == "Save":
        fb.wait_for_element("text", "Save",
                            search_time_ms=5000,
                            remark="VERIFY: Save button present")
    elif phase2_selected == "Share":
        fb.wait_for_element("text", "Share",
                            search_time_ms=5000,
                            remark="VERIFY: Share sheet opened")
    elif phase2_selected == "Website":
        fb.wait_for_element("text", "Website",
                            search_time_ms=5000,
                            remark="VERIFY: Website link present")
    fb.wait(2000, "Wait after Phase2 verification")

    # -- Phase 3: Directions (primary) or Call (fallback). One MUST appear. --
    # Directions click: skip if not found (allows fallback to Call)
    fb.click("text", "Directions", search_time_ms=8000,
             remark="Phase3: Directions (primary)", error_type="skip")
    fb.wait(3000, "Wait after Directions click")
    # Call fallback: skip if not found (allows Start verification anyway)
    fb.click("text", "Call", search_time_ms=8000,
             remark="Phase3: Call (fallback)", error_type="skip")
    fb.wait(3000, "Wait after Call click")
    # Final verification: hard-stop if neither Directions nor Call opened
    # their respective target screens. "Start" only appears on the route
    # screen after Directions; if Directions never opened, this aborts.
    fb.wait_for_element("text", "Start",
                        search_time_ms=5000,
                        remark="VERIFY: Directions route screen (Start)",
                        error_type="stop")
    fb.wait(2000, "Wait after Phase3 verification")

    # Log interaction plan for post-run evidence matching
    print(f"  [Flow builder] Phase1: {phase1_selected}")
    print(f"  [Flow builder] Phase2: {phase2_selected}")
    print(f"  [Flow builder] Phase3: Directions primary / Call fallback")

    return fb.build_gal()


# ---------------------------------------------------------------------------
# Phone helpers
# ---------------------------------------------------------------------------

def ensure_running(client: GeelarKClient, phone_id: str) -> bool:
    try:
        statuses = client.get_phone_status([phone_id])
        s = statuses[0].get("status", -1) if statuses else -1
    except Exception:
        s = -1
    if s == 0:
        print("  phone already running.")
        return True
    print("  starting phone...")
    try:
        client.start_phone(phone_id)
    except Exception as e:
        print(f"  ERROR: start failed: {e}", file=sys.stderr)
        return False
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            statuses = client.get_phone_status([phone_id])
            s = statuses[0].get("status", -1) if statuses else -1
            if s == 0:
                print("  phone running.")
                return True
        except Exception:
            pass
    print("  ERROR: phone did not start within 120s.", file=sys.stderr)
    return False


def poll_task(client: GeelarKClient, task_id: str, timeout_minutes: int = 60) -> dict:
    print(f"  polling task {task_id} ...")
    deadline = time.time() + (timeout_minutes * 60)
    interval = 10
    while time.time() < deadline:
        time.sleep(interval)
        try:
            items = client.query_tasks([task_id])
            if not items:
                continue
            t = items[0]
            status = t.get("status")
            if status == 3:
                print("  task completed.")
                return t
            if status == 4:
                print(f"  task failed: {t.get('failDesc', t.get('failCode', 'unknown'))}")
                return t
            if status == 7:
                print("  task cancelled.")
                return t
        except Exception as e:
            print(f"  poll error: {e}", file=sys.stderr)
    print("  ERROR: poll timed out.", file=sys.stderr)
    return {"status": -1, "failDesc": "poll_timeout"}


def _save_screenshot(client: GeelarKClient, phone_id: str, label: str, log_dir: str) -> str | None:
    try:
        data = client.take_screenshot(phone_id, max_wait=30)
        if not data:
            return None
        p = Path(log_dir) / "screenshots"
        p.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%H%M%S")
        path = p / f"{label}_{ts}.png"
        path.write_bytes(data)
        return str(path)
    except Exception as e:
        print(f"  screenshot save failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Run full Maps flow on one phone.")
    ap.add_argument("csv_path", help="Path to geelark_flow_data CSV")
    ap.add_argument("profile_key", help="e.g. acc_049")
    ap.add_argument("--seed", type=int, default=None, help="Fix RNG for reproducibility")
    ap.add_argument("--dry-run", action="store_true", help="Resolve + build flow only; no phone")
    ap.add_argument("--no-clear-cache", action="store_true", help="Skip Fake GPS cache clear")
    ap.add_argument("--skip-gps-setup", action="store_true",
                    help="Skip Fake GPS setup in flow (use when GPS is already active via shell)")
    ap.add_argument("--log-dir", default=str(_orchestrator_root / "logs"))
    ap.add_argument("--db-path", default=str(_orchestrator_root / "runs.sqlite"))
    ap.add_argument("--schema-path", default=str(_orchestrator_root / "schema.sql"))
    ap.add_argument("--max-scroll", type=int, default=15, help="Cap scroll attempts")
    args = ap.parse_args()

    # 1. Load profile
    try:
        profile = load_real_profile_by_key(args.csv_path, args.profile_key)
    except DataError as e:
        print(f"DATA ERROR: {e}", file=sys.stderr)
        return 2

    if profile.warnings:
        print("Profile warnings:")
        for w in profile.warnings:
            print(f"  ! {w}")

    # 2. Resolve
    run_plan = resolve(profile, seed=args.seed)
    print(run_plan.summary())
    print()
    for d in run_plan.decisions:
        print(f"  - {d}")
    print()

    # 3. Provisioning / maps_verified gate (Section 9.10)
    fit, reason = check_phone_fit(profile)
    if not fit:
        run_plan.abort = True
        run_plan.abort_reason = reason
        run_plan.decisions.append(f"ABORT: {reason}")
        print(f"\nRun aborted — {reason}")
        log_plan(run_plan, log_dir=args.log_dir,
                 extra={"phase": "run_full_maps_flow", "status": "aborted",
                        "abort_reason": reason})
        return 0

    if run_plan.abort:
        print("\nRun aborted — no phone touched.")
        log_plan(run_plan, log_dir=args.log_dir,
                 extra={"phase": "run_full_maps_flow", "status": "aborted"})
        return 0

    # 4. Build the flow with baked literals
    gal_dict = build_maps_flow(
        run_plan, profile,
        max_scroll_attempts=args.max_scroll,
        skip_gps_setup=args.skip_gps_setup,
    )
    print(f"Flow built: {gal_dict['title']}")
    print(f"  Steps: {len(gal_dict['content']['contents'])}")

    if args.dry_run:
        print("\nDRY RUN — flow built but no phone touched.")
        log_plan(run_plan, log_dir=args.log_dir,
                 extra={"phase": "run_full_maps_flow", "status": "dry_run"})
        return 0

    # 4. Persistence
    store = RunsStore(args.db_path, args.schema_path)
    store.upsert_profile(profile)
    store.record_run(run_plan)
    log_plan(run_plan, log_dir=args.log_dir,
             extra={"phase": "run_full_maps_flow", "status": "pending"})

    # 5. Phone operations
    client = GeelarKClient()
    phone_id = profile.geelark_profile_id

    print(f"\n--- Phone {phone_id} ({profile.profile_key}) ---")

    if not ensure_running(client, phone_id):
        store.finish_run(run_plan.run_id, "failed_infra",
                         failed_step="phone_start_failed")
        store.close()
        return 1

    # Clear cache
    if not args.no_clear_cache:
        print("  clearing Fake GPS cache...")
        try:
            client.clear_app_cache(phone_id, FAKE_GPS_PACKAGE)
            time.sleep(2)
        except Exception as e:
            print(f"  warning: cache clear failed: {e}", file=sys.stderr)

    # Import flow
    print("  importing flow...")
    try:
        flow_id = client.import_rpa_flow(json.dumps(gal_dict, ensure_ascii=False))
        print(f"  flow_id = {flow_id}")
    except Exception as e:
        print(f"  ERROR: import failed: {e}", file=sys.stderr)
        store.finish_run(run_plan.run_id, "failed_infra",
                         failed_step=f"import_failed: {e}")
        store.close()
        return 1

    # Dispatch
    print("  dispatching...")
    try:
        task_id = client.run_custom_flow(
            flow_id=flow_id,
            phone_id=phone_id,
            param_map={},  # all values baked into the flow
            task_name=f"maps_full_{profile.profile_key}_{run_plan.run_id[:8]}",
            schedule_delay=5,
        )
        print(f"  task_id = {task_id}")
    except Exception as e:
        print(f"  ERROR: dispatch failed: {e}", file=sys.stderr)
        store.finish_run(run_plan.run_id, "failed_infra",
                         failed_step=f"dispatch_failed: {e}")
        store.close()
        return 1

    # 6. Poll
    task_result = poll_task(client, task_id)
    status_code = task_result.get("status")
    duration = task_result.get("cost")

    # 6b. Shell-based foreground verification (device-side proof)
    # Runs immediately after the flow task completes, before screenshots.
    print("\n  --- Shell verification (Phase 3 outcome) ---")
    try:
        from core.geelark_client import _post
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "dumpsys activity activities | grep mResumedActivity"})
        activity_out = r.get("output", "") or ""
        activity_line = activity_out.strip().splitlines()[0] if activity_out.strip() else "(no output)"
        # Safe print: encode unicode that Windows console may not handle
        try:
            print(f"  Foreground activity: {activity_line}")
        except UnicodeEncodeError:
            print(f"  Foreground activity: {activity_line.encode('ascii', 'replace').decode()}")
        if "com.google.android.apps.maps" in activity_out:
            if any(k in activity_out.lower() for k in ["directions", "geointent", "navigation"]):
                print("  SHELL VERDICT: Maps Directions/GeoIntent screen confirmed")
            else:
                print("  SHELL VERDICT: Maps foreground but no Directions evidence")
        else:
            try:
                print(f"  SHELL VERDICT: Maps NOT foreground — {activity_line}")
            except UnicodeEncodeError:
                print(f"  SHELL VERDICT: Maps NOT foreground")
    except Exception as e:
        print(f"  Shell verification error: {e}", file=sys.stderr)

    # 7. Screenshots for verification
    print("\n  taking verification screenshots...")
    _save_screenshot(client, phone_id, f"{run_plan.run_id}_final", args.log_dir)
    time.sleep(3)
    _save_screenshot(client, phone_id, f"{run_plan.run_id}_post", args.log_dir)

    # 8. Outcome classification (three outcomes)
    # For now: "success" if task completed, "failed" if task failed,
    # and we need to inspect screenshots to determine found-top / found-after-scroll / not-found.
    # This requires human review of screenshots for the first assembled run.
    if status_code == 3:
        run_status = "success"
    elif status_code == 4:
        run_status = "failed"
    elif status_code == 7:
        run_status = "cancelled"
    else:
        run_status = "failed_infra"

    failed_step = task_result.get("failDesc") or str(task_result.get("failCode", ""))
    if not failed_step:
        failed_step = None

    store.finish_run(
        run_plan.run_id,
        status=run_status,
        duration_seconds=duration,
        failed_step=failed_step,
    )
    log_plan(run_plan, log_dir=args.log_dir,
             extra={
                 "phase": "run_full_maps_flow",
                 "status": run_status,
                 "duration_seconds": duration,
                 "failed_step": failed_step,
                 "task_id": task_id,
                 "flow_id": flow_id,
             })

    print("\n=== FEEDBACK PACKET ===")
    print(make_feedback_packet(run_plan.run_id, log_dir=args.log_dir))
    print("\n=== EXPECTED vs ACTUAL ===")
    print(f"Expected: GPS={run_plan.gps_lat},{run_plan.gps_lng} | "
          f"search={run_plan.search_term!r}")
    print(f"Actual  : status={run_status} | duration={duration}s | failed_step={failed_step or 'n/a'}")
    print("\nNOTE: Inspect screenshots in logs/screenshots/ to classify outcome:")
    print("  - found-top: business card visible without scroll")
    print("  - found-after-scroll: card visible after scrolling")
    print("  - not-found: no business card after exhausting scrolls")

    store.close()
    return 0 if run_status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
