"""
Google Account Login via GeelarK Cloud Phone — Pure ADB Mode.

# Why Pure ADB?
GeelarK's built-in RPA flows (openApp / googleLogin) require Android 11+ for Wireless
Debugging. All our phones are Samsung Galaxy S9+ (SM-G9650) running Android 10.
The only automation path available on Android 10 is ADB shell commands issued through
GeelarK's /open/v1/shell/execute API endpoint.

# MinuteMaidActivity — How Google Sign-in Works on Android
ALL Google sign-in screens run inside a single activity:
  com.google.android.gms.auth.uiflows.minutemaid.MinuteMaidActivity
The actual UI is rendered inside a Chrome WebView child. On our SM-G9650 / Android 10 /
GMS build, uiautomator dump DOES expose WebView content — button text, link text, and
input field IDs (resource-id="identifierId" for email, etc.) are all visible in the XML.
This allows _find_and_tap() to locate elements like "Try another way" by text.
Key observable elements:
  • android.widget.EditText    — email, password, TOTP, security-code input fields
  • resource-id="identifierId" — uniquely identifies the email entry EditText
  • password="true"            — distinguishes the password field
  • text="Try another way"     — link on the "security code from device" screen
  • android.webkit.WebView     — present on ALL MinuteMaid screens
  • Post-login native buttons  (Skip, Next, I agree, Allow, etc.)

# CRITICAL: Screen Type vs. State
_detect_minutemaid_screen() returns "totp_entry" for ANY EditText without a password
flag. This means EMAIL screens and TOTP screens look IDENTICAL to the detector.
The caller (_run_pure_adb_login) MUST distinguish them using state flags:
  email_entered, password_entered, _2fa_step

# Confirmed Working Login Flow (tested 2026-04-09, Android 10, 720x1440)
  1. Press HOME — dismiss any screen left over from a previous attempt.
  2. Check AccountManager — if already registered, return success immediately.
  3. Launch ADD_ACCOUNT_SETTINGS intent → opens MinuteMaidActivity at email screen.
  4. EMAIL screen  (screen_type="totp_entry", email_entered=False)
       → tap EditText, clear 40× DEL, type email, press Enter
  5. PASSWORD screen  (screen_type="password")
       → tap EditText, clear 30× DEL, type password, press Enter
  6. SECURITY-CODE screen  (screen_type="totp_entry", _2fa_step=0, password_entered=True)
       → Google shows "Get security code from signed-in Android device" — this is NOT TOTP.
       → Dismiss keyboard (KEYCODE_BACK), then find "Try another way" by text in dump
         (falls back to fractional (0.176, 0.896) if not found).
       → _2fa_step → 1
  7. METHOD LIST screen  (screen_type="webview_prompt", _2fa_step=1)
       → WebView-rendered list of 2FA methods. No labels visible in uiautomator.
       → Tap "Get code from Google Authenticator" at fractional (0.50, 0.491).
       → _2fa_step → 2
  8. TOTP ENTRY screen  (screen_type="totp_entry", _2fa_step>=2, password_entered=True)
       → This is the REAL TOTP screen. Generate pyotp code, clear field, type code, Enter.
       → totp_entered → True, _2fa_step → 3
  9. POST-TOTP screens  (screen_type="totp_entry" or "webview_prompt", totp_entered=True)
       → Google shows recovery phone, account setup, etc. Tap Skip/Not now/Next.
  10. LEFT MinuteMaidActivity → navigate any remaining consent screens → done.
  11. Verify via dumpsys account — look for Account {name=email, type=com.google}.

# Proxy
All phones share a single StreamVia rotating mobile SOCKS5 proxy (GEELARK_PROXY).
Proxy IP is rotated before each login attempt via GEELARK_PROXY_CONTROL_URL.
Rate limit: one rotation per 180s.

# TOTP Secrets
Stored in geelark_accounts.yaml. May contain spaces — normalize with
  .replace(" ", "").upper()
before passing to pyotp. AYCD CSV accounts (acc_032–051) have confirmed correct
secrets. Older accounts (acc_004–031) have uncertain secrets.
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
    Confirm the Google account is actually signed into the device by querying
    the Android accounts database via ADB.  Returns True if email appears in
    a com.google account entry.
    """
    _, out = _shell(phone_id, "dumpsys account | grep -i 'com.google'")
    if not out:
        # Fallback: broader search
        _, out = _shell(phone_id, "dumpsys account")
    if not out:
        log.warning("dumpsys account returned no output — cannot verify")
        return False
    # Look for the email address in the account dump
    found = email.lower() in out.lower()
    log.info("Account verification for %s: %s", email, "FOUND" if found else "NOT FOUND")
    if not found:
        log.debug("dumpsys account output: %s", out[:500])
    return found


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


