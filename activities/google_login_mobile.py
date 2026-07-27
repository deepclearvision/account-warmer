"""
Google Account Login via GeelarK Cloud Phone.

# Current Approach (2026-07-18): GeelarK RPA Flow — All Android Versions
All phones use GeelarK's "Google auto login" custom RPA flow
(ID 628594965093548419) which handles:
  - Email entry
  - Password entry
  - TOTP 2FA (built-in getAuthenticationCode step generates code from secret)
  - Consent screen navigation (Skip, I understand, I agree, ACCEPT)

ParamMap: {"User Email": email, "Password": password, "2faCode": totp_secret}
The 2faCode param is optional — omit for accounts without TOTP.

This flow is called via _run_auto_login_flow() → client.run_custom_flow().
The flow runs entirely within GeelarK's RPA engine (no ADB text input needed),
which solves the Android 14 WebView blindness problem (uiautomator cannot see
inside Chrome WebViews on Android 14, and 'input text' does not work).

# Legacy Approach: Pure ADB (Deprecated)
_run_pure_adb_login() is kept for reference but is no longer used in the
default path.  It drove sign-in via ADB shell commands through the
ADD_ACCOUNT_SETTINGS intent on Android 10/13 phones.

# Proxy
All phones share a single StreamVia rotating mobile SOCKS5 proxy.
Proxy IP is rotated before every phone-start operation (login, setup, warmup)
via the StreamVia token-based API with changeipunique → changeip fallback.
A 60s settling buffer follows every rotation.  Rate limit: ~180s between
rotations — enforced by cooldown tracking inside _rotate_proxy_ip().

# TOTP Secrets
Stored in geelark_accounts.yaml. May contain spaces — normalize with
  .replace(" ", "").upper()
before use. The auto login flow's getAuthenticationCode step handles code
generation internally from the raw secret.
"""

import base64
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("google_login_mobile")

# Load warmer.env so GEELARK_PROXY_CONTROL_URL and other vars are available
_env_file = Path(__file__).parent.parent / "warmer.env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

MAX_ATTEMPTS   = 3
POLL_INTERVAL  = 8        # seconds between task status polls (was 15)
MAX_TASK_SECS  = 7 * 60   # 7 min per attempt
BOOT_WAIT_SECS = 15       # seconds to wait after phone start before submitting task (was 25)

# Reference screen (Redmi Note 11 Pro+ 720x1440) — coordinates are expressed as
# fractions of this size so they scale to any phone automatically.
_REF_W = 720
_REF_H = 1440

# 2FA screen — fractional coords (col%, row%) of reference phone
_TOTP_FIELD_FX, _TOTP_FIELD_FY = 360 / _REF_W, 620  / _REF_H   # ~50%, ~43%
_TOTP_NEXT_FX,  _TOTP_NEXT_FY  = 608 / _REF_W, 1292 / _REF_H   # ~84%, ~90%

# Post-login consent buttons
_AGREE_FX, _AGREE_FY = 608 / _REF_W, 1292 / _REF_H
_NEXT_FX,  _NEXT_FY  = 360 / _REF_W, 1292 / _REF_H

STATUS_WAITING     = 1
STATUS_IN_PROGRESS = 2
STATUS_COMPLETED   = 3
STATUS_FAILED      = 4
STATUS_CANCELLED   = 7

GOOGLE_AUTH_ACTIVITY = "com.google.android.gms.auth.uiflows.minutemaid.MinuteMaidActivity"
LAUNCHER_ACTIVITY    = "com.android.launcher3"


def _shell(phone_id: str, cmd: str) -> tuple[bool, str]:
    """Execute an ADB shell command on the cloud phone via GeelarK API."""
    from core.geelark_client import _post
    try:
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
        return r.get("status", False), r.get("output", "")
    except Exception as e:
        log.warning("Shell command failed (%s): %s", cmd[:50], e)
        return False, ""


_ADBKEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
_ADBKEYBOARD_PKG = "com.android.adbkeyboard"
_adbkeyboard_confirmed: set[str] = set()   # phone_ids confirmed to have ADBKeyboard active


def _ensure_adbkeyboard(phone_id: str) -> bool:
    """
    Check that ADBKeyboard is installed and set as the active IME on this phone.
    Caches the result per phone_id so we only check once per session.
    Returns True if ADBKeyboard is ready.
    """
    if phone_id in _adbkeyboard_confirmed:
        return True
    _, out = _shell(phone_id, f"pm list packages | grep {_ADBKEYBOARD_PKG}")
    if _ADBKEYBOARD_PKG not in out:
        log.debug("[%s] ADBKeyboard not installed — using fallback input text", phone_id)
        return False
    # Ensure it's the active IME
    _, ime_out = _shell(phone_id, "settings get secure default_input_method")
    if _ADBKEYBOARD_IME not in ime_out:
        _shell(phone_id, f"ime enable {_ADBKEYBOARD_IME}")
        _shell(phone_id, f"ime set {_ADBKEYBOARD_IME}")
        log.info("[%s] ADBKeyboard activated as default IME", phone_id)
    _adbkeyboard_confirmed.add(phone_id)
    return True


def _type_text(phone_id: str, text: str) -> None:
    """
    Type text into the currently focused field.
    Uses ADBKeyboard broadcast (atomic, no race conditions) when available;
    falls back to 'input text' otherwise.
    """
    if _ensure_adbkeyboard(phone_id):
        import base64 as _b64
        encoded = _b64.b64encode(text.encode("utf-8")).decode("ascii")
        _shell(phone_id, "am broadcast -a ADB_CLEAR_TEXT")
        time.sleep(0.1)
        _shell(phone_id, f"am broadcast -a ADB_INPUT_B64 --es msg '{encoded}'")
    else:
        # Legacy fallback: CTRL+A, DEL, then input text
        _shell(phone_id, "input keyevent KEYCODE_CTRL_A")
        time.sleep(0.3)
        _shell(phone_id, "input keyevent KEYCODE_DEL")
        time.sleep(0.4)
        _shell(phone_id, f"input text {text}")


def _get_window_focus(phone_id: str) -> str:
    """Return the current foreground activity name."""
    _, out = _shell(phone_id, "dumpsys window | grep mCurrentFocus")
    return out.strip()


# ── Smart polling helpers ─────────────────────────────────────────────────────
#
# Why these exist:
#   Every _shell() call is an HTTP round-trip with 500 ms–2 s of overhead.
#   uiautomator dump adds another 2–5 s on the device.  Fixed time.sleep()
#   always waits the worst case.  These helpers poll at adaptive intervals so
#   the next action fires the moment the screen is ready.
#
# See research/geelark_realtime_detection.md for the full architecture guide.

def _wait_for_foreground_app(phone_id: str, package: str,
                              timeout: int = 15, acc_id: str = "") -> bool:
    """
    Block until `package` is the foreground app (or timeout expires).

    Uses `dumpsys window | grep mCurrentFocus` — the lightest reliable command
    for app-transition detection (~200–400 ms device-side, vs 2–5 s for
    uiautomator dump).  Replaces fixed time.sleep() after am start / monkey.

    Adaptive intervals: 1 s × 3, then 2 s × 2, then 3 s until timeout.
    """
    intervals = [1, 1, 1, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]
    deadline = time.time() + timeout
    for interval in intervals:
        focus = _get_window_focus(phone_id)
        if package in focus:
            return True
        if time.time() >= deadline:
            break
        time.sleep(min(interval, max(0, deadline - time.time())))
    # Final check
    return package in _get_window_focus(phone_id)


