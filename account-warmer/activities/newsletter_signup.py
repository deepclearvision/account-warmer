"""
Newsletter Signup Activity
Signs the account up to a small number of real newsletters.
- Targets sites using Mailchimp, Klaviyo, or simple custom forms
- Tracks which newsletters have already been signed up to (won't duplicate)
- Runs only in weeks 1-2 (sign up early so confirmations arrive during ramp-up)
- The email_read module handles clicking confirmation links automatically
"""

import random
import json
from pathlib import Path
from playwright.async_api import Page

from activities.base_activity import BaseActivity
from core.orchestrator import STATE_DIR

NEWSLETTER_FILE = Path(__file__).parent.parent / "data" / "newsletters.json"


class NewsletterSignupActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict):
        super().__init__(page, account, behaviour_cfg)
        with open(NEWSLETTER_FILE, encoding="utf-8") as f:
            self._data = json.load(f)
        self._state_file = STATE_DIR / f"{account['id']}_newsletters.json"
        self._subscribed  = self._load_subscribed()

    async def run(self) -> bool:
        """Sign up to 1-2 newsletters this session."""
        available = self._get_unsubscribed()
        if not available:
            self.log.info("Already subscribed to all available newsletters.")
            return True

        # Pick 1-2 to sign up to this session (don't do too many at once)
        to_signup = random.sample(available, min(len(available), random.randint(1, 2)))

        for newsletter in to_signup:
            success = await self._signup(newsletter)
            if success:
                self._mark_subscribed(newsletter["name"])
            await self.humaniser.pause(8000, 20000)  # Long gap between signups

        return True

    # ------------------------------------------------------------------
    # Signup logic
    # ------------------------------------------------------------------

    async def _signup(self, newsletter: dict) -> bool:
        name     = newsletter["name"]
        url      = newsletter["url"]
        platform = newsletter.get("platform", "generic")
        email    = self.account["email"]

        self.log.info(f"Signing up to newsletter: {name} ({url})")

        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await self.humaniser.pause(2000, 5000)
            await self._dismiss_cookie_banner()
            await self.humaniser.human_scroll(self.page, max_fraction=0.4)
            await self.humaniser.pause(1000, 3000)

            # Pick the right selector strategy for this platform
            email_sel, submit_sel = self._get_selectors(newsletter, platform)

            found = await self._try_fill_and_submit(email_sel, submit_sel, email)

            if not found:
                # Fallback: try generic selectors
                generic = self._data["generic_selectors"]
                found = await self._try_fill_and_submit(
                    generic["email"], generic["submit"], email
                )

            if found:
                self.log.info(f"Signup form submitted for: {name}")
                await self.humaniser.pause(3000, 6000)
                # Some sites show a success message — pause to "read" it
                await self.humaniser.reading_pause()
                return True
            else:
                self.log.warning(f"Could not find signup form on: {url}")
                return False

        except Exception as e:
            self.log.error(f"Newsletter signup failed for {name}: {e}")
            return False

    def _get_selectors(self, newsletter: dict, platform: str) -> tuple[str, str]:
        """Return (email_selector, submit_selector) for the given platform."""
        if platform == "mailchimp":
            s = self._data["mailchimp_selectors"]
            return s["email"], s["submit"]
        elif platform == "klaviyo":
            s = self._data["klaviyo_selectors"]
            return s["email"], s["submit"]
        else:
            # Use newsletter-specific selectors if provided, else generic
            email_sel  = newsletter.get("email_selector",  self._data["generic_selectors"]["email"])
            submit_sel = newsletter.get("submit_selector", self._data["generic_selectors"]["submit"])
            return email_sel, submit_sel

    async def _try_fill_and_submit(self, email_sel: str, submit_sel: str, email: str) -> bool:
        """
        Try to find an email input, fill it, and submit.
        Returns True if the form was found and submitted.
        """
        try:
            email_input = self.page.locator(email_sel).first
            if not await email_input.is_visible(timeout=4000):
                return False

            # Scroll the input into view
            await email_input.scroll_into_view_if_needed()
            await self.humaniser.pause(500, 1200)

            # Click and type the email address
            await email_input.click()
            await self.humaniser.pause(300, 700)
            for char in email:
                delay = max(40, min(300, int(random.gauss(95, 35))))
                await email_input.type(char, delay=delay)

            await self.humaniser.pause(800, 2000)

            # Find and click submit
            submit_btn = self.page.locator(submit_sel).first
            if await submit_btn.is_visible(timeout=3000):
                await self.humaniser.human_click(self.page, submit_sel)
                return True

            # Fallback: press Enter
            await self.page.keyboard.press("Enter")
            return True

        except Exception as e:
            self.log.debug(f"Form fill attempt failed: {e}")
            return False

    async def _dismiss_cookie_banner(self) -> None:
        for selector in [
            'button:has-text("Accept all")',
            'button:has-text("Accept cookies")',
            'button:has-text("I accept")',
            'button:has-text("OK")',
            'button:has-text("Got it")',
            'button:has-text("Agree")',
            '[id*="cookie"] button',
            '[class*="cookie"] button',
        ]:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await self.humaniser.pause(500, 1000)
                    return
            except Exception:
                pass

    # ------------------------------------------------------------------
    # State tracking — don't re-subscribe
    # ------------------------------------------------------------------

    def _load_subscribed(self) -> set:
        if self._state_file.exists():
            try:
                with open(self._state_file, encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception:
                pass
        return set()

    def _mark_subscribed(self, name: str) -> None:
        self._subscribed.add(name)
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(list(self._subscribed), f, indent=2)

    def _get_unsubscribed(self) -> list:
        all_nl = self._data["newsletters"]
        return [n for n in all_nl if n["name"] not in self._subscribed]
