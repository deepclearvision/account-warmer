"""
Search Activity
Performs Google searches with human-like behaviour:
- Picks localised + general search terms
- Optionally clicks into a result and reads the page
- Optionally follows an internal link on the result page
"""

import random
import json
from pathlib import Path
from playwright.async_api import Page
from activities.base_activity import BaseActivity

SEARCH_TERMS_FILE = Path(__file__).parent.parent / "data" / "search_terms.json"
GOOGLE_URL = "https://www.google.com"


class SearchActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict):
        super().__init__(page, account, behaviour_cfg)
        with open(SEARCH_TERMS_FILE, encoding="utf-8") as f:
            self._terms = json.load(f)
        self._search_cfg = behaviour_cfg.get("search", {})

    async def run(self) -> bool:
        num_searches = random.randint(
            *self._search_cfg.get("searches_per_session", [2, 5])
        )
        self.log.info(f"Search session: {num_searches} searches planned")

        for i in range(num_searches):
            term = self._pick_term()
            success = await self._do_search(term)
            if success:
                try:
                    from core.activity_log import log_event
                    log_event(
                        account_id    = self.account["id"],
                        device        = "desktop",
                        activity_type = "search",
                        detail        = {"search_term": term},
                    )
                except Exception:
                    pass
            else:
                self.log.warning(f"Search failed for term: {term!r}")
                return False
            if i < num_searches - 1:
                await self.humaniser.pause(3000, 9000)

        return True

    # Extra category keys added in the expanded search_terms.json.
    # Weights are relative — normalised automatically below.
    _EXTRA_CATEGORIES = {
        "shopping":        0.07,
        "food_drink":      0.07,
        "health_fitness":  0.07,
        "entertainment":   0.07,
        "sport":           0.07,
        "travel":          0.06,
        "finance":         0.05,
        "home_property":   0.05,
        "technology":      0.07,
        "news_current":    0.05,
    }

    def _pick_term(self) -> str:
        location = self.account.get("location", "")
        local_terms = self._terms.get("local", {}).get(location, [])

        pools: list = []
        pool_weights: list = []

        if local_terms:
            pools.append(local_terms)
            pool_weights.append(0.25)

        pools.append(self._terms["general"])
        pool_weights.append(0.18)
        pools.append(self._terms["informational"])
        pool_weights.append(0.10)
        pools.append(self._terms["navigational"])
        pool_weights.append(0.06)

        # Add expanded categories if present in the loaded JSON
        for cat, weight in self._EXTRA_CATEGORIES.items():
            terms = self._terms.get(cat)
            if terms:
                pools.append(terms)
                pool_weights.append(weight)

        total = sum(pool_weights)
        pool_weights = [w / total for w in pool_weights]

        chosen_pool = random.choices(pools, weights=pool_weights, k=1)[0]
        return random.choice(chosen_pool)

    async def _do_search(self, term: str) -> bool:
        self.log.info(f"Searching: {term!r}")
        try:
            # Navigate — 40% via address bar typing, 60% programmatic
            await self.humaniser.navigate(self.page, GOOGLE_URL)
            await self.humaniser.pause(800, 2500)

            # Accept cookies if the consent dialog appears
            await self._dismiss_consent()

            # Type in the search box
            search_box = 'textarea[name="q"], input[name="q"]'
            await self.humaniser.type_text(self.page, search_box, term)
            await self.humaniser.pause(300, 900)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(1000, 3000)

            # Scroll the results page
            await self.humaniser.human_scroll(self.page, max_fraction=0.6)
            await self.humaniser.reading_pause()
            await self.humaniser.maybe_shortcut(self.page)

            # Occasionally navigate to page 2 (real users do this when page 1 doesn't satisfy)
            page2_prob = self._search_cfg.get("page_two_probability", 0.20)
            if random.random() < page2_prob:
                await self._browse_page_two()
                return True

            # Maybe click a result, or hover over a snippet without clicking
            click_prob = self._search_cfg.get("click_result_probability", 0.65)
            roll = random.random()
            if roll < click_prob:
                await self._click_result()
            elif roll < click_prob + 0.15:
                # Hover over a result snippet without clicking — reading the preview
                await self._hover_result_snippet()

            return True

        except Exception as e:
            self.log.error(f"Search error: {e}")
            return False

    async def _dismiss_consent(self) -> None:
        """Dismiss Google's cookie/consent dialog if present."""
        try:
            for selector in [
                'button:has-text("Accept all")',
                'button:has-text("I agree")',
                'button:has-text("Reject all")',
            ]:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await self.humaniser.human_click(self.page, selector)
                    await self.humaniser.pause(500, 1200)
                    return
        except Exception:
            pass

    async def _click_result(self) -> None:
        """Hover over a search result, dwell, then click and read the page."""
        try:
            weights = self._search_cfg.get(
                "result_position_weights", [0.45, 0.28, 0.15, 0.08, 0.04]
            )
            position = random.choices(range(1, len(weights) + 1), weights=weights, k=1)[0]

            results = self.page.locator("div#search a[href]:not([href^='/search'])")
            count = await results.count()
            if count == 0:
                return

            idx = min(position - 1, count - 1)
            result = results.nth(idx)
            href = await result.get_attribute("href")
            self.log.debug(f"Clicking result #{position}: {href}")

            # Move cursor to the result and hover before clicking —
            # Google tracks mouseover events on result cards
            selector = f"div#search a[href]:not([href^='/search']) >> nth={idx}"
            await self.humaniser.hover_then_click(self.page, selector)

            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(1500, 3000)

            # Dismiss any cookie/GDPR banner before interacting
            await self.dismiss_cookie_banner()
            await self.humaniser.pause(500, 1200)

            # Read the page
            read_time = random.uniform(
                *self._search_cfg.get("result_read_seconds", [20, 90])
            )
            self.log.debug(f"Reading result page for {read_time:.0f}s")
            await self.humaniser.human_scroll(self.page)
            await self.humaniser.reading_pause()

            # Maybe follow an internal link
            follow_prob = self._search_cfg.get("follow_internal_link_probability", 0.25)
            if random.random() < follow_prob:
                await self._follow_internal_link()

            # Go back to results
            await self.humaniser.pause(2000, 5000)
            await self.page.go_back(wait_until="domcontentloaded", timeout=45000)
            await self.page.wait_for_load_state("domcontentloaded", timeout=45000)
            await self.humaniser.pause(800, 2000)

        except Exception as e:
            self.log.debug(f"Could not click result: {e}")

    async def _follow_internal_link(self) -> None:
        """Click a random internal link on the current page."""
        try:
            current_host = self.page.url.split("/")[2] if self.page.url.startswith("http") else ""
            links = self.page.locator(f'a[href*="{current_host}"]')
            count = await links.count()
            if count > 0:
                idx = random.randint(0, min(count - 1, 5))
                await self.humaniser.pause(3000, 8000)
                await links.nth(idx).click()
                await self.page.wait_for_load_state("domcontentloaded", timeout=45000)
                await self.humaniser.pause(1500, 4000)
                await self.dismiss_cookie_banner()
                await self.humaniser.human_scroll(self.page)
                await self.humaniser.reading_pause()
                await self.page.go_back(wait_until="domcontentloaded", timeout=45000)
                await self.page.wait_for_load_state("domcontentloaded", timeout=45000)
        except Exception as e:
            self.log.debug(f"Internal link follow failed: {e}")

    async def _browse_page_two(self) -> None:
        """Navigate to Google page 2 and scroll it briefly."""
        try:
            next_btn = self.page.locator('a#pnnext, a[aria-label="Next page"]').first
            if await next_btn.count() > 0 and await next_btn.is_visible(timeout=3000):
                await self.humaniser.hover_then_click(self.page, 'a#pnnext, a[aria-label="Next page"]')
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1500, 3500)
                await self.humaniser.human_scroll(self.page, max_fraction=0.5)
                await self.humaniser.reading_pause()
                self.log.debug("Browsed page 2 of results")
        except Exception as e:
            self.log.debug(f"Page 2 browse failed: {e}")

    async def _hover_result_snippet(self) -> None:
        """Move cursor over a result snippet to read it without clicking."""
        try:
            results = self.page.locator("div#search div.g")
            count = await results.count()
            if count == 0:
                return
            idx = random.randint(0, min(count - 1, 4))
            result = results.nth(idx)
            box = await result.bounding_box()
            if box:
                tx = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                await self.humaniser.move_to(self.page, tx, ty)
                await self.humaniser.pause(1500, 4000)
                self.log.debug("Hovered result snippet without clicking")
        except Exception as e:
            self.log.debug(f"Snippet hover failed: {e}")
