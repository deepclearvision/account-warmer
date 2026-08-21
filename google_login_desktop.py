"""
google_login_desktop.py — Log a Google account into a Multilogin desktop browser profile.

Uses Playwright via the Multilogin ProfileSession to automate the Google sign-in flow,
including TOTP 2FA. Saves result to geelark_accounts.yaml (unified login status).

Usage:
    python google_login_desktop.py --account acc_048
    python google_login_desktop.py --accounts acc_048 acc_049
"""

import argparse
import asyncio
import sys
from datetime import datetime
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

from core.paths import DATA_DIR
from core.logger import get_logger


def _load_credentials(acc_id: str) -> dict:
    """Pull password + totp_secret from the AccountStore (unified data layer)."""
    from core.account_store import get_account_store
    store = get_account_store()

    # Find the desktop account to get its email, then find mobile by email
    desktop = next((a for a in store.get_desktop_accounts() if a.get("id") == acc_id), None)
    email = desktop.get("email", "") if desktop else ""

    mobile = store.find_mobile_by_email(email) if email else None
    if not mobile:
        # Fallback: try matching by acc_id directly in mobile accounts
        mobile = next((a for a in store.get_mobile_accounts() if a.get("id") == acc_id), None)

    if not mobile:
        return {}
    return {
        "password":    mobile.get("password", ""),
        "totp_secret": mobile.get("totp_secret", ""),
    }


async def _pause(min_ms: int, max_ms: int) -> None:
    import random
    ms = random.randint(min_ms, max_ms)
    await asyncio.sleep(ms / 1000)


