"""
Google Calendar Activity

Three modes:

  setup (week 1, runs once per account):
    Creates a realistic personal calendar — recurring events (gym, weekly shop),
    one-off appointments (dentist, haircut), a few birthdays spread across
    the year. Turns an empty calendar into one that looks like a real person's.

  browse (weeks 3+, ongoing):
    Opens the calendar, navigates through the week/month view, clicks on
    events, scrolls around. Simulates a real person checking their schedule.

  add_event (weeks 3+, occasional):
    Adds a single new event — an appointment, a social plan, a reminder.
    Keeps the calendar growing naturally over time.
"""

import random
import json
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from playwright.async_api import Page

from activities.base_activity import BaseActivity
from core.orchestrator import STATE_DIR

CALENDAR_DATA_FILE = Path(__file__).parent.parent / "data" / "calendar_data.json"
CALENDAR_URL       = "https://calendar.google.com"


class CalendarActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict,
                 mode: str = "browse"):
        """
        mode: "setup" | "browse" | "add_event"
        """
        super().__init__(page, account, behaviour_cfg)
        with open(CALENDAR_DATA_FILE, encoding="utf-8") as f:
            self._data = json.load(f)
        self._mode       = mode
        self._state_file = STATE_DIR / f"{account['id']}_calendar.json"
        self._state      = self._load_state()

    async def run(self) -> bool:
        self.log.info(f"Calendar session | mode={self._mode}")

        # If setup already done, fall back to browse
        if self._mode == "setup" and self._state.get("setup_complete"):
            self.log.info("Calendar setup already complete — switching to browse")
            self._mode = "browse"

        try:
            await self.page.goto(CALENDAR_URL, wait_until="domcontentloaded", timeout=20000)
            await self.humaniser.pause(2000, 4000)
            await self._dismiss_consent()
            await self.humaniser.pause(1000, 2500)

            if self._mode == "setup":
                await self._run_setup()
            elif self._mode == "add_event":
                await self._add_single_event()
            else:
                await self._browse_calendar()

            self._save_state()
            return True

        except Exception as e:
            self.log.error(f"Calendar session failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Setup — runs once, creates the full initial calendar
    # ------------------------------------------------------------------

    async def _run_setup(self) -> None:
        """
        Create the initial calendar — spread across multiple sessions (2-3 events each)
        rather than a burst. Real users build up their calendar gradually.
        State tracks a queue of pending events so each session picks up where the last left off.
        """
        # Build the pending queue on first call
        if "setup_queue" not in self._state:
            self._state["setup_queue"] = self._build_setup_queue()
            self._state["events_created"] = 0

        queue = self._state["setup_queue"]
        if not queue:
            self.log.info("Calendar setup already complete — switching to browse")
            self._state["setup_complete"] = True
            await self._browse_calendar()
            return

        # Process a small batch this session (2-3 events)
        batch_size = random.randint(2, 3)
        batch      = queue[:batch_size]
        self._state["setup_queue"] = queue[batch_size:]

        self.log.info(
            f"Calendar setup: creating {len(batch)} events "
            f"({len(queue) - len(batch)} remaining after this session)"
        )

        events_created = 0
        for spec in batch:
            success = False
            if spec["kind"] == "recurring":
                target_date = date.today() + timedelta(days=spec["days_from_now"])
                success = await self._create_event(
                    title      = spec["title"],
                    event_date = target_date,
                    time       = spec["time"],
                    recurrence = spec.get("recurrence"),
                )
            elif spec["kind"] == "one_off":
                target_date = date.today() + timedelta(days=spec["days_offset"])
                success = await self._create_event(
                    title      = spec["title"],
                    event_date = target_date,
                    time       = spec["time"],
                )
            elif spec["kind"] == "birthday":
                target_date = date.today() + timedelta(days=spec["days_offset"])
                success = await self._create_event(
                    title      = spec["title"],
                    event_date = target_date,
                    all_day    = True,
                    recurrence = "yearly",
                )
            if success:
                events_created += 1
            await self.humaniser.pause(3000, 8000)

        self._state["events_created"] = self._state.get("events_created", 0) + events_created

        if not self._state["setup_queue"]:
            self.log.info(
                f"Calendar setup complete — "
                f"{self._state['events_created']} total events created over multiple sessions"
            )
            self._state["setup_complete"] = True
        else:
            self.log.info(
                f"Calendar setup batch done ({events_created} events). "
                f"{len(self._state['setup_queue'])} events remain for future sessions."
            )

    def _build_setup_queue(self) -> list:
        """Build a randomised queue of all setup events to be created over time."""
        queue = []

        for event in self._data["setup_events"]["recurring"]:
            queue.append({
                "kind":         "recurring",
                "title":        event["title"],
                "days_from_now": event["days_from_now"],
                "time":         event["time"],
                "recurrence":   event.get("recurrence"),
            })

        for event in self._data["setup_events"]["one_off"]:
            days_range  = event["days_from_now"]
            days_offset = random.randint(days_range[0], days_range[1])
            queue.append({
                "kind":        "one_off",
                "title":       event["title"],
                "days_offset": days_offset,
                "time":        random.choice(event["time_range"]),
            })

        num_birthdays = random.randint(2, 3)
        birthdays = random.sample(self._data["setup_events"]["birthdays"], k=num_birthdays)
        for birthday in birthdays:
            queue.append({
                "kind":        "birthday",
                "title":       birthday,
                "days_offset": random.randint(10, 340),
            })

        # Shuffle so each session gets a mix of event types
        random.shuffle(queue)
        return queue

    # ------------------------------------------------------------------
    # Browse — navigates the calendar naturally
    # ------------------------------------------------------------------

    async def _browse_calendar(self) -> None:
        self.log.info("Browsing calendar")

        # Start on the week view
        await self._switch_view(random.choice(["week", "month"]))
        await self.humaniser.pause(1500, 3000)

        # Browse for a while
        browse_actions = random.randint(3, 6)
        for _ in range(browse_actions):
            action = random.choices(
                ["navigate_forward", "navigate_back", "click_event", "switch_view", "go_today"],
                weights=[0.35, 0.20, 0.25, 0.10, 0.10],
                k=1
            )[0]

            if action == "navigate_forward":
                await self._navigate_calendar("next")
            elif action == "navigate_back":
                await self._navigate_calendar("back")
            elif action == "click_event":
                await self._click_random_event()
            elif action == "switch_view":
                await self._switch_view(random.choice(["week", "month", "day"]))
            elif action == "go_today":
                await self._go_to_today()

            await self.humaniser.pause(2000, 5000)
            await self.humaniser.reading_pause()

    # ------------------------------------------------------------------
    # Add a single event — ongoing activity
    # ------------------------------------------------------------------

    async def _add_single_event(self) -> None:
        templates = self._data["ongoing_events"]
        template  = random.choice(templates)

        # Build the title (fill in {name}/{bill} placeholders if present)
        title = template["title"]
        if "{name}" in title:
            name  = random.choice(template.get("names", ["a friend"]))
            title = title.replace("{name}", name)

        # Pick date and time
        days_ahead  = template.get("days_ahead", [1, 14])
        days_offset = random.randint(days_ahead[0], days_ahead[1])
        target_date = date.today() + timedelta(days=days_offset)
        event_time  = random.choice(template["time_range"])

        self.log.info(f"Adding event: {title!r} on {target_date} at {event_time}")

        success = await self._create_event(
            title      = title,
            event_date = target_date,
            time       = event_time,
        )
        if success:
            self._state["events_added"] = self._state.get("events_added", 0) + 1

    # ------------------------------------------------------------------
    # Core event creation
    # ------------------------------------------------------------------

    async def _create_event(
        self,
        title: str,
        event_date: date,
        time: str = None,
        all_day: bool = False,
        recurrence: str = None,
    ) -> bool:
        """
        Create a Google Calendar event.
        recurrence: None | "weekly" | "yearly"
        """
        try:
            # Navigate to the target date by clicking on it
            # Easiest approach: use the URL to land on the right date
            date_str   = event_date.strftime("%Y%m%d")
            view_url   = f"{CALENDAR_URL}/r/week/{event_date.year}/{event_date.month}/{event_date.day}"
            await self.page.goto(view_url, wait_until="domcontentloaded", timeout=15000)
            await self.humaniser.pause(1500, 3000)

            # Click the + Create button
            create_btn = self.page.locator(
                'button[aria-label="Create"], a[aria-label="Create new event"],'
                ' div[data-view="md"] button, button:has-text("Create")'
            ).first
            if not await create_btn.is_visible(timeout=5000):
                self.log.debug("Create button not found")
                return False

            await create_btn.click()
            await self.humaniser.pause(1000, 2500)

            # If a quick-create popup appeared, click "More options" for the full form
            more_options = self.page.locator(
                'button:has-text("More options"), a:has-text("More options")'
            ).first
            if await more_options.is_visible(timeout=2000):
                await more_options.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1500, 3000)

            # --- Title ---
            title_field = self.page.locator(
                'input[data-eventchip-title], input[placeholder*="title" i],'
                ' input[aria-label*="title" i], input.YPqjbf'
            ).first
            if await title_field.is_visible(timeout=5000):
                await title_field.click()
                await self.humaniser.pause(300, 700)
                for char in title:
                    delay = max(50, min(280, int(random.gauss(105, 38))))
                    await title_field.type(char, delay=delay)
            await self.humaniser.pause(800, 1800)

            # --- Date ---
            await self._set_event_date(event_date)
            await self.humaniser.pause(600, 1500)

            # --- Time ---
            if not all_day and time:
                await self._set_event_time(time)
                await self.humaniser.pause(600, 1500)

            # --- All day toggle ---
            if all_day:
                all_day_cb = self.page.locator(
                    'input[type="checkbox"][aria-label*="all day" i],'
                    ' label:has-text("All day") input'
                ).first
                if await all_day_cb.is_visible(timeout=2000):
                    if not await all_day_cb.is_checked():
                        await all_day_cb.click()
                        await self.humaniser.pause(400, 900)

            # --- Recurrence ---
            if recurrence:
                await self._set_recurrence(recurrence)
                await self.humaniser.pause(600, 1500)

            # --- Save ---
            await self.humaniser.pause(2000, 5000)
            save_btn = self.page.locator(
                'button[aria-label="Save"], button:has-text("Save")'
            ).first
            if await save_btn.is_visible(timeout=5000):
                await save_btn.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1500, 3000)
                self.log.info(f"Event created: {title!r} on {event_date}")
                return True
            else:
                self.log.warning(f"Save button not found for event: {title!r}")
                await self.page.keyboard.press("Escape")
                return False

        except Exception as e:
            self.log.debug(f"Event creation failed for {title!r}: {e}")
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    async def _set_event_date(self, event_date: date) -> None:
        """Fill in the event date field."""
        try:
            date_field = self.page.locator(
                'input[aria-label*="date" i][type="text"],'
                ' input[data-hvaId*="start"], input[id*="startDateInput"]'
            ).first
            if await date_field.is_visible(timeout=3000):
                await date_field.click()
                await date_field.triple_click()
                await self.humaniser.pause(200, 500)
                formatted = event_date.strftime("%d/%m/%Y")
                for char in formatted:
                    delay = max(50, min(200, int(random.gauss(90, 30))))
                    await date_field.type(char, delay=delay)
                await self.page.keyboard.press("Enter")
        except Exception as e:
            self.log.debug(f"Date set failed: {e}")

    async def _set_event_time(self, time_str: str) -> None:
        """Fill in the event start time field."""
        try:
            time_field = self.page.locator(
                'input[aria-label*="time" i][type="text"],'
                ' input[data-hvaId*="startTime"], input[id*="startTimeInput"]'
            ).first
            if await time_field.is_visible(timeout=3000):
                await time_field.click()
                await time_field.triple_click()
                await self.humaniser.pause(200, 500)
                # Convert 24h to 12h AM/PM format Google Calendar uses
                h, m = map(int, time_str.split(":"))
                ampm   = "AM" if h < 12 else "PM"
                h12    = h % 12 or 12
                display = f"{h12}:{m:02d} {ampm}"
                for char in display:
                    delay = max(50, min(200, int(random.gauss(90, 30))))
                    await time_field.type(char, delay=delay)
                await self.page.keyboard.press("Enter")
        except Exception as e:
            self.log.debug(f"Time set failed: {e}")

    async def _set_recurrence(self, recurrence: str) -> None:
        """Set event recurrence (weekly / yearly)."""
        try:
            recurrence_btn = self.page.locator(
                'button[aria-label*"repeat" i], div[data-view*="recurrence"],'
                ' button:has-text("Does not repeat")'
            ).first
            if not await recurrence_btn.is_visible(timeout=3000):
                return

            await recurrence_btn.click()
            await self.humaniser.pause(800, 1800)

            if recurrence == "weekly":
                option = self.page.locator(
                    'li:has-text("Every week"), [data-value="weekly"]'
                ).first
            elif recurrence == "yearly":
                option = self.page.locator(
                    'li:has-text("Every year"), [data-value="yearly"]'
                ).first
            else:
                return

            if await option.is_visible(timeout=2000):
                await option.click()
                await self.humaniser.pause(500, 1200)
        except Exception as e:
            self.log.debug(f"Recurrence set failed: {e}")

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    async def _switch_view(self, view: str) -> None:
        """Switch between day/week/month views."""
        try:
            btn = self.page.locator(
                f'button[aria-label="{view.capitalize()} view"],'
                f' button:has-text("{view.capitalize()}")'
            ).first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1000, 2500)
                self.log.debug(f"Switched to {view} view")
        except Exception as e:
            self.log.debug(f"View switch failed: {e}")

    async def _navigate_calendar(self, direction: str) -> None:
        """Click the next/back navigation buttons."""
        try:
            label = "Next" if direction == "next" else "Previous"
            btn = self.page.locator(
                f'button[aria-label="{label} period"],'
                f' button[aria-label="{label}"]'
            ).first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1000, 3000)
        except Exception as e:
            self.log.debug(f"Navigation failed: {e}")

    async def _click_random_event(self) -> None:
        """Click on a random visible event to view its details."""
        try:
            events = self.page.locator(
                'div[data-eventchip], div[role="button"][data-eventid],'
                ' a[data-eventid]'
            )
            count = await events.count()
            if count == 0:
                return

            idx = random.randint(0, min(count - 1, 8))
            await events.nth(idx).click()
            await self.humaniser.pause(2000, 5000)

            # Close the event popup
            close_btn = self.page.locator(
                'button[aria-label="Close"], button[aria-label="close"]'
            ).first
            if await close_btn.is_visible(timeout=2000):
                await close_btn.click()
                await self.humaniser.pause(500, 1200)

            self.log.debug("Clicked and closed an event")
        except Exception as e:
            self.log.debug(f"Event click failed: {e}")

    async def _go_to_today(self) -> None:
        try:
            today_btn = self.page.locator(
                'button[aria-label="Today"], button:has-text("Today")'
            ).first
            if await today_btn.is_visible(timeout=3000):
                await today_btn.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1000, 2500)
        except Exception as e:
            self.log.debug(f"Go to today failed: {e}")

    # ------------------------------------------------------------------
    # Consent dialog
    # ------------------------------------------------------------------

    async def _dismiss_consent(self) -> None:
        for selector in [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Got it")',
        ]:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self.humaniser.pause(500, 1000)
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
        return {}

    def _save_state(self) -> None:
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)