def _rotate_proxy_ip() -> str:
    """
    Request a unique fresh IP from the StreamVia mobile proxy control portal.
    Uses changeipunique so each account session gets an IP not used in the
    last 24 hours — critical for account isolation when 5 phones share one proxy.

    Polls the check-IP endpoint until Ready: x.x.x.x is returned (up to 3 min).
    If throttled, waits the required number of seconds and retries once.
    Returns the new IP string on success, '' on failure/not-configured.

    Rate limit: changeip/changeipunique can only run once every 180s.
    Control portal base URL (with credentials): GEELARK_PROXY_CONTROL_URL
    """
    import requests as _req
    import re as _re
    import warnings as _w
    _w.filterwarnings("ignore", message="Unverified HTTPS")

    control_url = os.environ.get("GEELARK_PROXY_CONTROL_URL", "").rstrip("/")
    if not control_url:
        log.warning("GEELARK_PROXY_CONTROL_URL not set — skipping proxy rotation")
        return ""

    # curl User-Agent causes portal to return plain text instead of HTML
    headers = {"User-Agent": "curl/7.68.0"}

    def _check_ip() -> str | None:
        """Return current IP if portal says Ready, else None."""
        r = _req.get(control_url, headers=headers, timeout=15, verify=False)
        text = r.text.strip()
        m = _re.search(r'Ready:\s*([\d.]+)', text)
        return m.group(1) if m else None

    def _issue_changeip() -> str:
        """Issue changeipunique and return raw response text."""
        r = _req.get(f"{control_url}/?changeipunique=true",
                     headers=headers, timeout=30, verify=False)
        return r.text.strip()

    try:
        old_ip = _check_ip()
        log.info("Proxy current IP: %s — requesting unique IP change …", old_ip)

        resp = _issue_changeip()
        log.info("Proxy change response: %s", resp[:80])

        # Handle throttle on the initial request
        throttle = _re.search(r'Throttled:\s*Wait\s*(\d+)\s*Seconds?', resp, _re.I)
        if throttle:
            wait_sec = int(throttle.group(1)) + 5  # +5s buffer
            log.warning("Proxy throttled — waiting %ds before retry …", wait_sec)
            time.sleep(wait_sec)
            resp = _issue_changeip()
            log.info("Proxy change retry response: %s", resp[:80])

        # Poll until Ready: x.x.x.x (up to ~3 minutes, 5s intervals)
        for _ in range(36):
            time.sleep(5)
            r = _req.get(control_url, headers=headers, timeout=15, verify=False)
            text = r.text.strip()
            log.debug("Proxy poll: %s", text[:60])

            # If still throttled mid-poll, wait and continue
            mid_throttle = _re.search(r'Throttled:\s*Wait\s*(\d+)\s*Seconds?', text, _re.I)
            if mid_throttle:
                wait_sec = int(mid_throttle.group(1)) + 5
                log.warning("Proxy mid-poll throttle — sleeping %ds …", wait_sec)
                time.sleep(wait_sec)
                continue

            m = _re.search(r'Ready:\s*([\d.]+)', text)
            if m:
                new_ip = m.group(1)
                if new_ip != old_ip:
                    log.info("Proxy IP rotated: %s → %s", old_ip, new_ip)
                    return new_ip
                # Still same IP — rotation not yet complete, keep polling

        log.warning("Proxy rotation timed out after 3 min — using current IP")
    except Exception as e:
        log.warning("Proxy rotation error: %s", e)
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

    # Set mock GPS location via GeelarK ADB (LocationManager mock provider)
    # This seeds the device's location so Maps Timeline starts in the right city
    _shell(phone_id, "settings put secure mock_location 1")
    _shell(phone_id,
           f"am startservice -n com.android.location.fused/.FusedLocationService "
           f"--ef latitude {lat} --ef longitude {lon}")

    log.info("[%s] Device locale configured.", acc_id)