async def _dismiss_post_login_prompts(page, log) -> None:
    """Click through the various post-login Google prompts."""
    _DISMISS = [
        'button:has-text("Not now")',
        'button:has-text("Skip")',
        'button:has-text("No thanks")',
        'button:has-text("Continue")',
        'button:has-text("Done")',
        'button:has-text("Got it")',
        'button:has-text("Accept")',
        'button:has-text("I agree")',
        'button:has-text("Allow")',
        'a:has-text("Not now")',
        'a:has-text("Skip")',
    ]
    for _ in range(8):
        clicked = False
        for sel in _DISMISS:
            btn = page.locator(sel).first
            try:
                if await btn.count() > 0 and await btn.is_visible(timeout=1500):
                    await btn.click()
                    log.info(f"[login] Post-login dismissed: {sel}")
                    await _pause(1000, 2000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            break


async def _classify_desktop_failure(page, log) -> str:
    """Inspect the current page and return a LOGIN_ISSUES reason string.

    Order matters — most specific signatures first.  Falls back to "unknown"
    when the page doesn't match any known failure.
    """
    try:
        text = ""
        try:
            text = await page.inner_text("body", timeout=2500)
        except Exception:
            pass
        t = (text or "").lower()

        if "wrong password" in t or "couldn't find your google account" in t:
            return "wrong_password"
        if ("verify it's you" in t or "verify it’s you" in t
                or "recover your account" in t or "phone verification" in t):
            return "phone_verification"
        if ("appeal" in t or "suspended" in t
                or "your account has been disabled" in t):
            return "appeal_required"
        if "wrong code" in t or "incorrect code" in t:
            return "totp_failed"
        if "captcha" in t or "unusual traffic" in t:
            return "captcha"
        return "unknown"
    except Exception as e:
        log.warning(f"[login] failure classification failed: {e}")
        return "unknown"


async def login_account(account: dict) -> bool:
    """Open the Multilogin profile and log into Google. Returns True on success."""
    from core.profile_manager import ProfileSession

    acc_id = account["id"]
    email  = account.get("email", "")
    log    = get_logger(acc_id)

    creds       = _load_credentials(acc_id)
    password    = creds.get("password", "")
    totp_secret = creds.get("totp_secret", "")

    if not password:
        log.error(f"[login] No password found for {acc_id} — check geelark_accounts.yaml")
        return False

    try:
        import pyotp
    except ImportError:
        log.error("[login] pyotp not installed — run: pip install pyotp")
        return False

    log.info(f"[login] Starting desktop login for {acc_id} ({email})")
    success       = False
    failure_issue = None

    try:
        async with ProfileSession(
            profile_id=account["multilogin_profile_id"],
            account_id=acc_id,
            account=account,
        ) as page:

            # ── 1. Warm up the profile on Google first to build reCAPTCHA signals ──
            log.info("[login] Warming up profile on Google...")
            await page.goto("https://www.google.co.uk", wait_until="domcontentloaded", timeout=20000)
            await _pause(2000, 3500)
            await page.evaluate("window.scrollTo(0, 300)")
            await _pause(1000, 2000)
            await page.evaluate("window.scrollTo(0, 0)")
            await _pause(1000, 2000)

            # ── 2. Navigate to Google sign-in ──────────────────────────────────
            await page.goto(
                "https://accounts.google.com/ServiceLogin?service=mail&continue=https://mail.google.com",
                wait_until="domcontentloaded", timeout=25000,
            )
            await _pause(3000, 5000)
            log.info(f"[login] Sign-in page URL: {page.url[:80]}")
            await page.screenshot(path="C:/WarmingData/login_debug_start.png")

            # ── 2a. Handle account chooser page ───────────────────────────────
            # If the profile already has this account (signed out), click it directly
            # to go straight to the password page.
            if "accountchooser" in page.url or "chooser" in page.url:
                log.info("[login] Account chooser detected — bypassing with login_hint URL")
                import urllib.parse
                # Navigate directly with Email param to skip the chooser entirely
                login_hint_url = (
                    "https://accounts.google.com/signin/v2/identifier"
                    f"?Email={urllib.parse.quote(email)}"
                    "&flowEntry=ServiceLogin"
                    "&continue=https%3A%2F%2Fmail.google.com"
                )
                await page.goto(login_hint_url, wait_until="domcontentloaded", timeout=20000)
                await _pause(2000, 3000)
                log.info(f"[login] After login_hint nav, URL: {page.url[:80]}")
                await page.screenshot(path="C:/WarmingData/login_debug_after_chooser.png")

            # ── 2b. Enter email (if still on identifier page) ─────────────────
            email_sel = 'input#identifierId, input[type="email"], input[name="identifier"]'
            email_field = page.locator(email_sel).first
            if await email_field.count() > 0:
                await email_field.click()
                await _pause(400, 800)
                # Clear then type character-by-character to trigger Google's JS validation
                await email_field.click(click_count=3)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await _pause(300, 500)
                await page.keyboard.type(email, delay=80)
                await _pause(800, 1500)
                log.info(f"[login] Typed email: {email}")

                # Click outside the field to trigger blur/validation, then pause
                await page.locator("h1, body").first.click()
                await _pause(600, 1000)

                await page.screenshot(path="C:/WarmingData/login_debug_email.png")

                # Click Next — use Tab then Enter for most natural input
                await page.keyboard.press("Tab")
                await _pause(400, 700)
                await page.keyboard.press("Enter")
                log.info("[login] Pressed Tab+Enter to submit email")
                try:
                    await page.wait_for_url(
                        lambda url: "identifier" not in url,
                        timeout=12000,
                    )
                except Exception:
                    pass
                await _pause(1500, 2500)
                log.info(f"[login] After email Next, URL: {page.url[:80]}")
                await page.screenshot(path="C:/WarmingData/login_debug_after_email.png")
            else:
                log.info(f"[login] No email field — at URL: {page.url[:80]} — proceeding to password step")

            # ── 3. Enter password ──────────────────────────────────────────────
            pwd_sel = 'input[type="password"], input[name="password"]'
            pwd_field = page.locator(pwd_sel).first
            try:
                await pwd_field.wait_for(state="visible", timeout=12000)
            except Exception:
                # Dump page text and all inputs to help diagnose
                try:
                    body_text = await page.inner_text("body")
                    inputs = await page.locator("input").all()
                    input_info = []
                    for inp in inputs[:10]:
                        try:
                            inp_type = await inp.get_attribute("type")
                            inp_name = await inp.get_attribute("name")
                            inp_vis  = await inp.is_visible(timeout=500)
                            input_info.append(f"type={inp_type} name={inp_name} visible={inp_vis}")
                        except Exception:
                            pass
                    log.error(f"[login] Password field not visible — URL: {page.url[:80]}")
                    log.error(f"[login] Page text snippet: {body_text[:300]}")
                    log.error(f"[login] Inputs found: {input_info}")
                except Exception:
                    pass
                await page.screenshot(path="C:/WarmingData/login_debug_no_pwd.png")
                return False

            await pwd_field.click()
            await _pause(400, 800)
            await page.fill(pwd_sel, password)
            await _pause(800, 1500)
            log.info("[login] Typed password")

            clicked = False
            for next_sel in [
                '#passwordNext',
                'button[jsname="LgbsSe"]',
                'div[jsname="soHxf"] button',
                'button:has-text("Next")',
                'button:has-text("Sign in")',
            ]:
                btn = page.locator(next_sel).first
                if await btn.count() > 0:
                    try:
                        await btn.click(timeout=3000)
                        clicked = True
                        log.info(f"[login] Clicked password Next via: {next_sel}")
                        break
                    except Exception:
                        continue
            if not clicked:
                log.info("[login] Falling back to Enter key for password step")
                await page.keyboard.press("Enter")
            await _pause(3000, 5000)
            log.info(f"[login] After password Next, URL: {page.url[:80]}")

            # ── 4. Handle 2FA ──────────────────────────────────────────────────
            # Loop handles: challenge/selection page → pick TOTP → enter code
            _AUTH_SELECTORS = [
                'li:has-text("Authenticator")',
                'li:has-text("authenticator app")',
                'div[data-challengetype="6"]',
                'div[jsname]:has-text("Google Authenticator")',
                'div:has-text("Google Authenticator app")',
                'button:has-text("Authenticator")',
                '[data-authtype="totp"]',
                '[data-challengeid="totp"]',
            ]
            for attempt in range(6):
                current_url = page.url.lower()
                log.info(f"[login] 2FA attempt {attempt+1}, URL: {page.url[:80]}")

                # If we're already at myaccount, we're done
                if "myaccount.google.com" in current_url:
                    break

                # Check for TOTP input first
                totp_field = page.locator(
                    'input[type="tel"], input[id*="totpPin" i], '
                    'input[aria-label*="code" i], input[autocomplete="one-time-code"]'
                ).first
                if await totp_field.count() > 0:
                    try:
                        await totp_field.wait_for(state="visible", timeout=3000)
                        secret_clean = totp_secret.replace(" ", "").upper()
                        code = pyotp.TOTP(secret_clean).now()
                        log.info(f"[login] Entering TOTP code: {code}")
                        await totp_field.click()
                        await _pause(400, 800)
                        await totp_field.fill(code)
                        await _pause(800, 1500)
                        for v_sel in ['button:has-text("Next")', 'button:has-text("Verify")', 'button[type="submit"]']:
                            vbtn = page.locator(v_sel).first
                            if await vbtn.count() > 0 and await vbtn.is_visible(timeout=1000):
                                await vbtn.click()
                                break
                        await _pause(3000, 5000)
                        log.info(f"[login] After TOTP, URL: {page.url[:80]}")
                        continue
                    except Exception as e:
                        log.warning(f"[login] TOTP entry error: {e}")

                # Look for authenticator app option directly on challenge/selection page
                auth_selected = False
                for auth_sel in _AUTH_SELECTORS:
                    auth_opt = page.locator(auth_sel).first
                    try:
                        if await auth_opt.count() > 0 and await auth_opt.is_visible(timeout=1000):
                            await auth_opt.click()
                            log.info(f"[login] Selected authenticator option via: {auth_sel}")
                            await _pause(2000, 3000)
                            auth_selected = True
                            break
                    except Exception:
                        continue
                if auth_selected:
                    continue

                # Click "Try another way" to reveal more options
                try_another = page.locator(
                    'button:has-text("Try another way"), a:has-text("Try another way"), '
                    'button:has-text("More options"), a:has-text("More options")'
                ).first
                if await try_another.count() > 0 and await try_another.is_visible(timeout=2000):
                    await try_another.click()
                    log.info("[login] Clicked 'Try another way'")
                    await _pause(2000, 3500)
                    continue

                # Dismiss any interstitials (phone prompts, etc.)
                dismissed = False
                for skip_sel in [
                    'button:has-text("Not now")', 'button:has-text("Skip")',
                    'button:has-text("No thanks")', 'a:has-text("Not now")',
                ]:
                    skip_btn = page.locator(skip_sel).first
                    if await skip_btn.count() > 0 and await skip_btn.is_visible(timeout=1500):
                        await skip_btn.click()
                        log.info(f"[login] Dismissed 2FA interstitial: {skip_sel}")
                        await _pause(2000, 3000)
                        dismissed = True
                        break
                if not dismissed:
                    log.info(f"[login] 2FA loop: nothing actionable found at {page.url[:80]}")
                    break  # Nothing to click — either done or stuck

            # ── 5. Dismiss post-login prompts ──────────────────────────────────
            await _dismiss_post_login_prompts(page, log)

            # ── 6. Verify logged in ────────────────────────────────────────────
            await page.goto(
                "https://myaccount.google.com",
                wait_until="domcontentloaded", timeout=20000,
            )
            await _pause(2000, 3000)
            url = page.url.lower()
            if "myaccount.google.com" in url and "signin" not in url and "login" not in url:
                log.info(f"[login] SUCCESS — {acc_id} ({email}) is now logged in")
                success = True
            else:
                log.error(f"[login] FAILED — ended at {page.url[:80]}")
                failure_issue = await _classify_desktop_failure(page, log)
                log.info(f"[login] Failure classified as: {failure_issue}")

    except Exception as e:
        log.error(f"[login] Session error: {e}")
        return False

    # ── Save status to unified login store (geelark_accounts.yaml) ──────────────
    from core.account_store import get_account_store
    store = get_account_store()
    mobile_accounts = store.get_mobile_accounts()
    target = None
    for a in mobile_accounts:
        if a.get("id") == acc_id:
            target = a
            break
    if target is None:
        # Account doesn't exist in geelark_accounts.yaml — create a minimal entry
        target = {"id": acc_id, "email": email}
        mobile_accounts.append(target)
        log.warning(f"[login] {acc_id} not found in geelark_accounts.yaml — created minimal entry for login status")

    if success:
        target["desktop_login_status"] = "logged_in"
        target.pop("desktop_login_issue", None)
        target.pop("desktop_login_note", None)
    else:
        target["desktop_login_status"] = "login_failed"
        target["desktop_login_issue"]  = failure_issue or "unknown"
    target["desktop_login_checked_at"] = datetime.now().isoformat()
    store.save_mobile_accounts(mobile_accounts)
    log.info(f"[login] Desktop login status saved: {target['desktop_login_status']}"
             + (f" (issue: {target.get('desktop_login_issue')})"
                if target.get("desktop_login_status") == "login_failed" else ""))
    return success


def main():
    parser = argparse.ArgumentParser(description="Log Google accounts into Multilogin profiles")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--account",  metavar="ID")
    group.add_argument("--accounts", metavar="ID", nargs="+")
    args = parser.parse_args()

    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")

    from core.account_store import get_account_store
    all_accs = get_account_store().get_desktop_accounts()

    ids = [args.account] if args.account else args.accounts
    targets = []
    for acc_id in ids:
        acc = next((a for a in all_accs if a["id"] == acc_id), None)
        if not acc:
            print(f"ERROR: {acc_id} not found in accounts.yaml")
            sys.exit(1)
        targets.append(acc)

    async def run_all():
        ok = failed = 0
        for acc in targets:
            result = await login_account(acc)
            if result:
                ok += 1
            else:
                failed += 1
        print(f"\nDone. Succeeded: {ok}  Failed: {failed}")

    asyncio.run(run_all())


if __name__ == "__main__":
    main()
