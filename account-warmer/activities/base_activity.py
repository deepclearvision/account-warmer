"""
Base class for all activity modules.
Each activity receives a Playwright Page, account config, and shared utilities.
"""

import random
from abc import ABC, abstractmethod
from playwright.async_api import Page
from core.humaniser import Humaniser
from core.logger import get_logger


# Button texts that dismiss cookie/consent banners.
# "Reject all" is intentionally included — real users click either.
_COOKIE_ACCEPT_TEXTS = [
    "Accept all", "Accept All", "Accept cookies", "Accept Cookies",
    "Allow all", "Allow All", "Allow cookies", "I accept", "I Agree",
    "Agree", "Agree & close", "Consent", "Got it", "OK", "Okay",
    "Continue", "Reject all", "Reject All", "Decline all",
    "Close", "Dismiss",
]

# Well-known cookie framework element IDs / selectors
_COOKIE_FRAMEWORK_SELECTORS = [
    "#onetrust-accept-btn-handler",                         # OneTrust
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",  # Cookiebot
    ".cc-accept", ".cc-btn.cc-allow",                       # cc-cookie
    "#accept-cookies", "#acceptCookies", "#cookie-accept",
    "[data-testid='cookie-policy-banner-accept']",
    "[aria-label='Accept cookies']",
    "[aria-label='Accept all cookies']",
    "button.accept-all", "button.accept-cookies",
    ".cookie-consent-accept", ".js-accept-cookies",
    "#gdpr-cookie-accept", "#cookie_action_close_header",
]


class BaseActivity(ABC):
    def __init__(self, page: Page, account: dict, behaviour_cfg: dict):
        self.page     = page
        self.account  = account
        self.humaniser = Humaniser(behaviour_cfg)
        self.log      = get_logger(account["id"])

    @abstractmethod
    async def run(self) -> bool:
        """Execute the activity. Return True on success, False on failure."""
        ...

    async def dismiss_cookie_banner(self) -> bool:
        """
        Attempt to dismiss a cookie/GDPR consent banner on the current page.
        Tries well-known framework selectors first, then falls back to button
        text matching. Uses short timeouts so it never blocks the activity.
        Returns True if a banner was dismissed, False if none was found.

        Always accepts — pauses 1.5–4 seconds first (reading time).
        """
        # Reading pause before interacting — 1.5 to 4 seconds
        await self.humaniser.pause(1500, 4000)

        # 1. Known framework selectors (fastest, most specific)
        for sel in _COOKIE_FRAMEWORK_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                    await loc.click()
                    await self.humaniser.pause(400, 800)
                    self.log.debug(f"Cookie banner dismissed via selector: {sel}")
                    return True
            except Exception:
                continue

        # 2. Text-based button matching — always prefer accept
        for text in _COOKIE_ACCEPT_TEXTS:
            try:
                loc = self.page.locator(f'button:has-text("{text}")').first
                if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                    await loc.click()
                    await self.humaniser.pause(400, 800)
                    self.log.debug(f"Cookie banner dismissed via text: '{text}'")
                    return True
            except Exception:
                continue

        return False
