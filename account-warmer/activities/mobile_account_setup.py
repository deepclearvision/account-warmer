"""
Mobile Account Setup — One-Time Post-Login Configuration

Runs once per GeelarK phone after the Google account has been logged in.
Checks mobile_setup_done in geelark_accounts.yaml — skips if already true.

Steps performed (all via ADB on the cloud phone):
  1. Install required apps (Maps, Gmail, YouTube) via Play Store
  2. Enroll in Local Guides program
  3. Run 2 initial Maps driving direction sessions (seeds location history)

Run via:
  python account_setup_run.py --account gl_001
  python account_setup_run.py --all
"""

import logging
import time
from pathlib import Path

log = logging.getLogger("mobile_account_setup")

# ── Import shared ADB helpers from login module ───────────────────────────────
from activities.google_login_mobile import (
    _shell, _find_and_tap, _get_window_focus,
    _wait_for_foreground_app, _wait_for_ui_text, _poll_until_installed,
)


def _open_app(phone_id: str, package: str, acc_id: str = "") -> None:
    """Launch an app by package name, waiting until it reaches the foreground."""
    _shell(phone_id, f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    _wait_for_foreground_app(phone_id, package, timeout=12, acc_id=acc_id)


def _press_back(phone_id: str, times: int = 1) -> None:
    for _ in range(times):
        _shell(phone_id, "input keyevent KEYCODE_BACK")
        time.sleep(1)


def _press_home(phone_id: str) -> None:
    _shell(phone_id, "input keyevent KEYCODE_HOME")
    time.sleep(2)


def _swipe_up(phone_id: str) -> None:
    _shell(phone_id, "input swipe 540 1200 540 400 600")
    time.sleep(1)


def _type_text(phone_id: str, text: str) -> None:
    """Type text via ADB input, escaping spaces."""
    escaped = text.replace(" ", "%s")
    _shell(phone_id, f"input text {escaped}")
    time.sleep(0.5)


def _clear_field(phone_id: str) -> None:
    """Select-all then delete to clear a text field."""
    _shell(phone_id, "input keyevent KEYCODE_CTRL_A")
    time.sleep(0.3)
    _shell(phone_id, "input keyevent KEYCODE_DEL")
    time.sleep(0.3)


# ── App installation ──────────────────────────────────────────────────────────

# Apps required on every phone before warmup can run
_REQUIRED_APPS = [
    ("Google Maps",  "com.google.android.apps.maps"),
    ("Gmail",        "com.google.android.gm"),
    ("YouTube",      "com.google.android.youtube"),
]


def _is_installed(phone_id: str, package: str) -> bool:
    """Return True if the package is already installed."""
    _, out = _shell(phone_id, "pm list packages")
    return package in out


def _dismiss_play_store_dialogs(phone_id: str, acc_id: str) -> None:
    """
    Dismiss first-run Play Store dialogs that appear before the Install button.

    Known blockers on fresh accounts:
      1. "Google is optimizing app installs with your help" → Got it
      2. "You can choose additional web browsers" → No thanks
    """
    # Dialog 1: app install optimization
    if _find_and_tap(phone_id, ["Got it"]):
        log.info("[%s] Dismissed Play Store optimization dialog.", acc_id)
        time.sleep(2)
    # Dialog 2: browser choice sheet
    if _find_and_tap(phone_id, ["No thanks"]):
        log.info("[%s] Dismissed browser choice dialog.", acc_id)
        time.sleep(2)


def _open_play_store_app_page(phone_id: str, package: str) -> None:
    """Force-stop Play Store then open a specific app page, waiting until it's in focus."""
    _shell(phone_id, "am force-stop com.android.vending")
    time.sleep(1)
    _shell(phone_id,
           f"am start -a android.intent.action.VIEW "
           f"-d 'market://details?id={package}' com.android.vending")
    # Wait until Play Store is actually in the foreground (replaces fixed sleep(6))
    _wait_for_foreground_app(phone_id, "com.android.vending", timeout=15)


def _install_app(phone_id: str, acc_id: str, app_name: str, package: str) -> bool:
    """
    Install a single app from the Play Store.

    Verified flow on fresh GeelarK accounts:
      1. Open Play Store → wait until in foreground (smart, not fixed sleep)
      2. Dismiss first-run dialogs ("Got it", "No thanks")
      3. Tap Install — if not found: Back + dismiss blockers + retry
      4. Wait for "Continue" to appear (smart wait), then tap it
      5. Payment method screen → tap Skip (or back), wait for it
      6. Re-open app page → tap Install again
      7. Adaptive poll for installation (2 s → 15 s intervals, up to 10 min)

    Smart waits replace all fixed time.sleep() calls for screen transitions.
    Behavioural dwell times (intentional human-like pauses) are kept as-is.
    See research/geelark_realtime_detection.md for the full design rationale.
    """
    if _is_installed(phone_id, package):
        log.info("[%s] %s already installed — skipping.", acc_id, app_name)
        return True

    log.info("[%s] Installing %s (%s) …", acc_id, app_name, package)

    # ── Step 1: open app page (smart wait replaces sleep(6)) ─────────────────
    _open_play_store_app_page(phone_id, package)

    # ── Step 2: dismiss first-run Play Store dialogs ──────────────────────────
    _dismiss_play_store_dialogs(phone_id, acc_id)

    # ── Step 3: tap Install ───────────────────────────────────────────────────
    if _find_and_tap(phone_id, ["Install"]):
        log.info("[%s] Tapped Install for %s.", acc_id, app_name)
    else:
        log.warning("[%s] Install button not found — pressing Back and retrying.", acc_id)
        # A dialog is blocking (e.g. terms, Play Store update prompt).
        # Press Back to dismiss, handle common buttons, re-open and retry.
        _shell(phone_id, "input keyevent KEYCODE_BACK")
        time.sleep(1)
        _find_and_tap(phone_id, ["OK", "Accept", "I agree", "Accept & continue", "Got it", "No thanks"])
        _open_play_store_app_page(phone_id, package)
        _dismiss_play_store_dialogs(phone_id, acc_id)
        if _find_and_tap(phone_id, ["Install"]):
            log.info("[%s] Tapped Install for %s (after Back+retry).", acc_id, app_name)
        else:
            log.warning("[%s] Install button still not found after retry.", acc_id)

    # ── Step 4: handle "Complete account setup" sheet ─────────────────────────
    # Wait up to 8 s for "Continue" to appear, then tap it.
    # (Replaces blind sleep(4) — fires the moment the sheet loads.)
    found_continue, _ = _wait_for_ui_text(phone_id, "Continue", timeout=8, acc_id=acc_id)
    if found_continue and _find_and_tap(phone_id, ["Continue"]):
        log.info("[%s] Tapped Continue on 'Complete account setup' sheet.", acc_id)

        # ── Step 5: skip payment method screen ───────────────────────────────
        # Wait for Skip/No thanks to appear, then tap.
        found_skip, _ = _wait_for_ui_text(phone_id, "Skip", timeout=8, acc_id=acc_id)
        if found_skip and _find_and_tap(phone_id, ["Skip", "No thanks", "Not now", "Maybe later"]):
            log.info("[%s] Skipped payment setup.", acc_id)
        else:
            log.info("[%s] No skip button — pressing back from payment screen.", acc_id)
            _shell(phone_id, "input keyevent KEYCODE_BACK")
            time.sleep(2)

        # ── Step 6: re-open app page and tap Install (account setup now done) ─
        log.info("[%s] Re-opening Play Store for %s …", acc_id, app_name)
        _open_play_store_app_page(phone_id, package)
        _dismiss_play_store_dialogs(phone_id, acc_id)
        if _find_and_tap(phone_id, ["Install"]):
            log.info("[%s] Tapped Install for %s (post account setup).", acc_id, app_name)
        else:
            log.warning("[%s] Install button not found after account setup.", acc_id)
        # Brief wait for download to register before polling starts
        time.sleep(2)
        _dismiss_play_store_dialogs(phone_id, acc_id)

    # ── Step 7: adaptive poll until installed (replaces fixed 10 s grid) ─────
    log.info("[%s] Waiting for %s to download and install …", acc_id, app_name)
    if _poll_until_installed(phone_id, package, timeout=600, acc_id=acc_id):
        _press_home(phone_id)
        return True

    log.warning("[%s] %s install timed out after 10 min.", acc_id, app_name)
    _press_home(phone_id)
    return False


def _install_required_apps(phone_id: str, acc_id: str) -> bool:
    """
    Install Maps, Gmail and YouTube via Play Store.
    Skips any that are already installed.
    Returns True if all three end up installed.
    """
    log.info("[%s] Installing required apps …", acc_id)
    results = {}
    for app_name, package in _REQUIRED_APPS:
        ok = _install_app(phone_id, acc_id, app_name, package)
        results[app_name] = ok
        if ok:
            time.sleep(3)  # brief pause between installs

    # Retry any failures — account setup (Accept/Continue/Skip) may now be complete
    # after a later app triggered it, allowing previously blocked apps to install.
    failed = [(n, p) for n, p in _REQUIRED_APPS if not results.get(n)]
    if failed:
        log.info("[%s] Retrying %d failed app(s) now account setup is done: %s",
                 acc_id, len(failed), [n for n, _ in failed])
        for app_name, package in failed:
            ok = _install_app(phone_id, acc_id, app_name, package)
            results[app_name] = ok
            if ok:
                time.sleep(3)

    all_ok = all(results.values())
    log.info("[%s] App install results: %s", acc_id,
             {k: ("OK" if v else "FAILED") for k, v in results.items()})
    return all_ok


def _run_initial_maps_directions(phone_id: str, acc_id: str) -> bool:
    """
    Run 2 driving direction sessions during initial setup.

    Builds early location history in Google Maps — important for accounts
    using the maps_heavy strategy and for Local Guides trust signals.
    """
    from activities.mobile_warmup import _warmup_maps_directions
    log.info("[%s] Running initial Maps directions sessions ...", acc_id)
    try:
        ok1 = _warmup_maps_directions(phone_id, acc_id)
        time.sleep(5)
        ok2 = _warmup_maps_directions(phone_id, acc_id)
        success = ok1 or ok2
        log.info("[%s] Initial Maps directions: session1=%s session2=%s", acc_id, ok1, ok2)
        return success
    except Exception as e:
        log.warning("[%s] Initial Maps directions failed: %s", acc_id, e)
        return False


def _enroll_local_guides(phone_id: str, acc_id: str) -> bool:
    """Enroll the account in the Google Local Guides program via Maps."""
    log.info("[%s] Enrolling in Local Guides …", acc_id)
    try:
        _open_app(phone_id, "com.google.android.apps.maps")
        time.sleep(4)
        # Navigate to Contribute tab
        _find_and_tap(phone_id, ["Contribute", "Contributions"])
        time.sleep(3)
        # Tap Join Local Guides if shown
        _find_and_tap(phone_id, ["Join Local Guides", "Get started", "Join"])
        time.sleep(2)
        _find_and_tap(phone_id, ["Join Local Guides", "Get started", "Continue", "Done"])
        time.sleep(2)
        _press_home(phone_id)
        log.info("[%s] Local Guides enrollment done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Local Guides enrollment failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _set_home_address_in_maps(phone_id: str, acc_id: str, home_address: str) -> bool:
    """
    Set the account's home address in Google Maps saved places.

    Flow:
      1. Open Maps → wait for foreground
      2. Tap the Saved tab at the bottom nav
      3. Tap Labeled section
      4. Tap Home
      5. Type address, wait for autocomplete, tap first suggestion
      6. Return to home screen

    This step is best-effort — failure does NOT block account setup.
    The GPS coordinates are already set by _refresh_gps() each session,
    which is the more important location signal.
    """
    log.info("[%s] Setting home address in Maps: %s", acc_id, home_address)
    try:
        _open_app(phone_id, "com.google.android.apps.maps", acc_id)
        time.sleep(3)

        # Navigate to Saved tab
        if not _find_and_tap(phone_id, ["Saved", "SAVED"]):
            log.warning("[%s] Maps: Saved tab not found.", acc_id)
            _press_home(phone_id)
            return False
        time.sleep(2)

        # Tap Labeled section
        if not _find_and_tap(phone_id, ["Labeled", "LABELED"]):
            log.warning("[%s] Maps: Labeled section not found.", acc_id)
            _press_back(phone_id)
            _press_home(phone_id)
            return False
        time.sleep(2)

        # Tap Home label
        if not _find_and_tap(phone_id, ["Home", "HOME"]):
            log.warning("[%s] Maps: Home label not found.", acc_id)
            _press_back(phone_id, 2)
            _press_home(phone_id)
            return False
        time.sleep(2)

        # Type the address
        _type_text(phone_id, home_address)
        time.sleep(2)

        # Tap the first autocomplete result (first ~25 chars of address)
        hint = home_address[:25]
        found, _ = _wait_for_ui_text(phone_id, hint, timeout=6, acc_id=acc_id)
        if found:
            _find_and_tap(phone_id, [hint])
            time.sleep(1)

        _press_home(phone_id)
        log.info("[%s] Home address set in Maps.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Failed to set home address in Maps: %s", acc_id, e)
        _press_home(phone_id)
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def run_account_setup(account: dict) -> dict:
    """
    Run the one-time account setup flow on the GeelarK phone.

    Args:
        account: dict from geelark_accounts.yaml

    Returns:
        dict with keys: success (bool), steps_completed (list), error (str)
    """
    from core.geelark_client import GeelarKClient

    acc_id   = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id")
    result   = {"success": False, "steps_completed": [], "error": ""}

    if not phone_id:
        result["error"] = "No phone ID — provision the phone first"
        return result

    if account.get("mobile_setup_done"):
        log.info("[%s] Setup already done — skipping.", acc_id)
        result["success"] = True
        result["error"]   = "Already done"
        return result

    client = GeelarKClient()

    # Start phone
    log.info("[%s] Starting phone for setup …", acc_id)
    try:
        client.start_phone(phone_id)
    except Exception as e:
        result["error"] = f"Failed to start phone: {e}"
        return result

    from activities.mobile_warmup import _wait_for_phone_ready
    log.info("[%s] Waiting for phone to boot …", acc_id)
    if not _wait_for_phone_ready(phone_id, acc_id, timeout=90):
        log.warning("[%s] Phone did not boot in time — proceeding anyway", acc_id)

    home_address = account.get("home_address", "")
    steps = [
        ("install_apps",            lambda: _install_required_apps(phone_id, acc_id)),
        ("local_guides",            lambda: _enroll_local_guides(phone_id, acc_id)),
        # Set home address in Maps saved places (best-effort — skipped if not generated yet)
        *([("set_home_address",     lambda: _set_home_address_in_maps(phone_id, acc_id, home_address))]
          if home_address else []),
        ("maps_initial_directions", lambda: _run_initial_maps_directions(phone_id, acc_id)),
    ]

    for step_name, step_fn in steps:
        try:
            ok = step_fn()
            if ok:
                result["steps_completed"].append(step_name)
                log.info("[%s] ✓ %s", acc_id, step_name)
            else:
                log.warning("[%s] ✗ %s (step returned False — continuing)", acc_id, step_name)
        except Exception as e:
            log.warning("[%s] ✗ %s raised: %s — continuing", acc_id, step_name, e)
        time.sleep(2)

    # Stop phone
    try:
        client.stop_phone(phone_id)
        log.info("[%s] Phone stopped.", acc_id)
    except Exception as e:
        log.warning("[%s] Failed to stop phone: %s", acc_id, e)

    result["success"] = len(result["steps_completed"]) >= 2  # partial success is ok
    log.info("[%s] Setup complete. Steps done: %s", acc_id, result["steps_completed"])
    return result
