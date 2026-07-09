"""
Base class for all activity modules.
Each activity receives a Playwright Page, account config, and shared utilities.
"""

import json
import random
from datetime import datetime
from abc import ABC, abstractmethod
from pathlib import Path
from playwright.async_api import Page
from core.humaniser import Humaniser
from core.logger import get_logger
from core.paths import STATE_DIR, LOGS_DIR


# ── Cookie / consent banner dismissal ─────────────────────────────────────────
#
#  Centralised handler used by every activity.  Three-phase detection with
#  retry (banners often load asynchronously), visual confirmation, and
#  structured logging so we always know what happened.
#
#  Design principles:
#   1.  Wait for the page to settle before checking (banners load async).
#   2.  Three detection phases: framework selectors → button text → overlay scan.
#   3.  Retry at increasing intervals (0s / 2s / 5s) before giving up.
#   4.  After clicking, verify the banner actually disappeared.
#   5.  Always log — INFO on success, WARNING on failure, DEBUG on absent.
#   6.  Accept AND reject are both valid — real users do both.
#   7.  Short per-selector timeout (2s) so the whole check is fast.
#   8.  Returns a dict so callers can inspect what happened.

import asyncio

# Duration to wait for the page to settle before the first check.
# Many sites load the consent banner 0.5–2 s after the main content.
_COOKIE_INITIAL_PAUSE_MS = (1200, 2500)

# Per-selector visibility timeout — kept short so 40+ selectors don't add up.
_COOKIE_SELECTOR_TIMEOUT_MS = 2000

# How many times to retry the full detection loop, and the delay between retries.
_COOKIE_RETRY_DELAYS_SEC = [0, 2.5, 5.5]

# Post-click pause (lets the banner animate out).
_COOKIE_POST_CLICK_PAUSE_MS = (400, 900)


# ── Phase 1: Well-known framework selectors ──────────────────────────────────
#   Fast and specific.  OneTrust, Cookiebot, TrustArc, Quantcast, etc.
_COOKIE_FRAMEWORK_SELECTORS = [
    # OneTrust
    "#onetrust-accept-btn-handler",
    "#onetrust-reject-all-handler",
    "#onetrust-pc-btn-handler button",
    "button[aria-label='Accept All']",          # OneTrust v9
    # Cookiebot
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#CybotCookiebotDialogBodyButtonDecline",
    # TrustArc / TRUSTe
    "#truste-consent-button",
    "#truste-consent-required",
    ".truste-consent-button",
    # Quantcast
    ".qc-cmp2-summary-buttons button[mode='primary']",
    ".qc-cmp2-footer button[mode='primary']",
    # Generic cookie plugins
    ".cc-accept", ".cc-btn.cc-allow",
    ".cc-dismiss", ".cc-btn-dismiss",
    "#cookie-law-info-bar .cli_action_button",
    "#catapultCookie",
    ".cookie-accept", ".cookie-consent button",
    ".gdpr-accept", "#gdpr-cookie-accept",
    "#cookie_action_close_header",
    "#accept-cookies", "#acceptCookies", "#cookie-accept",
    # Consent Management Platforms
    "[data-testid='cookie-policy-banner-accept']",
    "[data-testid='cookie-policy-manager'] button",
    "[aria-label='Accept cookies']",
    "[aria-label='Accept all cookies']",
    "[aria-label='Allow all cookies']",
    "[aria-label='Allow cookies']",
    "[aria-label='Reject cookies']",
    "[aria-label='Reject all cookies']",
    # Generic class-based
    "button.accept-all", "button.accept-cookies",
    ".cookie-consent-accept", ".js-accept-cookies",
    ".cookie-banner button",
    # Google-specific consent (varies by region/product)
    "form[action*='consent'] button",
    "div[jsname='b3L6Sc'] button",
    "div[role='dialog'] button[jsname]",
    # Meta / Facebook
    "[data-testid='cookie-policy-manage-dialog-accept-button']",
    # TCF v2
    ".tcf-consent button",
    "button[aria-label*='Consent']",
]

