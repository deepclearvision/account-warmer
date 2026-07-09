"""
diagnose_install.py — Screenshot each step of the Maps install flow for gl_005.
Saves screenshots to C:/WarmingData/diag/ so we can see exactly what's on screen.
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
_env = Path(__file__).parent / "warmer.env"
if _env.exists():
    import os
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag")

from core.geelark_client import GeelarKClient
from core.account_store import get_account_store
from activities.google_login_mobile import _shell, _find_and_tap, _get_window_focus
from activities.mobile_account_setup import _is_installed, _dismiss_play_store_dialogs

DIAG_DIR = Path("C:/WarmingData/diag")
DIAG_DIR.mkdir(parents=True, exist_ok=True)

acc = next(a for a in get_account_store().get_mobile_accounts() if a["id"] == "gl_005")
phone_id = acc["geelark_phone_id"]

client = GeelarKClient()


def snap(label: str):
    log.info("Taking screenshot: %s", label)
    try:
        img = client.take_screenshot(phone_id, max_wait=30)
        if img:
            path = DIAG_DIR / f"{label}.png"
            path.write_bytes(img)
            log.info("Saved: %s", path)
        else:
            log.warning("Screenshot returned nothing for: %s", label)
    except Exception as e:
        log.warning("Screenshot failed (%s): %s", label, e)


log.info("Starting phone …")
client.start_phone(phone_id)
time.sleep(15)
ok, _ = _shell(phone_id, "echo ready")
log.info("Phone ready: %s", ok)

snap("01_boot")

# Open Play Store on Maps page
log.info("Opening Play Store for Maps …")
_shell(phone_id,
       "am start -a android.intent.action.VIEW "
       "-d 'market://details?id=com.google.android.apps.maps' com.android.vending")
time.sleep(6)
snap("02_play_store_opened")

log.info("Window focus: %s", _get_window_focus(phone_id))

# Try dismissing dialogs
log.info("Dismissing any dialogs …")
_dismiss_play_store_dialogs(phone_id, "gl_005")
time.sleep(2)
snap("03_after_dialog_dismiss")

# Check if Install is visible
log.info("Looking for Install button …")
tapped = _find_and_tap(phone_id, ["Install"])
log.info("Install tapped: %s", tapped)
time.sleep(5)
snap("04_after_install_tap")

log.info("Window focus after Install: %s", _get_window_focus(phone_id))

# Dismiss dialogs again
_dismiss_play_store_dialogs(phone_id, "gl_005")
time.sleep(2)
snap("05_after_second_dialog_dismiss")

# Handle payment dialog
focus = _get_window_focus(phone_id)
log.info("Focus: %s", focus)
if "billing" in focus.lower() or "wallet" in focus.lower() or "delegator" in focus.lower():
    log.info("Payment dialog — pressing back")
    _shell(phone_id, "input keyevent KEYCODE_BACK")
    time.sleep(3)
    snap("06_after_back_from_payment")
    _dismiss_play_store_dialogs(phone_id, "gl_005")
    time.sleep(2)
    tapped = _find_and_tap(phone_id, ["Skip"])
    if tapped:
        log.info("Tapped Skip")
        time.sleep(3)
    tapped = _find_and_tap(phone_id, ["Install"])
    log.info("Second Install tapped: %s", tapped)
    time.sleep(5)
    snap("07_after_second_install_tap")
    _dismiss_play_store_dialogs(phone_id, "gl_005")
    time.sleep(2)
    snap("08_after_third_dialog_dismiss")

# Wait 30s and check what's happening
log.info("Waiting 30s to observe download …")
time.sleep(30)
snap("09_thirty_seconds_in")
log.info("Maps installed: %s", _is_installed(phone_id, "com.google.android.apps.maps"))

time.sleep(30)
snap("10_sixty_seconds_in")
log.info("Maps installed: %s", _is_installed(phone_id, "com.google.android.apps.maps"))

log.info("Stopping phone …")
client.stop_phone(phone_id)
log.info("Done. Check C:/WarmingData/diag/ for screenshots.")
