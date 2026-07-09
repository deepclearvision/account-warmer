"""
Search Activity
Performs Google searches with human-like behaviour:
- Picks localised + general search terms
- Uses template expansion with {city}, {year} tokens for massive variety
- Tracks per-account term history to avoid robotic repeats
- Optionally clicks into a result and reads the page
- Optionally follows an internal link on the result page
"""

import random
import json
from datetime import date, datetime
from pathlib import Path
from playwright.async_api import Page
from activities.base_activity import BaseActivity
from core.paths import STATE_DIR

SEARCH_TERMS_FILE      = Path(__file__).parent.parent / "data" / "search_terms.json"
SEARCH_TEMPLATES_FILE  = Path(__file__).parent.parent / "data" / "search_templates.json"
UK_CITIES_FILE         = Path(__file__).parent.parent / "data" / "uk_cities.json"
GOOGLE_URL             = "https://www.google.com"

# How many days before a term can be re-used by the same account
_TERM_COOLDOWN_DAYS = 14

# Chance a query modifier is appended to a term at runtime
_MODIFIER_CHANCE = 0.20

# Max retries to find a non-recently-used term before giving up
_MAX_TERM_RETRIES = 20


def _resolve_years(text: str) -> str:
    """Replace {current_year}, {last_year}, {next_year} tokens in a string."""
    this_year = date.today().year
    return (text
            .replace("{current_year}", str(this_year))
            .replace("{last_year}",    str(this_year - 1))
            .replace("{next_year}",    str(this_year + 1))
            .replace("{month_name}",   date.today().strftime("%B")))


class SearchActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict):
        super().__init__(page, account, behaviour_cfg)
        # Static term pools (always loaded)
        with open(SEARCH_TERMS_FILE, encoding="utf-8") as f:
            self._terms = json.load(f)
        self._search_cfg = behaviour_cfg.get("search", {})

        # Template system — optional, graceful fallback if files missing
        self._templates = self._load_json(SEARCH_TEMPLATES_FILE)
        self._uk_cities = self._load_json(UK_CITIES_FILE)

        # Per-account term history
        self._history_file = STATE_DIR / f"{account['id']}_search_history.json"
        self._history = self._load_history()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> bool:
        num_searches = random.randint(
            *self._search_cfg.get("searches_per_session", [2, 5])
        )
        self.log.info(f"Search session: {num_searches} searches planned")

        for i in range(num_searches):
            term = self._pick_term()
            success = await self._do_search(term)
            if success:
                self._record_term(term)
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

    # ------------------------------------------------------------------
    # Term selection
    # ------------------------------------------------------------------

    # Weights for static category pools (used when templates unavailable)
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
        """
        Select a search term with the following strategy:

        1.  Local pool (25% weight) → always template-based if templates available,
            with {city} token resolved from the account's geo city + nearby cities.
            Falls back to static local terms if templates unavailable.

        2.  General + informational + navigational pools (34% combined) →
            50% from templates (with token resolution), 50% from static terms.

        3.  All other pools → static terms from search_terms.json.

        4.  Year rotation applied everywhere ({current_year} → 2026, etc.).

        5.  Terms used by this account in the last _TERM_COOLDOWN_DAYS are
            avoided (retries up to _MAX_TERM_RETRIES).

        6.  ~20% chance of appending a random modifier ("near me", "uk", etc.).
        """
        location = self.account.get("location", "")
        local_terms = self._terms.get("local", {}).get(location, [])

        pools: list = []
        pool_weights: list = []

        # --- Local pool (0.25) ---
        if local_terms or self._has_local_templates():
            pools.append("__LOCAL__")
            pool_weights.append(0.25)

        # --- General (0.18) ---
        pools.append("general")
        pool_weights.append(0.18)

        # --- Informational (0.10) ---
        pools.append("informational")
        pool_weights.append(0.10)

        # --- Navigational (0.06) ---
        if self._terms.get("navigational"):
            pools.append("navigational")
            pool_weights.append(0.06)

        # --- Extra categories ---
        for cat, weight in self._EXTRA_CATEGORIES.items():
            if self._terms.get(cat):
                pools.append(cat)
                pool_weights.append(weight)

        # Normalise
        total = sum(pool_weights)
        pool_weights = [w / total for w in pool_weights]
        chosen_pool = random.choices(pools, weights=pool_weights, k=1)[0]

        # --- Generate term ---
        # Local pool: always use templates if available
        if chosen_pool == "__LOCAL__":
            term = self._generate_local_term(local_terms)
        # General / informational: 50% templates, 50% static
        elif chosen_pool in ("general", "informational") and self._templates:
            if random.random() < 0.5:
                term = self._generate_general_template_term()
            else:
                term = random.choice(self._terms[chosen_pool])
        # Navigational: always static (brand names don't need templates)
        elif chosen_pool == "navigational":
            term = random.choice(self._terms["navigational"])
        # Extra categories: static
        else:
            term = random.choice(self._terms[chosen_pool])

        # Year rotation
        term = _resolve_years(term)

        # Optional query modifier
        if random.random() < _MODIFIER_CHANCE:
            term = self._apply_modifier(term)

        return term

    # ------------------------------------------------------------------
    # Template-based term generation
    # ------------------------------------------------------------------

    def _has_local_templates(self) -> bool:
        """Check whether the local templates section exists."""
        return bool(self._templates and self._templates.get("local", {}).get("templates"))

    def _generate_local_term(self, static_local_terms: list) -> str:
        """
        Generate a local search term using the template system.
        Falls back to static terms if templates unavailable or after retries fail.
        """
        # If no templates, use static fallback
        if not self._has_local_templates():
            return self._pick_with_history(static_local_terms)

        local_cfg = self._templates["local"]
        sub_weights = local_cfg.get("weights", {})
        templates_by_sub = local_cfg.get("templates", {})

        if not templates_by_sub:
            return self._pick_with_history(static_local_terms or ["news near me"])

        # Try up to _MAX_TERM_RETRIES to find a non-recent term
        for _ in range(_MAX_TERM_RETRIES):
            # Pick subcategory (skip metadata keys like _note)
            sub_names = [k for k in templates_by_sub.keys() if not k.startswith("_")]
            sub_w = [sub_weights.get(s, 0.05) for s in sub_names]
            total_w = sum(sub_w) or 1.0
            sub_w = [w / total_w for w in sub_w]
            chosen_sub = random.choices(sub_names, weights=sub_w, k=1)[0]

            # Pick template
            template = random.choice(templates_by_sub[chosen_sub])

            # Resolve city tokens
            city = self._get_city()
            city2 = self._get_city2(city)
            term = (template
                    .replace("{city}",  city)
                    .replace("{city2}", city2))

            # Year rotation
            term = _resolve_years(term)

            if not self._is_term_recent(term):
                return term

        # All retries exhausted — return a fresh static term or the last generated one
        if static_local_terms:
            return self._pick_with_history(static_local_terms)
        return term  # last generated term (even if recent — better than crashing)

    def _generate_general_template_term(self) -> str:
        """Generate a term from the general_templates section with token resolution."""
        templates_cfg = self._templates.get("general_templates", {})
        token_values  = self._templates.get("token_values", {})

        # Pick a template category
        categories = [k for k in templates_cfg.keys() if not k.startswith("_")]
        if not categories:
            return "news today"  # ultimate fallback

        chosen_cat = random.choice(categories)
        template = random.choice(templates_cfg[chosen_cat])

        # Resolve tokens in the template
        term = template
        for token_name in [
            "food", "household_item", "skill", "hobby", "personal_skill",
            "life_problem", "expensive_thing", "activity",
            "product", "product2", "price",
            "concept", "term", "term2",
            "uk_thing", "uk_system", "financial_term",
            "condition", "supplement", "medicine", "medicine2",
            "minor_ailment", "symptom",
        ]:
            placeholder = "{" + token_name + "}"
            if placeholder in term:
                pool = token_values.get(token_name, [])
                if pool:
                    term = term.replace(placeholder, random.choice(pool), 1)

        return term

    # ------------------------------------------------------------------
    # City resolution
    # ------------------------------------------------------------------

    def _get_city(self) -> str:
        """
        Return the city to use for local search templates.
        Uses geo_city from account config, falling back to location field,
        then to a nearby city from the most relevant region.
        """
        geo_city = self.account.get("geo_city", "").strip()
        if geo_city:
            return geo_city.title()

        location = self.account.get("location", "").strip()
        if location:
            parts = location.split(",")[0].strip()
            if parts:
                return parts

        # Fallback: pick a random city from the London pool
        cities = self._uk_cities.get("london", ["London"])
        return random.choice(cities)

    def _get_city2(self, primary_city: str) -> str:
        """
        Return a second city (different from primary) for distance/directions
        templates like 'how far is {city} from {city2}'.
        """
        primary_lower = primary_city.lower()
        # Find which region the primary city belongs to
        primary_region = None
        for region, cities in self._uk_cities.items():
            if any(primary_lower == c.lower() for c in cities):
                primary_region = region
                break

        if primary_region:
            pool = [c for c in self._uk_cities[primary_region]
                    if c.lower() != primary_lower]
            if pool:
                return random.choice(pool)

        # Fallback: pick any city
        all_cities = []
        for cities in self._uk_cities.values():
            all_cities.extend(cities)
        pool = [c for c in all_cities if c.lower() != primary_lower]
        return random.choice(pool) if pool else "London"

    # ------------------------------------------------------------------
    # Query modifiers
    # ------------------------------------------------------------------

    def _apply_modifier(self, term: str) -> str:
        """Apply a random query modifier to the term for additional variance."""
        modifiers_cfg = self._templates.get("modifiers", {}) if self._templates else {}
        if not modifiers_cfg:
            return term

        # Pick a modifier type and a value from it
        modifier_type = random.choice(list(modifiers_cfg.keys()))
        pool = modifiers_cfg[modifier_type]
        if not pool:
            return term

        modifier = random.choice(pool)
        # Resolve years in the modifier too
        modifier = _resolve_years(modifier)

        # Decide placement: prefix or suffix
        if modifier_type == "question_words":
            # Don't double-prefix if term already starts with a question word
            first_word = term.split()[0].lower() if term else ""
            if first_word in ("how", "what", "why", "when", "where", "can", "should"):
                return term
            return f"{modifier} {term}"
        else:
            return f"{term} {modifier}"

    # ------------------------------------------------------------------
    # Term history (per-account de-duplication)
    # ------------------------------------------------------------------

    def _load_history(self) -> dict:
        """Load the per-account search term history, returning {term: iso_date}."""
        if self._history_file.exists():
            try:
                data = json.loads(self._history_file.read_text(encoding="utf-8"))
                return data.get("used_terms", {})
            except Exception:
                pass
        return {}

    def _save_history(self) -> None:
        """Persist the search term history."""
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text(
                json.dumps({"used_terms": self._history}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _is_term_recent(self, term: str) -> bool:
        """Return True if this account used this term within the cooldown window."""
        used_date_str = self._history.get(term)
        if not used_date_str:
            return False
        try:
            used_date = date.fromisoformat(used_date_str)
            return (date.today() - used_date).days < _TERM_COOLDOWN_DAYS
        except Exception:
            return False

    def _record_term(self, term: str) -> None:
        """Record a search term as used today."""
        self._history[term] = str(date.today())
        # Prune entries older than cooldown window to keep file small
        cutoff = date.today()
        self._history = {
            t: d for t, d in self._history.items()
            if (cutoff - date.fromisoformat(d)).days <= _TERM_COOLDOWN_DAYS + 7
        }
        self._save_history()

    def _pick_with_history(self, pool: list) -> str:
        """
        Pick a term from a static pool, avoiding recently-used terms.
        Falls back to random choice if all terms are recent.
        """
        if not pool:
            return "news today"
        # Shuffle to avoid always hitting the same non-recent term first
        shuffled = pool.copy()
        random.shuffle(shuffled)
        for term in shuffled:
            if not self._is_term_recent(term):
                return term
        # All recent — pick randomly anyway
        return random.choice(pool)

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        """Load a JSON file silently, returning None on any failure."""
        try:
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Search execution
    # ------------------------------------------------------------------

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

            # Occasionally navigate to page 2
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
                await self._hover_result_snippet()

            return True

        except Exception as e:
            self.log.error(f"Search error: {e}")
            return False

    async def _dismiss_consent(self) -> None:
        """Dismiss Google's cookie/consent dialog if present."""
        await self.dismiss_google_consent()

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

            # Strip target="_blank" from ALL search result links before clicking.
            # Google sometimes wraps results in <a target="_blank"> which opens a new
            # tab and closes the current page, breaking the CDP connection.
            await self.page.evaluate("""() => {
                document.querySelectorAll('div#search a[href]').forEach(a => {
                    a.removeAttribute('target');
                    a.setAttribute('rel', '');
                });
            }""")

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
                await self.humaniser.hover_then_click(
                    self.page, 'a#pnnext, a[aria-label="Next page"]'
                )
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