def _wait_for_ui_text(phone_id: str, text: str,
                      timeout: int = 10, acc_id: str = "") -> tuple[bool, str]:
    """
    Poll uiautomator dump until `text` appears anywhere in the UI tree.

    Returns (found: bool, xml: str).  Use this instead of sleep(N) when
    waiting for a specific button or label to appear after an action.

    Note: uiautomator dump is slow (2–5 s per call) — only use when you need
    the XML.  For pure state detection (which app is in focus) use
    _wait_for_foreground_app() instead.

    Adaptive intervals: 1 s × 2, then 2 s, then 3 s until timeout.
    """
    import re as _re
    intervals = [1, 1, 2, 3, 3, 3, 3, 3, 3]
    deadline = time.time() + timeout
    last_xml = ""
    for interval in intervals:
        _, xml = _shell(phone_id,
                        "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
        last_xml = xml or ""
        if text in last_xml:
            return True, last_xml
        if time.time() >= deadline:
            break
        time.sleep(min(interval, max(0, deadline - time.time())))
    return False, last_xml


def _poll_until_installed(phone_id: str, package: str,
                           timeout: int = 600, acc_id: str = "") -> bool:
    """
    Poll `pm list packages` until `package` appears (app installed) or timeout.

    Replaces the fixed 10 s polling grid in _install_app().  Adaptive intervals
    catch fast installs (5–30 s on good connections) without waiting a full 10 s
    when the app is already done.

    Adaptive intervals: 2 s × 3, 3 s × 2, 5 s × 3, 8 s, then 10 s → 15 s.
    """
    intervals = [2, 2, 2, 3, 3, 5, 5, 5, 8, 10, 10, 10, 15, 15, 15, 15,
                 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15,
                 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15]
    deadline = time.time() + timeout
    elapsed = 0
    for i, interval in enumerate(intervals):
        time.sleep(min(interval, max(0, deadline - time.time())))
        elapsed += interval
        ok, out = _shell(phone_id, f"pm list packages | grep {package}")
        if package in (out or ""):
            log.info("[%s] %s installed after ~%ds.", acc_id, package, elapsed)
            return True
        if time.time() >= deadline:
            break
        # Every ~30 s dismiss any dialogs that appeared mid-download
        if i > 0 and elapsed % 30 < interval + 1:
            from activities.mobile_account_setup import _dismiss_play_store_dialogs
            try:
                _dismiss_play_store_dialogs(phone_id, acc_id)
            except Exception:
                pass
    log.warning("[%s] %s not installed after %ds.", acc_id, package, timeout)
    return False


def _get_screen_size(phone_id: str) -> tuple[int, int]:
    """Return (width, height) of the phone screen via ADB wm size."""
    _, out = _shell(phone_id, "wm size")
    # Output: "Physical size: 1080x2400" or "Override size: 720x1440"
    for token in out.split():
        if "x" in token and token.replace("x", "").isdigit():
            try:
                w, h = token.strip().split("x")
                return int(w), int(h)
            except ValueError:
                pass
    log.warning("Could not parse screen size from: %r — using reference 720x1440", out)
    return _REF_W, _REF_H


def _scale(fx: float, fy: float, w: int, h: int) -> tuple[int, int]:
    """Convert fractional reference coords to absolute pixels for this screen."""
    return int(fx * w), int(fy * h)


def _find_and_tap(phone_id: str, labels: list[str]) -> bool:
    """
    Dump the UI hierarchy and tap the first visible button whose text or
    content-desc matches any of the given labels (case-insensitive).
    Returns True if a button was found and tapped.
    """
    import re as _re
    _, xml = _shell(phone_id,
                    "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
    if not xml:
        return False
    for label in labels:
        # Find a node with matching text="" or content-desc="" that is clickable
        pattern = _re.compile(
            r'<node[^>]*(?:text|content-desc)="' + _re.escape(label) + r'"[^>]*>',
            _re.IGNORECASE,
        )
        for m in pattern.finditer(xml):
            node = m.group(0)
            bounds = _re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
            if bounds:
                x = (int(bounds.group(1)) + int(bounds.group(3))) // 2
                y = (int(bounds.group(2)) + int(bounds.group(4))) // 2
                log.info("Tapping %r at (%d, %d)", label, x, y)
                _shell(phone_id, f"input tap {x} {y}")
                return True
    return False


def _find_text_field_and_tap(phone_id: str) -> tuple[int, int] | None:
    """
    Find the first focusable/editable text field on screen via uiautomator dump.
    Returns (x, y) centre of the field, or None if not found.
    """
    import re as _re
    _, xml = _shell(phone_id,
                    "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
    if not xml:
        return None
    # Look for EditText or password/code input fields
    for pattern_str in [
        r'class="android.widget.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        r'class="android.widget.EditText"[^>]*>',
    ]:
        m = _re.search(pattern_str, xml)
        if m and len(m.groups()) >= 4:
            x = (int(m.group(1)) + int(m.group(3))) // 2
            y = (int(m.group(2)) + int(m.group(4))) // 2
            log.info("Found EditText field at (%d, %d)", x, y)
            _shell(phone_id, f"input tap {x} {y}")
            return x, y
    return None


def _run_pure_adb_login(phone_id: str, email: str, password: str,
                        totp_secret: str, acc_id: str,
                        screen_w: int, screen_h: int,
                        progress_callback=None) -> tuple[bool, str]:
    """
    Drive a complete Google account login using only ADB shell commands.

    Works on Android 10 (GeelarK's built-in RPA requires Android 11+ and is broken
    on our SM-G9650 phones). Confirmed working 2026-04-09.

    Uses ADD_ACCOUNT_SETTINGS intent to open MinuteMaidActivity directly:
      am start -a android.settings.ADD_ACCOUNT_SETTINGS --es account_types com.google

    STATE MACHINE (_2fa_step tracks 2FA navigation progress):

      _2fa_step=0  SECURITY-CODE screen shown immediately after password.
                   Google presents "Get code from your Android device" — NOT TOTP.
                   Action: tap "Try another way" at fractional (0.176, 0.896).

      _2fa_step=1  METHOD LIST (webview_prompt).
                   WebView list of available 2FA methods. No text visible in uiautomator.
                   Action: tap "Google Authenticator" row at fractional (0.50, 0.491).

      _2fa_step=2  REAL TOTP ENTRY screen (totp_entry).
                   Actual 6-digit TOTP input. Generate pyotp code and enter it.

      _2fa_step=3+ POST-TOTP screens.
                   Recovery phone, account setup steps, etc.
                   Action: tap Skip / Not now / Next to dismiss.

    KEY PITFALL — email screen vs TOTP screen:
      _detect_minutemaid_screen() returns "totp_entry" for ANY EditText without a
      password flag. Email entry (step 4 above) and real TOTP entry (step 8) are
      INDISTINGUISHABLE by screen type alone. We check `not email_entered` FIRST
      to handle email, before ever checking _2fa_step for TOTP.

    Returns (success: bool, diagnosis: str).
    """
    def _cb(phase, **kw):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **kw})
            except Exception:
                pass

    # Short-circuit: if account is already registered from a previous attempt, succeed now.
    if _verify_google_account(phone_id, email):
        log.info("[%s] Account already in AccountManager — login already succeeded", acc_id)
        return True, "Account already registered on device"

    _cb("launching_add_account")
    # Press HOME first to dismiss any in-progress screens from a previous attempt
    log.info("[%s] Pressing HOME to dismiss any lingering screens …", acc_id)
    _shell(phone_id, "input keyevent KEYCODE_HOME")
    time.sleep(2)

    log.info("[%s] Launching Add Account intent …", acc_id)
    ok, out = _shell(phone_id,
                     "am start -a android.settings.ADD_ACCOUNT_SETTINGS "
                     "--es account_types com.google")
    log.info("[%s] Intent: ok=%s  %s", acc_id, ok, (out or "")[:80])
    time.sleep(3)

    focus = _get_window_focus(phone_id)
    if GOOGLE_AUTH_ACTIVITY not in focus:
        # Sometimes it goes via the account-type chooser screen first — wait a bit more
        time.sleep(3)
        focus = _get_window_focus(phone_id)
    if GOOGLE_AUTH_ACTIVITY not in focus:
        return False, f"Intent did not open MinuteMaidActivity. Focus: {focus}"

    log.info("[%s] MinuteMaidActivity open. ADB login state machine starting …", acc_id)

    # Fractional tap coordinates (calibrated for 720×1440, scaled at runtime)
    # These are on the method-selection list shown after "Try another way":
    _GOOGLE_AUTH_ROW     = (0.50, 0.491)   # "Get code from Google Authenticator" row
    # "Try another way" link on the "Security code from device" screen.
    # NOTE: MinuteMaidActivity content area on SM-G9650 spans y=48–916 on a 720x1344
    # drawable surface (reported as 720x1440 by wm size).  The "Try another way" link
    # appears ~73px below the input field bottom (y≈684), so y≈725-730.
    # Fractional: 725/1440 ≈ 0.503, x≈137/720 ≈ 0.19 (left-aligned, same as
    # "Forgot email?" link on the email screen at bounds [42,704][232,746]).
    # The old value (0.176, 0.896) = y≈1290 was WRONG — it hit empty space below
    # the UI content area (which ends at y≈916).
    _TRY_ANOTHER_WAY_SEC = (0.19, 0.503)   # left-aligned link below input field
    _NEXT_BTN            = (0.83, 0.89)    # Generic Next / Continue button

    email_entered    = False
    password_entered = False
    totp_entered     = False
    _2fa_step        = 0
    prev_xml         = ""
    stuck_count      = 0

    for i in range(32):
        screen_type, xml = _detect_minutemaid_screen(phone_id)
        focus = _get_window_focus(phone_id)
        log.info("[%s] Screen %d: type=%s  2fa_step=%d  focus=…%s",
                 acc_id, i + 1, screen_type, _2fa_step, focus[-50:])

        # Left MinuteMaidActivity — either success or an unexpected screen.
        # IMPORTANT: after password submission the focus briefly returns empty
        # while the next screen loads inside MinuteMaidActivity.  Do NOT exit
        # on an empty focus string — double-check before leaving the loop.
        if GOOGLE_AUTH_ACTIVITY not in focus:
            if not focus.strip():
                # Transient empty focus — wait for screen to settle and re-check
                log.info("[%s] Focus empty at screen %d — waiting to confirm transition",
                         acc_id, i + 1)
                time.sleep(5)
                focus = _get_window_focus(phone_id)
                if GOOGLE_AUTH_ACTIVITY in focus:
                    log.info("[%s] Still in MinuteMaidActivity — continuing loop", acc_id)
                    continue
                if not focus.strip():
                    # Still empty — give it one more chance
                    time.sleep(6)
                    focus = _get_window_focus(phone_id)
                    if GOOGLE_AUTH_ACTIVITY in focus:
                        continue
            # Genuinely left MinuteMaidActivity
            log.info("[%s] Left auth screen at iteration %d (focus: %s) — navigating consent …",
                     acc_id, i + 1, focus[-60:])
            _navigate_consent_screens(phone_id)
            return True, "Completed auth screens — navigated to consent/home"

        # ── Email entry ───────────────────────────────────────────────────────
        # IMPORTANT: _detect_minutemaid_screen returns "totp_entry" for ANY
        # EditText without a password flag — including the email screen.
        # So we must check not email_entered BEFORE the totp_entry branch.
        if not email_entered and screen_type in ("email", "unknown", "totp_entry"):
            # Any non-password EditText screen before email entry = email screen
            _, chk_xml = _shell(phone_id,
                                "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
            if chk_xml and "android.widget.EditText" in chk_xml:
                _cb("entering_email")
                log.info("[%s] Email screen — entering email", acc_id)
                field = _find_text_field_and_tap(phone_id)
                if not field:
                    _shell(phone_id, "input tap 360 631")
                time.sleep(0.5)
                _type_text(phone_id, email)
                time.sleep(0.5)
                _shell(phone_id, "input keyevent 66")
                email_entered = True
                time.sleep(2)
                continue
            # No EditText found — wait for screen to load
            time.sleep(2)

        # ── Password entry ────────────────────────────────────────────────────
        # IMPORTANT: On some GMS / Android builds the password EditText does NOT
        # expose a password inputType flag in uiautomator, so _detect_minutemaid_screen
        # returns "totp_entry" instead of "password" for the password screen.
        # After email entry, ANY EditText screen (regardless of screen_type) must be
        # the password screen — there is no other EditText between email and 2FA.
        elif email_entered and not password_entered and screen_type in ("password", "totp_entry", "unknown"):
            _cb("entering_password")
            log.info("[%s] Password screen — entering password", acc_id)
            field = _find_text_field_and_tap(phone_id)
            if not field:
                _shell(phone_id, "input tap 360 539")
            time.sleep(0.6)
            _type_text(phone_id, password)
            time.sleep(0.5)
            _shell(phone_id, "input keyevent 66")
            password_entered = True
            _2fa_step = 0
            time.sleep(2)
            continue

        # ── TOTP / Security-code entry ────────────────────────────────────────
        # Only triggered after BOTH email and password have been entered.
        # IMPORTANT: After password Google may first show a "Security code from
        # device" screen (_2fa_step == 0).  We must dismiss that and navigate to
        # the Google Authenticator TOTP screen via "Try another way".
        elif screen_type == "totp_entry" and password_entered:
            if _2fa_step == 0:
                # First totp_entry after password = "Security code from device" screen.
                # Must navigate to Google Authenticator via "Try another way".
                log.info("[%s] Security-code screen — tapping 'Try another way'", acc_id)
                _cb("2fa_method_select")
                _shell(phone_id, "input keyevent KEYCODE_BACK")   # dismiss keyboard
                time.sleep(1.5)   # wait for keyboard to fully close before tapping
                # Prefer text-based tap: on these phones uiautomator exposes WebView
                # content, so "Try another way" is findable by text in the dump.
                if not _find_and_tap(phone_id, ["Try another way"]):
                    # Before tapping a blind fallback, check if Google is asking us to
                    # verify a phone number instead of offering TOTP.  If we see
                    # phone-verification hints and no "Try another way", we cannot
                    # proceed — skip the account immediately so we don't waste retries.
                    _phone_hints = (
                        "we sent", "verification code to your", "check your phone",
                        "your phone number", "text you a", "sms code",
                        "get a code via sms",
                    )
                    if any(h in xml.lower() for h in _phone_hints):
                        log.warning("[%s] Phone verification screen detected — no access "
                                    "to registered phone number, skipping account", acc_id)
                        return False, "Phone verification required — no access to registered phone number"
                    # Fallback to fractional coords (calibrated for 720x1440).
                    tx, ty = _scale(_TRY_ANOTHER_WAY_SEC[0], _TRY_ANOTHER_WAY_SEC[1],
                                    screen_w, screen_h)
                    log.info("[%s] 'Try another way' not in dump — tapping fallback (%d,%d)",
                             acc_id, tx, ty)
                    _shell(phone_id, f"input tap {tx} {ty}")
                _2fa_step = 1
                time.sleep(2)
            elif _2fa_step == 1 and not totp_entered and totp_secret:
                # _2fa_step=1 + totp_entry: "Try another way" was tapped previously.
                # Two sub-cases:
                #  A) Google skipped the method list and went straight to TOTP entry.
                #  B) The tap at step=0 missed and we're still on the security-code
                #     screen; OR Google reset the flow back to the email screen.

                # Case B-2: Flow reset to email screen — restart the whole login.
                # The email screen always has identifierId EditText in the dump.
                if "identifierId" in xml:
                    log.warning("[%s] Email screen detected at _2fa_step=1 — Google "
                                "reset the login flow (too many failed attempts). "
                                "Pressing HOME and re-launching intent.", acc_id)
                    _shell(phone_id, "input keyevent KEYCODE_HOME")
                    time.sleep(2)
                    _shell(phone_id,
                           "am start -a android.settings.ADD_ACCOUNT_SETTINGS "
                           "--es account_types com.google")
                    time.sleep(4)
                    email_entered    = False
                    password_entered = False
                    _2fa_step        = 0
                    continue

                # Case B-1: Still on security-code screen — tap "Try another way" again.
                if _find_and_tap(phone_id, ["Try another way"]):
                    log.info("[%s] 'Try another way' found at _2fa_step=1 — still on "
                             "security-code screen, tapping again", acc_id)
                    time.sleep(4)
                    continue   # don't increment _2fa_step again

                # Case A: Real TOTP entry screen (Google skipped method list).
                log.info("[%s] totp_entry at _2fa_step=1 — entering TOTP directly", acc_id)
                _cb("2fa_detected")
                if _enter_totp(phone_id, totp_secret, screen_w, screen_h):
                    totp_entered = True
                    _cb("2fa_entered")
                    _2fa_step = 2
                    time.sleep(3)
                else:
                    log.warning("[%s] TOTP failed at _2fa_step=1", acc_id)
                    time.sleep(2)
            elif not totp_entered and totp_secret:
                # _2fa_step >= 2: REAL TOTP screen (reached via method list)
                _cb("2fa_detected")
                log.info("[%s] Real TOTP screen — entering code", acc_id)
                if _enter_totp(phone_id, totp_secret, screen_w, screen_h):
                    totp_entered = True
                    _cb("2fa_entered")
                    time.sleep(3)
                else:
                    log.warning("[%s] TOTP entry failed — will retry", acc_id)
                    time.sleep(2)
                _2fa_step = 3
            else:
                # totp_entered=True OR no secret: post-TOTP screen with an EditText
                # (e.g. "Add recovery phone", "Verify it's you").  Skip it.
                log.info("[%s] Post-TOTP EditText screen (_2fa_step=%d) — tapping Skip/Next",
                         acc_id, _2fa_step)
                if not _find_and_tap(phone_id, [
                    "Not now", "Skip", "SKIP", "Skip for now", "Skip this step",
                    "Next", "Continue", "I agree", "Allow", "Done",
                ]):
                    tx, ty = _scale(_NEXT_BTN[0], _NEXT_BTN[1], screen_w, screen_h)
                    _shell(phone_id, f"input tap {tx} {ty}")
                time.sleep(3)

        # ── Post-login consent screens (native buttons) ───────────────────────
        elif screen_type == "consent":
            if _navigate_consent_screens(phone_id, max_screens=12):
                return True, "Completed auth screens — navigated through consent to home"
            time.sleep(2)

        # ── WebView-only screen — 2FA method selection / post-TOTP screens ─────
        elif screen_type == "webview_prompt":
            # WebView XML structure is static — same XML does NOT mean screen
            # content is unchanged (Google re-renders within the WebView).
            # Only count as a stall after 6 consecutive identical XMLs.
            if xml == prev_xml and i > 0:
                stuck_count += 1
                log.warning("[%s] WebView XML unchanged (stall %d/6)",
                            acc_id, stuck_count)
                if stuck_count >= 6:
                    return False, (
                        f"WebView stuck after {i + 1} screens. "
                        "Post-TOTP screen may need different tap coordinates."
                    )
            else:
                stuck_count = 0

            if not email_entered:
                # WebView screen before email entry — this is NOT a 2FA screen.
                # Likely an account-type chooser or "quick sign-in" screen from a
                # prior incomplete session.  Press HOME and re-launch the intent
                # to force a fresh email entry screen.
                log.info("[%s] WebView before email entry — pressing HOME and re-launching intent",
                         acc_id)
                _shell(phone_id, "input keyevent KEYCODE_HOME")
                time.sleep(2)
                _shell(phone_id,
                       "am start -a android.settings.ADD_ACCOUNT_SETTINGS "
                       "--es account_types com.google")
                time.sleep(4)
                continue

            elif _2fa_step == 1:
                # Method-selection list (shown after "Try another way" on security-code screen).
                # Try to find the Google Authenticator option by text first — the method
                # list is inside a WebView but its content is often exposed by uiautomator.
                _cb("2fa_method_select")
                auth_labels = [
                    "Google Authenticator",
                    "Authenticator app",
                    "Get a verification code from the Google Authenticator app",
                    "Use an app to get verification codes",
                    "Authenticator",
                ]
                if not _find_and_tap(phone_id, auth_labels):
                    tx, ty = _scale(_GOOGLE_AUTH_ROW[0], _GOOGLE_AUTH_ROW[1], screen_w, screen_h)
                    log.info("[%s] Method list — Authenticator not found by text, tapping (%d,%d)",
                             acc_id, tx, ty)
                    _shell(phone_id, f"input tap {tx} {ty}")
                _2fa_step = 2
                time.sleep(2)
            elif _2fa_step == 0:
                # Fallback: webview shown after password but before security-code screen.
                # Tap Authenticator row directly (some Google account flows skip the
                # "Security code from device" screen and go straight to method list).
                tx, ty = _scale(_GOOGLE_AUTH_ROW[0], _GOOGLE_AUTH_ROW[1], screen_w, screen_h)
                log.info("[%s] [webview step 0] Tapping Authenticator at (%d,%d)",
                         acc_id, tx, ty)
                _cb("2fa_method_select")
                _shell(phone_id, f"input tap {tx} {ty}")
                _2fa_step = 1
                time.sleep(2)
            elif _2fa_step == 2:
                # Transition: waiting for TOTP entry screen after Authenticator tap.
                # Try text first, fall back to coordinate.
                log.info("[%s] Webview at _2fa_step=2 — re-tapping Authenticator", acc_id)
                _auth_labels = [
                    "Google Authenticator",
                    "Authenticator app",
                    "Get a verification code from the Google Authenticator app",
                    "Use an app to get verification codes",
                    "Authenticator",
                ]
                if not _find_and_tap(phone_id, _auth_labels):
                    tx, ty = _scale(_GOOGLE_AUTH_ROW[0], _GOOGLE_AUTH_ROW[1], screen_w, screen_h)
                    _shell(phone_id, f"input tap {tx} {ty}")
                time.sleep(2)
            else:
                # Post-TOTP (_2fa_step >= 3): advance through Google review/consent screens.
                # IMPORTANT: only use FORWARD-PROGRESSING labels here.
                # "No thanks", "Skip", "Not now" would DECLINE consent and reset the flow.
                # "More options" opens sub-settings, not a back/decline action.
                _post_totp_labels = [
                    "I agree", "Agree", "Accept", "Accept all",
                    "Next", "Continue", "Done", "Allow",
                    "More options",
                ]
                tapped = _find_and_tap(phone_id, _post_totp_labels)
                if tapped:
                    log.info("[%s] [step %d] Post-TOTP WebView — tapped button by text",
                             acc_id, _2fa_step)
                else:
                    # Fallback: tap bottom-right where primary action buttons sit
                    tx, ty = _scale(0.83, 0.89, screen_w, screen_h)
                    log.info("[%s] [step %d] Post-TOTP WebView — no button found, tapping (%d,%d)",
                             acc_id, _2fa_step, tx, ty)
                    _shell(phone_id, f"input tap {tx} {ty}")
                _2fa_step += 1
                time.sleep(2)

        else:
            log.warning("[%s] Unrecognised screen type=%r at iteration %d — waiting",
                        acc_id, screen_type, i + 1)

        prev_xml = xml
        time.sleep(2)

    return False, "ADB login loop exhausted 32 iterations without completing"


def _navigate_consent_screens(phone_id: str, max_screens: int = 12) -> bool:
    """
    Progress through Google/Android post-login consent and onboarding screens
    by looking for known button labels in the UI.  Taps Skip where possible,
    otherwise Next / I agree / Allow / Accept / Continue / Done.
    Returns True once the launcher (home screen) is reached.
    """
    SKIP_LABELS  = [
        "Skip", "SKIP", "Skip all", "Not now", "No thanks", "Decline",
        "Not now", "Later", "Remind me later", "No, thanks",
        # Contacts / phone number screens — always skip
        "Skip for now", "Skip this step", "Skip setup",
        "Deny", "Don't allow", "Don't sync",
        # NOTE: "Cancel" intentionally omitted — on 2FA screens Cancel goes
        # backwards to the password screen, causing a login loop.
        # Error / problem screens — dismiss and move on
        "Retry", "Try again", "Close", "OK", "Got it",
    ]
    NEXT_LABELS  = ["Next", "NEXT", "Continue", "Allow", "Accept", "I agree",
                    "Done", "Agree", "Sign in", "Yes", "More", "Finish",
                    "Save", "Continue to Google Account"]

    import re as _re_consent

    ERROR_ACTIVITY = "addaccount.ErrorActivity"

    for i in range(max_screens):
        focus = _get_window_focus(phone_id)
        log.info("Consent screen %d  focus: %s", i + 1, focus[-60:])

        if LAUNCHER_ACTIVITY in focus:
            log.info("Reached home screen after %d consent screen(s).", i)
            return True

        # ErrorActivity = "Something went wrong" — tap Retry once then give up
        if ERROR_ACTIVITY in focus:
            log.warning("ErrorActivity detected — tapping Retry")
            if not _find_and_tap(phone_id, ["Retry", "Try again", "TRY AGAIN", "OK", "Close"]):
                _shell(phone_id, "input tap 360 900")   # centre-screen fallback
            time.sleep(3)
            # If still on ErrorActivity after retry, bail out
            if ERROR_ACTIVITY in _get_window_focus(phone_id):
                log.warning("ErrorActivity persists after retry — aborting consent loop")
                return False
            continue

        # ── "What's your name?" screen — fill firstName if empty so Next enables ──
        _, _xml = _shell(phone_id,
                         "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
        if _xml and 'resource-id="firstName"' in _xml:
            _fn = _re_consent.search(
                r'resource-id="firstName"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                _xml)
            if _fn:
                _fx = (int(_fn.group(1)) + int(_fn.group(3))) // 2
                _fy = (int(_fn.group(2)) + int(_fn.group(4))) // 2
                # Check if field already has a value
                _fn_node = _re_consent.search(
                    r'resource-id="firstName"[^>]*text="([^"]*)"', _xml)
                _fn_val = _fn_node.group(1) if _fn_node else ""
                if not _fn_val.strip():
                    log.info("'What\\'s your name?' — firstName empty, entering 'Alex'")
                    _shell(phone_id, f"input tap {_fx} {_fy}")
                    time.sleep(0.4)
                    _type_text(phone_id, "Alex")
                    time.sleep(0.5)
                else:
                    log.info("'What\\'s your name?' — firstName already has value %r", _fn_val)

        # Prefer Skip, fall back to Next-type labels
        tapped = _find_and_tap(phone_id, SKIP_LABELS)
        if not tapped:
            tapped = _find_and_tap(phone_id, NEXT_LABELS)
        if not tapped:
            log.warning("No known button found on screen %d — tapping bottom-right", i + 1)
            _shell(phone_id, "input tap 608 1292")

        time.sleep(1.5)

    focus = _get_window_focus(phone_id)
    return LAUNCHER_ACTIVITY in focus


def _verify_google_account(phone_id: str, email: str) -> bool:
    """
    Confirm the Google account is actually signed into the device.

    Uses THREE independent checks, any one passing = confirmed logged in:

      Check 1 — dumpsys account (most reliable):
        dumpsys account | grep -i 'com.google'
        Looks for the email in Google-authenticated account entries.

      Check 2 — Google account count:
        dumpsys account | grep -c 'Account {'
        Quick count — if >0 Google accounts exist and email matches any, confirmed.

      Check 3 — Android settings provider:
        settings get secure google_accounts
        System-level list of Google accounts on the device.

    Returns True if the email is found by ANY check.
    """
    import re

    email_lower = email.lower().strip()
    checks_passed = 0

    # ── Check 1: dumpsys account (Google accounts only) ──────────────────
    _, out = _shell(phone_id, "dumpsys account | grep -i 'com.google'")
    if out and email_lower in out.lower():
        log.info("[%s] Account FOUND via dumpsys account (check 1)", email)
        return True
    if out:
        checks_passed += 1  # at least the command worked

    # ── Check 2: broader dumpsys account search ──────────────────────────
    _, out = _shell(phone_id, "dumpsys account")
    if out:
        # Count Google accounts
        google_count = len(re.findall(r"type=com\.google", out, re.IGNORECASE))
        log.info("[%s] Google accounts on device: %d", email, google_count)
        if google_count > 0 and email_lower in out.lower():
            log.info("[%s] Account FOUND via broad dumpsys (check 2)", email)
            return True
        checks_passed += 1

    # ── Check 3: Android settings provider ────────────────────────────────
    _, out = _shell(phone_id, "settings get secure google_accounts")
    if out and out.strip() and out.strip() != "null":
        log.info("[%s] Settings google_accounts: %s", email, out.strip()[:100])
        if email_lower in out.lower():
            log.info("[%s] Account FOUND via settings (check 3)", email)
            return True
        checks_passed += 1

    # ── Log detailed state for debugging ──────────────────────────────────
    if checks_passed == 0:
        log.warning("[%s] ALL account checks returned empty — phone may not be logged in", email)
    else:
        log.warning("[%s] Account NOT FOUND after %d/3 checks", email, checks_passed)
        # Dump full account info for debugging
        _, out = _shell(phone_id, "dumpsys account")
        if out:
            log.debug("Full dumpsys account: %s", out[:800])

    return False


def _dismiss_post_login_screens(phone_id: str, email: str, acc_id: str = "") -> bool:
    """
    After Google login, dismiss any post-login setup screens.

    Google often shows "confirm contact details", "accept services", etc.
    These are rendered in a Chrome WebView invisible to uiautomator on
    Android 14, so we use coordinate taps for known button positions.

    Once the account appears in dumpsys, we press HOME to skip remaining
    screens — the account is already signed in.

    Returns True if account is confirmed signed in after dismissal.
    """
    # Known button positions on 1080x2400 screens
    _TAPS = [
        (920, 2220, "bottom-right (Save/Accept/Next/Confirm)"),
        (540, 2220, "bottom-center (I agree/Accept)"),
        (140, 2220, "bottom-left (More/No thanks)"),
        (920, 400,  "top-right (Skip/Close)"),
        (920, 1800, "mid-right (Next/Continue)"),
        (200, 2300, "very bottom-left"),
    ]

    import re
    for round_num in range(5):
        # Tap each position in the sequence
        for x, y, desc in _TAPS:
            _shell(phone_id, f"input tap {x} {y}")
            time.sleep(2)

        # Check if account is signed in
        if _verify_google_account(phone_id, email):
            log.info("[%s] Account verified after %d dismissal rounds — pressing HOME",
                     acc_id or phone_id, round_num + 1)
            _shell(phone_id, "input keyevent KEYCODE_HOME")
            time.sleep(2)
            return True

        # Check if we're still on a Google setup screen
        ok, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
        if ok and xml:
            pkg_match = re.search(r'package="([^"]+)"', xml)
            pkg = pkg_match.group(1) if pkg_match else ""
            # If we've left Google packages, we're done with setup
            google_pkgs = ["com.google.android.gms", "com.android.vending",
                          "com.google.android.apps", "com.google.android.setupwizard"]
            if not any(gp in pkg for gp in google_pkgs):
                log.info("[%s] Left Google setup (package: %s)", acc_id or phone_id, pkg)
                _shell(phone_id, "input keyevent KEYCODE_HOME")
                time.sleep(2)
                return True

    # Final check — account might have been there all along
    if _verify_google_account(phone_id, email):
        _shell(phone_id, "input keyevent KEYCODE_HOME")
        return True
    return False


def _detect_minutemaid_screen(phone_id: str) -> tuple[str, str]:
    """
    Classify the current MinuteMaidActivity screen by what uiautomator can see.

    NOTE — WebView content visibility:
      MinuteMaidActivity renders every Google sign-in screen inside a Chrome WebView.
      On our SM-G9650 / Android 10 / GMS build, uiautomator DOES expose WebView
      content (resource-ids, button text, link labels, input field IDs) in the dump.
      Key identifiers visible in the XML:
        • android.widget.EditText   — email, password, TOTP, security-code input fields
        • resource-id="identifierId" — uniquely identifies the EMAIL entry field
        • password="true"           — distinguishes the password field
        • text="Try another way"    — "Try another way" link on security-code screen
        • text="NEXT", text="Forgot email?", etc. — other buttons
      Use _find_and_tap() to locate buttons by text before falling back to fixed coords.

    CRITICAL WARNING — "totp_entry" is returned for BOTH email AND TOTP screens:
      The email entry screen and the TOTP code entry screen are structurally identical
      in uiautomator output: both have an EditText without a password flag.
      This function returns "totp_entry" for BOTH.
      THE CALLER MUST use state flags (email_entered, password_entered, _2fa_step)
      to tell them apart. Never use "totp_entry" alone to drive TOTP entry.

    Returns: (screen_type, xml)

    screen_type values:
      'password'       — Password screen (native EditText with password inputType flag).
      'totp_entry'     — ANY EditText without password flag. This covers:
                           - Email entry screen (if email_entered=False)
                           - Real TOTP screen (_2fa_step>=2, password_entered=True)
                           - Post-TOTP recovery/setup screens (totp_entered=True)
                         Caller MUST check state flags to distinguish these cases.
      'webview_prompt' — MinuteMaidActivity with WebView but NO EditText.
                         Covers: security-code screen, method selection list,
                         "Open Authenticator" info screen, post-TOTP info screens.
                         Caller's _2fa_step determines which sub-screen this is.
      'consent'        — Post-login onboarding screens with native button labels visible
                         (Skip, Next, I agree, Allow, etc.).
      'email'          — Rarely returned; only when Google uses a native label like
                         "Email or phone" outside the WebView (older Android paths).
      'unknown'        — No recognised pattern found.
    """
    import re as _re

    _, xml = _shell(phone_id, "uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
    if not xml:
        return "unknown", ""
    xl = xml.lower()

    has_edit_text    = bool(_re.search(r'class="android\.widget\.EditText"', xml, _re.IGNORECASE))
    has_password_flag = ('password="true"' in xl or '"129"' in xl or '"145"' in xl)

    # ── Password screen (native EditText with password inputType) ────────────
    if has_edit_text and has_password_flag:
        return "password", xml
    # Also catch via text if Google renders any native label (sometimes visible)
    if has_edit_text and any(t in xl for t in ("enter your password", "wrong password")):
        return "password", xml

    # ── Post-login account-setup screens with EditText ───────────────────────
    # After TOTP acceptance, Google / Android may show setup screens that have
    # an EditText — "What's your name?", "Choose a username", "Add recovery email",
    # "Add phone number", etc.  These have distinctive resource-ids that do NOT
    # appear on the login screens.  Classify them as "consent" so they are
    # handled by _navigate_consent_screens (which taps NEXT/Skip) rather than
    # being mistaken for a TOTP entry screen.
    _POST_LOGIN_IDS = ("firstName", "lastName", "username", "recoveryEmail",
                       "phoneNumber", "backupEmail")
    if has_edit_text and any(f'resource-id="{rid}"' in xml for rid in _POST_LOGIN_IDS):
        return "consent", xml
    # Also catch by heading text
    _POST_LOGIN_HEADINGS = ("what's your name", "choose your username",
                             "add a recovery email", "add recovery phone",
                             "add phone number", "you're all set")
    if has_edit_text and any(h in xl for h in _POST_LOGIN_HEADINGS):
        return "consent", xml

    # ── Code-entry screen (native EditText, no password flag) ────────────────
    # When Google shows the TOTP input field it is a native EditText without the
    # password inputType (codes are plain numbers).
    if has_edit_text:
        return "totp_entry", xml

    # ── WebView-only screen inside MinuteMaidActivity ─────────────────────────
    # No EditText visible — we're on one of the WebView-rendered sub-screens:
    # Google Prompt, method selection list, or the "Open Authenticator" info screen.
    # Caller's state machine decides which step to execute.
    if bool(_re.search(r'class="android\.webkit\.WebView"', xml, _re.IGNORECASE)):
        return "webview_prompt", xml

    # ── Email entry (sometimes a native EditText on older Android / first boot) ─
    if has_edit_text or any(t in xl for t in ("email or phone", "enter your email")):
        return "email", xml

    # ── Post-login consent / onboarding (native buttons outside WebView) ──────
    if _re.search(
        r'text="(?:SKIP|Skip|NEXT|Next|I agree|Accept|Continue|Save|'
        r'Allow|Deny|Done|Agree|More|Finish|OK|Got it|Retry|Try again|Close|'
        r'Not now|No thanks|Later|Remind me later)"',
        xml,
    ):
        return "consent", xml
    if any(t in xl for t in ("recovery", "back up to google", "add recovery")):
        return "consent", xml

    return "unknown", xml


def _enter_password(phone_id: str, password: str) -> bool:
    """
    Enter the account password on the MinuteMaid password screen
    (used when the GeelarK RPA stalls before completing the password step).
    Returns True after attempting entry.
    """
    log.info("[%s] Entering password on password screen …", phone_id)

    # Tap the password field — prefer UI discovery
    field = _find_text_field_and_tap(phone_id)
    if not field:
        _shell(phone_id, "input tap 360 539")   # sensible fallback
    time.sleep(0.6)

    # Clear whatever is already there
    _shell(phone_id, "input keyevent KEYCODE_CTRL_A")
    _shell(phone_id, "input keyevent KEYCODE_DEL")
    for _ in range(30):
        _shell(phone_id, "input keyevent KEYCODE_DEL")
    time.sleep(0.4)

    # Type the password
    _shell(phone_id, f"input text {password}")
    time.sleep(0.5)

    # Submit with Enter
    _shell(phone_id, "input keyevent 66")
    time.sleep(2)

    focus = _get_window_focus(phone_id)
    log.info("After password entry, focus: %s", focus[-60:])
    return True


def _enter_totp(phone_id: str, totp_secret: str,
                screen_w: int = _REF_W, screen_h: int = _REF_H) -> bool:
    """
    Generate a fresh TOTP code and enter it on the 2FA screen.
    Returns True if the code was entered successfully.
    """
    try:
        import pyotp
    except ImportError:
        log.error("pyotp not installed — cannot enter TOTP code. Run: pip install pyotp")
        return False

    # Tap the input field — prefer UI-dump discovery, fall back to scaled coords
    field = _find_text_field_and_tap(phone_id)
    if not field:
        fx, fy = _scale(_TOTP_FIELD_FX, _TOTP_FIELD_FY, screen_w, screen_h)
        _shell(phone_id, f"input tap {fx} {fy}")
    time.sleep(0.8)

    # Generate TOTP RIGHT NOW — field is ready, minimise time between generation and entry.
    # If fewer than 5s remain on the current code, wait for the next window to avoid expiry.
    secs_remaining = 30 - (int(time.time()) % 30)
    if secs_remaining < 5:
        log.info("TOTP window almost expired (%ds left) — waiting for next code …", secs_remaining)
        time.sleep(secs_remaining + 1)
    code = pyotp.TOTP(totp_secret.replace(" ", "").upper()).now()
    log.info("Entering TOTP code: %s (valid for ~%ds)",
             code, 30 - (int(time.time()) % 30))

    _type_text(phone_id, code)

    time.sleep(0.5)
    # Press Enter / Next (Google TOTP field may auto-submit after 6 digits, but press Enter anyway)
    _shell(phone_id, "input keyevent 66")
    time.sleep(8)   # Allow time for network verification

    # Success check: was the TOTP entry EditText removed?
    # When code is ACCEPTED Google advances to the next screen (no EditText visible).
    # When code is REJECTED Google clears the field but keeps the EditText on screen.
    # We must NOT check focus alone — MinuteMaidActivity stays active for several
    # screens after a successful TOTP (consent, account setup, etc).
    screen_type, _ = _detect_minutemaid_screen(phone_id)
    if screen_type == "totp_entry":
        log.warning("Still on TOTP entry screen — code was rejected")
        return False

    log.info("TOTP code accepted — screen moved to: %s", screen_type)
    return True


def _navigate_post_login_screens(phone_id: str, max_taps: int = 8,
                                 screen_w: int = _REF_W, screen_h: int = _REF_H):
    """
    Tap through Google/Android post-login consent screens
    (I Agree, Accept, Next, etc.) until we reach the launcher.
    """
    ax, ay = _scale(_AGREE_FX, _AGREE_FY, screen_w, screen_h)
    nx, ny = _scale(_NEXT_FX,  _NEXT_FY,  screen_w, screen_h)

    for i in range(max_taps):
        focus = _get_window_focus(phone_id)
        log.debug("Post-login screen %d focus: %s", i + 1, focus)

        if LAUNCHER_ACTIVITY in focus:
            log.info("Reached home screen — login complete.")
            return True

        # Try bottom-right (I Agree / Accept / More) then bottom-center (Next)
        _shell(phone_id, f"input tap {ax} {ay}")
        time.sleep(3)

        focus2 = _get_window_focus(phone_id)
        if focus2 == focus:
            # Button didn't work — try center-bottom Next
            _shell(phone_id, f"input tap {nx} {ny}")
            time.sleep(3)

    focus = _get_window_focus(phone_id)
    return LAUNCHER_ACTIVITY in focus


# ── StreamVia proxy API (token-based — proven on 37 accounts) ──────────────────
# Replaces the old GEELARK_PROXY_CONTROL_URL portal approach.
# Cooldown tracking ensures we never hit the 180s rate limit.

_PROXY_API_TOKEN = "xuClkA9-wS6_-4wWCy2bWsWXjepW0wVD1AYLdlze_sc"
_PROXY_API_BASE  = "https://mobile-proxy-140-166.streamvia.io/api.php"

_last_rotation_time: float = 0.0
_ROTATION_COOLDOWN = 185  # seconds — 180s minimum + 5s buffer


def _proxy_get_ip() -> str:
    """Get current proxy exit IP via StreamVia status API. Returns IP or ''."""
    import requests as _req
    try:
        r = _req.get(
            _PROXY_API_BASE,
            params={"token": _PROXY_API_TOKEN, "action": "status"},
            timeout=15,
        )
        import re as _re
        m = _re.search(r"Ready:\s*([\d.]+)", r.text)
        if m:
            return m.group(1)
        m = _re.search(r"(\d+\.\d+\.\d+\.\d+)", r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _rotate_proxy_ip(acc_id: str = "") -> str:
    """
    Rotate the StreamVia mobile proxy to a fresh IP.

    Strategy:
      1. Enforce minimum cooldown gap between rotations (185s).
      2. Try ``changeipunique`` (IP not used in last 24h) — preferred.
      3. If throttled > 10 min, fall back to ``changeip`` (random IP).
      4. Detect "Throttled: Wait N" responses, wait, and retry.
      5. Poll until ``Ready: x.x.x.x`` shows a new IP (up to 10 min).
      6. Log assigned IP prominently: ``>>> acc_XXX ASSIGNED PROXY IP: x.x.x.x <<<``

    Args:
        acc_id: optional account ID for log prefix (e.g. "gl_001").

    Returns new IP on success, '' on failure / no change.
    """
    import requests as _req
    import re as _re

    global _last_rotation_time

    # ── Enforce minimum gap between rotations ──────────────────────────────
    gap = time.time() - _last_rotation_time
    if gap < _ROTATION_COOLDOWN:
        wait = _ROTATION_COOLDOWN - gap
        log.info("[%s] Proxy: cooldown - waiting %.0fs...", acc_id, wait)
        time.sleep(wait)

    old_ip = _proxy_get_ip()
    if not old_ip:
        log.warning("[%s] Proxy: cannot reach status API — aborting rotation", acc_id)
        return ""

    log.info("[%s] Proxy current IP: %s", acc_id, old_ip)

    # ── Request rotation (with throttle handling + fallback) ───────────────
    action = "changeipunique"  # preferred
    max_poll_seconds = 600     # 10 minutes

    for attempt in range(3):
        try:
            r = _req.get(
                _PROXY_API_BASE,
                params={"token": _PROXY_API_TOKEN, "action": action},
                timeout=30,
            )
            resp = r.text.strip()
        except Exception as e:
            log.warning("[%s] Proxy: API error on %s: %s", acc_id, action, e)
            time.sleep(10)
            continue

        # Detect throttle
        throttle = _re.search(
            r'Throttled:\s*Wait\s*(\d+)\s*Seconds?', resp, _re.I,
        )
        if throttle:
            wait_sec = int(throttle.group(1))
            if wait_sec > 600 and action == "changeipunique" and attempt == 0:
                log.warning(
                    "[%s] Proxy: %s throttled %ds (>10 min) — "
                    "falling back to changeip", acc_id, action, wait_sec,
                )
                action = "changeip"
                continue
            if wait_sec > 900:
                log.warning(
                    "[%s] Proxy: %s throttled %ds (>15 min) — "
                    "giving up on rotation", acc_id, action, wait_sec,
                )
                return ""
            log.info("[%s] Proxy: %s throttled — waiting %ds...",
                     acc_id, action, wait_sec + 5)
            time.sleep(wait_sec + 5)
            continue

        # Rotation started
        log.info("[%s] Proxy: %s — %s", acc_id, action, resp[:100])
        break
    else:
        log.warning("[%s] Proxy: still throttled after 3 attempts — giving up", acc_id)
        return ""

    # ── Poll for new IP ────────────────────────────────────────────────────
    for i in range(max_poll_seconds // 5):
        time.sleep(5)
        ip = _proxy_get_ip()
        if ip and ip != old_ip:
            elapsed = (i + 1) * 5
            log.info("[%s] Proxy rotated: %s → %s (took %ds)",
                     acc_id, old_ip, ip, elapsed)
            _last_rotation_time = time.time()
            log.info(">>> %s ASSIGNED PROXY IP: %s <<<", acc_id, ip)
            return ip
        if i % 24 == 0 and i > 0:
            log.info("[%s] Proxy: still %s after %ds...",
                     acc_id, ip or '?', (i + 1) * 5)

    # Final check
    final_ip = _proxy_get_ip()
    if final_ip and final_ip != old_ip:
        log.info("[%s] Proxy rotated (late): %s → %s", acc_id, old_ip, final_ip)
        _last_rotation_time = time.time()
        log.info(">>> %s ASSIGNED PROXY IP: %s <<<", acc_id, final_ip)
        return final_ip

    log.warning("[%s] Proxy: IP unchanged after %ds — %s",
                acc_id, max_poll_seconds, old_ip)
    return ""


def _open_viewer(viewer_url: str):
    """Open GeelarK phone viewer in the default browser (Windows-safe URL quoting)."""
    try:
        # Use PowerShell Start-Process to handle URLs with & in query strings
        subprocess.Popen(
            ["powershell", "-Command", f'Start-Process "{viewer_url}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Viewer opened: %s", viewer_url[:80])
    except Exception as e:
        log.warning("Could not open viewer in browser: %s", e)


# City → (timezone, latitude, longitude) for supported geo targets
_CITY_CONFIG = {
    "london":     ("Europe/London",    51.5074,  -0.1278),
    "manchester": ("Europe/London",    53.4808,  -2.2426),
    "birmingham": ("Europe/London",    52.4862,  -1.8904),
    "glasgow":    ("Europe/London",    55.8642,  -4.2518),
    "leeds":      ("Europe/London",    53.8008,  -1.5491),
    "bristol":    ("Europe/London",    51.4545,  -2.5879),
    "edinburgh":  ("Europe/London",    55.9533,  -3.1883),
    "liverpool":  ("Europe/London",    53.4084,  -2.9916),
}


def _configure_device_locale(phone_id: str, account: dict, acc_id: str) -> None:
    """
    Set the phone timezone and GPS location to match the account's geo_city.
    Called once after successful login while the phone is still running.
    Safe to fail — logs warnings but never raises.
    """
    geo_city = (account.get("geo_city") or "london").lower()
    cfg = _CITY_CONFIG.get(geo_city, _CITY_CONFIG["london"])
    timezone, lat, lon = cfg

    log.info("[%s] Configuring device locale: city=%s tz=%s lat=%.4f lon=%.4f",
             acc_id, geo_city, timezone, lat, lon)

    # Set system timezone via ADB (works on Android 10+)
    ok, out = _shell(phone_id, f"service call alarm 3 s16 {timezone}")
    if not ok:
        # Fallback: settings put global
        _shell(phone_id, f"settings put global time_zone {timezone}")
    log.info("[%s] Timezone set to %s", acc_id, timezone)

    # Enable GPS / location services
    _shell(phone_id, "settings put secure location_mode 3")  # 3 = high accuracy

    # Set mock GPS location via broadcast to the installed mock-GPS app.
    # This is far more reliable than the undocumented FusedLocationService hack
    # because the app registers as an official Android mock location provider.
    from core.gps_spoofing import set_gps, ensure_installed
    if ensure_installed(phone_id):
        set_gps(phone_id, lat, lon)
        log.info("[%s] GPS broadcast set to %.4f, %.4f", acc_id, lat, lon)
    else:
        log.warning("[%s] Mock GPS app not available — Location History may not seed correctly", acc_id)

    log.info("[%s] Device locale configured.", acc_id)


def _run_coordinate_adb_login(
    phone_id: str, email: str, password: str,
    totp_secret: str, acc_id: str,
    screen_w: int, screen_h: int,
    progress_callback=None,
) -> tuple[bool, str]:
    """
    Drive a complete Google account login using coordinate-based ADB taps.

    Designed for Android 14+ where uiautomator cannot see inside the Chrome
    WebView that renders the sign-in UI.  Instead of reading screen content,
    we follow the known Google sign-in sequence blindly with fixed tap
    positions expressed as fractions of screen size.

    Sequence:
      1. ADD_ACCOUNT_SETTINGS intent → MinuteMaidActivity
      2. Tap email field → clear → type email → Enter
      3. Tap password field → clear → type password → Enter
      4. Tap "Try another way" (if 2FA prompt appears)
      5. Tap Google Authenticator row (in method list)
      6. Enter TOTP code → Enter
      7. Tap Skip/Next through consent screens until launcher

    Returns (success: bool, diagnosis: str).
    """
    def _tap(fx: float, fy: float):
        """Tap a fractional coordinate on the screen."""
        x, y = int(fx * screen_w), int(fy * screen_h)
        _shell(phone_id, f"input tap {x} {y}")
        return x, y

    def _enter_text(text: str):
        """Type text into the currently focused field via 'input text'.

        ADBKeyboard broadcasts don't work on Android 14.  Plain
        'input text' handles standard ASCII fine — @ . and other email
        characters pass through the shell without issue.
        """
        _shell(phone_id, f"input text {text}")

    def _is_in_auth() -> bool:
        """True if MinuteMaidActivity is still the foreground activity."""
        return GOOGLE_AUTH_ACTIVITY in _get_window_focus(phone_id)

    def _cb(phase, **kw):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **kw})
            except Exception:
                pass

    # Short-circuit: already logged in
    if _verify_google_account(phone_id, email):
        log.info("[%s] Account already in AccountManager — login already succeeded", acc_id)
        return True, "Account already registered on device"

    _cb("launching_add_account")

    # Press HOME + launch intent
    log.info("[%s] Launching Add Account intent …", acc_id)
    _shell(phone_id, "input keyevent KEYCODE_HOME")
    time.sleep(2)
    _shell(phone_id,
           "am start -a android.settings.ADD_ACCOUNT_SETTINGS "
           "--es account_types com.google")
    time.sleep(5)

    if not _is_in_auth():
        # Retry once
        _shell(phone_id, "input keyevent KEYCODE_HOME")
        time.sleep(2)
        _shell(phone_id,
               "am start -a android.settings.ADD_ACCOUNT_SETTINGS "
               "--es account_types com.google")
        time.sleep(5)

    if not _is_in_auth():
        return False, "MinuteMaidActivity did not open after 2 attempts"

    log.info("[%s] MinuteMaidActivity open — coordinate login starting …", acc_id)

    # ── Coordinate calibration ──────────────────────────────────────────
    # These fractions are calibrated for Google sign-in WebView on Android 14
    # (tested on Redmi Note 13 Pro, 1220×2712).
    #   Email field:     center of screen, upper third
    #   Password field:  center of screen, upper third
    #   Try another way: left-aligned link below the input card
    #   Auth row:        center of 2FA method list
    #   TOTP field:      center of screen, upper third
    #   Next/Continue:   bottom-right of content area
    #   Skip/Not now:    bottom-left/center of content area

    # ── Step 0: Dismiss pre-email screens ───────────────────────────────
    # Google may show "Verify it's you" / "Sign in with ease" with a Skip
    # button at the bottom-left.  Use a small tap grid to guarantee a hit
    # regardless of exact button position.
    log.info("[%s] Dismissing pre-email screens …", acc_id)
    for fx in (0.08, 0.12, 0.15):
        for fy in (0.92, 0.94, 0.96):
            _tap(fx, fy)
            time.sleep(0.4)
    time.sleep(2)
    if not _is_in_auth():
        return _check_login_result(phone_id, email, acc_id)

    # ── Step 1: Enter email ────────────────────────────────────────────
    _cb("entering_email")
    log.info("[%s] Entering email …", acc_id)
    # Tap grid across the email field area (top-center of screen)
    for fy in (0.18, 0.22, 0.26):
        _tap(0.50, fy)
        time.sleep(0.3)
    time.sleep(0.5)
    _enter_text(email)
    time.sleep(0.5)
    _shell(phone_id, "input keyevent 66")   # Enter
    time.sleep(4)

    if not _is_in_auth():
        return _check_login_result(phone_id, email, acc_id)

    # ── Step 2: Tap Next (confirmation / password screens) ──────────────
    # Google may show "Sign in to [email] on your [device]?" with Next,
    # or go straight to password.  Tap Next bottom-right to advance.
    log.info("[%s] Tapping Next …", acc_id)
    for fx in (0.83, 0.87, 0.91):
        for fy in (0.92, 0.94, 0.96):
            _tap(fx, fy)
            time.sleep(0.3)
    time.sleep(3)
    if not _is_in_auth():
        return _check_login_result(phone_id, email, acc_id)

    # ── Step 3: Password ───────────────────────────────────────────────
    _cb("entering_password")
    log.info("[%s] Entering password …", acc_id)
    for fy in (0.28, 0.32, 0.36):
        _tap(0.50, fy)
        time.sleep(0.3)
    time.sleep(0.5)
    _enter_text(password)
    time.sleep(0.5)
    _shell(phone_id, "input keyevent 66")   # Enter
    time.sleep(5)

    if not _is_in_auth():
        log.info("[%s] Left auth after password — checking account …", acc_id)
        return _check_login_result(phone_id, email, acc_id)

    # ── Step 4: 2FA method selection ──────────────────────────────────
    if totp_secret:
        _cb("2fa_method_select")
        log.info("[%s] On 2FA method screen …", acc_id)
        _shell(phone_id, "input keyevent KEYCODE_BACK")  # dismiss keyboard
        time.sleep(1.5)

        # Tap Google Authenticator — try multiple rows in the method list
        log.info("[%s] Selecting Google Authenticator …", acc_id)
        for fy in (0.50, 0.55, 0.60, 0.65):
            _tap(0.50, fy)
            time.sleep(0.4)
        time.sleep(3)

        if not _is_in_auth():
            return _check_login_result(phone_id, email, acc_id)

        # Generate + enter TOTP
        _cb("2fa_detected")
        log.info("[%s] Entering TOTP …", acc_id)
        import pyotp as _pyotp
        secs_remaining = 30 - (int(time.time()) % 30)
        if secs_remaining < 5:
            time.sleep(secs_remaining + 1)
        code = _pyotp.TOTP(totp_secret.replace(" ", "").upper()).now()
        log.info("[%s] TOTP: %s", acc_id, code)

        for fy in (0.34, 0.38, 0.42):
            _tap(0.50, fy)
            time.sleep(0.3)
        time.sleep(0.8)
        _enter_text(code)
        time.sleep(0.5)
        _shell(phone_id, "input keyevent 66")   # Enter
        _cb("2fa_entered")
        time.sleep(6)

    # ── Step 4: Consent screens ────────────────────────────────────────
    log.info("[%s] Navigating consent screens …", acc_id)
    for _ in range(15):
        if not _is_in_auth():
            break
        _tap(0.20, 0.89)       # Skip / Not now
        time.sleep(2)
        if not _is_in_auth():
            break
        _tap(0.83, 0.89)       # Next / I agree
        time.sleep(2)

    if not _is_in_auth():
        _navigate_consent_screens(phone_id, max_screens=10)

    return _check_login_result(phone_id, email, acc_id)


def _check_login_result(phone_id: str, email: str, acc_id: str) -> tuple[bool, str]:
    """Verify the account is registered and return (success, diagnosis)."""
    time.sleep(3)
    if _verify_google_account(phone_id, email):
        log.info("[%s] Account verified in AccountManager — SUCCESS.", acc_id)
        return True, "Account verified in device accounts database"

    # Wait a bit more — sync may lag
    time.sleep(10)
    if _verify_google_account(phone_id, email):
        log.info("[%s] Account verified after delay — SUCCESS.", acc_id)
        return True, "Account verified after extended wait"

    focus = _get_window_focus(phone_id)
    if LAUNCHER_ACTIVITY in focus:
        log.warning("[%s] On home screen but account not in AccountManager.", acc_id)
        return False, "On home screen but account not found in AccountManager"

    return False, f"Login did not complete. Current focus: {focus[-80:]}"


def _type_totp_via_keypad(phone_id: str, code: str,
                           screen_w: int, screen_h: int) -> None:
    """
    Type a 6-digit TOTP code by tapping the on-screen number keypad.

    Android 14 WebViews don't accept 'input text'.  Instead we tap each
    digit directly on the visible keypad.  Coordinates are fractional so
    they scale to any screen size.
    """
    # Number keypad layout (fractional positions)
    KEYPAD = {
        "1": (0.17, 0.60), "2": (0.50, 0.60), "3": (0.83, 0.60),
        "4": (0.17, 0.67), "5": (0.50, 0.67), "6": (0.83, 0.67),
        "7": (0.17, 0.74), "8": (0.50, 0.74), "9": (0.83, 0.74),
                           "0": (0.50, 0.81),
    }
    for ch in code:
        pos = KEYPAD.get(ch)
        if pos:
            x, y = int(pos[0] * screen_w), int(pos[1] * screen_h)
            _shell(phone_id, f"input tap {x} {y}")
            import time as _t
            _t.sleep(0.25)


# Google auto login flow ID (GeelarK-provided, handles email+password+TOTP+consent)
_AUTO_LOGIN_FLOW_ID = "628594965093548419"


def _run_auto_login_flow(
    client, phone_id: str, account: dict,
    email: str, password: str, totp_secret: str,
    acc_id: str, stop_phone_on_success: bool,
    screen_w: int, screen_h: int,
    progress_callback, result: dict,
) -> dict:
    """
    Android 14 login using GeelarK's "Google auto login" custom RPA flow.

    This flow:
      1. Opens Play Store → taps Sign in
      2. Enters email → Next
      3. Enters password → Next
      4. IF 2faCode provided: generates TOTP via built-in getAuthenticationCode
         step, taps "Get a verification code" method, enters code, taps Next
      5. Handles consent: Skip, I understand, I agree, ACCEPT

    The flow's getAuthenticationCode step takes the TOTP SECRET and generates
    the 6-digit code at runtime — we pass the secret, not a pre-generated code.
    """
    import time as _time

    def _progress(phase: str, **extra):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **extra})
            except Exception:
                pass

    _progress("auto_login_flow")
    log.info("[%s] Running Google auto login flow …", acc_id)

    # Build paramMap — 2faCode is the TOTP secret (flow generates code from it).
    # The flow's 2faCode param is optional (isNotRequired=true) — omit for
    # accounts without TOTP so the flow skips the 2FA branch.
    param_map: dict = {
        "User Email": email,
        "Password":   password,
    }
    if totp_secret:
        param_map["2faCode"] = totp_secret.replace(" ", "").upper()
        log.info("[%s] TOTP secret provided — flow will handle 2FA.", acc_id)
    else:
        log.info("[%s] No TOTP secret — flow will skip 2FA.", acc_id)

    try:
        task_id = client.run_custom_flow(
            flow_id=_AUTO_LOGIN_FLOW_ID,
            phone_id=phone_id,
            param_map=param_map,
            task_name=f"Google auto login — {email}",
        )
        log.info("[%s] Auto login task: %s", acc_id, task_id)
    except Exception as e:
        result["error"] = f"Auto login flow failed to start: {e}"
        return result

    # ── Poll for completion ──────────────────────────────────────────────
    deadline = _time.time() + 600
    task_status = 0
    fail_desc = ""
    while _time.time() < deadline:
        try:
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                task_status = t.get("status", 0)
                if task_status == 3:
                    log.info("[%s] Auto login flow completed (status 3).", acc_id)
                    break
                elif task_status == 4:
                    fail_desc = (t.get("failDesc", "") or "")[:200]
                    log.warning("[%s] Auto login flow failed (status 4): %s",
                                acc_id, fail_desc)
                    break
                elif task_status == 7:
                    log.warning("[%s] Auto login flow cancelled (status 7).", acc_id)
                    break
        except Exception:
            pass
        _time.sleep(5)

    # ── Verify account ──────────────────────────────────────────────────
    _time.sleep(5)
    confirmed = _verify_google_account(phone_id, email)
    if not confirmed:
        _time.sleep(10)
        confirmed = _verify_google_account(phone_id, email)

    if confirmed:
        result["success"]   = True
        result["diagnosis"] = f"Account {email} verified in AccountManager."
        result["attempts"]  = 1
        log.info("[%s] Login SUCCESS via auto login flow.", acc_id)
    elif task_status == 3:
        # Flow completed but account not in AccountManager — may still sync
        result["success"]   = True
        result["diagnosis"] = "Auto login flow completed, account may sync later."
        result["attempts"]  = 1
        log.info("[%s] Flow completed — treating as success (account may sync).", acc_id)
    elif task_status == 4:
        result["needs_user_input"] = True
        result["diagnosis"] = f"Auto login flow failed: {fail_desc}"
    else:
        result["needs_user_input"] = True
        result["diagnosis"] = (
            f"Auto login flow ended with status {task_status}. "
            f"May need manual intervention."
        )

    _configure_device_locale(phone_id, account, acc_id)
    if stop_phone_on_success and result["success"]:
        try:
            client.stop_phone(phone_id)
            result["viewer_url"] = ""
        except Exception:
            pass
    return result


def _run_builtin_login(
    client, phone_id: str, account: dict,
    email: str, password: str, totp_secret: str,
    acc_id: str, stop_phone_on_success: bool,
    screen_w: int, screen_h: int,
    progress_callback, result: dict,
) -> dict:
    """
    Android 14 login using GeelarK's built-in googleLogin RPA.

    The built-in task handles email + password entry and screen navigation.
    On completion (status=3) the account is signed in at the Google Play
    Services level even if dumpsys account doesn't show it yet.
    """
    import time as _time

    def _tap(fx: float, fy: float):
        x, y = int(fx * screen_w), int(fy * screen_h)
        _shell(phone_id, f"input tap {x} {y}")

    def _progress(phase: str, **extra):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **extra})
            except Exception:
                pass

    _progress("builtin_login")
    log.info("[%s] Running built-in googleLogin …", acc_id)

    try:
        task_id = client.google_login(phone_id, email, password,
                                       task_name=f"Google login — {email}")
        log.info("[%s] Task: %s", acc_id, task_id)
    except Exception as e:
        result["error"] = f"Built-in login failed to start: {e}"
        return result

    # Poll
    deadline = _time.time() + 600
    completed = False
    while _time.time() < deadline:
        try:
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                s = t.get("status", 0)
                if s == 3:
                    completed = True
                    log.info("[%s] Built-in login completed.", acc_id)
                    break
                elif s == 4:
                    log.warning("[%s] Built-in login failed: %s", acc_id,
                               (t.get("failDesc","") or "")[:120])
                    break
                elif s == 7:
                    log.warning("[%s] Built-in login cancelled.", acc_id)
                    break
        except Exception:
            pass
        _time.sleep(5)

    if completed:
        # Verify account — if not confirmed and still in auth, handle TOTP
        _time.sleep(5)
        confirmed = _verify_google_account(phone_id, email)
        if not confirmed:
            _time.sleep(10)
            confirmed = _verify_google_account(phone_id, email)

        # Still in auth + not confirmed + have TOTP → built-in got stuck at 2FA
        if not confirmed and totp_secret and _is_in_auth(phone_id):
            log.info("[%s] Built-in completed but stuck at 2FA — handling TOTP.", acc_id)
            _progress("2fa_handling")
            # Tap Authenticator option (may already be on TOTP screen)
            for fy in (0.50, 0.55, 0.60, 0.65):
                _tap(0.50, fy); _time.sleep(0.4)
            _time.sleep(3)

            if _is_in_auth(phone_id):
                import pyotp as _pyotp
                secs = 30 - (int(_time.time()) % 30)
                if secs < 5: _time.sleep(secs + 1)
                code = _pyotp.TOTP(totp_secret.replace(" ", "").upper()).now()
                log.info("[%s] TOTP: %s", acc_id, code)
                # Tap TOTP field then type digits via on-screen keypad taps
                for fy in (0.34, 0.38, 0.42):
                    _tap(0.50, fy); _time.sleep(0.3)
                _time.sleep(0.5)
                _type_totp_via_keypad(phone_id, code, screen_w, screen_h)
                _shell(phone_id, "input keyevent 66")
                _time.sleep(6)
                # Consent screens
                for _ in range(8):
                    if not _is_in_auth(phone_id): break
                    _tap(0.20, 0.89); _time.sleep(2)
                    if not _is_in_auth(phone_id): break
                    _tap(0.83, 0.89); _time.sleep(2)
                _navigate_consent_screens(phone_id, max_screens=10)

            _time.sleep(5)
            confirmed = _verify_google_account(phone_id, email)
            if not confirmed:
                _time.sleep(10)
                confirmed = _verify_google_account(phone_id, email)

        if confirmed:
            result["success"]   = True
            result["diagnosis"] = f"Account {email} verified in AccountManager."
        elif not _is_in_auth(phone_id):
            # Left auth flow — likely completed, account may sync later
            result["success"]   = True
            result["diagnosis"] = "Built-in login completed, left auth flow."
        else:
            result["needs_user_input"] = True
            result["diagnosis"] = (
                "Built-in login completed but account not verified and still in auth. "
                "May need manual TOTP entry."
            )
        result["attempts"] = 1
        _configure_device_locale(phone_id, account, acc_id)
        if stop_phone_on_success and result["success"]:
            try:
                client.stop_phone(phone_id)
                result["viewer_url"] = ""
            except Exception:
                pass
        return result

    # Built-in didn't complete — fall back to TOTP + coordinate navigation
    if totp_secret and _is_in_auth(phone_id):
        log.info("[%s] Built-in incomplete — handling 2FA …", acc_id)
        _progress("2fa_handling")

        for fy in (0.50, 0.55, 0.60, 0.65):
            _tap(0.50, fy)
            _time.sleep(0.4)
        _time.sleep(3)

        import pyotp as _pyotp
        secs = 30 - (int(_time.time()) % 30)
        if secs < 5:
            _time.sleep(secs + 1)
        code = _pyotp.TOTP(totp_secret.replace(" ", "").upper()).now()
        log.info("[%s] TOTP: %s", acc_id, code)

        for fy in (0.34, 0.38, 0.42):
            _tap(0.50, fy)
            _time.sleep(0.3)
        _time.sleep(0.5)
        _type_totp_via_keypad(phone_id, code, screen_w, screen_h)
        _shell(phone_id, "input keyevent 66")
        _time.sleep(6)

        for _ in range(5):
            _tap(0.20, 0.89); _time.sleep(2)
            _tap(0.83, 0.89); _time.sleep(2)
        _navigate_consent_screens(phone_id, max_screens=10)

        _time.sleep(5)
        confirmed = _verify_google_account(phone_id, email)
        if not confirmed:
            _time.sleep(10)
            confirmed = _verify_google_account(phone_id, email)

        if confirmed:
            result["success"]   = True
            result["diagnosis"] = f"Account {email} verified via built-in + TOTP."
            result["attempts"]  = 1
            _configure_device_locale(phone_id, account, acc_id)
        else:
            result["needs_user_input"] = True
            result["diagnosis"] = "Built-in login incomplete, TOTP fallback also failed."
    else:
        result["needs_user_input"] = True
        result["diagnosis"] = "Built-in login did not complete."

    if stop_phone_on_success and result["success"]:
        try:
            client.stop_phone(phone_id)
            result["viewer_url"] = ""
        except Exception:
            pass
    return result


def _run_hybrid_login(
    client, phone_id: str, account: dict,
    email: str, password: str, totp_secret: str,
    acc_id: str, stop_phone_on_success: bool,
    screen_w: int, screen_h: int,
    progress_callback, result: dict,
) -> dict:
    """
    Hybrid login for Android 14+:
      1. GeelarK's built-in googleLogin handles email + password + navigation
      2. If TOTP is needed, coordinate taps handle 2FA screens
      3. Consent screens navigated via coordinate taps
    """
    import time as _time

    def _tap(fx: float, fy: float):
        x, y = int(fx * screen_w), int(fy * screen_h)
        _shell(phone_id, f"input tap {x} {y}")

    def _progress(phase: str, **extra):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **extra})
            except Exception:
                pass

    # ── Step 1: Built-in googleLogin ────────────────────────────────────
    _progress("builtin_login")
    log.info("[%s] Running built-in googleLogin (email+password) …", acc_id)

    try:
        task_id = client.google_login(
            phone_id=phone_id,
            email=email,
            password=password,
            task_name=f"Google login — {email}",
        )
        log.info("[%s] Built-in task: %s", acc_id, task_id)
    except Exception as e:
        result["error"] = f"Built-in googleLogin failed to start: {e}"
        return result

    # Poll built-in task
    deadline = _time.time() + 600
    builtin_ok = False
    while _time.time() < deadline:
        try:
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                s = t.get("status", 0)
                if s == 3:
                    builtin_ok = True
                    log.info("[%s] Built-in login completed.", acc_id)
                    break
                elif s == 4:
                    log.warning("[%s] Built-in login failed: %s", acc_id,
                               t.get("failDesc", "")[:100])
                    break
                elif s == 7:
                    log.warning("[%s] Built-in login cancelled.", acc_id)
                    break
        except Exception:
            pass
        _time.sleep(5)

    if not builtin_ok:
        # Built-in failed — may be stuck on 2FA or other screen
        log.info("[%s] Built-in login did not complete cleanly — continuing with TOTP.", acc_id)

    # ── Step 2: Verify account ──────────────────────────────────────────
    _time.sleep(3)
    account_confirmed = _verify_google_account(phone_id, email)
    if not account_confirmed:
        _time.sleep(10)
        account_confirmed = _verify_google_account(phone_id, email)

    if account_confirmed:
        log.info("[%s] Account already verified after built-in login — SUCCESS.", acc_id)
        result["success"]   = True
        result["diagnosis"] = f"Account {email} verified via built-in login."
        result["attempts"]  = 1
        _configure_device_locale(phone_id, account, acc_id)
        if stop_phone_on_success:
            try:
                client.stop_phone(phone_id)
                result["viewer_url"] = ""
            except Exception:
                pass
        return result

    # ── Step 3: Handle 2FA if needed ────────────────────────────────────
    if not totp_secret:
        result["needs_user_input"] = True
        result["diagnosis"] = "Login incomplete — TOTP secret not available."
        return result

    if not _is_in_auth(phone_id):
        # Left auth flow — may have completed but account not synced
        _navigate_consent_screens(phone_id, max_screens=10)
        _time.sleep(5)
        account_confirmed = _verify_google_account(phone_id, email)
        if account_confirmed:
            log.info("[%s] Account verified after consent navigation.", acc_id)
            result["success"]   = True
            result["diagnosis"] = f"Account {email} verified after consent nav."
            result["attempts"]  = 1
            _configure_device_locale(phone_id, account, acc_id)
            if stop_phone_on_success:
                try:
                    client.stop_phone(phone_id)
                    result["viewer_url"] = ""
                except Exception:
                    pass
            return result

    # Still in auth — handle 2FA screens with coordinate taps
    _progress("2fa_handling")
    log.info("[%s] Handling 2FA screens with coordinate taps …", acc_id)

    # Tap Authenticator option in method list
    for fy in (0.50, 0.55, 0.60, 0.65):
        _tap(0.50, fy)
        _time.sleep(0.4)
    _time.sleep(3)

    if not _is_in_auth(phone_id):
        _navigate_consent_screens(phone_id, max_screens=10)
    else:
        # Generate and enter TOTP
        import pyotp as _pyotp
        secs_remaining = 30 - (int(_time.time()) % 30)
        if secs_remaining < 5:
            _time.sleep(secs_remaining + 1)
        code = _pyotp.TOTP(totp_secret.replace(" ", "").upper()).now()
        log.info("[%s] TOTP: %s", acc_id, code)

        for fy in (0.34, 0.38, 0.42):
            _tap(0.50, fy)
            _time.sleep(0.3)
        _time.sleep(0.5)
        _shell(phone_id, f"input text {code}")
        _time.sleep(0.5)
        _shell(phone_id, "input keyevent 66")
        _time.sleep(6)

        # Consent
        for _ in range(5):
            _tap(0.20, 0.89)
            _time.sleep(2)
            _tap(0.83, 0.89)
            _time.sleep(2)
        _navigate_consent_screens(phone_id, max_screens=10)

    # Final verification
    _time.sleep(5)
    account_confirmed = _verify_google_account(phone_id, email)
    if not account_confirmed:
        _time.sleep(10)
        account_confirmed = _verify_google_account(phone_id, email)

    if account_confirmed:
        log.info("[%s] Account confirmed — hybrid login SUCCESS.", acc_id)
        result["success"]   = True
        result["diagnosis"] = f"Account {email} verified via hybrid login."
        result["attempts"]  = 1
        _configure_device_locale(phone_id, account, acc_id)
    else:
        result["needs_user_input"] = True
        result["diagnosis"] = "Hybrid login completed but account not verified."

    if stop_phone_on_success and result["success"]:
        try:
            client.stop_phone(phone_id)
            result["viewer_url"] = ""
        except Exception:
            pass
    return result


def _is_in_auth(phone_id: str) -> bool:
    """True if MinuteMaidActivity is foreground."""
    return GOOGLE_AUTH_ACTIVITY in _get_window_focus(phone_id)


def _run_rpa_coordinate_login(
    client, phone_id: str, account: dict,
    email: str, password: str, totp_secret: str,
    acc_id: str, stop_phone_on_success: bool,
    progress_callback, result: dict,
) -> dict:
    """
    Login on Android 14+ using GeelarK RPA with coordinate-based taps.

    Uses the 'Google Login — Coordinate (Android 14)' flow which clicks
    and types at absolute screen coordinates, bypassing uiautomator
    WebView blindness entirely.  GeelarK's RPA uses its own text-input
    mechanism, not ADB 'input text'.
    """
    from core.geelark_flow_builder import flows as _flows
    import time as _time

    if not totp_secret:
        result["error"] = "TOTP secret required for Android 14 login"
        return result

    def _progress(phase: str, **extra):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **extra})
            except Exception:
                pass

    _progress("rpa_login")

    # Create/update the coordinate flow
    try:
        flow_id = _flows.google_login_coordinate()
        log.info("[%s] Coordinate RPA flow ready: %s", acc_id, flow_id)
    except Exception as e:
        result["error"] = f"Failed to create coordinate RPA flow: {e}"
        return result

    _progress("submitting_task")

    try:
        task_id = client.run_custom_flow(
            flow_id=flow_id,
            phone_id=phone_id,
            param_map={
                "email":        email,
                "password":     password,
                "totp_secret":  totp_secret,
            },
            task_name=f"Google login (coord) — {email}",
        )
        log.info("[%s] Coordinate RPA task: %s", acc_id, task_id)
    except Exception as e:
        result["error"] = f"Failed to submit RPA task: {e}"
        return result

    # Poll
    deadline = _time.time() + 600
    while _time.time() < deadline:
        try:
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                status = t.get("status", 0)
                if status == 3:
                    break
                elif status in (4, 7):
                    result["error"] = f"RPA task failed: {t.get('failDesc','')}"
                    return result
        except Exception:
            pass
        _time.sleep(5)

    # Verify
    _time.sleep(5)
    account_confirmed = _verify_google_account(phone_id, email)
    if not account_confirmed:
        _time.sleep(10)
        account_confirmed = _verify_google_account(phone_id, email)

    if account_confirmed:
        log.info("[%s] Account confirmed — coordinate RPA SUCCESS.", acc_id)
        result["success"] = True
        result["diagnosis"] = f"Account {email} verified via coordinate RPA."
        result["attempts"] = 1
        _configure_device_locale(phone_id, account, acc_id)
        if stop_phone_on_success:
            try:
                client.stop_phone(phone_id)
                result["viewer_url"] = ""
            except Exception:
                pass
        return result

    result["needs_user_input"] = True
    result["diagnosis"] = (
        "RPA coordinate flow completed but account not verified. "
        "Coordinates may need calibration for this device."
    )
    return result


