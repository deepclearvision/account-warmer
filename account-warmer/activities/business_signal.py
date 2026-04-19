"""
Business Signal Activity

Builds a believable history of a real customer relationship with a target
business. Signals are split across three phases spread over multiple sessions:

  Phase 1 — Discovery (sessions 1-2, days 1-3):
    - Google search for the business by name
    - Search for "[type] near me" or "[type] [area]" (finds it naturally)
    - Open Maps listing, scroll through it
    - Browse business photos
    - Check opening hours
    - Read a few existing reviews
    - Save to Maps (Saved places)
    - Visit website if one is configured

  Phase 2 — Intent (sessions 3-4, days 3-7):
    - Get directions to the address
    - Search for "[business name] reviews" or "[business name] prices"
    - Create a Google Calendar appointment (e.g. "Haircut - Joe's Barbers")
      set 2-5 days in the future
    - View the Maps listing again briefly

  Phase 3 — Post-visit (after appointment date has passed):
    - Search for the business again
    - View the Maps listing
    - Account is now flagged as eligible for a review
      (the MapsActivity review module picks this up)

Each phase only runs once per business per account — state is tracked.
Phases advance automatically as sessions run.
"""

import random
import json
from datetime import date, timedelta, datetime
from pathlib import Path
from playwright.async_api import Page

from activities.base_activity import BaseActivity
from core.paths import STATE_DIR, BUSINESSES_FILE, LOGS_DIR
MAPS_URL        = "https://www.google.com/maps"
GOOGLE_URL      = "https://www.google.com"

# Calendar appointment titles by business type
APPOINTMENT_TITLES = {
    "barber":     ["{name}", "Haircut - {name}", "Barbers appointment"],
    "salon":      ["{name}", "Hair appointment - {name}", "Salon - {name}"],
    "cafe":       ["Coffee with {contact} at {name}", "Meeting at {name}"],
    "restaurant": ["Dinner at {name}", "Lunch at {name}", "Table at {name}"],
    "pub":        ["Drinks at {name}", "Meet at {name}"],
    "gym":        ["Gym - {name}", "PT session - {name}", "{name}"],
    "dentist":    ["Dentist - {name}", "Dental appointment", "{name}"],
    "garage":     ["Car service - {name}", "MOT - {name}", "{name}"],
    "other":      ["Appointment - {name}", "{name}", "Visit {name}"],
}

CONTACTS = ["James", "Sarah", "Tom", "Emma", "Mike", "Dan", "the lads", "friends"]

# Search query variants for each phase
DISCOVERY_SEARCHES = [
    "{name}",
    "{name} {location}",
    "{type} {location}",
    "{type} near me",
    "{name} opening hours",
    "{name} {location} reviews",
]

INTENT_SEARCHES = [
    "{name} reviews",
    "{name} prices",
    "{name} {location}",
    "{name} phone number",
    "{name} book appointment",
    "how to get to {name} {location}",
]

POST_VISIT_SEARCHES = [
    "{name}",
    "{name} {location}",
    "{name} reviews",
]


class BusinessSignalActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict,
                 business: dict):
        super().__init__(page, account, behaviour_cfg)
        self._biz              = business
        self._state_file       = STATE_DIR / f"{account['id']}_biz_{business['id']}.json"
        self._state            = self._load_state()
        self._interactions_file = STATE_DIR / f"{account['id']}_biz_{business['id']}_interactions.json"
        self._screenshot_dir   = LOGS_DIR / "screenshots"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> bool:
        biz_name = self._biz["name"]
        phase    = self._current_phase()

        self.log.info(f"Business signal | {biz_name!r} | phase={phase}")

        if phase is None:
            self.log.info(f"All phases complete for {biz_name!r}")
            return True

        try:
            if phase == 1:
                await self._run_phase_1()
            elif phase == 2:
                await self._run_phase_2()
            elif phase == 3:
                await self._run_phase_3()

            self._save_state()
            return True

        except Exception as e:
            self.log.error(f"Business signal failed for {biz_name!r}: {e}")
            return False

    # ------------------------------------------------------------------
    # Phase determination
    # ------------------------------------------------------------------

    def _current_phase(self) -> int | None:
        """
        Return which phase to run next, or None if all done.
        Phase 3 only runs after the calendar appointment date has passed.
        """
        if not self._state.get("phase_1_complete"):
            return 1
        if not self._state.get("phase_2_complete"):
            return 2

        # Phase 3: only after the appointment date
        appt_date_str = self._state.get("appointment_date")
        if appt_date_str:
            appt_date = date.fromisoformat(appt_date_str)
            if date.today() >= appt_date and not self._state.get("phase_3_complete"):
                return 3
        elif not self._state.get("phase_3_complete"):
            # No appointment date set — run phase 3 anyway (e.g. no calendar access)
            return 3

        return None  # All done

    def is_review_eligible(self) -> bool:
        """Return True if this account has completed phase 3 for this business."""
        return bool(self._state.get("phase_3_complete"))

    # ------------------------------------------------------------------
    # Phase 1 — Discovery
    # ------------------------------------------------------------------

    async def _run_phase_1(self) -> None:
        self.log.info("Phase 1: Discovery")
        actions = self._build_phase_1_actions()
        random.shuffle(actions)

        for action in actions:
            await action()
            await self.humaniser.pause(4000, 12000)

        # Capture proof screenshot of Maps listing at end of discovery
        shot = await self._take_proof_screenshot("phase1_listing")
        self._log_interaction(1, "Discovery complete — listing viewed, saved to Maps", shot)

        self._state["phase_1_complete"] = True
        self._state["phase_1_date"] = str(date.today())
        self.log.info(f"Phase 1 complete for: {self._biz['name']!r}")

    def _build_phase_1_actions(self) -> list:
        return [
            self._search_for_business_naturally,
            self._view_maps_listing,
            self._browse_listing_photos,
            self._check_opening_hours,
            self._read_existing_reviews,
            self._save_to_maps,
            *([ self._visit_website ] if self._biz.get("website") else []),
        ]

    async def _search_for_business_naturally(self) -> None:
        """Search Google for the business using a natural query."""
        query = self._build_search_query(random.choice(DISCOVERY_SEARCHES))
        self.log.debug(f"Searching: {query!r}")

        await self.page.goto(GOOGLE_URL, wait_until="domcontentloaded")
        await self.humaniser.pause(1500, 3500)
        await self._dismiss_consent_google()

        search_box = self.page.locator('textarea[name="q"], input[name="q"]').first
        await self.humaniser.type_text(self.page, 'textarea[name="q"], input[name="q"]', query)
        await self.humaniser.pause(300, 800)
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_load_state("domcontentloaded")
        await self.humaniser.pause(2000, 5000)

        # Scroll results, look for the business
        await self.humaniser.human_scroll(self.page, max_fraction=0.5)
        await self.humaniser.reading_pause()

        # Click the Maps result or knowledge panel if visible
        maps_result = self.page.locator(
            f'a[href*="maps.google"]:has-text("{self._biz["name"][:15]}"), '
            f'div[data-attrid="title"]:has-text("{self._biz["name"][:15]}")'
        ).first
        if await maps_result.is_visible(timeout=3000):
            await self.humaniser.pause(2000, 4000)
            await maps_result.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 4000)
            await self.humaniser.human_scroll(self.page)
            await self.page.go_back()
            await self.page.wait_for_load_state("domcontentloaded")

    async def _view_maps_listing(self) -> None:
        """Open Google Maps and find the business listing."""
        self.log.debug(f"Viewing Maps listing for: {self._biz['name']!r}")
        await self.page.goto(MAPS_URL, wait_until="domcontentloaded")
        await self.humaniser.pause(2000, 4000)
        await self._dismiss_consent_maps()

        # Search by name + address
        search_query = f"{self._biz['name']} {self._biz.get('address', '')}"
        search_box   = self.page.locator('input#searchboxinput').first
        await search_box.click()
        await self.humaniser.pause(400, 900)
        for char in search_query:
            delay = max(40, min(250, int(random.gauss(95, 30))))
            await search_box.type(char, delay=delay)
        await self.humaniser.pause(400, 800)
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_load_state("domcontentloaded")
        await self.humaniser.pause(2500, 5000)

        # Click on the listing if results appear
        await self._click_business_result()

        # Scroll through the listing
        for _ in range(random.randint(3, 5)):
            await self.page.mouse.wheel(0, random.randint(200, 400))
            await self.humaniser.pause(1000, 3000)

        # Store the URL of the listing for later phases
        if "maps.google" in self.page.url or "/maps/" in self.page.url:
            self._state["listing_url"] = self.page.url

        await self.humaniser.reading_pause()

    async def _browse_listing_photos(self) -> None:
        """Open the photo gallery on the Maps listing."""
        self.log.debug("Browsing business photos")
        await self._ensure_on_listing()

        try:
            photos_btn = self.page.locator(
                'button[aria-label*="photo"], div[aria-label*="photo"],'
                ' button[jsaction*="photo"]'
            ).first
            if await photos_btn.is_visible(timeout=4000):
                await photos_btn.click()
                await self.humaniser.pause(1500, 3500)

                # Browse 3-7 photos
                num = random.randint(3, 7)
                for _ in range(num):
                    next_btn = self.page.locator(
                        'button[aria-label="Next photo"], button[aria-label="Go to next item"]'
                    ).first
                    if await next_btn.is_visible(timeout=2000):
                        await next_btn.click()
                        await self.humaniser.pause(2000, 5000)

                # Close
                close = self.page.locator('button[aria-label="Close"], button[aria-label="Back"]').first
                if await close.is_visible(timeout=2000):
                    await close.click()
                    await self.humaniser.pause(1000, 2000)

                self.log.debug(f"Viewed {num} photos")
        except Exception as e:
            self.log.debug(f"Photo browse failed: {e}")

    async def _check_opening_hours(self) -> None:
        """Expand and read the opening hours on the listing."""
        self.log.debug("Checking opening hours")
        await self._ensure_on_listing()
        try:
            hours_btn = self.page.locator(
                'button[aria-label*="hour"], div[aria-label*="hour"],'
                ' span:has-text("Opening hours"), button[jsaction*="hours"]'
            ).first
            if await hours_btn.is_visible(timeout=4000):
                await self.humaniser.pause(2000, 4000)
                await hours_btn.click()
                await self.humaniser.pause(2000, 5000)
                # Read the hours
                await self.humaniser.human_scroll(self.page, max_fraction=0.3)
                await self.humaniser.reading_pause()
        except Exception as e:
            self.log.debug(f"Hours check failed: {e}")

    async def _read_existing_reviews(self) -> None:
        """Navigate to the reviews tab and read a few."""
        self.log.debug("Reading existing reviews")
        await self._ensure_on_listing()
        try:
            reviews_tab = self.page.locator(
                'button[aria-label*="Reviews"], div[aria-label*="reviews"]'
            ).first
            if await reviews_tab.is_visible(timeout=4000):
                await self.humaniser.pause(2000, 4000)
                await reviews_tab.click()
                await self.humaniser.pause(1500, 3500)

                # Scroll through reviews
                for _ in range(random.randint(2, 4)):
                    await self.page.mouse.wheel(0, random.randint(300, 600))
                    await self.humaniser.pause(2000, 5000)

                # Expand a "More" link on a review
                more_links = self.page.locator('button[aria-label*="See more"], button:has-text("More")').all()
                more_list  = await more_links
                if more_list:
                    await random.choice(more_list[:3]).click()
                    await self.humaniser.pause(2000, 4000)

                self.log.debug("Read reviews section")
        except Exception as e:
            self.log.debug(f"Reviews read failed: {e}")

    async def _save_to_maps(self) -> None:
        """Save the business to the account's Maps saved places."""
        self.log.debug("Saving business to Maps")
        await self._ensure_on_listing()
        try:
            save_btn = self.page.locator(
                'button[data-value="Save"], button[aria-label*="Save"],'
                ' button[jsaction*="save"]'
            ).first
            if await save_btn.is_visible(timeout=4000):
                await self.humaniser.pause(3000, 7000)
                await save_btn.click()
                await self.humaniser.pause(1000, 2500)

                # Confirm in the list picker dialog
                first_list = self.page.locator('li[data-index="0"], li.HcKTHb').first
                if await first_list.is_visible(timeout=2000):
                    await first_list.click()
                    await self.humaniser.pause(500, 1200)

                self._state["saved_to_maps"] = True
                self.log.debug("Saved to Maps")
        except Exception as e:
            self.log.debug(f"Save to Maps failed: {e}")

    async def _visit_website(self) -> None:
        """Visit the business website from the Maps listing or directly."""
        website = self._biz.get("website", "")
        if not website:
            return
        self.log.debug(f"Visiting website: {website}")
        try:
            # Try clicking the website link from Maps listing first
            await self._ensure_on_listing()
            website_link = self.page.locator(
                'a[data-item-id*="website"], a[aria-label*="website"],'
                ' a[href*="website"]'
            ).first
            if await website_link.is_visible(timeout=3000):
                await self.humaniser.pause(2000, 5000)
                await website_link.click()
                await self.page.wait_for_load_state("domcontentloaded")
            else:
                await self.page.goto(website, wait_until="domcontentloaded", timeout=20000)

            await self.humaniser.pause(2000, 5000)
            await self._dismiss_cookie_banner()
            await self.humaniser.human_scroll(self.page, max_fraction=0.6)
            await self.humaniser.reading_pause()

            # Maybe click a link (about, services, contact)
            for link_text in ["About", "Services", "Contact", "Book", "Menu"]:
                link = self.page.locator(f'a:has-text("{link_text}")').first
                if await link.is_visible(timeout=1500):
                    await self.humaniser.pause(2000, 4000)
                    await link.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.humaniser.pause(2000, 5000)
                    await self.humaniser.human_scroll(self.page)
                    await self.humaniser.reading_pause()
                    break

        except Exception as e:
            self.log.debug(f"Website visit failed: {e}")

    # ------------------------------------------------------------------
    # Phase 2 — Intent
    # ------------------------------------------------------------------

    async def _run_phase_2(self) -> None:
        self.log.info("Phase 2: Intent")

        # Search for info/reviews
        await self._search_intent_query()
        await self.humaniser.pause(5000, 15000)

        # Get directions
        await self._get_directions_to_business()
        await self.humaniser.pause(5000, 15000)

        # Create calendar appointment
        appt_date = await self._create_appointment()
        if appt_date:
            self._state["appointment_date"] = str(appt_date)

        await self.humaniser.pause(3000, 8000)

        # Briefly view listing again
        await self._view_maps_listing_briefly()

        # Capture proof screenshot of directions / listing
        shot = await self._take_proof_screenshot("phase2_directions")
        self._log_interaction(2, "Intent complete — directions obtained, appointment created", shot)

        self._state["phase_2_complete"] = True
        self._state["phase_2_date"]     = str(date.today())
        self.log.info(f"Phase 2 complete for: {self._biz['name']!r}")

    async def _search_intent_query(self) -> None:
        """Search for info about the business — reviews, prices, booking."""
        query = self._build_search_query(random.choice(INTENT_SEARCHES))
        self.log.debug(f"Intent search: {query!r}")

        await self.page.goto(GOOGLE_URL, wait_until="domcontentloaded")
        await self.humaniser.pause(1500, 3500)
        await self._dismiss_consent_google()

        await self.humaniser.type_text(self.page, 'textarea[name="q"], input[name="q"]', query)
        await self.humaniser.pause(300, 800)
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_load_state("domcontentloaded")
        await self.humaniser.pause(2000, 5000)
        await self.humaniser.human_scroll(self.page, max_fraction=0.6)
        await self.humaniser.reading_pause()

    async def _get_directions_to_business(self) -> None:
        """Get directions to the business address in Maps."""
        address = self._biz.get("address", self._biz["name"])
        self.log.debug(f"Getting directions to: {address}")

        await self.page.goto(MAPS_URL, wait_until="domcontentloaded")
        await self.humaniser.pause(2000, 4000)
        await self._dismiss_consent_maps()

        try:
            # Click directions button on listing if we have a saved URL
            listing_url = self._state.get("listing_url")
            if listing_url:
                await self.page.goto(listing_url, wait_until="domcontentloaded")
                await self.humaniser.pause(2000, 4000)
                await self._click_business_result()

                directions_btn = self.page.locator(
                    'button[aria-label*="Directions"], button[jsaction*="directions"],'
                    ' a[aria-label*="Directions"]'
                ).first
                if await directions_btn.is_visible(timeout=4000):
                    await self.humaniser.pause(2000, 5000)
                    await directions_btn.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.humaniser.pause(2000, 4000)
                    # Look at the route
                    await self.humaniser.human_scroll(self.page, max_fraction=0.5)
                    await self.humaniser.pause(3000, 8000)
                    self._state["got_directions"] = True
                    self.log.debug("Directions retrieved")
                    return

            # Fallback: type address in directions search
            search_box = self.page.locator('input#searchboxinput').first
            await search_box.click()
            for char in address:
                delay = max(50, min(250, int(random.gauss(95, 30))))
                await search_box.type(char, delay=delay)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 4000)
            await self.humaniser.human_scroll(self.page)
            self._state["got_directions"] = True

        except Exception as e:
            self.log.debug(f"Directions failed: {e}")

    async def _create_appointment(self) -> date | None:
        """Create a Google Calendar appointment for the business visit."""
        from activities.calendar import CalendarActivity

        biz_type   = self._biz.get("type", "other")
        name       = self._biz["name"]
        title_tmpl = random.choice(APPOINTMENT_TITLES.get(biz_type, APPOINTMENT_TITLES["other"]))
        contact    = random.choice(CONTACTS)
        title      = title_tmpl.format(name=name, contact=contact)

        # Set appointment 2-5 days in the future
        days_ahead  = random.randint(2, 5)
        appt_date   = date.today() + timedelta(days=days_ahead)

        # Pick a realistic time for the business type
        time_map = {
            "barber":     ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"],
            "salon":      ["09:30", "10:30", "11:00", "14:00", "15:00"],
            "cafe":       ["08:30", "09:00", "10:00", "11:00"],
            "restaurant": ["12:30", "13:00", "19:00", "19:30", "20:00"],
            "pub":        ["17:00", "18:00", "18:30", "19:00", "20:00"],
            "gym":        ["06:30", "07:00", "08:00", "17:30", "18:00"],
            "dentist":    ["09:00", "10:00", "11:00", "14:00", "15:00"],
            "garage":     ["08:00", "08:30", "09:00", "10:00"],
            "other":      ["09:00", "10:00", "11:00", "14:00", "15:00"],
        }
        appt_time = random.choice(time_map.get(biz_type, time_map["other"]))

        self.log.info(f"Creating appointment: {title!r} on {appt_date} at {appt_time}")

        try:
            cal = CalendarActivity(self.page, self.account, self.behaviour_cfg, mode="setup")
            success = await cal._create_event(
                title      = title,
                event_date = appt_date,
                time       = appt_time,
            )
            if success:
                self._state["appointment_title"] = title
                self.log.info(f"Appointment created: {title!r} on {appt_date}")
                return appt_date
        except Exception as e:
            self.log.debug(f"Appointment creation failed: {e}")

        return None

    async def _view_maps_listing_briefly(self) -> None:
        """Quick visit back to the listing — confirming details before visit."""
        listing_url = self._state.get("listing_url")
        if not listing_url:
            return
        try:
            await self.page.goto(listing_url, wait_until="domcontentloaded")
            await self.humaniser.pause(2000, 5000)
            await self._click_business_result()
            await self.humaniser.human_scroll(self.page, max_fraction=0.4)
            await self.humaniser.pause(2000, 5000)
        except Exception as e:
            self.log.debug(f"Brief listing view failed: {e}")

    # ------------------------------------------------------------------
    # Phase 3 — Post-visit
    # ------------------------------------------------------------------

    async def _run_phase_3(self) -> None:
        self.log.info("Phase 3: Post-visit")

        # Search for business again (like someone who just visited and is thinking about it)
        query = self._build_search_query(random.choice(POST_VISIT_SEARCHES))
        await self.page.goto(GOOGLE_URL, wait_until="domcontentloaded")
        await self.humaniser.pause(1500, 3500)
        await self._dismiss_consent_google()
        await self.humaniser.type_text(self.page, 'textarea[name="q"], input[name="q"]', query)
        await self.humaniser.pause(300, 800)
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_load_state("domcontentloaded")
        await self.humaniser.pause(2000, 4000)
        await self.humaniser.human_scroll(self.page, max_fraction=0.5)
        await self.humaniser.reading_pause()

        await self.humaniser.pause(5000, 12000)

        # View the Maps listing one more time
        await self._view_maps_listing_briefly()

        # Capture proof screenshot of post-visit listing view
        shot = await self._take_proof_screenshot("phase3_postvisit")
        self._log_interaction(3, "Post-visit complete — account now eligible to review", shot)

        self._state["phase_3_complete"] = True
        self._state["phase_3_date"]     = str(date.today())
        self.log.info(f"Phase 3 complete — account eligible to review: {self._biz['name']!r}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_search_query(self, template: str) -> str:
        biz_type_label = self._biz.get("type", "business")
        location       = self._biz.get("location", self.account.get("location", ""))
        city           = location.split(",")[0].strip() if "," in location else location
        return (template
                .replace("{name}",     self._biz["name"])
                .replace("{type}",     biz_type_label)
                .replace("{location}", city)
                .replace("{address}",  self._biz.get("address", "")))

    async def _click_business_result(self) -> None:
        """Click the first prominent result in the Maps panel."""
        try:
            result = self.page.locator('div[role="feed"] div.Nv2PK').first
            if await result.is_visible(timeout=4000):
                await result.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(2000, 4000)
        except Exception as e:
            self.log.debug(f"Business result click failed: {e}")

    async def _ensure_on_listing(self) -> None:
        """Make sure we're on the business Maps listing. Re-navigate if needed."""
        listing_url = self._state.get("listing_url")
        if listing_url and "maps.google" not in self.page.url and "/maps/" not in self.page.url:
            await self.page.goto(listing_url, wait_until="domcontentloaded")
            await self.humaniser.pause(2000, 4000)
            await self._click_business_result()

    async def _dismiss_consent_google(self) -> None:
        for selector in ['button:has-text("Accept all")', 'button:has-text("I agree")']:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await self.humaniser.pause(500, 1000)
                    return
            except Exception:
                pass

    async def _dismiss_consent_maps(self) -> None:
        await self._dismiss_consent_google()

    async def _dismiss_cookie_banner(self) -> None:
        for selector in [
            'button:has-text("Accept all")', 'button:has-text("Accept")',
            'button:has-text("OK")', 'button:has-text("Got it")',
            'button:has-text("I agree")', 'button:has-text("Agree")',
        ]:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await self.humaniser.pause(400, 900)
                    return
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Interaction logging
    # ------------------------------------------------------------------

    async def _take_proof_screenshot(self, label: str) -> str | None:
        """Capture a screenshot and return its relative path, or None on failure."""
        try:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            acc_id = self.account["id"]
            biz_id = self._biz["id"]
            fname  = f"{acc_id}_{biz_id}_{label}_{ts}.png"
            fpath  = self._screenshot_dir / fname
            await self.page.screenshot(path=str(fpath), full_page=False)
            # Return a relative path from LOGS_DIR so URLs can be constructed by the API
            return f"screenshots/{fname}"
        except Exception as e:
            self.log.debug(f"Screenshot failed ({label}): {e}")
            return None

    def _log_interaction(self, phase: int, action: str, screenshot: str | None = None) -> None:
        """Append one interaction record to the interactions log file."""
        try:
            if self._interactions_file.exists():
                records = json.loads(self._interactions_file.read_text(encoding="utf-8"))
            else:
                records = []
        except Exception:
            records = []

        entry = {
            "timestamp":   datetime.utcnow().isoformat() + "Z",
            "phase":       phase,
            "action":      action,
            "business_id": self._biz["id"],
            "business_name": self._biz["name"],
        }
        if screenshot:
            entry["screenshot"] = screenshot

        records.append(entry)
        try:
            self._interactions_file.write_text(
                json.dumps(records, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.log.debug(f"Could not write interaction log: {e}")

        # Also write to the unified activity log
        try:
            from core.activity_log import log_event
            log_event(
                account_id    = self.account["id"],
                device        = "desktop",
                activity_type = "business_signal",
                detail        = {
                    "phase":         phase,
                    "action":        action,
                    "business_id":   self._biz["id"],
                    "business_name": self._biz["name"],
                },
                screenshot = screenshot,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                with open(self._state_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self) -> None:
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    @property
    def behaviour_cfg(self) -> dict:
        return self.humaniser.__dict__  # Pass behaviour config through
