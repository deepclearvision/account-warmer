"""
Pin Warmer

Warming script for Google accounts intended to add a new place pin to Maps.
Strategy is fundamentally different from account_warmer.py:

  - Maps activity is 60-70% of all sessions
  - All Maps browsing and reviews are concentrated in the target area
  - Explicitly builds Local Guide contribution points (reviews, photo views, edits)
  - Tracks a readiness score and tells you when the account is ready
  - Culminates in the Add a Missing Place submission

Google's trust bar for pin addition is higher than for reviews.
This script is designed to build the specific signals Google checks:
  1. Account age (6+ weeks)
  2. Contribution history in the area (5+ reviews)
  3. Local Guide points (50+ = Level 3)
  4. Location history near the area (from emulator_sessions.py)

Run warming sessions daily or every other day:
  python pin_warmer.py --all

Check if accounts are ready to add a pin:
  python pin_warmer.py --check

Add the pin (run when --check shows ready):
  python pin_warmer.py --add-pin acc_001

Preview what would run without doing anything:
  python pin_warmer.py --all --dry-run
"""

import argparse
import asyncio
import json
import random
import sys
from datetime import date
from pathlib import Path

import yaml

from core.logger    import get_logger
from core.humaniser import pause, type_text, human_click, human_scroll, reading_pause, maybe_distracted
from core.profile_manager import ProfileSession

# ── Paths ──────────────────────────────────────────────────────────────────────

from core.paths import PIN_WARMER_FILE as CONFIG_FILE, STATE_DIR
MAPS_URL    = "https://www.google.com/maps"

# ── Local Guide points ─────────────────────────────────────────────────────────
# https://support.google.com/maps/answer/6225846

POINTS = {
    "review":    10,
    "photo":      5,
    "edit":       5,
    "qa_answer":  1,
}

# ── Readiness thresholds ───────────────────────────────────────────────────────

READY_WEEKS    = 6   # Minimum account age in weeks
READY_REVIEWS  = 5   # Minimum reviews written in target area
READY_SESSIONS = 8   # Minimum emulator GPS sessions in target area
READY_POINTS   = 50  # Minimum Local Guide points (Level 3 = 50 pts)

# ── Activity schedule by week band ────────────────────────────────────────────
# Much more Maps-heavy than the general warmer.
# All Maps activities are focused on the target area.

SCHEDULE = {
    "weeks_1_2": {
        "label":    "Establishing local presence",
        "weights":  {"area_browse": 0.40, "review_local": 0.20,
                     "search": 0.25, "email_read": 0.15},
        "actions":  [1, 2],
    },
    "weeks_3_4": {
        "label":    "Building contribution history",
        "weights":  {"area_browse": 0.25, "review_local": 0.40,
                     "suggest_edit": 0.15, "search": 0.10, "email_read": 0.10},
        "actions":  [2, 3],
    },
    "weeks_5_6": {
        "label":    "Deepening local footprint",
        "weights":  {"area_browse": 0.20, "review_local": 0.40,
                     "suggest_edit": 0.20, "search": 0.10, "email_read": 0.10},
        "actions":  [2, 3],
    },
    "weeks_7_plus": {
        "label":    "Final push",
        "weights":  {"area_browse": 0.15, "review_local": 0.45,
                     "suggest_edit": 0.25, "search": 0.10, "email_read": 0.05},
        "actions":  [2, 4],
    },
}

# ── Review content ─────────────────────────────────────────────────────────────

REVIEW_TEMPLATES = [
    "Really good {type}. Staff were friendly and the place was clean. Would go back.",
    "Been here a couple of times now, always a solid experience. Good value.",
    "Decent {type}, nothing fancy but does the job well. Convenient location.",
    "Popped in on a whim and was pleasantly surprised. Will definitely return.",
    "Good service, reasonable prices. A solid local option.",
    "Nice atmosphere and helpful staff. Would visit again without hesitation.",
    "Clean and well-run. Does what it says on the tin.",
    "Local gem. Far better than the chain alternatives nearby.",
    "Can't fault it really. Exactly what you'd want from a local {type}.",
    "Pretty good overall. Staff clearly know what they're doing.",
    "Always reliable. Been a few times and it's consistently good.",
    "Friendly staff, fair prices. Happy to support a local business like this.",
]