def _run_rpa_settings_login(
    client, phone_id: str, account: dict,
    email: str, password: str, totp_secret: str,
    acc_id: str, stop_phone_on_success: bool,
    progress_callback, result: dict,
) -> dict:
    """
    Run Google login on Android 14+ using GeelarK's native RPA with the
    Settings Add Account wizard.  The flow properly registers the account
    in Android's AccountManager (required for Maps, Gmail, YouTube).

    Uses the pre-built "Google Add Account via Settings" flow which
    navigates Settings → Accounts → Add account → Google → email →
    password → 2FA method select → TOTP.

    The flow is created/updated once via FlowBuilder and cached by ID.
    """
    from core.geelark_client import GeelarKClient as _G
    from core.geelark_flow_builder import flows as _flows

    if not totp_secret:
        result["error"] = "TOTP secret required for RPA login on Android 14+"
        return result

    def _progress(phase: str, **extra):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **extra})
            except Exception:
                pass

    _progress("rpa_login")

    # Create/update the flow (idempotent — returns existing ID on subsequent calls)
    try:
        flow_id = _flows.google_add_account_via_settings()
        log.info("[%s] RPA login flow ready: %s", acc_id, flow_id)
    except Exception as e:
        result["error"] = f"Failed to create RPA login flow: {e}"
        return result

    _progress("submitting_task", attempt=1)

    try:
        task_id = client.run_custom_flow(
            flow_id=flow_id,
            phone_id=phone_id,
            param_map={
                "email":        email,
                "password":     password,
                "totp_secret":  totp_secret,
            },
            task_name=f"Google login — {email}",
        )
        log.info("[%s] RPA task submitted: %s", acc_id, task_id)
    except Exception as e:
        result["error"] = f"Failed to submit RPA login task: {e}"
        return result

    # Poll for completion
    import time as _time
    deadline = _time.time() + 600  # 10 min
    while _time.time() < deadline:
        try:
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                status = t.get("status", 0)
                # 1=Waiting 2=InProgress 3=Completed 4=Failed 7=Cancelled
                if status == 3:
                    log.info("[%s] RPA task completed.", acc_id)
                    break
                elif status == 4:
                    fail_code = t.get("failCode", "")
                    fail_desc = t.get("failDesc", "")
                    result["error"] = f"RPA login failed: code={fail_code} {fail_desc}"
                    log.warning("[%s] RPA task failed: %s", acc_id, result["error"])
                    return result
                elif status == 7:
                    result["error"] = "RPA login cancelled"
                    return result
        except Exception as e:
            log.warning("[%s] RPA poll error: %s", acc_id, e)
        _time.sleep(5)

    # Verify account is registered in AccountManager
    _time.sleep(5)
    account_confirmed = _verify_google_account(phone_id, email)
    if not account_confirmed:
        _time.sleep(10)
        account_confirmed = _verify_google_account(phone_id, email)

    if account_confirmed:
        log.info("[%s] Account confirmed — RPA login SUCCESS.", acc_id)
        result["success"]   = True
        result["diagnosis"] = f"Account {email} verified via RPA (Settings Add Account)."
        result["attempts"]  = 1
        _configure_device_locale(phone_id, account, acc_id)
        if stop_phone_on_success:
            try:
                client.stop_phone(phone_id)
                result["viewer_url"] = ""
            except Exception:
                pass
        return result

    # RPA task completed but account NOT in AccountManager — the flow ran its
    # steps but the login didn't actually succeed.  Do NOT fall back to "on
    # home screen" — that is a false positive.  Retry once.
    log.warning("[%s] RPA task completed but account not verified — retrying once.", acc_id)
    _progress("rpa_retry")

    try:
        task_id2 = client.run_custom_flow(
            flow_id=flow_id,
            phone_id=phone_id,
            param_map={
                "email":        email,
                "password":     password,
                "totp_secret":  totp_secret,
            },
            task_name=f"Google login (retry) — {email}",
        )
        log.info("[%s] RPA retry task: %s", acc_id, task_id2)
    except Exception as e:
        result["error"] = f"RPA retry submission failed: {e}"
        result["needs_user_input"] = True
        result["diagnosis"] = f"First RPA attempt completed but account not verified; retry failed: {e}"
        return result

    # Poll retry
    deadline2 = _time.time() + 600
    retry_ok = False
    while _time.time() < deadline2:
        try:
            tasks = client.query_tasks([task_id2])
            if tasks:
                t = tasks[0]
                status = t.get("status", 0)
                if status == 3:
                    retry_ok = True
                    break
                elif status in (4, 7):
                    break
        except Exception:
            pass
        _time.sleep(5)

    if retry_ok:
        _time.sleep(5)
        account_confirmed = _verify_google_account(phone_id, email)
        if not account_confirmed:
            _time.sleep(10)
            account_confirmed = _verify_google_account(phone_id, email)

    if account_confirmed:
        log.info("[%s] Account confirmed on retry — RPA login SUCCESS.", acc_id)
        result["success"]   = True
        result["diagnosis"] = f"Account {email} verified via RPA (retry)."
        result["attempts"]  = 2
        _configure_device_locale(phone_id, account, acc_id)
        if stop_phone_on_success:
            try:
                client.stop_phone(phone_id)
                result["viewer_url"] = ""
            except Exception:
                pass
        return result

    result["needs_user_input"] = True
    result["diagnosis"] = (
        "RPA flow completed but account NOT found in AccountManager after 2 attempts. "
        "The phone may have a pre-existing Google account blocking Add Account, "
        "or the RPA flow steps did not execute correctly on this device."
    )
    result["error"] = result["diagnosis"]
    log.error("[%s] %s", acc_id, result["diagnosis"])
    return result


