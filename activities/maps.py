"""
Google Maps Activity — Browse Only

Searches for local businesses, clicks listings, views photos, reads existing
reviews, saves places, and occasionally gets directions.  Builds location
history and Maps engagement.

Reviews are NOT written through this module — desktop warming is account
warming only.  Business actions (reviews, edits, pins) happen elsewhere.

State tracks recently visited listings so the same business isn't
browsed too often within a short window.
"""

import random
import json
from pathlib import Path
from playwright.async_api import Page

from activities.base_activity import BaseActivity
from core.orchestrator import STATE_DIR

MAPS_DATA_FILE = Path(__file__).parent.parent / "data" / "maps_data.json"
MAPS_URL       = "https://www.google.com/maps"


class MapsActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict):
        super().__init__(page, account, behaviour_cfg)
        with open(MAPS_DATA_FILE, encoding="utf-8") as f:
            self._data = json.load(f)
        self._state_file   = STATE_DIR / f"{account['id']}_maps.json"
        self._state        = self._load_state()
        self._interactions = self._data["listing_interactions"]

    async def run(self) -> bool:
        self.log.info("Maps session | browse")
        try:
            await self.humaniser.navigate(self.page, MAPS_URL)
            await self.humaniser.pause(2000, 4000)
            await self._dismiss_consent()

            # Search for a local business category
            category, results = await self._search_category()
            if not results:
                self.log.warning("No map results found — session ending")
                return False

            # Browse 1-3 listings
            num_to_browse = random.randint(1, 3)
            browsed_businesses = []

            for i in range(min(num_to_browse, len(results))):
                business = await self._browse_listing(results[i])
                if business:
                    browsed_businesses.append(business)
                    self._record_visit(business)
                if i < num_to_browse - 1:
                    await self.humaniser.pause(3000, 8000)
                    await self.page.go_back()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.humaniser.pause(1500, 3000)

            # Maybe get directions (natural Maps behaviour)
            if random.random() < self._interactions.get("get_directions_probability", 0.25):
                await self._get_directions()

            self._save_state()
            return True

        except Exception as e:
            self.log.error(f"Maps session failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def _search_category(self) -> tuple[str, list]:
        """Search for a business category and return the result elements."""
        categories = self._data["search_categories"]
        # Flatten all categories and pick one
        all_categories = [
            term
            for group in categories.values()
            for term in group
        ]

        # Prefer location-relevant terms
        location = self.account.get("location", "").lower()
        if "uk" in location or "london" in location or "manchester" in location:
            # UK-flavoured terms (near me is universal)
            preferred = [t for t in all_categories if "near me" in t]
        else:
            preferred = all_categories

        term = random.choice(preferred if preferred else all_categories)
        self.log.info(f"Maps search: {term!r}")

        try:
            search_box = await self._find_search_box()
            if not search_box:
                self.log.debug("Maps search box not found in _search_category")
                return term, []
            await search_box.click()
            await self.humaniser.pause(500, 1200)

            for char in term:
                delay = max(40, min(250, int(random.gauss(95, 35))))
                await search_box.type(char, delay=delay)

            await self.humaniser.pause(400, 800)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 4000)

            # Wait for results panel
            await self.page.wait_for_selector(
                'div[role="feed"] a, div.Nv2PK, div[jsaction*="placeCard"]',
                timeout=20000
            )

            # Collect result elements
            results = await self.page.locator(
                'div[role="feed"] div.Nv2PK, div[role="feed"] a[href*="maps"]'
            ).all()

            self.log.debug(f"Found {len(results)} map results")
            return term, results

        except Exception as e:
            self.log.debug(f"Map search failed: {e}")
            return term, []

    # ------------------------------------------------------------------
    # Browse a listing
    # ------------------------------------------------------------------

    async def _browse_listing(self, result_element) -> dict | None:
        """Click a result and browse the business listing. Returns business info."""
        try:
            # Get the business name before clicking
            try:
                name_el = result_element.locator('div.qBF1Pd, span.fontHeadlineSmall').first
                name = await name_el.inner_text(timeout=2000)
            except Exception:
                name = "Unknown business"

            # Skip recently visited
            if name in self._state.get("recent_visits", []):
                self.log.debug(f"Skipping recently visited: {name}")
                return None

            self.log.info(f"Browsing listing: {name}")
            await result_element.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 4000)

            # Scroll through the listing panel
            listing_panel = self.page.locator('div[role="main"], div.m6QErb').first
            await self.humaniser.pause(1000, 2000)

            # Scroll down through the listing
            for _ in range(random.randint(2, 4)):
                await self.page.mouse.wheel(0, random.randint(200, 400))
                await self.humaniser.pause(800, 2000)

            # Get business type for later
            business_type = await self._detect_business_type()

            # View photos
            if random.random() < self._interactions.get("view_photos_probability", 0.70):
                await self._view_photos()

            # Read reviews
            if random.random() < self._interactions.get("read_reviews_probability", 0.65):
                await self._read_reviews()

            # Save place
            if random.random() < self._interactions.get("save_place_probability", 0.10):
                await self._save_place()

            return {"name": name, "type": business_type}

        except Exception as e:
            self.log.debug(f"Listing browse failed: {e}")
            return None

    async def _detect_business_type(self) -> str:
        """Try to detect the type of business from the listing page."""
        try:
            category_el = self.page.locator(
                'button[jsaction*="category"], span.DkEaL'
            ).first
            category_text = await category_el.inner_text(timeout=2000)
            category_lower = category_text.lower()

            if any(w in category_lower for w in ["cafe", "coffee", "tea room"]):
                return "cafe"
            elif any(w in category_lower for w in ["restaurant", "takeaway", "food"]):
                return "restaurant"
            elif any(w in category_lower for w in ["barber", "hair", "salon"]):
                return "barber"
            elif any(w in category_lower for w in ["gym", "fitness", "sport"]):
                return "gym"
            elif any(w in category_lower for w in ["pub", "bar", "tavern"]):
                return "pub"
            else:
                return "generic"
        except Exception:
            return "generic"

    async def _view_photos(self) -> None:
        """Open the photo gallery and browse a few photos."""
        try:
            photos_btn = self.page.locator(
                'button[aria-label*="photo"], div[data-photo-index]'
            ).first
            if await photos_btn.is_visible(timeout=3000):
                await photos_btn.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1500, 3000)

                # Browse 2-5 photos
                num_photos = random.randint(2, 5)
                for _ in range(num_photos):
                    next_btn = self.page.locator(
                        'button[aria-label="Next photo"], button[data-photo-navigate="1"]'
                    ).first
                    if await next_btn.is_visible(timeout=2000):
                        await next_btn.click()
                        await self.humaniser.pause(1500, 4000)

                # Close photos
                close_btn = self.page.locator(
                    'button[aria-label="Close"], button[aria-label="Back"]'
                ).first
                if await close_btn.is_visible(timeout=2000):
                    await close_btn.click()
                    await self.humaniser.pause(1000, 2000)

                self.log.debug(f"Viewed {num_photos} photos")
        except Exception as e:
            self.log.debug(f"Photo view failed: {e}")

    async def _read_reviews(self) -> None:
        """Scroll through the reviews section."""
        try:
            reviews_tab = self.page.locator(
                'button[aria-label*="Reviews"], div[data-tab-index="1"]'
            ).first
            if await reviews_tab.is_visible(timeout=3000):
                await reviews_tab.click()
                await self.humaniser.pause(1500, 3000)

                # Scroll through reviews
                for _ in range(random.randint(2, 4)):
                    await self.page.mouse.wheel(0, random.randint(300, 600))
                    await self.humaniser.pause(1500, 4000)

                self.log.debug("Read reviews section")
        except Exception as e:
            self.log.debug(f"Reviews read failed: {e}")

    async def _save_place(self) -> None:
        """Save the place to a list (builds Maps engagement)."""
        try:
            save_btn = self.page.locator(
                'button[data-value="Save"], button[aria-label*="Save"]'
            ).first
            if await save_btn.is_visible(timeout=2000):
                await save_btn.click()
                await self.humaniser.pause(1000, 2500)
                # Confirm save in dialog — pick a random list (0 to min(count-1, 3))
                list_items = self.page.locator('li[data-index]')
                list_count = await list_items.count()
                if list_count > 0:
                    chosen_idx = random.randint(0, min(list_count - 1, 3))
                    confirm = list_items.nth(chosen_idx)
                    if await confirm.is_visible(timeout=2000):
                        await confirm.click()
                        await self.humaniser.pause(500, 1200)
                self.log.debug("Saved place to list")
        except Exception as e:
            self.log.debug(f"Save place failed: {e}")

    # ------------------------------------------------------------------
    # Directions
    # ------------------------------------------------------------------

    async def _get_directions(self) -> None:
        """Search for directions to a nearby destination."""
        try:
            destination = random.choice(self._data["directions_destinations"])
            self.log.info(f"Getting directions to: {destination}")

            await self.page.goto(MAPS_URL, wait_until="domcontentloaded", timeout=45000)
            await self.humaniser.pause(1500, 3000)

            directions_btn = self.page.locator(
                'button[aria-label="Directions"], a[href*="dir"]'
            ).first
            if await directions_btn.is_visible(timeout=3000):
                await directions_btn.click()
            else:
                # Search for it via search box or direct URL
                import urllib.parse
                search_box = await self._find_search_box()
                if search_box:
                    await search_box.click()
                    for char in destination:
                        delay = max(50, min(250, int(random.gauss(95, 35))))
                        await search_box.type(char, delay=delay)
                    await self.page.keyboard.press("Enter")
                else:
                    encoded = urllib.parse.quote_plus(destination)
                    await self.page.goto(
                        f"https://www.google.com/maps/search/{encoded}",
                        wait_until="domcontentloaded",
                    )
                    await self._dismiss_consent()

            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 5000)
            self.log.debug(f"Directions retrieved for: {destination}")

        except Exception as e:
            self.log.debug(f"Directions failed: {e}")

    # ------------------------------------------------------------------
    # Consent dialog
    # ------------------------------------------------------------------

    async def _dismiss_consent(self) -> None:
        """Dismiss Google Maps consent dialogs."""
        await self.dismiss_google_consent()

    async def _find_search_box(self):
        """Wait for the Maps search box and return its locator, or None."""
        selector = 'input#searchboxinput, input[aria-label*="Search"], input[name="q"]'
        try:
            await self.page.wait_for_selector(selector, timeout=10000, state="visible")
            return self.page.locator(selector).first
        except Exception:
            return None

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
        return {"recent_visits": []}

    def _save_state(self) -> None:
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    def _record_visit(self, business: dict) -> None:
        recent = self._state.setdefault("recent_visits", [])
        name   = business.get("name", "")
        if name and name not in recent:
            recent.append(name)
        # Keep only last 30 visits
        self._state["recent_visits"] = recent[-30:]