LOCAL_SEARCH_TERMS = [
    "coffee shop", "restaurant", "pub", "pharmacy", "barber",
    "gym", "supermarket", "dentist", "takeaway", "park",
    "hairdresser", "garage", "estate agent", "newsagent", "bakery",
    "post office", "off licence", "butcher", "fish and chips",
]

TYPE_LABELS = {
    "coffee shop": "cafe", "restaurant": "restaurant", "pub": "pub",
    "pharmacy": "pharmacy", "barber": "barber", "gym": "gym",
    "hairdresser": "salon", "garage": "garage", "bakery": "bakery",
}


# ── State helpers ──────────────────────────────────────────────────────────────

def _load_config() -> list:
    if not CONFIG_FILE.exists():
        print(f"\n  Config not found: {CONFIG_FILE}")
        print(f"  Copy config/pin_warmer_accounts.yaml.example and fill in your accounts.\n")
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f).get("accounts", [])


def _load_state(account_id: str) -> dict:
    path = STATE_DIR / f"pin_{account_id}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "reviews_written":  0,
        "photos_uploaded":  0,
        "edits_suggested":  0,
        "points":           0,
        "reviewed_places":  [],
        "emulator_sessions": 0,
        "pin_submitted":    False,
        "pin_submit_date":  None,
    }