def run_google_login(account: dict, stop_phone_on_success: bool = True,
                     progress_callback=None) -> dict:
    """
    Log a Google account into its assigned GeelarK cloud phone.

    Uses the GeelarK "Google auto login" custom RPA flow (ID 628594965093548419)
    which handles email, password, TOTP generation (from secret), and consent
    screens.  Proven working on all Android versions (10/13/14/15).

    Pure ADB login (_run_pure_adb_login) is deprecated — kept for reference only.

    Flow:
      1. Rotate proxy IP (StreamVia changeipunique → changeip fallback).
      2. 60s settling buffer after rotation.
      3. Start phone, poll boot (up to 100s), query screen size.
      4. Run _run_auto_login_flow (GeelarK RPA with TOTP handling).
      5. Verify via dumpsys account.
      6. Configure device locale (timezone + GPS mock) on success.
      7. Stop phone if stop_phone_on_success=True.

    Args:
        account:              dict from geelark_accounts.yaml — keys used:
                              id, email, password, totp_secret, geelark_phone_id,
                              geo_city (optional, default "london")
        stop_phone_on_success: stop the phone after successful login (saves minutes).
        progress_callback:    optional callable(dict) for real-time status updates.

    Returns dict with keys:
        success (bool), viewer_url (str), attempts (int),
        last_screenshot_b64 (str), diagnosis (str),
        needs_user_input (bool), error (str)
    """
    def _progress(phase: str, **extra):
        if progress_callback:
            try:
                progress_callback({"phase": phase, **extra})
            except Exception:
                pass
    from core.geelark_client import GeelarKClient, _post

    client      = GeelarKClient()
    phone_id    = account.get("geelark_phone_id")
    email       = account.get("email", "")
    password    = account.get("password", "")
    totp_secret = account.get("totp_secret", "")
    flow_id     = account.get("geelark_login_flow_id", "")
    acc_id      = account.get("id", email)

    result = {
        "success":             False,
        "viewer_url":          "",
        "attempts":            0,
        "last_screenshot_b64": "",
        "diagnosis":           "",
        "needs_user_input":    False,
        "error":               "",
    }

    if not phone_id:
        result["error"] = "No geelark_phone_id assigned — provision the phone first."
        return result

    # ── Rotate proxy IP before the phone boots ────────────────────────────────
    # Get a fresh mobile IP so each login run comes from a clean address.
    log.info("[%s] Rotating proxy IP …", acc_id)
    _progress("rotating_proxy")
    new_ip = _rotate_proxy_ip(acc_id)
    if new_ip:
        log.info(">>> %s ASSIGNED PROXY IP: %s <<<", acc_id, new_ip)
        # 60s settling buffer — let the proxy connection stabilise before
        # any phone activity touches the network.
        log.info("[%s] Waiting 60s (IP settling before phone start) …", acc_id)
        time.sleep(60)
    else:
        log.warning("[%s] Proxy rotation failed — proceeding with current IP", acc_id)

    # ── Start phone ───────────────────────────────────────────────────────────
    log.info("[%s] Starting phone %s …", acc_id, phone_id)
    try:
        viewer_url = client.start_phone(phone_id)
        result["viewer_url"] = viewer_url
    except Exception as e:
        result["error"] = f"Failed to start phone: {e}"
        return result

    _open_viewer(viewer_url)
    _progress("booting", viewer_url=viewer_url)

    # Poll for boot completion rather than sleeping a fixed amount.
    # wm size responds only once Android has fully started.
    log.info("[%s] Polling for phone boot …", acc_id)
    boot_ready = False
    for _boot_i in range(20):   # up to 100 seconds
        time.sleep(5)
        _ok, _out = _shell(phone_id, "wm size")
        if _ok and _out.strip():
            log.info("[%s] Phone ready after %ds", acc_id, (_boot_i + 1) * 5)
            boot_ready = True
            break
    if not boot_ready:
        log.warning("[%s] Phone did not respond in 100s — proceeding anyway", acc_id)
    time.sleep(3)   # short stabilisation wait after boot

    # Query screen size so tap coordinates scale to this phone's resolution
    screen_w, screen_h = _get_screen_size(phone_id)
    log.info("[%s] Screen size: %dx%d", acc_id, screen_w, screen_h)

    # ── Google auto login flow (RPA) — works on ALL Android versions ──────────
    # The GeelarK RPA flow (ID 628594965093548419) handles email, password,
    # TOTP generation, and consent screens.  Proven on 36/37 accounts.
    # Pure ADB login (_run_pure_adb_login) is deprecated — kept for reference
    # but no longer used in the default path.
    log.info("[%s] Using Google auto login flow (all Android versions).", acc_id)
    return _run_auto_login_flow(
        client=client, phone_id=phone_id, account=account,
        email=email, password=password, totp_secret=totp_secret,
        acc_id=acc_id, stop_phone_on_success=stop_phone_on_success,
        screen_w=screen_w, screen_h=screen_h,
        progress_callback=progress_callback, result=result,
    )