# ── Phase 2: Button text matching ────────────────────────────────────────────
#   Both ACCEPT and REJECT patterns.  Real users click either; both
#   clear the overlay so the page is usable.
_COOKIE_ACCEPT_TEXTS = [
    # Strong accept
    "Accept all", "Accept All", "Accept all cookies",
    "Allow all", "Allow All", "Allow all cookies",
    "Accept cookies", "Accept Cookies",
    "Allow cookies", "Allow Cookies",
    "I accept", "I Agree", "I agree",
    "Agree", "Agree & close", "Agree and close",
    "Consent", "I consent",
    "Use necessary cookies only",
    # Neutral / dismiss
    "OK", "Okay", "Got it", "I understand",
    "Continue", "Continue to site",
    "Dismiss", "Close", "No thanks",
    # Reject
    "Reject all", "Reject All", "Reject non-essential",
    "Decline all", "Decline All", "Decline",
    "Deny", "Deny all",
    "Manage options", "Cookie settings",
    # French / German (common on CDN-fronted sites)
    "Tout accepter", "Accepter",
    "Alle akzeptieren", "Alles akzeptieren",
]

# ── Phase 3: Overlay detection — generic consent overlays ────────────────────
#   Fixed-position elements that look like consent banners.  We scan for
#   any fixed element containing consent-like text and click the first
#   button inside it.  Only tried after Phase 1 & 2 fail.
_COOKIE_OVERLAY_TEXTS = [
    "cookie", "consent", "gdpr", "privacy", "data protection",
    "we use cookies", "this site uses", "we value your privacy",
    "your privacy", "personal data", "cookie policy",
]