def run_google_login(account: dict, stop_phone_on_success: bool = True,
                     progress_callback=None) -> dict:
    """
    Log a Google account into its assigned GeelarK cloud phone using pure ADB.

    GeelarK's built-in RPA (openApp / googleLogin) is broken on Android 10 (SM-G9650).
    geelark_login_flow_id is ignored — pure ADB is always used regardless.

    Flow:
      1. Rotate proxy IP (StreamVia changeipunique) — fresh IP per login.
      2. Start phone, poll boot (up to 100s), query screen size.
      3. Run _run_pure_adb_login (up to MAX_ATTEMPTS times).
      4. Verify via dumpsys account after each attempt.
      5. Configure device locale (timezone + GPS mock) on success.
      6. Stop phone if stop_phone_on_success=True.

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
    new_ip = _rotate_proxy_ip()
    if new_ip:
        log.info("[%s] Proxy IP rotated to %s", acc_id, new_ip)
    else:
        log.warning("[%s] Proxy rotation skipped — proceeding with current IP", acc_id)

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

    # ── Pure ADB login — works on all Android versions incl. Android 10 ───────
    # GeelarK's custom RPA flow (openApp) is broken on Android 10 (SM-G9650).
    # We drive the entire login via ADB shell commands using the Add Account intent
    # which was confirmed to open MinuteMaidActivity with native EditText fields.
    # geelark_login_flow_id is intentionally ignored — pure ADB is always used.
    if flow_id:
        log.info("[%s] geelark_login_flow_id=%s present but ignored — "
                 "using pure ADB login (openApp broken on Android 10)", acc_id, flow_id)

    log.info("[%s] Using pure ADB login mode", acc_id)

    # ── Login attempts ────────────────────────────────────────────────────────
    last_screenshot: Optional[bytes] = None
    diagnosis = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result["attempts"] = attempt
        log.info("[%s] Attempt %d/%d …", acc_id, attempt, MAX_ATTEMPTS)

        _progress("submitting_task", attempt=attempt)

        # Run the pure ADB login state machine
        adb_ok, adb_diag = _run_pure_adb_login(
            phone_id     = phone_id,
            email        = email,
            password     = password,
            totp_secret  = totp_secret,
            acc_id       = acc_id,
            screen_w     = screen_w,
            screen_h     = screen_h,
            progress_callback = progress_callback,
        )
        log.info("[%s] ADB login result: ok=%s  %s", acc_id, adb_ok, adb_diag)

        # Give AccountManager a moment to register the account
        time.sleep(5)

        # ── Verify login ──────────────────────────────────────────────────────
        account_confirmed = _verify_google_account(phone_id, email)
        if not account_confirmed:
            # AccountManager can take a few extra seconds
            log.info("[%s] Account not in AccountManager yet — waiting 10s …", acc_id)
            time.sleep(10)
            account_confirmed = _verify_google_account(phone_id, email)

        screenshot = client.take_screenshot(phone_id)
        if screenshot:
            result["last_screenshot_b64"] = base64.standard_b64encode(screenshot).decode()

        if account_confirmed:
            log.info("[%s] Account confirmed — login SUCCESS.", acc_id)
            result["success"]   = True
            result["diagnosis"] = f"Account {email} verified in device accounts database."
            _configure_device_locale(phone_id, account, acc_id)
            if stop_phone_on_success:
                try:
                    client.stop_phone(phone_id)
                    result["viewer_url"] = ""
                except Exception:
                    pass
            return result

        # Not confirmed — check if we're on home screen (sync may be in progress)
        focus = _get_window_focus(phone_id)
        if LAUNCHER_ACTIVITY in focus:
            log.warning("[%s] On home but not in accounts DB — marking success (syncing).",
                        acc_id)
            result["success"]   = True
            result["diagnosis"] = "On home screen — account may still be syncing."
            _configure_device_locale(phone_id, account, acc_id)
            if stop_phone_on_success:
                try:
                    client.stop_phone(phone_id)
                    result["viewer_url"] = ""
                except Exception:
                    pass
            return result

        diagnosis = adb_diag or f"Account not confirmed after attempt {attempt}. Focus: {focus}"
        log.warning("[%s] Login not confirmed: %s", acc_id, diagnosis)

        if attempt < MAX_ATTEMPTS:
            time.sleep(10)

    # ── All attempts exhausted ────────────────────────────────────────────────
    log.error("[%s] Login failed after %d attempts. Stopping phone.", acc_id, MAX_ATTEMPTS)
    result["needs_user_input"] = True
    result["diagnosis"]        = diagnosis

    if last_screenshot:
        result["last_screenshot_b64"] = base64.standard_b64encode(last_screenshot).decode()

    try:
        client.stop_phone(phone_id)
    except Exception as e:
        log.warning("[%s] Failed to stop phone: %s", acc_id, e)

    return result


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
    _rotate_proxy_ip()

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
