"""
Google Maps Activity

Two modes depending on week:
  - Browse mode (weeks 3+):  Search for local businesses, click listings,
                              view photos, read reviews, occasionally get directions.
                              Builds location history and Maps engagement.

  - Review mode (weeks 6+):  Leave a genuine-looking review on a business.
                              Builds Local Guide points and review portfolio.
                              Only runs if browse_only=False.

State is saved per account so the same business is never reviewed twice
and recently visited listings aren't repeated too often.
"""

import random
import json
from datetime import date
from pathlib import Path
from playwright.async_api import Page

from activities.base_activity import BaseActivity
from core.orchestrator import STATE_DIR

MAPS_DATA_FILE = Path(__file__).parent.parent / "data" / "maps_data.json"
MAPS_URL       = "https://www.google.com/maps"


class MapsActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict,
                 browse_only: bool = True):
        super().__init__(page, account, behaviour_cfg)
        with open(MAPS_DATA_FILE, encoding="utf-8") as f:
            self._data = json.load(f)
        self._browse_only  = browse_only
        self._state_file   = STATE_DIR / f"{account['id']}_maps.json"
        self._state        = self._load_state()
        self._interactions = self._data["listing_interactions"]

    async def run(self) -> bool:
        self.log.info(f"Maps session | browse_only={self._browse_only}")
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

            # Maybe get directions somewhere unrelated (natural Maps behaviour)
            if random.random() < self._interactions.get("get_directions_probability", 0.25):
                await self._get_directions()

            # Review mode: leave a review on one of the businesses browsed
            if not self._browse_only and browsed_businesses:
                candidate = await self._pick_reviewable(browsed_businesses)
                if candidate:
                    await self._leave_review(candidate, category)

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
            search_box = self.page.locator(
                'input#searchboxinput, input[name="q"], input[aria-label*="Search"]'
            ).first
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
                # Search for it directly
                search_box = self.page.locator('input#searchboxinput').first
                await search_box.click()
                for char in destination:
                    delay = max(50, min(250, int(random.gauss(95, 35))))
                    await search_box.type(char, delay=delay)
                await self.page.keyboard.press("Enter")

            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 5000)
            self.log.debug(f"Directions retrieved for: {destination}")

        except Exception as e:
            self.log.debug(f"Directions failed: {e}")

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------

    async def _pick_reviewable(self, businesses: list) -> dict | None:
        """Pick a business to review — must not have been reviewed before."""
        reviewed = set(self._state.get("reviewed", []))
        candidates = [b for b in businesses if b.get("name") not in reviewed]
        return random.choice(candidates) if candidates else None

    async def _leave_review(self, business: dict, search_term: str) -> None:
        """Navigate back to the business and leave a review."""
        name          = business["name"]
        business_type = business.get("type", "generic")

        # Velocity cap: minimum 3-5 days between reviews to avoid spam signals
        last_review = self._state.get("last_review_date")
        if last_review:
            from datetime import timedelta
            min_gap = random.randint(3, 5)
            days_since = (date.today() - date.fromisoformat(last_review)).days
            if days_since < min_gap:
                self.log.info(
                    f"Review velocity cap: last review {days_since}d ago "
                    f"(min {min_gap}d) — skipping review this session"
                )
                return

        self.log.info(f"Leaving review for: {name} (type: {business_type})")

        try:
            # Re-search for the business by name to get back to its listing
            await self.page.goto(MAPS_URL, wait_until="domcontentloaded", timeout=45000)
            await self.humaniser.pause(1500, 3000)

            search_box = self.page.locator('input#searchboxinput').first
            await search_box.click()
            await self.humaniser.pause(400, 800)
            # Clear and type the business name
            await search_box.fill("")
            for char in name:
                delay = max(50, min(250, int(random.gauss(95, 35))))
                await search_box.type(char, delay=delay)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 4000)

            # Click into the listing
            first_result = self.page.locator('div[role="feed"] div.Nv2PK').first
            if await first_result.is_visible(timeout=5000):
                await first_result.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(2000, 4000)

            # Find the Write a review button
            review_btn = self.page.locator(
                'button[aria-label*="Write a review"], '
                'button[data-value*="review"], '
                'a[href*="writereview"]'
            ).first

            if not await review_btn.is_visible(timeout=5000):
                self.log.warning(f"Review button not found for: {name}")
                return

            # Pause before clicking — like we're deciding to leave a review
            await self.humaniser.pause(5000, 12000)
            await review_btn.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 4000)

            # Select star rating
            rating = self._pick_rating()
            await self._click_star(rating)
            await self.humaniser.pause(2000, 5000)

            # Write review text (Claude-generated, template fallback)
            review_text = await self._build_review_text(business_type, name, rating)
            await self._type_review(review_text)
            await self.humaniser.pause(3000, 8000)

            # Submit
            submit_btn = self.page.locator(
                'button[aria-label="Post"], button:has-text("Post")'
            ).first
            if await submit_btn.is_visible(timeout=5000):
                await submit_btn.click()
                await self.humaniser.pause(2000, 4000)
                self.log.info(f"Review posted: {rating} stars | {name}")
                self._record_review(name)
            else:
                self.log.warning("Submit button not found — discarding review")
                await self.page.keyboard.press("Escape")

        except Exception as e:
            self.log.error(f"Review failed for {name}: {e}")
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass

    def _pick_rating(self) -> int:
        weights = self._data["rating_weights"]
        ratings = [int(k) for k in weights.keys()]
        probs   = list(weights.values())
        return random.choices(ratings, weights=probs, k=1)[0]

    async def _build_review_text(self, business_type: str, business_name: str,
                                  rating: int) -> str:
        """Generate review text via Claude API, falling back to templates on failure."""
        try:
            return await self._claude_review_text(business_type, business_name, rating)
        except Exception as e:
            self.log.debug(f"Claude review gen failed ({e}) — using template")
            return self._template_review_text(business_type)

    async def _claude_review_text(self, business_type: str, business_name: str,
                                   rating: int) -> str:
        """Call Claude Haiku to generate a unique, natural-sounding review."""
        import os
        import asyncio as _asyncio

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        # Derive area from account location field
        location = self.account.get("location", "London")
        area = location.split(",")[0].strip() if "," in location else location

        items = self._data["review_items"].get(
            business_type, self._data["review_items"].get("generic", [])
        )
        item_line = (
            f" The reviewer ordered/had: {random.choice(items)}." if items else ""
        )
        sentiments = {
            5: "excellent, highly positive",
            4: "good, would return",
            3: "okay, nothing special",
        }
        sentiment = sentiments.get(rating, "mixed")

        prompt = (
            f"Write a short Google Maps review (2–3 sentences) for a {business_type} "
            f"in {area}, London. Rating: {rating}/5 stars ({sentiment}).{item_line} "
            f"Rules: casual natural British English, no exclamation marks, "
            f"don't start the first sentence with 'I', don't mention the business name, "
            f"vary sentence structure, sound like a genuine local who visited once or twice."
        )

        def _sync_call() -> str:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()

        loop = _asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)

    def _template_review_text(self, business_type: str) -> str:
        """Fallback: template-based review generation (original logic)."""
        templates = self._data["review_templates"].get(
            business_type, self._data["review_templates"]["generic"]
        )
        items = self._data["review_items"].get(
            business_type, self._data["review_items"].get("generic", ["service"])
        )
        template = random.choice(templates)
        item     = random.choice(items) if items else "service"
        review   = template.replace("{item}", item)

        if random.random() < 0.35:
            openers = [
                "Visited last week. ", "Popped in recently. ",
                "Been here a couple of times now. ",
                "Tried this place for the first time last weekend. ",
                "Came here with a friend. ", "Stopped in on a whim. ",
                "Went here on a recommendation. ", "Visited on a Saturday afternoon. ",
            ]
            opener = random.choice(openers)
            review = opener + review[0].lower() + review[1:]

        if random.random() < 0.25:
            closers = [
                " Would recommend.", " Definitely worth a visit.",
                " Good value for the area.", " Will be back.",
                " Handy spot to know about.", " Worth trying if you're nearby.",
            ]
            if review.rstrip()[-1] not in ".!?":
                review = review.rstrip() + "."
            review += random.choice(closers)

        return review

    async def _click_star(self, rating: int) -> None:
        """Click the appropriate star in the rating widget."""
        try:
            star = self.page.locator(
                f'button[aria-label="{rating} star"], '
                f'span[aria-label="{rating} stars"], '
                f'div[data-rating="{rating}"]'
            ).first
            if await star.is_visible(timeout=5000):
                await star.click()
                self.log.debug(f"Selected {rating} stars")
            else:
                # Fallback: click by position in a star row
                stars = self.page.locator('button[aria-label*="star"]').all()
                star_list = await stars
                if len(star_list) >= rating:
                    await star_list[rating - 1].click()
        except Exception as e:
            self.log.debug(f"Star click failed: {e}")

    async def _type_review(self, text: str) -> None:
        """Type the review text into the compose box."""
        try:
            review_box = self.page.locator(
                'textarea[aria-label*="review"], div[contenteditable="true"]'
            ).first
            await review_box.click()
            await self.humaniser.pause(800, 1500)

            for char in text:
                delay = max(40, min(300, int(random.gauss(110, 40))))
                await review_box.type(char, delay=delay)
                if random.random() < 0.04:
                    import asyncio
                    await asyncio.sleep(random.uniform(0.5, 1.5))

            self.log.debug(f"Typed review: {text[:60]}…")
        except Exception as e:
            self.log.debug(f"Review typing failed: {e}")

    # ------------------------------------------------------------------
    # Consent dialog
    # ------------------------------------------------------------------

    async def _dismiss_consent(self) -> None:
        for selector in [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Reject all")',
        ]:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self.humaniser.pause(500, 1200)
                    return
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
        return {"recent_visits": [], "reviewed": []}

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

    def _record_review(self, name: str) -> None:
        reviewed = self._state.setdefault("reviewed", [])
        if name not in reviewed:
            reviewed.append(name)
        self._state["last_review_date"] = str(date.today())
        self._save_state()
