"""
Profile Ager

Runs pre-account-creation browsing sessions on Multilogin profiles to build
cookie history before you create a Google account in them.

A fresh Multilogin profile looks like a brand new device with zero history.
Google responds to this with aggressive verification (QR codes, extra SMS steps,
phone-based sign-in prompts).

Aging the profile first builds:
  - Google.com search cookies and preferences
  - YouTube browsing cookies
  - Google Maps location cookies
  - General web browsing history across non-Google sites

After 5-7 days of daily sessions the profile looks like an existing device
rather than something that appeared purely to create an account. Verification
requirements drop noticeably — typically just a standard SMS confirmation.

Workflow:
  1. Add your Multilogin profile IDs to config/profile_ager.yaml
  2. Run daily:  python profile_ager.py --all
  3. Check:      python profile_ager.py --check
  4. When a profile shows READY, create the Google account inside that profile
  5. Move the profile to accounts.yaml and start the main warmer

Run a single profile:
  python profile_ager.py --profile profile_001

Preview without browser:
  python profile_ager.py --all --dry-run
"""

import argparse
import asyncio
import json
import random
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

from core.logger    import get_logger
from core.humaniser import (pause, type_text, human_click,
                            human_scroll, reading_pause, maybe_distracted)
from core.profile_manager import ProfileSession

# ── Paths ──────────────────────────────────────────────────────────────────────

from core.paths import PROFILE_AGER_FILE as CONFIG_FILE, STATE_DIR

# ── Readiness thresholds ───────────────────────────────────────────────────────

DEFAULT_TARGET_DAYS     = 7   # Days to age before creating account
DEFAULT_MIN_SESSIONS    = 5   # Minimum sessions (roughly one per day)

# ── Content ────────────────────────────────────────────────────────────────────

SEARCH_TERMS = [
    # General curiosity
    "how to make pasta carbonara", "best budget smartphones 2026",
    "mortgage rates uk", "how to change a tyre",
    "symptoms of flu", "chicken curry recipe easy",
    "things to do in {city}", "weather {city} this week",
    # Shopping / lifestyle
    "cheap flights europe", "sofa sale uk", "running shoes for beginners",
    "birthday gift ideas", "amazon deals today",
    # News / current
    "uk news today", "football results", "latest world news",
    "stock market today", "petrol prices uk",
    # Local intent
    "best restaurants near me", "takeaway near me",
    "plumber near me", "dentist near me",
    "cinema near me", "supermarket opening times",
    # Informational
    "how to start a small business uk", "train times london",
    "driving test tips uk", "best vpn 2026",
    "how to fix a leaking tap", "home insurance comparison",
]

YOUTUBE_SEARCHES = [
    "cooking tutorial beginner", "travel vlog",
    "funny fails compilation", "documentary nature",
    "music playlist chill", "sports highlights",
    "how to change oil car", "comedy sketch",
    "home renovation tips", "news today",
]

# Non-Google sites — makes the profile look like general internet usage
GENERAL_SITES = [
    ("https://www.bbc.co.uk/news",          "BBC News"),
    ("https://www.theguardian.com",          "The Guardian"),
    ("https://www.reddit.com",               "Reddit"),
    ("https://en.wikipedia.org/wiki/Main_Page", "Wikipedia"),
    ("https://www.amazon.co.uk",             "Amazon"),
    ("https://www.rightmove.co.uk",          "Rightmove"),
    ("https://www.autotrader.co.uk",         "AutoTrader"),
    ("https://www.tripadvisor.co.uk",        "TripAdvisor"),
    ("https://www.met.gov.uk",               "Met Office"),
    ("https://www.imdb.com",                 "IMDb"),
]


# ── Config / state ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"\n  Config not found: {CONFIG_FILE}")
        print("  Create it using config/profile_ager.yaml as a template.\n")
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_state(profile_id: str) -> dict:
    path = STATE_DIR / f"ager_{profile_id}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "sessions_completed": 0,
        "first_session_date": None,
        "last_session_date":  None,
        "account_created":    False,
    }