def _save_state(account_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / f"pin_{account_id}.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _weeks_elapsed(account: dict) -> int:
    start = date.fromisoformat(account.get("warmup_start_date", str(date.today())))
    return max(0, (date.today() - start).days // 7)


def _week_config(weeks: int) -> dict:
    if weeks < 2:
        return SCHEDULE["weeks_1_2"]
    elif weeks < 4:
        return SCHEDULE["weeks_3_4"]
    elif weeks < 6:
        return SCHEDULE["weeks_5_6"]
    return SCHEDULE["weeks_7_plus"]


def _choose_activities(week_cfg: dict) -> list:
    allowed  = list(week_cfg["weights"].keys())
    weights  = [week_cfg["weights"][a] for a in allowed]
    n        = random.randint(*week_cfg["actions"])
    chosen   = []
    seen     = set()
    attempts = 0
    while len(chosen) < n and attempts < n * 4:
        pick = random.choices(allowed, weights=weights, k=1)[0]
        if pick in seen and pick not in ("area_browse", "search"):
            attempts += 1
            continue
        chosen.append(pick)
        seen.add(pick)
        attempts += 1
    random.shuffle(chosen)
    return chosen


# ── Readiness check ────────────────────────────────────────────────────────────

def check_readiness(account: dict, state: dict) -> dict:
    weeks    = _weeks_elapsed(account)
    reviews  = state.get("reviews_written",   0)
    points   = state.get("points",            0)
    sessions = state.get("emulator_sessions", 0)
    done     = state.get("pin_submitted",     False)

    checks = {
        "Account age":        (weeks >= READY_WEEKS,     f"{weeks}/{READY_WEEKS} weeks"),
        "Reviews in area":    (reviews >= READY_REVIEWS,  f"{reviews}/{READY_REVIEWS}"),
        "Local Guide points": (points >= READY_POINTS,   f"{points}/{READY_POINTS}"),
        "Emulator sessions":  (sessions >= READY_SESSIONS, f"{sessions}/{READY_SESSIONS}"),
    }

    passed = sum(1 for ok, _ in checks.values() if ok)
    ready  = (passed == len(checks)) and not done

    return {
        "ready":    ready,
        "score":    f"{passed}/{len(checks)}",
        "checks":   checks,
        "pin_done": done,
    }


def print_readiness_report(accounts: list) -> None:
    print()
    for account in accounts:
        state = _load_state(account["id"])
        r     = check_readiness(account, state)
        tag   = "READY" if r["ready"] else ("DONE" if r["pin_done"] else f"{r['score']}")
        print(f"  {account['id']}  ({account['email']})")
        print(f"  Status: {tag}")
        for label, (ok, detail) in r["checks"].items():
            tick = "✓" if ok else "✗"
            print(f"    {tick} {label}: {detail}")
        if r["ready"]:
            print(f"  → Ready to add pin: python pin_warmer.py --add-pin {account['id']}")
        elif r["pin_done"]:
            date_done = state.get("pin_submit_date", "unknown date")
            print(f"  → Pin submitted on {date_done}. Check Maps for listing status.")
        else:
            print(f"  → Keep running daily warming sessions.")
        print()


# ── Maps helper ────────────────────────────────────────────────────────────────

async def _dismiss_consent(page) -> None:
    for selector in [
        'button:has-text("Accept all")',
        'button:has-text("Reject all")',
        'button:has-text("Accept")',
        '[aria-label="Accept all"]',
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1500):
                await human_click(page, btn)
                await pause(1, 2)
                return
        except Exception:
            pass


async def _maps_search(page, query: str) -> bool:
    """Type a query into the Maps search box and wait for results."""
    try:
        box = page.locator(
            'input#searchboxinput, input[aria-label="Search Google Maps"]'
        ).first
        await human_click(page, box)
        await box.fill("")
        await type_text(page, box, query)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("networkidle", timeout=12000)
        return True
    except Exception:
        return False


# ── Activities ─────────────────────────────────────────────────────────────────

async def activity_area_browse(page, account: dict, log) -> bool:
    """
    Browse Maps in the target area — pan, view local listings, look at photos.
    Establishes the account as a regular presence in this area.
    """
    area = account["target_area"]
    lat, lng = area["lat"], area["lng"]
    term = random.choice(LOCAL_SEARCH_TERMS)

    log.info(f"Area browse: '{term}' near {area['name']}")

    await page.goto(f"{MAPS_URL}/@{lat},{lng},15z", wait_until="domcontentloaded")
    await _dismiss_consent(page)
    await reading_pause()

    if not await _maps_search(page, f"{term} near {area['name']}"):
        return False

    await reading_pause()
    await maybe_distracted()

    try:
        results = page.locator('[role="feed"] a[href*="/maps/place/"]')
        count   = min(await results.count(), random.randint(2, 4))
        for i in range(count):
            try:
                result = results.nth(i)
                await human_click(page, result)
                await page.wait_for_load_state("networkidle", timeout=8000)
                await reading_pause()
                await human_scroll(page, distance=random.randint(150, 350))
                await reading_pause()

                # Occasionally browse photos
                if random.random() < 0.35:
                    photo_btn = page.locator(
                        'button[aria-label*="photo" i], [data-tab-index="1"]'
                    ).first
                    if await photo_btn.is_visible(timeout=2000):
                        await human_click(page, photo_btn)
                        await reading_pause()
                        await page.keyboard.press("Escape")

                await page.go_back()
                await page.wait_for_load_state("networkidle", timeout=8000)
                await pause(3, 8)
            except Exception:
                continue
    except Exception as e:
        log.warning(f"Browse failed: {e}")

    log.info("Area browse complete")
    return True


async def activity_review_local(page, account: dict, state: dict, log) -> bool:
    """
    Find an unreviewed local business in the target area and leave a review.
    Skips places already reviewed by this account.
    """
    area     = account["target_area"]
    lat, lng = area["lat"], area["lng"]
    term     = random.choice(LOCAL_SEARCH_TERMS)
    reviewed = set(state.get("reviewed_places", []))

    log.info(f"Looking for local business to review: '{term}'")

    await page.goto(f"{MAPS_URL}/@{lat},{lng},15z", wait_until="domcontentloaded")
    await _dismiss_consent(page)

    if not await _maps_search(page, f"{term} near {area['name']}"):
        return False

    await reading_pause()

    # Find an unreviewed result
    target_el, target_href, target_name = None, None, "unknown"
    try:
        results = page.locator('[role="feed"] a[href*="/maps/place/"]')
        count   = await results.count()
        for i in range(min(count, 10)):
            href = await results.nth(i).get_attribute("href") or ""
            text = (await results.nth(i).inner_text()).strip()[:50]
            if href and href not in reviewed:
                target_el, target_href, target_name = results.nth(i), href, text
                break
    except Exception:
        pass

    if not target_el:
        log.info("All nearby results already reviewed — skipping")
        return True

    try:
        await human_click(page, target_el)
        await page.wait_for_load_state("networkidle", timeout=10000)
        await reading_pause()
        await human_scroll(page, distance=random.randint(200, 400))
        await reading_pause()
    except Exception as e:
        log.warning(f"Navigation to listing failed: {e}")
        return False

    # Find the Write a Review button
    try:
        review_btn = page.locator(
            'button[aria-label*="review" i], button:has-text("Write a review")'
        ).first
        if not await review_btn.is_visible(timeout=5000):
            log.info("No review button visible — skipping")
            return True

        await human_click(page, review_btn)
        await pause(2, 3)

        # Star rating — weighted towards 4-5 stars
        rating = random.choices([5, 4, 3], weights=[0.55, 0.30, 0.15])[0]
        star_sel = page.locator(
            f'[aria-label="{rating} star"], span[aria-label="{rating} stars"]'
        ).first
        if await star_sel.is_visible(timeout=3000):
            await human_click(page, star_sel)
            await pause(1, 2)

        # Type review text
        btype    = TYPE_LABELS.get(term, "place")
        text     = random.choice(REVIEW_TEMPLATES).format(type=btype)
        text_box = page.locator('textarea, div[contenteditable="true"]').first
        if await text_box.is_visible(timeout=3000):
            await human_click(page, text_box)
            await type_text(page, text_box, text)
            await pause(1, 3)

        # Post
        post_btn = page.locator(
            'button:has-text("Post"), button[aria-label*="Post" i]'
        ).first
        if await post_btn.is_visible(timeout=4000):
            await human_click(page, post_btn)
            await pause(3, 5)
            log.info(f"Review posted: {target_name} ({rating}★)")

            state["reviews_written"] = state.get("reviews_written", 0) + 1
            state.setdefault("reviewed_places", []).append(target_href)
            state["points"] = state.get("points", 0) + POINTS["review"]
            return True
        else:
            log.warning("Post button not found")

    except Exception as e:
        log.warning(f"Review flow failed: {e}")

    return False


async def activity_suggest_edit(page, account: dict, state: dict, log) -> bool:
    """
    Open the Suggest an Edit panel on a local listing.
    Viewing and interacting with edit panels contributes to the account's
    contributor profile even without submitting a change.
    """
    area     = account["target_area"]
    lat, lng = area["lat"], area["lng"]
    term     = random.choice(LOCAL_SEARCH_TERMS)

    log.info("Opening suggest-edit panel on a local listing")

    await page.goto(f"{MAPS_URL}/@{lat},{lng},15z", wait_until="domcontentloaded")
    await _dismiss_consent(page)

    if not await _maps_search(page, term):
        return False

    await reading_pause()

    try:
        results = page.locator('[role="feed"] a[href*="/maps/place/"]')
        count   = await results.count()
        if count == 0:
            return False

        idx = random.randint(0, min(count - 1, 6))
        await human_click(page, results.nth(idx))
        await page.wait_for_load_state("networkidle", timeout=10000)
        await reading_pause()
        await human_scroll(page, distance=300)
        await reading_pause()

        edit_btn = page.locator(
            'button:has-text("Suggest an edit"), a:has-text("Suggest an edit")'
        ).first
        if not await edit_btn.is_visible(timeout=5000):
            log.info("No 'Suggest an edit' found on this listing")
            return False

        await human_click(page, edit_btn)
        await pause(2, 4)
        await reading_pause()

        # Browse the edit panel without submitting
        close_btn = page.locator(
            'button[aria-label="Close"], button:has-text("Cancel")'
        ).first
        if await close_btn.is_visible(timeout=3000):
            await reading_pause()
            await human_click(page, close_btn)

        state["edits_suggested"] = state.get("edits_suggested", 0) + 1
        log.info("Suggest edit panel browsed")
        return True

    except Exception as e:
        log.warning(f"Suggest edit failed: {e}")
        return False


async def activity_search(page, account: dict, log) -> bool:
    """General Google search — keeps the account active across Google products."""
    from activities.search import SearchActivity
    from core.humaniser import pause as h_pause

    behaviour = {
        "search": {
            "result_click_probability": 0.80,
            "time_on_result_seconds": [30, 90],
        }
    }
    try:
        return await SearchActivity(page, account, behaviour).run()
    except Exception as e:
        log.warning(f"Search activity failed: {e}")
        return False


async def activity_email_read(page, account: dict, log) -> bool:
    """Quick email check — keeps Gmail active."""
    behaviour = {
        "email": {
            "reply_probability": 0.20,
            "star_probability": 0.10,
        }
    }
    try:
        from activities.email_read import EmailReadActivity
        return await EmailReadActivity(page, account, behaviour).run()
    except Exception as e:
        log.warning(f"Email read failed: {e}")
        return False


# ── Pin addition ───────────────────────────────────────────────────────────────

async def activity_add_pin(page, account: dict, state: dict, log) -> bool:
    """
    Add the configured place as a new Maps listing via the Add a Missing Place form.

    Fills: name, category, address, phone, website.
    Opening hours and photos should be added manually via Maps once the
    listing is approved — the hours UI is too complex to reliably automate,
    and authentic photos need to come from a device at the location.
    """
    area = account["target_area"]
    pin  = account.get("pin_details", {})
    lat, lng = area["lat"], area["lng"]

    if not pin.get("name"):
        log.error("pin_details.name not set in config — cannot add pin")
        return False

    if state.get("pin_submitted"):
        log.info("Pin already submitted — skipping")
        return True

    log.info(f"Starting pin addition: {pin['name']}")

    # Go to the target area on Maps
    await page.goto(f"{MAPS_URL}/@{lat},{lng},17z", wait_until="domcontentloaded")
    await _dismiss_consent(page)
    await reading_pause()

    # ── Find the Add a Missing Place entry point ────────────────────────────
    found_entry = False

    # Attempt 1: search for the place address first — Maps may offer the option
    if pin.get("address"):
        if await _maps_search(page, pin["address"]):
            await reading_pause()
            link = page.locator(
                'a:has-text("Add a missing place"), button:has-text("Add a missing place")'
            ).first
            if await link.is_visible(timeout=4000):
                await human_click(page, link)
                found_entry = True
                await pause(2, 3)

    # Attempt 2: hamburger menu
    if not found_entry:
        try:
            menu = page.locator('[aria-label="Menu"]').first
            if await menu.is_visible(timeout=3000):
                await human_click(page, menu)
                await pause(1, 2)
                link = page.locator(
                    'a:has-text("Add a missing place"), li:has-text("Add a missing place")'
                ).first
                if await link.is_visible(timeout=3000):
                    await human_click(page, link)
                    found_entry = True
                    await pause(2, 3)
        except Exception:
            pass

    # Attempt 3: right-click on map
    if not found_entry:
        try:
            await page.mouse.click(
                page.viewport_size["width"] // 2,
                page.viewport_size["height"] // 2,
                button="right"
            )
            await pause(1, 2)
            link = page.locator(
                'li:has-text("Add a missing place"), [data-value*="Add"]'
            ).first
            if await link.is_visible(timeout=3000):
                await human_click(page, link)
                found_entry = True
                await pause(2, 3)
        except Exception:
            pass

    if not found_entry:
        log.warning("Could not open 'Add a missing place' form automatically.")
        log.info("Manual step: open Maps → right-click the location → Add a missing place")
        log.info("Then run the script again after submitting manually.")
        return False

    # ── Fill in the form ───────────────────────────────────────────────────
    log.info("Filling in place details …")

    async def fill_field(labels: list, value: str) -> bool:
        if not value:
            return False
        for label in labels:
            try:
                field = page.locator(
                    f'input[aria-label*="{label}" i], input[placeholder*="{label}" i]'
                ).first
                if await field.is_visible(timeout=2000):
                    await human_click(page, field)
                    await field.fill("")
                    await type_text(page, field, value)
                    await pause(1, 2)
                    return True
            except Exception:
                continue
        return False

    await fill_field(["Name", "Place name"], pin.get("name", ""))
    await pause(1, 2)

    # Category — type and select first suggestion
    if pin.get("category"):
        cat_filled = await fill_field(["Category"], pin["category"])
        if cat_filled:
            try:
                suggestion = page.locator('[role="option"], [role="listbox"] li').first
                if await suggestion.is_visible(timeout=3000):
                    await human_click(page, suggestion)
                    await pause(1, 2)
            except Exception:
                pass

    await fill_field(["Address", "Street address"], pin.get("address", ""))
    await fill_field(["Phone", "Phone number"],     pin.get("phone", ""))
    await fill_field(["Website"],                   pin.get("website", ""))

    await reading_pause()

    # ── Submit ─────────────────────────────────────────────────────────────
    submit_btn = page.locator(
        'button:has-text("Submit"), button:has-text("Send"), button[aria-label*="Submit" i]'
    ).first

    if await submit_btn.is_visible(timeout=5000):
        log.info("Submitting …")
        await human_click(page, submit_btn)
        await pause(4, 6)

        state["pin_submitted"]   = True
        state["pin_submit_date"] = str(date.today())
        _save_state(account["id"], state)

        log.info(f"Pin submitted for: {pin['name']}")
        log.info("Google typically takes 1-7 days to review a new place submission.")
        log.info("Once approved, open the listing in Maps and add:")
        log.info("  - Opening hours (via 'Edit this place')")
        log.info("  - Photos (via 'Add photos')")
        return True
    else:
        log.warning("Submit button not found — form may not have loaded correctly.")
        log.info("Check the browser to see if a verification step appeared.")
        return False


# ── Session runner ─────────────────────────────────────────────────────────────

async def run_session(account: dict, dry_run: bool = False) -> None:
    account_id = account["id"]
    log        = get_logger(account_id)
    state      = _load_state(account_id)
    weeks      = _weeks_elapsed(account)
    week_cfg   = _week_config(weeks)

    if state.get("pin_submitted"):
        log.info("Pin already submitted — no further warming needed for this account.")
        return

    log.info(
        f"Week {weeks + 1} — phase: {week_cfg['label']} — account: {account_id}"
    )

    activities = _choose_activities(week_cfg)
    log.info(f"Planned activities: {activities}")

    if dry_run:
        readiness = check_readiness(account, state)
        log.info(f"[DRY RUN] Readiness: {readiness['score']} — {activities}")
        return

    geo_account = account.copy()
    geo_account.setdefault("location", account.get("target_area", {}).get("name", ""))

    async with ProfileSession(
        profile_id = account["multilogin_profile_id"],
        folder_id  = account.get("multilogin_folder_id"),
        account_id = account_id,
        timeout    = 30,
        account    = geo_account,
    ) as page:
        for name in activities:
            log.info(f"Running: {name}")
            try:
                if name == "area_browse":
                    await activity_area_browse(page, account, log)
                elif name == "review_local":
                    await activity_review_local(page, account, state, log)
                elif name == "suggest_edit":
                    await activity_suggest_edit(page, account, state, log)
                elif name == "search":
                    await activity_search(page, account, log)
                elif name == "email_read":
                    await activity_email_read(page, account, log)
            except Exception as e:
                log.error(f"Activity '{name}' failed: {e}")

            if name != activities[-1]:
                await asyncio.sleep(random.uniform(12, 40))

        _save_state(account_id, state)

        cooldown = random.uniform(30, 90)
        log.info(f"Session cooldown: {cooldown:.0f}s")
        await asyncio.sleep(cooldown)


async def run_add_pin_session(account: dict) -> None:
    account_id = account["id"]
    log        = get_logger(account_id)
    state      = _load_state(account_id)

    readiness = check_readiness(account, state)
    if not readiness["ready"]:
        print(f"\n  Account {account_id} is not ready yet ({readiness['score']}).")
        print("  Run --check to see what's missing.\n")
        return

    async with ProfileSession(
        profile_id = account["multilogin_profile_id"],
        folder_id  = account.get("multilogin_folder_id"),
        account_id = account_id,
        timeout    = 30,
        account    = account,
    ) as page:
        await activity_add_pin(page, account, state, log)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Pin Warmer — warm accounts for Maps pin addition")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--all",      action="store_true", help="Run warming sessions for all accounts")
    group.add_argument("--account",  type=str,            help="Run one account by ID")
    group.add_argument("--check",    action="store_true", help="Check readiness for all accounts")
    group.add_argument("--add-pin",  type=str, metavar="ACCOUNT_ID",
                       help="Attempt pin addition for a specific account (must be ready)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no browser actions")
    args = parser.parse_args()

    accounts = _load_config()

    if args.check:
        print_readiness_report(accounts)
        return

    if args.add_pin:
        account = next((a for a in accounts if a["id"] == args.add_pin), None)
        if not account:
            print(f"  Account not found: {args.add_pin}")
            sys.exit(1)
        asyncio.run(run_add_pin_session(account))
        return

    if args.account:
        account = next((a for a in accounts if a["id"] == args.account), None)
        if not account:
            print(f"  Account not found: {args.account}")
            sys.exit(1)
        asyncio.run(run_session(account, dry_run=args.dry_run))
        return

    if args.all:
        for i, account in enumerate(accounts):
            asyncio.run(run_session(account, dry_run=args.dry_run))
            if i < len(accounts) - 1:
                gap = random.randint(20, 60)
                print(f"  Waiting {gap}s before next account …")
                if not args.dry_run:
                    import time
                    time.sleep(gap)
        return

    print(__doc__)


if __name__ == "__main__":
    main()