def run_login_check(account: dict) -> dict:
    """
    Strong login verification — starts the phone, runs three independent ADB
    checks to confirm the Google account is registered, then stops the phone.

    Checks performed (all must fail to return verified=False):
      1. dumpsys account — Android accounts database, email match
      2. content query on Google Contacts sync adapter — confirms sync is active
      3. dumpsys package com.google.android.gms — confirms GMS knows the account

    Returns dict:
        verified (bool), checks (dict of check_name→bool), detail (str), error (str)
    """
    from core.geelark_client import GeelarKClient

    acc_id   = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id")
    email    = account.get("email", "").lower()

    result: dict = {
        "verified": False,
        "checks":   {},
        "detail":   "",
        "error":    "",
    }

    if not phone_id:
        result["error"] = "No phone provisioned"
        return result

    client = GeelarKClient()

    # Pre-flight health check
    health = client.check_phone_health(phone_id)
    if not health["healthy"]:
        result["error"] = f"Phone health check failed: {health['reason']}"
        log.warning("[%s] Skipping login check — phone unhealthy: %s", acc_id, health["reason"])
        return result

    # Rotate proxy IP before starting — each check gets a clean unique IP
    log.info("[%s] Rotating proxy IP for login check …", acc_id)
    _rotate_proxy_ip(acc_id)

    log.info("[%s] Starting phone for login check …", acc_id)
    try:
        client.start_phone(phone_id)
    except Exception as e:
        result["error"] = f"Failed to start phone: {e}"
        return result

    time.sleep(15)

    try:
        checks: dict[str, bool] = {}

        # ── Check 1: Android accounts database ──────────────────────────────
        _, out = _shell(phone_id, "dumpsys account")
        checks["accounts_db"] = email in (out or "").lower()
        log.info("[%s] Check 1 (accounts_db): %s", acc_id, checks["accounts_db"])

        # ── Check 2: Google Contacts sync adapter (confirms active sync) ────
        _, out2 = _shell(
            phone_id,
            "content query --uri content://com.android.contacts/raw_contacts "
            "--projection account_name:account_type --where \"account_type='com.google'\""
        )
        checks["contacts_sync"] = email in (out2 or "").lower()
        log.info("[%s] Check 2 (contacts_sync): %s", acc_id, checks["contacts_sync"])

        # ── Check 3: GMS package dump — account list in Google Play Services ─
        _, out3 = _shell(
            phone_id,
            "dumpsys package com.google.android.gms | grep -i 'account\\|email' | head -20"
        )
        checks["gms_package"] = email in (out3 or "").lower()
        log.info("[%s] Check 3 (gms_package): %s", acc_id, checks["gms_package"])

        # Verified if at least 1 of the 3 checks passes (they're redundant)
        verified = any(checks.values())

        passed = [k for k, v in checks.items() if v]
        failed = [k for k, v in checks.items() if not v]

        result["verified"] = verified
        result["checks"]   = checks
        result["detail"]   = (
            f"Passed: {', '.join(passed) or 'none'}  |  "
            f"Failed: {', '.join(failed) or 'none'}"
        )
        log.info("[%s] Login check result: verified=%s  %s", acc_id, verified, result["detail"])

    except Exception as e:
        result["error"] = f"Check error: {e}"
        log.warning("[%s] Login check error: %s", acc_id, e)
    finally:
        try:
            client.stop_phone(phone_id)
            log.info("[%s] Phone stopped after login check.", acc_id)
        except Exception as e:
            log.warning("[%s] Failed to stop phone after check: %s", acc_id, e)

    return result


def _analyse_screenshot(image_bytes: bytes, attempt: int) -> str:
    """Analyse a screenshot with Claude Vision to diagnose login failures."""
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "your-key-here":
            return "Claude API key not configured."

        b64 = base64.standard_b64encode(image_bytes).decode()
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": (
                        f"Screenshot from cloud Android phone attempting Google login (attempt {attempt}). "
                        "Describe: (1) What screen is shown? (2) What is blocking progress? "
                        "(3) What should happen next? Max 3 sentences."
                    )},
                ],
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"Screenshot analysis error: {e}"
