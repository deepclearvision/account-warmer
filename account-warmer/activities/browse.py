"""
Browse Activity (Week 7+)
Visits well-known websites directly (simulating navigational behaviour).
Gives the account a browsing history beyond just Gmail and Google.
"""

import random
from playwright.async_api import Page
from activities.base_activity import BaseActivity

# Sites to browse — reputable, varied, region-neutral
BROWSE_TARGETS = [
    ("https://www.bbc.com/news",            "BBC News"),
    ("https://www.wikipedia.org",           "Wikipedia"),
    ("https://www.reddit.com",              "Reddit"),
    ("https://www.theguardian.com",         "The Guardian"),
    ("https://www.weather.com",             "Weather"),
    ("https://stackoverflow.com",          "Stack Overflow"),
    ("https://www.imdb.com",               "IMDB"),
    ("https://www.youtube.com",            "YouTube"),
    ("https://www.amazon.co.uk",           "Amazon UK"),
    ("https://www.ebay.co.uk",             "eBay UK"),
]


class BrowseActivity(BaseActivity):

    async def run(self) -> bool:
        site_url, site_name = random.choice(BROWSE_TARGETS)
        self.log.info(f"Browsing: {site_name}")

        try:
            await self.humaniser.navigate(self.page, site_url)
            await self.humaniser.pause(2000, 5000)

            # Dismiss cookie banners if present
            for selector in [
                'button:has-text("Accept")',
                'button:has-text("Accept all")',
                'button:has-text("OK")',
                'button:has-text("Got it")',
            ]:
                try:
                    btn = self.page.locator(selector).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        await self.humaniser.pause(500, 1200)
                        break
                except Exception:
                    pass

            # Scroll through the page
            await self.humaniser.human_scroll(self.page, max_fraction=0.6)
            await self.humaniser.reading_pause()

            # Maybe click an article or link
            if random.random() < 0.5:
                await self._click_article()

            return True

        except Exception as e:
            self.log.error(f"Browse activity failed on {site_name}: {e}")
            return False

    async def _click_article(self) -> None:
        """Click a random article/content link on the current page."""
        try:
            links = self.page.locator("article a, h2 a, h3 a").all()
            if not links:
                return
            link = random.choice(links[:6])
            href = await link.get_attribute("href")
            if not href or href.startswith("#"):
                return

            self.log.debug(f"Clicking article: {href[:60]}")
            await self.humaniser.pause(2000, 5000)
            await link.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.humaniser.pause(2000, 4000)
            await self.humaniser.human_scroll(self.page)
            await self.humaniser.reading_pause()

            await self.page.go_back()
            await self.page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            self.log.debug(f"Article click failed: {e}")
