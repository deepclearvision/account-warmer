"""
multilogin_account_setup.py — One-time account setup via Multilogin browser.

Performs the same post-login configuration steps as the mobile setup, but
entirely through the Playwright-controlled Multilogin desktop browser profile.
No GeelarK phone required.

Steps:
  1. Upload profile photo (from data/profile_photos/, assigned via ml_setup_run.py)
  2. Enable Web & App Activity
  3. Enable YouTube History
  4. Enable Location History
  5. Set birthday

  Skipped here (handled elsewhere):
  - Display name: set at account creation
  - Local Guides: enrolled on mobile after first Maps contribution

Usage (as a BaseActivity from the orchestrator):
    setup = MultiloginAccountSetup(page, account, behaviour_cfg)
    success = await setup.run()

Or via the CLI runner ml_setup_run.py:
    python ml_setup_run.py --account acc_001
"""

import asyncio
import random
from datetime import date, timedelta
from pathlib import Path

from playwright.async_api import Page

from activities.base_activity import BaseActivity

_PHOTOS_DIR = Path(__file__).parent.parent / "data" / "profile_photos"

# ── Name pools ──────────────────────────────────────────────────────────────────
_FIRST_NAMES = [
    "James", "Oliver", "Harry", "Jack", "George", "Noah", "Charlie", "Jacob",
    "Alfie", "Freddie", "Thomas", "Oscar", "Henry", "William", "Ethan",
    "Amelia", "Olivia", "Isla", "Emily", "Ava", "Lily", "Sophia", "Grace",
    "Ella", "Mia", "Isabella", "Charlotte", "Freya", "Alice", "Florence",
]
_LAST_NAMES = [
    "Smith", "Jones", "Williams", "Taylor", "Brown", "Davies", "Evans",
    "Wilson", "Thomas", "Roberts", "Johnson", "Lewis", "Walker", "Robinson",
    "White", "Thompson", "Martin", "Jackson", "Clarke", "Hall", "Wood",
    "Turner", "Hughes", "Phillips", "Morris", "Clark", "Mitchell", "Bell",
    "Ward", "Moore", "Cooper", "King", "Harrison",
]


def _random_name() -> tuple[str, str]:
    return random.choice(_FIRST_NAMES), random.choice(_LAST_NAMES)


def _random_birthday(min_age: int = 25, max_age: int = 45) -> tuple[int, int, int]:
    """Return (day, month, year) for a birthday between min_age and max_age years ago."""
    today  = date.today()
    age    = random.randint(min_age, max_age)
    approx = today - timedelta(days=age * 365 + random.randint(0, 364))
    return approx.day, approx.month, approx.year