def _save_state(profile_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / f"ager_{profile_id}.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _days_aged(state: dict) -> int:
    first = state.get("first_session_date")
    if not first:
        return 0
    return (date.today() - date.fromisoformat(first)).days


def _already_run_today(state: dict) -> bool:
    return state.get("last_session_date") == str(date.today())


# ── Readiness ──────────────────────────────────────────────────────────────────

def check_readiness(profile: dict, state: dict, settings: dict) -> dict:
    target_days  = settings.get("target_age_days",  DEFAULT_TARGET_DAYS)
    min_sessions = settings.get("min_sessions",     DEFAULT_MIN_SESSIONS)

    days     = _days_aged(state)
    sessions = state.get("sessions_completed", 0)
    done     = state.get("account_created", False)

    checks = {
        f"Days aged ({target_days} needed)":    (days >= target_days,     f"{days}/{target_days}"),
        f"Sessions ({min_sessions} needed)":    (sessions >= min_sessions, f"{sessions}/{min_sessions}"),
    }

    passed = sum(1 for ok, _ in checks.values() if ok)
    ready  = (passed == len(checks)) and not done

    return {
        "ready":    ready,
        "score":    f"{passed}/{len(checks)}",
        "checks":   checks,
        "done":     done,
        "days":     days,
        "sessions": sessions,
    }


def print_readiness_report(profiles: list, settings: dict) -> None:
    print()
    print("  Profile Ager — Readiness Report")
    print("  " + "─" * 50)
    for profile in profiles:
        state = _load_state(profile["id"])
        r     = check_readiness(profile, state, settings)

        if r["done"]:
            status = "DONE — account created"
        elif r["ready"]:
            status = "READY — create account now"
        else:
            status = f"Aging  ({r['score']} checks passed)"

        print(f"\n  {profile['id']}  —  {status}")
        for label, (ok, detail) in r["checks"].items():
            tick = "✓" if ok else "✗"
            print(f"    {tick}  {label}: {detail}")

        last = state.get("last_session_date", "never")
        print(f"    Last session: {last}")

        if r["ready"]:
            pid = profile["id"]
            print(f"\n  → Open the Multilogin profile and go to accounts.google.com/signup")
            print(f"  → After creating the account, mark it done:")
            print(f"     python profile_ager.py --mark-done {pid}")

    print()


# ── Consent helper ─────────────────────────────────────────────────────────────

async def _dismiss_consent(page) -> None:
    for selector in [
        'button:has-text("Accept all")',
        'button:has-text("Reject all")',
        'button:has-text("Accept")',
        '[aria-label="Accept all"]',
        'button:has-text("I agree")',
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1500):
                await human_click(page, btn)
                await pause(1, 2)
                return
        except Exception:
            pass


# ── Activities ─────────────────────────────────────────────────────────────────

async def activity_google_search(page, location: str, log) -> bool:
    """
    Perform 2-4 Google searches, clicking into at least one result.
    This is the most important activity — builds Google.com cookie state.
    """
    log.info("Google search session")

    city = location.split(",")[0].strip().title() if location else "London"

    terms = random.sample(SEARCH_TERMS, k=random.randint(2, 4))
    terms = [t.replace("{city}", city) for t in terms]

    for i, term in enumerate(terms):
        try:
            await page.goto("https://www.google.com", wait_until="domcontentloaded")
            await _dismiss_consent(page)
            await reading_pause()

            search_box = page.locator('textarea[name="q"], input[name="q"]').first
            await human_click(page, search_box)
            await type_text(page, search_box, term)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle", timeout=12000)
            await reading_pause()

            # Click a result on the first or second search
            if i == 0 or random.random() < 0.6:
                results = page.locator('h3').all()
                results = await results
                if results:
                    target = results[random.randint(0, min(len(results) - 1, 4))]
                    try:
                        await human_click(page, target)
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        await reading_pause()
                        await human_scroll(page, distance=random.randint(300, 700))
                        await reading_pause()
                        await maybe_distracted()
                        await page.go_back()
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass

            await pause(5, 15)

        except Exception as e:
            log.warning(f"Search failed for '{term}': {e}")
            continue

    log.info(f"Google searches complete: {terms}")
    return True


async def activity_youtube_browse(page, log) -> bool:
    """
    Browse YouTube — homepage, search for something, watch part of a video.
    YouTube cookies are valuable Google property cookies.
    """
    log.info("YouTube browse")

    try:
        await page.goto("https://www.youtube.com", wait_until="domcontentloaded")
        await _dismiss_consent(page)
        await reading_pause()
        await human_scroll(page, distance=random.randint(300, 600))
        await reading_pause()

        # Click a video from homepage
        video_links = page.locator('a#video-title, a[href*="/watch"]')
        count = await video_links.count()
        if count > 0:
            idx = random.randint(0, min(count - 1, 8))
            try:
                await human_click(page, video_links.nth(idx))
                await page.wait_for_load_state("networkidle", timeout=12000)
                await reading_pause()

                # Watch for a natural-feeling duration
                watch_seconds = random.randint(35, 90)
                log.info(f"Watching video for {watch_seconds}s")
                await asyncio.sleep(watch_seconds)

                await page.go_back()
                await page.wait_for_load_state("networkidle", timeout=8000)
                await reading_pause()
            except Exception:
                pass

        # Optionally search for something
        if random.random() < 0.5:
            term = random.choice(YOUTUBE_SEARCHES)
            try:
                search_box = page.locator('input#search').first
                await human_click(page, search_box)
                await type_text(page, search_box, term)
                await page.keyboard.press("Enter")
                await page.wait_for_load_state("networkidle", timeout=10000)
                await reading_pause()
                await human_scroll(page, distance=200)
            except Exception:
                pass

    except Exception as e:
        log.warning(f"YouTube browse failed: {e}")
        return False

    log.info("YouTube browse complete")
    return True


async def activity_maps_browse(page, location: str, log) -> bool:
    """
    Browse Google Maps — search for local places, click a few listings.
    Builds Maps cookies and establishes location context.
    """
    log.info("Google Maps browse")

    city = location.split(",")[0].strip() if location else "London"
    term = random.choice(["restaurants", "coffee shops", "pubs", "shops", "parks"])

    try:
        await page.goto("https://www.google.com/maps", wait_until="domcontentloaded")
        await _dismiss_consent(page)
        await reading_pause()

        search_box = page.locator(
            'input#searchboxinput, input[aria-label="Search Google Maps"]'
        ).first
        await human_click(page, search_box)
        await type_text(page, search_box, f"{term} in {city}")
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("networkidle", timeout=12000)
        await reading_pause()

        # Click 1-3 results
        results = page.locator('[role="feed"] a[href*="/maps/place/"]')
        count   = min(await results.count(), random.randint(1, 3))
        for i in range(count):
            try:
                await human_click(page, results.nth(i))
                await page.wait_for_load_state("networkidle", timeout=8000)
                await reading_pause()
                await human_scroll(page, distance=random.randint(150, 300))
                await reading_pause()
                await page.go_back()
                await page.wait_for_load_state("networkidle", timeout=8000)
                await pause(3, 8)
            except Exception:
                continue

    except Exception as e:
        log.warning(f"Maps browse failed: {e}")
        return False

    log.info("Maps browse complete")
    return True


async def activity_general_browse(page, log) -> bool:
    """
    Visit 2-3 mainstream websites — news, shopping, reference.
    Makes the profile look like genuine general internet usage, not a
    Google-only bot.
    """
    log.info("General web browse")

    sites = random.sample(GENERAL_SITES, k=random.randint(2, 3))

    for url, label in sites:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await _dismiss_consent(page)
            await reading_pause()
            await human_scroll(page, distance=random.randint(400, 900))
            await reading_pause()
            await maybe_distracted()

            # Click an internal link occasionally
            if random.random() < 0.4:
                links = page.locator("a[href]")
                count = await links.count()
                if count > 5:
                    idx = random.randint(1, min(count - 1, 15))
                    try:
                        href = await links.nth(idx).get_attribute("href") or ""
                        if href.startswith("/") or url in href:
                            await human_click(page, links.nth(idx))
                            await page.wait_for_load_state("networkidle", timeout=10000)
                            await reading_pause()
                            await human_scroll(page, distance=random.randint(200, 500))
                    except Exception:
                        pass

            log.info(f"Visited: {label}")
            await pause(5, 15)

        except Exception as e:
            log.warning(f"Could not visit {label}: {e}")
            continue

    return True


# ── Session runner ─────────────────────────────────────────────────────────────

ACTIVITY_POOL = [
    ("google_search",   0.40),
    ("youtube_browse",  0.25),
    ("maps_browse",     0.20),
    ("general_browse",  0.15),
]


async def run_session(profile: dict, settings: dict, dry_run: bool = False) -> None:
    profile_id = profile["id"]
    location   = profile.get("location", "London")
    log        = get_logger(f"ager_{profile_id}")
    state      = _load_state(profile_id)

    if state.get("account_created"):
        log.info("Account already created — profile aging complete.")
        return

    if _already_run_today(state):
        log.info("Session already run today — skipping.")
        return

    days     = _days_aged(state)
    sessions = state.get("sessions_completed", 0)
    log.info(f"Profile {profile_id} — day {days + 1} of aging — session {sessions + 1}")

    # Pick 3-4 activities for this session
    activities = []
    names      = [a for a, _ in ACTIVITY_POOL]
    weights    = [w for _, w in ACTIVITY_POOL]
    picks      = random.choices(names, weights=weights, k=4)
    seen       = set()
    for p in picks:
        if p not in seen or p == "google_search":
            activities.append(p)
            seen.add(p)
    activities = activities[:random.randint(3, 4)]
    random.shuffle(activities)

    log.info(f"Activities: {activities}")

    if dry_run:
        log.info("[DRY RUN] — no browser launched")
        return

    async with ProfileSession(
        profile_id = profile["multilogin_profile_id"],
        folder_id  = profile.get("multilogin_folder_id"),
        account_id = f"ager_{profile_id}",
        timeout    = 30,
        account    = {"location": location},
    ) as page:
        for name in activities:
            log.info(f"Running: {name}")
            try:
                if name == "google_search":
                    await activity_google_search(page, location, log)
                elif name == "youtube_browse":
                    await activity_youtube_browse(page, log)
                elif name == "maps_browse":
                    await activity_maps_browse(page, location, log)
                elif name == "general_browse":
                    await activity_general_browse(page, log)
            except Exception as e:
                log.error(f"Activity '{name}' failed: {e}")

            if name != activities[-1]:
                await asyncio.sleep(random.uniform(15, 45))

        cooldown = random.uniform(20, 60)
        log.info(f"Cooldown: {cooldown:.0f}s")
        await asyncio.sleep(cooldown)

    # Update state
    today = str(date.today())
    if not state["first_session_date"]:
        state["first_session_date"] = today
    state["last_session_date"]   = today
    state["sessions_completed"]  = state.get("sessions_completed", 0) + 1
    _save_state(profile_id, state)

    r = check_readiness(profile, state, settings)
    if r["ready"]:
        log.info(
            f"Profile {profile_id} is now READY for account creation. "
            f"Run --check to see instructions."
        )
    else:
        days_left = max(0, settings.get("target_age_days", DEFAULT_TARGET_DAYS) - r["days"])
        log.info(f"Aging progress: {r['score']} — ~{days_left} day(s) remaining")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Profile Ager — pre-account-creation browser warming")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--all",       action="store_true", help="Run a session for every unfinished profile")
    group.add_argument("--profile",   type=str,            help="Run one profile by ID")
    group.add_argument("--check",     action="store_true", help="Show readiness report")
    group.add_argument("--mark-done", type=str, metavar="PROFILE_ID",
                       help="Mark a profile as having had its account created")
    parser.add_argument("--dry-run",  action="store_true", help="Preview only — no browser")
    args = parser.parse_args()

    cfg      = _load_config()
    profiles = cfg.get("profiles", [])
    settings = cfg.get("settings", {})

    if not profiles:
        print("\n  No profiles configured in config/profile_ager.yaml\n")
        sys.exit(1)

    if args.check:
        print_readiness_report(profiles, settings)
        return

    if args.mark_done:
        state = _load_state(args.mark_done)
        state["account_created"] = True
        _save_state(args.mark_done, state)
        print(f"\n  {args.mark_done} marked as done. Remove it from profile_ager.yaml")
        print(f"  and add to config/accounts.yaml to start warming.\n")
        return

    if args.profile:
        profile = next((p for p in profiles if p["id"] == args.profile), None)
        if not profile:
            print(f"  Profile not found: {args.profile}")
            sys.exit(1)
        asyncio.run(run_session(profile, settings, dry_run=args.dry_run))
        return

    if args.all:
        import time
        pending = [p for p in profiles if not _load_state(p["id"]).get("account_created")]
        print(f"\n  Running {len(pending)} profile(s)…\n")
        for i, profile in enumerate(pending):
            asyncio.run(run_session(profile, settings, dry_run=args.dry_run))
            if i < len(pending) - 1:
                gap = random.randint(20, 60)
                print(f"  Waiting {gap}s before next profile …")
                if not args.dry_run:
                    time.sleep(gap)
        return

    print(__doc__)


if __name__ == "__main__":
    main()