# Legal suffixes commonly appended to business names on Google Maps.
# Stripped during normalization so "Streatham Plumbing Ltd" matches
# "Streatham Plumbing & Heating Ltd".
_LEGAL_SUFFIXES = ["ltd", "limited", "llp", "plc", "inc", "co.", "co"]


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

    # ------------------------------------------------------------------
    # Cookie / consent banner dismissal
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Cookie / consent banner dismissal  (centralised, all activities)
    # ------------------------------------------------------------------

    async def dismiss_cookie_banner(self) -> dict:
        """
        Attempt to dismiss any cookie / GDPR consent banner on the current
        page.  Uses adaptive wait → three-phase detection → retry with
        increasing intervals → visual confirmation.

        Returns a dict so callers can inspect what happened:
          {"dismissed": True/False,
           "method":    "framework" | "text" | "overlay" | None,
           "detail":    "onetrust-accept-btn-handler" | "Accept all" | …,
           "attempts":  1-3,
           "confirmed": True/False}
        """
        result = {"dismissed": False, "method": None,
                  "detail": None, "attempts": 0, "confirmed": False}

        for attempt, delay_sec in enumerate(_COOKIE_RETRY_DELAYS_SEC, start=1):
            result["attempts"] = attempt
            if attempt == 1:
                # First attempt: wait for page to settle
                await self.humaniser.pause(*_COOKIE_INITIAL_PAUSE_MS)
            elif delay_sec > 0:
                await asyncio.sleep(delay_sec)

            # ── Phase 1: Framework selectors ──────────────────────────
            for sel in _COOKIE_FRAMEWORK_SELECTORS:
                try:
                    loc = self.page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible(
                        timeout=_COOKIE_SELECTOR_TIMEOUT_MS
                    ):
                        await self.humaniser.human_click(self.page, sel)
                        await self.humaniser.pause(*_COOKIE_POST_CLICK_PAUSE_MS)
                        confirmed = await self._confirm_banner_gone()
                        self.log.info(
                            f"Cookie banner dismissed | phase=framework "
                            f"selector={sel!r} attempt={attempt} "
                            f"confirmed={confirmed}"
                        )
                        return {"dismissed": True, "method": "framework",
                                "detail": sel, "attempts": attempt,
                                "confirmed": confirmed}
                except Exception:
                    continue

            # ── Phase 2: Button text matching ─────────────────────────
            for text in _COOKIE_ACCEPT_TEXTS:
                try:
                    loc = self.page.locator(
                        f'button:has-text("{text}"):visible'
                    ).first
                    if await loc.count() > 0:
                        await self.humaniser.human_click(
                            self.page,
                            f'button:has-text("{text}")'
                        )
                        await self.humaniser.pause(*_COOKIE_POST_CLICK_PAUSE_MS)
                        confirmed = await self._confirm_banner_gone()
                        self.log.info(
                            f"Cookie banner dismissed | phase=text "
                            f"text={text!r} attempt={attempt} "
                            f"confirmed={confirmed}"
                        )
                        return {"dismissed": True, "method": "text",
                                "detail": text, "attempts": attempt,
                                "confirmed": confirmed}
                except Exception:
                    continue

            # ── Phase 3: Generic overlay scan ─────────────────────────
            overlay_result = await self._dismiss_overlay_banner()
            if overlay_result:
                self.log.info(
                    f"Cookie banner dismissed | phase=overlay "
                    f"text={overlay_result!r} attempt={attempt}"
                )
                return {"dismissed": True, "method": "overlay",
                        "detail": overlay_result, "attempts": attempt,
                        "confirmed": True}

        self.log.debug(
            f"Cookie banner: none found after {result['attempts']} attempt(s) "
            f"on {self.page.url[:80]!r}"
        )
        return result

    async def _confirm_banner_gone(self) -> bool:
        """
        After clicking a dismiss button, verify the banner element is no
        longer visible.  Checks whether the clicked selector's element
        count dropped to zero or is hidden.
        Returns True if we can confirm the banner disappeared.
        """
        try:
            await asyncio.sleep(0.3)  # brief settle
            # Check a few common banner container selectors
            for banner_sel in [
                "#onetrust-consent-sdk",
                "#CybotCookiebotDialog",
                ".cc-window",
                "#cookie-law-info-bar",
                ".cookie-consent",
                ".gdpr-banner",
                "[data-testid='cookie-policy-banner']",
                "div[aria-label*='cookie' i]",
                "div[aria-label*='consent' i]",
            ]:
                try:
                    el = self.page.locator(banner_sel).first
                    if await el.count() > 0:
                        visible = await el.is_visible(timeout=500)
                        if visible:
                            return False  # banner still visible
                except Exception:
                    continue
            return True  # all checked, none visible
        except Exception:
            return True  # assume success if we can't check

    async def _dismiss_overlay_banner(self) -> str | None:
        """
        Scan for fixed-position elements containing consent-like text.
        If found, click the first button inside.  Returns the matched
        text snippet, or None if no overlay was detected.
        """
        try:
            for text in _COOKIE_OVERLAY_TEXTS:
                try:
                    overlay = self.page.locator(
                        f'div:has-text("{text}"):visible'
                    ).first
                    if await overlay.count() == 0:
                        continue
                    # Check it's positioned like a banner (fixed or sticky)
                    pos = await overlay.evaluate(
                        "el => getComputedStyle(el).position"
                    )
                    if pos not in ("fixed", "sticky"):
                        continue
                    # Found one — click the first button inside it
                    btn = overlay.locator("button").first
                    if await btn.count() > 0 and await btn.is_visible(
                        timeout=1000
                    ):
                        await self.humaniser.human_click(
                            self.page,
                            f'div:has-text("{text}") button'
                        )
                        await self.humaniser.pause(*_COOKIE_POST_CLICK_PAUSE_MS)
                        return text
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def dismiss_google_consent(self) -> dict:
        """
        Convenience wrapper that runs the main dismiss_cookie_banner and
        also tries Google-specific consent patterns (the full-page
        'Before you continue' dialog that appears before google.com).
        """
        result = await self.dismiss_cookie_banner()
        if result["dismissed"]:
            return result

        # Google sometimes shows a full-page consent wall with a blue
        # 'Accept all' / 'I agree' / 'Reject all' button inside a
        # dialog that the standard framework selectors may miss.
        google_selectors = [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Reject all")',
            'form[action*="consent"] button',
            'div[role="dialog"] button:has-text("Accept")',
        ]
        for sel in google_selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                    await self.humaniser.human_click(self.page, sel)
                    await self.humaniser.pause(*_COOKIE_POST_CLICK_PAUSE_MS)
                    self.log.info(
                        f"Google consent dismissed via: {sel!r}"
                    )
                    return {"dismissed": True, "method": "google",
                            "detail": sel, "attempts": 1, "confirmed": True}
            except Exception:
                continue

        return result

    # ------------------------------------------------------------------
    # Gmail first-time onboarding popup dismissal
    # ------------------------------------------------------------------

    # Google shows a series of onboarding modals the first time a profile
    # opens Gmail.  They chain — dismissing one often reveals another.
    # These are NOT cookie banners; they're Gmail-specific feature modals.
    #
    # Known popups in order of appearance:
    #   1. "Welcome to Gmail" / "Get started"
    #   2. "Choose your inbox view" (default vs compact)
    #   3. "Turn on smart features & personalization"
    #   4. "Try Google Chat" / "Stay connected with Meet"
    #   5. "Add a profile photo"
    #   6. Miscellaneous feature announcements ("Got it" / "OK" / Close)

    # Ordered from most-specific to least-specific so we don't click
    # "Got it" on a cookie banner when we meant to dismiss an onboarding modal.
    _GMAIL_ONBOARDING_SELECTORS = [
        # Step 1 — Welcome / get started
        'button:has-text("Get started")',
        'button:has-text("Welcome")',
        # Step 2 — Choose inbox type (radio + confirm)
        'button:has-text("Inbox type")',
        'button:has-text("Choose")',
        'label:has-text("Default")',
        'label:has-text("Important first")',
        'label:has-text("Unread first")',
        'label:has-text("Priority")',
        'div[role="radio"]:has-text("Default")',
        'div[role="radio"]:has-text("Important")',
        # Step 3 — Smart features & personalization
        'button:has-text("Turn on smart features")',
        'button:has-text("Turn on")',
        'button:has-text("No thanks")',
        'button:has-text("I\'ll do this later")',
        'button:has-text("Later")',
        'button:has-text("Not now")',
        # Step 4 — Chat
        'button:has-text("Try Google Chat")',
        'button:has-text("Try Chat")',
        'button:has-text("Skip")',
        # Step 5 — Meet / Hangouts
        'button:has-text("Try Meet")',
        'button:has-text("Try Google Meet")',
        'button:has-text("Stay connected")',
        # Step 6 — Profile photo
        'button:has-text("Add a photo")',
        'button:has-text("Add photo")',
        # Step 7 — Confirmation / Save / Done
        'button:has-text("Done")',
        'button:has-text("Save")',
        'button:has-text("Finish")',
        'button:has-text("Confirm")',
        # General purpose (last — too broad otherwise)
        'button:has-text("OK")',
        'button:has-text("Got it")',
        'button:has-text("Next")',
        'button:has-text("Continue")',
        'button:has-text("I agree")',
        'button:has-text("Set up")',
        'button:has-text("Start")',
        # Close / dismiss (most generic)
        '[aria-label="Close"]',
        '[aria-label="Dismiss"]',
        'div[role="dialog"] [aria-label="Close"]',
        'div[role="dialog"] [aria-label="Dismiss"]',
        # Material Design dialog actions
        'div[role="dialog"] button[data-mdc-dialog-action="close"]',
        'div[role="dialog"] button[data-mdc-dialog-action="accept"]',
        'div[role="dialog"] button[data-mdc-dialog-action="save"]',
        # Generic blue buttons in onboarding dialogs (last resort)
        'div[role="dialog"] button[jsname]:not([aria-label="Close"]):not([aria-label="Dismiss"])',
    ]

    async def dismiss_gmail_onboarding(self) -> dict:
        """
        Dismiss Gmail's first-time onboarding popups (Welcome, Choose view,
        Smart features, Chat/Meet, Profile photo, etc.).

        The flow chains through up to 7 steps. Some modals require two
        clicks (select option then confirm), so we run multiple passes
        within each round until nothing new can be clicked.

        After all steps are cleared, looks for a final Save/Done button
        and then reloads Gmail to persist the choices.

        Returns a dict:
          {"dismissed": True/False,
           "dismissals": ["Turn on", "Got it", ...],
           "attempts": 1-8,
           "saved": True/False}
        """
        result = {"dismissed": False, "dismissals": [], "attempts": 0, "saved": False}
        max_rounds = 8  # up to 7 steps + safety

        for round_num in range(1, max_rounds + 1):
            result["attempts"] = round_num
            changed = False

            if round_num == 1:
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.6)

            # Within each round, keep clicking as long as we find NEW
            # matching selectors — this handles 2-click modals (e.g.,
            # select inbox type THEN click Done)
            passes = 0
            while passes < 3:  # max 3 passes per round
                passes += 1
                clicked_something = False
                for sel in self._GMAIL_ONBOARDING_SELECTORS:
                    try:
                        loc = self.page.locator(sel).first
                        if await loc.count() > 0 and await loc.is_visible(timeout=500):
                            await loc.click()
                            await asyncio.sleep(0.3)
                            result["dismissals"].append(sel)
                            clicked_something = True
                            changed = True
                            break  # one click, then re-scan (new elements may appear)
                    except Exception:
                        continue
                if not clicked_something:
                    break  # no more visible selectors in this round

            if not changed:
                # No selectors matched — modals all gone
                break

        # ── Final: look for Save/Done to commit choices ────────────────────
        if len(result["dismissals"]) > 0:
            await asyncio.sleep(0.8)
            for save_sel in [
                'button:has-text("Save")',
                'button:has-text("Done")',
                'button:has-text("Finish")',
                'button:has-text("Confirm")',
                'button:has-text("OK")',
                'button:has-text("Got it")',
            ]:
                try:
                    loc = self.page.locator(save_sel).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=500):
                        await loc.click()
                        await asyncio.sleep(0.5)
                        result["saved"] = True
                        result["dismissals"].append(save_sel)
                        self.log.info(f"Gmail onboarding saved via: {save_sel}")
                        break
                except Exception:
                    continue

            # ── Reload Gmail so changes take effect ───────────────────────
            try:
                await self.page.goto(
                    "https://mail.google.com/mail/u/0/#inbox",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await asyncio.sleep(1.0)
                self.log.info("Gmail reloaded after onboarding")
            except Exception:
                pass

        result["dismissed"] = len(result["dismissals"]) > 0
        if result["dismissed"]:
            self.log.info(
                f"Gmail onboarding: {len(result['dismissals'])} dismissal(s) "
                f"over {result['attempts']} round(s)"
                + (", saved" if result["saved"] else "")
            )
        else:
            self.log.debug("Gmail onboarding: no popups found")
        return result

    # ------------------------------------------------------------------
    # Business search verification
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_business_name(name: str) -> str:
        """
        Normalize a business name for fuzzy comparison.
        - Lowercase
        - Remove common legal suffixes (Ltd, Limited, LLP, PLC, Inc, Co.)
        - Normalize & to 'and'
        - Collapse whitespace
        """
        s = name.lower().strip()
        # Remove trailing legal suffix (e.g. "Plumbing Ltd" -> "Plumbing")
        for suffix in _LEGAL_SUFFIXES:
            if s.endswith(" " + suffix):
                s = s[:-(len(suffix) + 1)].strip()
                break
        # Normalize ampersand
        s = s.replace(" & ", " and ").replace("&", " and ")
        # Collapse whitespace
        s = " ".join(s.split())
        return s

    @staticmethod
    def _fuzzy_match_name(expected: str, actual: str) -> float:
        """
        Return 0.0–1.0 confidence that `actual` refers to the same
        business as `expected`. Uses token overlap after normalization.
        """
        norm_expected = BaseActivity._normalize_business_name(expected)
        norm_actual   = BaseActivity._normalize_business_name(actual)

        # Exact match after normalization
        if norm_expected == norm_actual:
            return 1.0

        # Substring containment — one is fully inside the other
        if norm_expected in norm_actual or norm_actual in norm_expected:
            return 0.85

        # Token overlap
        expected_tokens = set(norm_expected.split())
        actual_tokens   = set(norm_actual.split())
        if not expected_tokens:
            return 0.0

        overlap   = len(expected_tokens & actual_tokens)
        recall    = overlap / len(expected_tokens)
        precision = overlap / len(actual_tokens) if actual_tokens else 0.0
        # Weight recall (found expected words) higher than precision
        score = (0.7 * recall) + (0.3 * precision)
        return round(score, 2)

    async def verify_business_result(
        self,
        expected_name: str,
        expected_address: str | None = None,
        max_results_to_check: int = 5,
        min_confidence: float = 0.6,
    ) -> dict | None:
        """
        Verify that a specific business appears in Google Maps search results
        and click through to its listing.

        Collects up to *max_results_to_check* result elements, extracts each
        business name, fuzzy-matches against *expected_name*, and clicks the
        first result whose confidence >= *min_confidence*.

        Returns a verification dict on success, or None if no result matched.

        Return dict shape:
            {
                "matched": True,
                "name": "Southwark Plumbers",       # actual name from Maps
                "expected_name": "Southwark Plumbers",
                "confidence": 0.92,
                "result_index": 0,                   # 0-based position
                "all_results_seen": [...],           # every name examined
            }
        """
        all_names = []

        # Wait for the results panel to appear
        try:
            await self.page.wait_for_selector(
                'div[role="feed"] div.Nv2PK, div[role="feed"] a[href*="maps"]',
                timeout=15000,
            )
        except Exception:
            self.log.warning(
                f"verify_business_result: Maps results feed did not appear "
                f"(expected {expected_name!r})"
            )
            await self._capture_verification_screenshot(expected_name, "no_results_feed")
            return None

        # Collect result elements
        result_elements = await self.page.locator(
            'div[role="feed"] div.Nv2PK, div[role="feed"] a[href*="maps"]'
        ).all()

        if not result_elements:
            self.log.warning(
                f"verify_business_result: zero result elements found "
                f"(expected {expected_name!r})"
            )
            await self._capture_verification_screenshot(expected_name, "zero_results")
            return None

        # Check each result
        for idx in range(min(len(result_elements), max_results_to_check)):
            try:
                el = result_elements[idx]
                # Extract business name
                name_el = el.locator(
                    'div.qBF1Pd, span.fontHeadlineSmall, '
                    'div.fontHeadlineSmall, span[class*="headline"]'
                ).first
                actual_name = (await name_el.inner_text(timeout=2000)).strip()
            except Exception:
                actual_name = "(could not extract name)"

            all_names.append(actual_name)

            if not actual_name or actual_name.startswith("("):
                continue

            # Fuzzy match
            confidence = self._fuzzy_match_name(expected_name, actual_name)

            # Address boost — if a street/town from expected address appears
            # in the result snippet, raise confidence slightly.
            if confidence > 0.3 and expected_address:
                addr_parts = expected_address.lower().replace(",", "").split()
                significant = [p for p in addr_parts if len(p) > 3 and not p[0].isdigit()]
                try:
                    snippet_text = await el.inner_text(timeout=1000)
                    snippet_lower = snippet_text.lower()
                    addr_hits = sum(1 for p in significant if p in snippet_lower)
                    if addr_hits >= 2:
                        confidence = min(1.0, confidence + 0.15)
                    elif addr_hits >= 1:
                        confidence = min(1.0, confidence + 0.08)
                except Exception:
                    pass

            self.log.debug(
                f"verify_business_result[{idx}]: {actual_name!r} "
                f"vs {expected_name!r} → confidence={confidence:.0%}"
            )

            if confidence >= min_confidence:
                # Click the matching result
                try:
                    await el.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.humaniser.pause(1500, 3000)
                except Exception as e:
                    self.log.warning(f"Click on verified result failed: {e}")
                    return None

                self.log.info(
                    f"Business verified: {actual_name!r} "
                    f"(expected {expected_name!r}, confidence={confidence:.0%}, "
                    f"position={idx + 1}/{len(result_elements)})"
                )
                return {
                    "matched": True,
                    "name": actual_name,
                    "expected_name": expected_name,
                    "confidence": confidence,
                    "result_index": idx,
                    "all_results_seen": all_names,
                }

        # No match after checking all results
        self.log.warning(
            f"Business NOT verified: expected {expected_name!r}, "
            f"saw {all_names}"
        )
        await self._capture_verification_screenshot(expected_name, "no_match")
        self._write_verification_failure(expected_name, all_names)
        return None

    async def _capture_verification_screenshot(
        self, expected_name: str, reason: str
    ) -> None:
        """Take a debug screenshot when verification fails."""
        try:
            shot_dir = LOGS_DIR / "screenshots"
            shot_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            safe_name = expected_name.replace(" ", "_")[:40]
            fname = f"{self.account['id']}_verify_{reason}_{safe_name}_{ts}.png"
            await self.page.screenshot(path=str(shot_dir / fname))
            self.log.debug(f"Verification screenshot saved: {fname}")
        except Exception as e:
            self.log.debug(f"Verification screenshot failed: {e}")

    def _write_verification_failure(
        self, expected_name: str, all_results_seen: list[str]
    ) -> None:
        """Append a verification failure record for later review."""
        try:
            record_file = (
                STATE_DIR / f"{self.account['id']}_verification_failures.json"
            )
            if record_file.exists():
                records = json.loads(record_file.read_text(encoding="utf-8"))
            else:
                records = []

            records.append({
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "expected_name": expected_name,
                "results_seen": all_results_seen,
                "page_url": self.page.url,
            })

            # Keep last 100 failures
            record_file.write_text(
                json.dumps(records[-100:], indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.log.debug(f"Could not write verification failure record: {e}")