_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class MultiloginAccountSetup(BaseActivity):
    """
    Runs a one-time Google account configuration inside the Multilogin browser.
    Designed to be called once per account after initial login is confirmed.
    """

    async def run(self, maps_only: bool = False, security_only: bool = False) -> bool:
        home_address = self.account.get("home_address", "")
        work_address = self.account.get("work_address", "")

        if security_only:
            self.log.info("[ml-setup] security_only: dismissing security alerts")
            await self._dismiss_security_alerts()
            return True

        if maps_only:
            # Only set Home/Work labeled places in Google Maps
            if not home_address and not work_address:
                self.log.warning("[ml-setup] maps_only: no home_address or work_address set — nothing to do")
                return False

            # Check login status before attempting Maps navigation
            try:
                await self.page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=15000)
                await self.page.wait_for_timeout(2000)
                logged_in = await self.page.locator(
                    'a[aria-label*="Google Account"], a[href*="myaccount.google.com"], img[alt*="Google Account"]'
                ).count() > 0
                if not logged_in:
                    self.log.error("[ml-setup] maps_only aborted — account is NOT logged in to Google. Run a login check first.")
                    return False
                self.log.info("[ml-setup] Login confirmed — proceeding with Maps address setup")
            except Exception as e:
                self.log.warning("[ml-setup] Login pre-check failed: %s — proceeding anyway", e)

            steps = [
                *([("home_address", lambda: self._set_maps_labeled_place(home_address, "Home"))]
                  if home_address else []),
                *([("work_address", lambda: self._set_maps_labeled_place(work_address, "Work"))]
                  if work_address else []),
            ]
            done = 0
            for name, fn in steps:
                self.log.info(f"[ml-setup] Running step: {name}")
                try:
                    ok = await fn()
                    if ok:
                        done += 1
                        self.log.info(f"[ml-setup] {name}: OK")
                    else:
                        self.log.warning(f"[ml-setup] {name}: step returned False")
                except Exception as e:
                    self.log.warning(f"[ml-setup] {name}: exception — {e}")
            return done >= 1

        day, month, year = _random_birthday()

        # Display name is set at account creation — skip here.
        # Local Guides enrollment is done on mobile after the first Maps
        # contribution, when Google prompts the user naturally.
        steps = [
            ("security_alerts",  lambda: self._dismiss_security_alerts()),
            ("profile_photo",    lambda: self._upload_profile_photo()),
            ("web_app_activity", lambda: self._enable_web_app_activity()),
            ("youtube_history",  lambda: self._enable_youtube_history()),
            ("location_history", lambda: self._enable_location_history()),
            ("birthday",         lambda: self._set_birthday(day, month, year)),
            *([("home_address",  lambda: self._set_maps_labeled_place(home_address, "Home"))]
              if home_address else []),
            *([("work_address",  lambda: self._set_maps_labeled_place(work_address, "Work"))]
              if work_address else []),
        ]

        done = 0
        for name, fn in steps:
            self.log.info(f"[ml-setup] Running step: {name}")
            try:
                ok = await fn()
                if ok:
                    done += 1
                    self.log.info(f"[ml-setup] {name}: OK")
                else:
                    self.log.warning(f"[ml-setup] {name}: step returned False")
            except Exception as e:
                self.log.warning(f"[ml-setup] {name}: exception — {e}")

        # security_alerts is step 0 and always returns True — don't count it
        # toward the minimum required steps (it's best-effort).
        self.log.info(f"[ml-setup] Completed {done}/{len(steps)} steps")
        return done >= 4  # security + at least 3 of the 5 substantive steps

    # ── Step implementations ─────────────────────────────────────────────────────

    async def _dismiss_security_alerts(self) -> bool:
        """
        Visit Google account security pages and confirm every 'was it you?' event.

        Google lists individual sign-in events on the security page. Each one must
        be clicked to open its detail panel, then "Yes, it was me" confirmed.
        Also dismisses top-level banners and any pending notification prompts.

        Always returns True — best-effort, never blocks setup.
        """
        confirmed = 0

        # ── 1. Security page ────────────────────────────────────────────────────
        try:
            await self.page.goto(
                "https://myaccount.google.com/security",
                wait_until="domcontentloaded", timeout=20000,
            )
            await self.humaniser.pause(2000, 3500)
            await self.dismiss_cookie_banner()
            acc_id = self.account.get("id", "unknown")
            await self.page.screenshot(path=f"C:/WarmingData/security_{acc_id}_1_page.png")

            # Pass 0: click the top "Critical security issues found" banner card if present.
            # This navigates to a dedicated review flow separate from the activity list.
            _CRITICAL_BTNS = [
                'a:has-text("Secure account")',
                'button:has-text("Secure account")',
                'a:has-text("Protect your account")',
                'button:has-text("Protect your account")',
                'a:has-text("Review security issue")',
                'a:has-text("Critical security issues found")',
                'div:has-text("Critical security issues found")',
                'a:has-text("secure it now")',
                'a[href*="security/critical"]',
                'a[href*="accountsecurity"]',
                'a[href*="securityissues"]',
            ]
            for sel in _CRITICAL_BTNS:
                btns = self.page.locator(sel)
                count = await btns.count()
                for i in range(count):
                    try:
                        btn = btns.nth(i)
                        if not await btn.is_visible(timeout=1500):
                            continue
                        url_before = self.page.url
                        self.log.info("[ml-setup] Pass0 CTA found — clicking: %s", sel)
                        await btn.click()
                        await self.humaniser.pause(2000, 3500)
                        if self.page.url != url_before:
                            await self.page.screenshot(path=f"C:/WarmingData/security_{acc_id}_pass0_review.png")
                            # Work through any confirmation steps on the review page
                            for _step in range(8):
                                clicked = False
                                for csel in [
                                    'button:has-text("Yes, it was me")',
                                    'a:has-text("Yes, it was me")',
                                    'button:has-text("Yes")',
                                    'button:has-text("Looks good")',
                                    'button:has-text("Confirm")',
                                    'button:has-text("Continue")',
                                    'button:has-text("Next")',
                                    'button:has-text("Done")',
                                ]:
                                    cb = self.page.locator(csel).first
                                    if await cb.count() > 0 and await cb.is_visible(timeout=2000):
                                        await cb.click()
                                        await self.humaniser.pause(1500, 2500)
                                        confirmed += 1
                                        clicked = True
                                        self.log.info("[ml-setup] Pass0 review step %d: %s", _step, csel)
                                        break
                                if not clicked:
                                    break
                            await self.page.goto("https://myaccount.google.com/security",
                                                 wait_until="domcontentloaded", timeout=20000)
                            await self.humaniser.pause(1500, 2500)
                        break
                    except Exception:
                        continue

            # Pass A: top-level "Yes, it was me" / "Looks good" banner buttons
            _TOP_CONFIRM = [
                'button:has-text("Yes, it was me")',
                'button:has-text("Looks good")',
                'a:has-text("Yes, it was me")',
                'a:has-text("Confirm it was me")',
            ]
            for sel in _TOP_CONFIRM:
                buttons = self.page.locator(sel)
                count = await buttons.count()
                for i in range(count):
                    try:
                        btn = buttons.nth(i)
                        if await btn.is_visible(timeout=1500):
                            await btn.click()
                            await self.humaniser.pause(800, 1500)
                            confirmed += 1
                            self.log.info("[ml-setup] Confirmed top-level security banner")
                            # Follow-up "Done" / "Got it" after confirmation
                            followup = self.page.locator(
                                'button:has-text("Done"), button:has-text("Got it"), '
                                'button:has-text("OK"), button:has-text("Dismiss")'
                            ).first
                            if await followup.count() > 0 and await followup.is_visible(timeout=1500):
                                await followup.click()
                                await self.humaniser.pause(500, 1000)
                    except Exception:
                        continue

            _CONFIRM_SELECTORS = [
                'button:has-text("Yes, it was me")',
                'a:has-text("Yes, it was me")',
                'button:has-text("Yes")',
                'button:has-text("Looks good")',
                'button:has-text("I recognize this activity")',
                'button:has-text("Confirm")',
                'button:has-text("Done")',
                'button:has-text("Continue")',
                'button:has-text("Next")',
            ]

            # Pass B-critical: handle each critical/suspicious alert row exactly once.
            # Uses visited_review_urls (not text) as dedup key — each distinct alert navigates
            # to a unique URL, so even if the row stays in the DOM we won't re-process it.
            _CRITICAL_ROW_SELECTORS = [
                # Exact text seen in the wild (screenshot confirmed)
                'a:has-text("Suspicious attempt to sign in")',
                'div:has-text("Suspicious attempt to sign in")',
                'a:has-text("Critical security issues found")',
                'div:has-text("Critical security issues found")',
                # "Action needed" badge items — the whole row is clickable
                'div:has-text("Action needed")',
                # Original expected text (kept as fallback for other account states)
                'a:has-text("Critical security alert")',
                'div:has-text("Critical security alert")',
                'span:has-text("Critical security alert")',
                'a:has-text("Suspicious activity detected")',
                'div:has-text("Suspicious activity detected")',
                'span:has-text("Suspicious activity detected")',
                '[aria-label*="Critical security" i]',
                '[aria-label*="Suspicious activity" i]',
            ]
            visited_review_urls: set[str] = set()
            for _crit_attempt in range(10):  # max 10 distinct critical alerts
                found_critical = False
                for sel in _CRITICAL_ROW_SELECTORS:
                    rows = self.page.locator(sel)
                    count = await rows.count()
                    for i in range(count):
                        try:
                            row = rows.nth(i)
                            if not await row.is_visible(timeout=800):
                                continue
                            url_before = self.page.url
                            await row.click()
                            await self.humaniser.pause(1500, 2500)
                            review_url = self.page.url
                            navigated_away = review_url != url_before

                            if navigated_away:
                                if review_url in visited_review_urls:
                                    # Already handled this exact alert — go back and stop
                                    self.log.info("[ml-setup] Already handled %s — skipping", review_url[:60])
                                    await self.page.goto("https://myaccount.google.com/security",
                                                         wait_until="domcontentloaded", timeout=20000)
                                    await self.humaniser.pause(1000, 1500)
                                    break
                                visited_review_urls.add(review_url)
                                self.log.info("[ml-setup] Critical/suspicious alert → %s", review_url[:80])
                                await self.page.screenshot(path=f"C:/WarmingData/security_{acc_id}_crit_{_crit_attempt}.png")
                                for _step in range(8):
                                    clicked_step = False
                                    for csel in _CONFIRM_SELECTORS:
                                        cb = self.page.locator(csel).first
                                        if await cb.count() > 0 and await cb.is_visible(timeout=2000):
                                            await cb.click()
                                            await self.humaniser.pause(1200, 2000)
                                            confirmed += 1
                                            clicked_step = True
                                            self.log.info("[ml-setup] Crit review step %d: %s", _step, csel)
                                            break
                                    if not clicked_step:
                                        break
                                await self.page.screenshot(path=f"C:/WarmingData/security_{acc_id}_crit_{_crit_attempt}_done.png")
                                await self.page.goto("https://myaccount.google.com/security",
                                                     wait_until="domcontentloaded", timeout=20000)
                                await self.humaniser.pause(1500, 2500)
                                found_critical = True
                                break
                            else:
                                # No navigation — dismiss any drawer that opened
                                close_btn = self.page.locator(
                                    'button[aria-label*="Close" i], button[aria-label*="Back" i],'
                                    'button:has-text("Done"), button:has-text("Got it")'
                                ).first
                                if await close_btn.count() > 0 and await close_btn.is_visible(timeout=1000):
                                    await close_btn.click()
                                    await self.humaniser.pause(500, 900)
                        except Exception:
                            continue
                        if found_critical:
                            break
                    if found_critical:
                        break
                if not found_critical:
                    break  # no more unhandled critical/suspicious alerts

            # Pass B-normal: loop for standard sign-in events (drawer-based, re-query after each confirm).
            _SIGNIN_ROW_SELECTORS = [
                # "Review security activity" link/row (aggregated count of sign-in events)
                'a:has-text("Review security activity")',
                'div:has-text("Review security activity")',
                # Individual sign-in event rows
                'a:has-text("New sign-in")',
                'div:has-text("New sign-in on")',
                '[aria-label*="sign-in" i]',
                '[aria-label*="security event" i]',
                'li[jsaction]',
                'div[role="listitem"]',
            ]
            max_passes = 15
            for _pass in range(max_passes):
                found_this_pass = False
                for sel in _SIGNIN_ROW_SELECTORS:
                    rows = self.page.locator(sel)
                    count = await rows.count()
                    if count == 0:
                        continue
                    for i in range(count):
                        try:
                            row = rows.nth(i)
                            if not await row.is_visible(timeout=800):
                                continue
                            await row.click()
                            await self.humaniser.pause(800, 1500)
                            confirm_btn = self.page.locator(
                                'button:has-text("Yes, it was me"), '
                                'a:has-text("Yes, it was me"), '
                                'button:has-text("Yes")'
                            ).first
                            if await confirm_btn.count() > 0 and await confirm_btn.is_visible(timeout=2000):
                                await confirm_btn.click()
                                await self.humaniser.pause(700, 1200)
                                confirmed += 1
                                found_this_pass = True
                                self.log.info(f"[ml-setup] Confirmed sign-in event {confirmed}")
                                close_btn = self.page.locator(
                                    'button[aria-label*="Close" i], button[aria-label*="Back" i],'
                                    'button:has-text("Done"), button:has-text("Got it")'
                                ).first
                                if await close_btn.count() > 0 and await close_btn.is_visible(timeout=1000):
                                    await close_btn.click()
                                    await self.humaniser.pause(600, 1000)
                                break
                            else:
                                close_btn = self.page.locator(
                                    'button:has-text("OK"), button:has-text("Ok"),'
                                    'button:has-text("Done"), button:has-text("Got it"),'
                                    'button[aria-label*="Close" i], button[aria-label*="Back" i]'
                                ).first
                                if await close_btn.count() > 0 and await close_btn.is_visible(timeout=1000):
                                    await close_btn.click()
                                    await self.humaniser.pause(400, 700)
                        except Exception:
                            continue
                        if found_this_pass:
                            break
                    if found_this_pass:
                        break
                if not found_this_pass:
                    break

        except Exception as e:
            self.log.debug(f"[ml-setup] security page error: {e}")

        # ── 2. Notifications page ────────────────────────────────────────────────
        try:
            await self.page.goto(
                "https://myaccount.google.com/notifications",
                wait_until="domcontentloaded", timeout=20000,
            )
            await self.humaniser.pause(1500, 2500)

            _NOTIF_SELECTORS = [
                'button:has-text("Yes, it was me")',
                'button:has-text("Looks good")',
                'button:has-text("Remind me later")',
                'button:has-text("Skip")',
                'button:has-text("Not now")',
                'button:has-text("Done")',
                'button:has-text("Got it")',
                'button:has-text("Dismiss")',
            ]
            for sel in _NOTIF_SELECTORS:
                buttons = self.page.locator(sel)
                count = await buttons.count()
                for i in range(count):
                    try:
                        btn = buttons.nth(i)
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            await self.humaniser.pause(500, 1000)
                            self.log.info(f"[ml-setup] Dismissed notification: {sel}")
                    except Exception:
                        continue
        except Exception as e:
            self.log.debug(f"[ml-setup] notifications page error: {e}")

        if confirmed == 0:
            self.log.info("[ml-setup] security_alerts: no pending alerts found (may already be clear)")
        else:
            self.log.info(f"[ml-setup] security_alerts: confirmed {confirmed} event(s)")
        return True

    async def _upload_profile_photo(self) -> bool:
        """Upload a profile photo assigned to this account in accounts.yaml."""
        photo_filename = self.account.get("profile_photo")
        if not photo_filename:
            self.log.warning("[ml-setup] No profile_photo assigned to account — skipping")
            return False

        photo_path = _PHOTOS_DIR / photo_filename
        if not photo_path.exists():
            self.log.warning("[ml-setup] Profile photo not found: %s", photo_path)
            return False

        await self.page.goto(
            "https://myaccount.google.com/personal-info",
            wait_until="domcontentloaded", timeout=25000,
        )
        await self.humaniser.pause(2000, 3500)
        await self.dismiss_cookie_banner()

        # Click the profile photo / avatar area.
        # Google renders this differently depending on whether a photo is already set:
        #   - No photo: initials avatar as a button or link
        #   - Photo set: <img> inside a clickable container
        # Try all known patterns.
        photo_trigger = self.page.locator(
            'img[data-atid="accountAvatar"], '
            '[data-atid="accountAvatar"], '
            'button[aria-label*="photo" i], '
            'button[aria-label*="profile picture" i], '
            'button[aria-label*="change" i], '
            'a[aria-label*="photo" i], '
            'a[aria-label*="profile" i], '
            '[jsname="C1BmOf"], '          # Google's profile photo container
            '[data-use-hover-menu] img'    # hoverable avatar img
        ).first

        if await photo_trigger.count() == 0:
            photo_trigger = self.page.locator(
                'img[alt*="profile" i], img[alt*="photo" i], '
                'img[src*="googleusercontent"], img[src*="lh3.google"]'
            ).first

        if await photo_trigger.count() == 0:
            self.log.warning("[ml-setup] Profile photo button not found on personal-info page")
            return False

        # The click may open a file chooser directly or a modal first
        try:
            async with self.page.expect_file_chooser(timeout=4000) as fc_info:
                await photo_trigger.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(photo_path))
        except Exception:
            # No immediate file chooser — a modal opened instead; look for upload option
            await self.humaniser.pause(1000, 2000)
            upload_btn = self.page.locator(
                'button:has-text("Upload"), '
                'button:has-text("Add a photo"), '
                'button:has-text("Change photo"), '
                '[aria-label*="Upload" i]'
            ).first
            if await upload_btn.count() == 0:
                self.log.warning("[ml-setup] Upload button not found after photo modal opened")
                return False
            try:
                async with self.page.expect_file_chooser(timeout=5000) as fc_info:
                    await upload_btn.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(str(photo_path))
            except Exception as e:
                self.log.warning("[ml-setup] File chooser not triggered: %s", e)
                return False

        await self.humaniser.pause(2000, 4000)

        # Confirm / save (crop dialog or confirmation button)
        confirm = self.page.locator(
            'button:has-text("Set as profile photo"), '
            'button:has-text("Save"), '
            'button:has-text("Done"), '
            'button:has-text("Apply")'
        ).first
        if await confirm.count() > 0:
            await confirm.click()
            await self.humaniser.pause(2000, 3500)

        self.log.info("[ml-setup] Profile photo uploaded: %s", photo_filename)
        return True

    async def _enable_web_app_activity(self) -> bool:
        """Navigate to myaccount.google.com and enable Web & App Activity."""
        await self.page.goto(
            "https://myaccount.google.com/activitycontrols/webandapp",
            wait_until="domcontentloaded", timeout=20000,
        )
        await self.humaniser.pause(2000, 4000)
        await self.dismiss_cookie_banner()
        result = await self._ensure_activity_toggle_on("web_app_activity")
        return result != "not_found"

    async def _enable_youtube_history(self) -> bool:
        await self.page.goto(
            "https://myaccount.google.com/activitycontrols/youtube",
            wait_until="domcontentloaded", timeout=20000,
        )
        await self.humaniser.pause(2000, 4000)
        result = await self._ensure_activity_toggle_on("youtube_history")
        return result != "not_found"

    async def _enable_location_history(self) -> bool:
        await self.page.goto(
            "https://myaccount.google.com/activitycontrols/location",
            wait_until="domcontentloaded", timeout=20000,
        )
        await self.humaniser.pause(2000, 4000)
        result = await self._ensure_activity_toggle_on("location_history")
        if result == "not_found":
            # Google's Timeline/Location History is often unavailable for new account
            # types ("not currently available for your account type"). The only checkbox
            # is disabled — this is not a failure, just not applicable via web.
            self.log.info("[ml-setup] location_history: not available via web (Timeline disabled for this account type) — skipping")
            return True
        return True

    async def _set_display_name(self, first: str, last: str) -> bool:
        await self.page.goto(
            "https://myaccount.google.com/name",
            wait_until="domcontentloaded", timeout=20000,
        )
        await self.humaniser.pause(2000, 3500)

        # Click the "Edit" pencil for name
        edit_btns = self.page.locator('button[aria-label*="Edit"], button[aria-label*="name"]')
        if await edit_btns.count() > 0:
            await edit_btns.first.click()
            await self.humaniser.pause(1000, 2000)

        # Fill first name
        first_field = self.page.locator('input[name="firstName"], input[aria-label*="First"]').first
        if await first_field.count() > 0:
            await first_field.triple_click()
            await self.humaniser.type_text(first_field, first)

        # Fill last name
        last_field = self.page.locator('input[name="lastName"], input[aria-label*="Last"]').first
        if await last_field.count() > 0:
            await last_field.triple_click()
            await self.humaniser.type_text(last_field, last)

        # Save
        save_btn = self.page.locator('button:has-text("Save"), button[type="submit"]').first
        if await save_btn.count() > 0:
            await self.humaniser.pause(800, 1500)
            await save_btn.click()
            await self.humaniser.pause(1500, 3000)

        self.log.info(f"[ml-setup] Display name set: {first} {last}")
        return True

    async def _set_birthday(self, day: int, month: int, year: int) -> bool:
        await self.page.goto(
            "https://myaccount.google.com/birthday",
            wait_until="domcontentloaded", timeout=20000,
        )
        await self.humaniser.pause(2000, 3500)

        # Click edit
        edit_btn = self.page.locator('button[aria-label*="Edit"], button[aria-label*="Birthday"]').first
        if await edit_btn.count() > 0:
            await edit_btn.click()
            await self.humaniser.pause(1000, 2000)

        # Month select
        month_sel = self.page.locator('select[aria-label*="Month"], select[id*="month"]').first
        if await month_sel.count() > 0:
            await month_sel.select_option(value=str(month))
            await self.humaniser.pause(300, 700)

        # Day input
        day_field = self.page.locator('input[aria-label*="Day"], input[id*="day"]').first
        if await day_field.count() > 0:
            await day_field.triple_click()
            await self.humaniser.type_text(day_field, str(day))

        # Year input
        year_field = self.page.locator('input[aria-label*="Year"], input[id*="year"]').first
        if await year_field.count() > 0:
            await year_field.triple_click()
            await self.humaniser.type_text(year_field, str(year))

        # Save
        save_btn = self.page.locator('button:has-text("Save"), button[type="submit"]').first
        if await save_btn.count() > 0:
            await self.humaniser.pause(800, 1500)
            await save_btn.click()
            await self.humaniser.pause(1500, 3000)

        self.log.info(f"[ml-setup] Birthday set: {day}/{month}/{year}")
        return True

    async def _set_maps_labeled_place(self, address: str, label: str) -> bool:
        """
        Set a Home or Work address via Google Account Personal Info.

        Flow:
          1. Go to myaccount.google.com/personal-info
          2. Click the Home / Work address row
          3. Type the address in the input box
          4. Click the first autocomplete suggestion (pin icon below the box)
          5. Click Save
        """
        self.log.info("[ml-setup] Setting %s address via Google Account: %s", label, address)

        # ── Step 1: Go to Google Account Personal Info ───────────────────────────
        await self.page.goto(
            "https://myaccount.google.com/personal-info",
            wait_until="domcontentloaded", timeout=25000,
        )
        await self.humaniser.pause(2000, 3500)
        await self.dismiss_cookie_banner()
        await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_1_personalinfo.png")

        # ── Step 2: Click the Home / Work address row ────────────────────────────
        # Google renders each field as a clickable row with the label text
        row_clicked = False
        for row_sel in [
            f'[data-section-id*="{label.lower()}" i]',
            f'a[href*="{label.lower()}address" i]',
            f'a[href*="address/{label.lower()}" i]',
            f'div[role="button"]:has-text("{label} address")',
            f'div[role="button"]:has-text("{label}")',
            f'li:has-text("{label} address")',
            f'li:has-text("{label}")',
        ]:
            row = self.page.locator(row_sel).first
            try:
                if await row.count() > 0 and await row.is_visible(timeout=1500):
                    await row.click()
                    row_clicked = True
                    self.log.info("[ml-setup] %s: clicked address row via %s", label, row_sel)
                    await self.humaniser.pause(1500, 2500)
                    break
            except Exception:
                continue

        if not row_clicked:
            self.log.warning("[ml-setup] %s: address row not found on personal-info page", label)
            await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_norow.png")
            return False

        await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_2_row_clicked.png")

        # ── Step 3: Find the address input and type ──────────────────────────────
        addr_input = None
        for inp_sel in [
            f'input[aria-label*="{label}" i]',
            f'input[placeholder*="{label}" i]',
            'input[aria-label*="address" i]',
            'input[aria-label*="Address" i]',
            'input[type="text"]:visible',
        ]:
            candidate = self.page.locator(inp_sel).first
            try:
                if await candidate.count() > 0 and await candidate.is_visible(timeout=2000):
                    addr_input = candidate
                    self.log.info("[ml-setup] %s: found address input via %s", label, inp_sel)
                    break
            except Exception:
                continue

        if addr_input is None:
            self.log.warning("[ml-setup] %s: address input not found", label)
            await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_noinput.png")
            return False

        await addr_input.click()
        await self.humaniser.pause(300, 600)
        await addr_input.fill("")
        # Type character by character to trigger autocomplete
        for char in address:
            await self.page.keyboard.type(char)
            await self.humaniser.pause(40, 90)
        await self.humaniser.pause(2000, 3000)
        await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_3_typed.png")

        # ── Step 4: Click first autocomplete suggestion (pin icon below the box) ─
        suggestion_clicked = False
        for sug_sel in [
            '[role="option"]:visible',
            '[role="listbox"] li:visible',
            '.pac-item:visible',
            'ul[role="listbox"] li:first-child',
            'div[data-attrid]:visible',
        ]:
            sug = self.page.locator(sug_sel).first
            try:
                if await sug.count() > 0 and await sug.is_visible(timeout=3000):
                    await sug.click()
                    suggestion_clicked = True
                    self.log.info("[ml-setup] %s: clicked suggestion via %s", label, sug_sel)
                    break
            except Exception:
                continue

        if not suggestion_clicked:
            self.log.info("[ml-setup] %s: no suggestion found — pressing Enter", label)
            await self.page.keyboard.press("ArrowDown")
            await self.humaniser.pause(300, 500)
            await self.page.keyboard.press("Enter")

        await self.humaniser.pause(1500, 2500)
        await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_4_selected.png")

        # ── Step 5: Click Save ───────────────────────────────────────────────────
        for save_sel in [
            'button:has-text("Save")',
            'button[aria-label*="Save" i]',
            'input[type="submit"][value*="Save" i]',
        ]:
            save_btn = self.page.locator(save_sel).first
            try:
                if await save_btn.count() > 0 and await save_btn.is_visible(timeout=3000):
                    await save_btn.click()
                    await self.humaniser.pause(2000, 3000)
                    self.log.info("[ml-setup] %s address saved: %s", label, address)
                    await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_5_saved.png")
                    return True
            except Exception:
                continue

        self.log.warning("[ml-setup] %s: Save button not found", label)
        await self.page.screenshot(path=f"C:/WarmingData/addr_{label.lower()}_nosave.png")
        return False

    async def _enrol_local_guides(self) -> bool:
        await self.page.goto(
            "https://maps.google.com/localguides/",
            wait_until="domcontentloaded", timeout=25000,
        )
        await self.humaniser.pause(3000, 5000)
        await self.dismiss_cookie_banner()

        # Look for "Join Local Guides" or "Get started" button
        join_btn = self.page.locator(
            'a:has-text("Join"), a:has-text("Get started"), '
            'button:has-text("Join"), button:has-text("Get started")'
        ).first
        if await join_btn.count() > 0:
            await join_btn.click()
            await self.humaniser.pause(2000, 4000)
            # Confirm any follow-up dialog
            confirm = self.page.locator('button:has-text("Join"), button:has-text("Confirm")').first
            if await confirm.count() > 0:
                await confirm.click()
                await self.humaniser.pause(1500, 3000)
            self.log.info("[ml-setup] Local Guides enrolment: join button clicked")
        else:
            self.log.debug("[ml-setup] Local Guides: join button not found (may already be member)")
        return True

    # ── Helpers ──────────────────────────────────────────────────────────────────

    async def _ensure_activity_toggle_on(self, label: str = "toggle") -> str:
        """
        Find the main activity toggle on the current activitycontrols page.

        Google renders these as Material Design checkboxes:
          <input type="checkbox" jsname="YPqjbf" ...>
        The first non-disabled one is the primary activity toggle.
        We use force=True / check() because these inputs are visually hidden.

        Returns:
          "turned_on"  — was off, we clicked it on and confirmed
          "already_on" — was already on, nothing to do
          "not_found"  — no enabled toggle detected on the page
        """
        try:
            # Google activitycontrols use Material Design hidden checkboxes
            # The first non-disabled one is the main activity on/off toggle
            _TOGGLE_SEL = 'input[type="checkbox"][jsname="YPqjbf"]:not([disabled])'

            toggle = self.page.locator(_TOGGLE_SEL).first
            if await toggle.count() == 0:
                # Fallback: any non-disabled checkbox on the page
                toggle = self.page.locator('input[type="checkbox"]:not([disabled])').first

            if await toggle.count() == 0:
                self.log.warning(f"[ml-setup] {label}: no enabled toggle found on page")
                return "not_found"

            # Check current state
            is_checked = await toggle.is_checked()
            if is_checked:
                self.log.info(f"[ml-setup] {label}: already ON")
                return "already_on"

            # Toggle is OFF — click it on (force=True bypasses visual hidden state)
            await toggle.check(force=True)
            await self.humaniser.pause(1000, 2500)

            # Dismiss any confirmation dialog that appears
            confirm = self.page.locator(
                'button:has-text("Turn on"), button:has-text("Confirm"), '
                'button:has-text("I agree"), button:has-text("Got it")'
            ).first
            if await confirm.count() > 0 and await confirm.is_visible(timeout=2000):
                await confirm.click()
                await self.humaniser.pause(1000, 2000)

            # Verify now ON
            is_checked_now = await toggle.is_checked()
            if is_checked_now:
                self.log.info(f"[ml-setup] {label}: toggled ON successfully")
            else:
                self.log.warning(f"[ml-setup] {label}: clicked but could not verify ON state")
            return "turned_on"

        except Exception as e:
            self.log.warning(f"[ml-setup] {label}: exception checking toggle — {e}")
            return "not_found"
