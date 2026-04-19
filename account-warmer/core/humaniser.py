"""
Humaniser — timing, typing, scrolling, and mouse movement simulation.
This is the anti-detection layer. All delays use statistical distributions
rather than fixed values to avoid creating detectable timing patterns.
"""

import asyncio
import random
import math
import numpy as np
from playwright.async_api import Page


class Humaniser:
    def __init__(self, behaviour_cfg: dict):
        t = behaviour_cfg.get("timing", {})
        self.action_mean    = t.get("action_delay_ms", {}).get("mean", 2200)
        self.action_std     = t.get("action_delay_ms", {}).get("std_dev", 800)
        self.action_min     = t.get("action_delay_ms", {}).get("min", 600)
        self.action_max     = t.get("action_delay_ms", {}).get("max", 8000)

        ty = t.get("typing_delay_ms", {})
        self.type_mean      = ty.get("mean", 110)
        self.type_std       = ty.get("std_dev", 45)
        self.type_min       = ty.get("min", 40)
        self.type_max       = ty.get("max", 350)

        self.read_pause_prob   = t.get("reading_pause_probability", 0.20)
        self.read_pause_range  = t.get("reading_pause_seconds", [15, 75])
        self.dist_pause_prob   = t.get("distraction_pause_probability", 0.04)
        self.dist_pause_range  = t.get("distraction_pause_seconds", [60, 240])
        self.typo_prob         = t.get("typo_probability", 0.06)
        self.scroll_back_prob  = t.get("scroll_backtrack_probability", 0.18)

    # ------------------------------------------------------------------
    # Delays
    # ------------------------------------------------------------------

    async def pause(self, min_ms: float = None, max_ms: float = None) -> None:
        """Short pause between UI actions using a log-normal distribution."""
        mn = min_ms if min_ms is not None else self.action_min
        mx = max_ms if max_ms is not None else self.action_max
        raw = np.random.lognormal(mean=math.log(self.action_mean), sigma=0.35)
        delay_ms = float(np.clip(raw, mn, mx))
        await asyncio.sleep(delay_ms / 1000)

    async def reading_pause(self) -> None:
        """A longer pause simulating reading a page."""
        if random.random() < self.read_pause_prob:
            secs = random.uniform(*self.read_pause_range)
            await asyncio.sleep(secs)

    async def maybe_distracted(self) -> None:
        """Occasionally simulate the user putting the device down briefly."""
        if random.random() < self.dist_pause_prob:
            secs = random.uniform(*self.dist_pause_range)
            await asyncio.sleep(secs)

    async def idle_cursor(self, page: Page) -> None:
        """
        Leave the cursor idle for a moment — real users rest the mouse
        between thoughts rather than always having it in motion.
        20% chance of triggering on any given call.
        """
        if random.random() < 0.20:
            await asyncio.sleep(random.uniform(1.0, 3.5))

    async def maybe_shortcut(self, page: Page) -> None:
        """
        Occasionally fire a harmless keyboard shortcut — real users use these
        reflexively without thinking (Ctrl+F to search for text, Ctrl+L to
        glance at the URL, Ctrl+Home to scroll back to the top).
        Triggers ~8% of the time when called.
        """
        if random.random() > 0.08:
            return
        shortcut = random.choices(
            ["find", "focus_address", "scroll_top"],
            weights=[0.50, 0.30, 0.20],
            k=1
        )[0]
        try:
            if shortcut == "find":
                # Open find-in-page, pause as if searching for something, then close
                await page.keyboard.press("Control+f")
                await asyncio.sleep(random.uniform(1.0, 3.5))
                await page.keyboard.press("Escape")
            elif shortcut == "focus_address":
                # Focus address bar — user reconsidered navigating somewhere
                await page.keyboard.press("Control+l")
                await asyncio.sleep(random.uniform(0.5, 2.0))
                await page.keyboard.press("Escape")
            elif shortcut == "scroll_top":
                # Jump back to the top of the page
                await page.keyboard.press("Control+Home")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Typing
    # ------------------------------------------------------------------

    async def type_text(self, page: Page, selector: str, text: str) -> None:
        """Type text character by character with human timing and occasional typos."""
        element = page.locator(selector).first
        await element.click()
        # Use element.press() (not page.keyboard) so Ctrl+A is scoped to this
        # element and cannot accidentally select all text on the page.
        await element.press("Control+a")
        await self.pause(100, 300)
        # Belt-and-suspenders: fill with empty string to guarantee the field is
        # cleared before we start typing, regardless of selection state.
        await element.fill("")
        await self.pause(100, 300)

        i = 0
        while i < len(text):
            char = text[i]

            # Occasional typo: type wrong char then backspace
            if random.random() < self.typo_prob and char.isalpha():
                wrong = random.choice("qwertyuiopasdfghjklzxcvbnm")
                delay = max(self.type_min, min(self.type_max,
                            int(np.random.normal(self.type_mean, self.type_std))))
                await element.type(wrong, delay=delay)
                # Longer pause after a typo — real humans notice and pause
                await asyncio.sleep(random.uniform(0.3, 1.0))
                await page.keyboard.press("Backspace")
                await asyncio.sleep(random.uniform(0.1, 0.3))

            delay = max(self.type_min, min(self.type_max,
                        int(np.random.normal(self.type_mean, self.type_std))))
            await element.type(char, delay=delay)

            # Occasional brief pause mid-word (thinking)
            if random.random() < 0.04:
                await asyncio.sleep(random.uniform(0.3, 1.0))

            i += 1

    # ------------------------------------------------------------------
    # Mouse movement
    # ------------------------------------------------------------------

    async def move_to(self, page: Page, x: float, y: float) -> None:
        """Move mouse to target along a curved path (Bezier approximation)."""
        current = await page.evaluate("() => ({ x: window.mouseX || 0, y: window.mouseY || 0 })")
        cx, cy = current.get("x", 0), current.get("y", 0)

        # Control points for a quadratic Bezier curve
        ctrl_x = (cx + x) / 2 + random.uniform(-80, 80)
        ctrl_y = (cy + y) / 2 + random.uniform(-80, 80)

        steps = random.randint(12, 25)
        for step in range(steps + 1):
            t = step / steps
            bx = (1 - t) ** 2 * cx + 2 * (1 - t) * t * ctrl_x + t ** 2 * x
            by = (1 - t) ** 2 * cy + 2 * (1 - t) * t * ctrl_y + t ** 2 * y
            bx += random.uniform(-1.5, 1.5)
            by += random.uniform(-1.5, 1.5)
            await page.mouse.move(bx, by)
            await asyncio.sleep(random.uniform(0.008, 0.025))

    async def human_click(self, page: Page, selector: str) -> None:
        """Move to an element and click it in a human-like way."""
        element = page.locator(selector).first
        box = await element.bounding_box()
        if box:
            tx = box["x"] + box["width"]  * random.uniform(0.3, 0.7)
            ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await self.move_to(page, tx, ty)
            await asyncio.sleep(random.uniform(0.05, 0.15))
        await element.click()

    async def hover_then_click(self, page: Page, selector: str,
                                hover_ms: tuple = (800, 2500)) -> None:
        """
        Move to element, dwell (hover) for a human-like period, then click.
        Use this instead of human_click when hover telemetry matters
        (e.g. search results, navigation links).
        """
        element = page.locator(selector).first
        box = await element.bounding_box()
        if box:
            tx = box["x"] + box["width"]  * random.uniform(0.2, 0.8)
            ty = box["y"] + box["height"] * random.uniform(0.2, 0.8)
            await self.move_to(page, tx, ty)
            # Dwell — simulate reading the URL / snippet before deciding to click
            await asyncio.sleep(random.uniform(hover_ms[0], hover_ms[1]) / 1000)
            # Small micro-movement while hovering (hand tremor)
            for _ in range(random.randint(1, 3)):
                await page.mouse.move(
                    tx + random.uniform(-3, 3),
                    ty + random.uniform(-3, 3),
                )
                await asyncio.sleep(random.uniform(0.05, 0.15))
        await element.click()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate(self, page: Page, url: str,
                       wait_until: str = "domcontentloaded") -> None:
        """
        Navigate to a URL, sometimes via the address bar (typing) and sometimes
        programmatically — matching real user behaviour.

        40% of the time: focus the address bar with Ctrl+L, select all,
        type the domain with human-like keystroke timing, press Enter.
        This generates keyboard events on the omnibar which automated
        page.goto() never produces.

        60% of the time: use programmatic goto (faster, still realistic for
        users who click bookmarks or history items).
        """
        # Note: page.keyboard events target the active *page* element via CDP,
        # not the browser's address bar (which lives in the browser chrome).
        # Typing "google.com" via page.keyboard would land in whatever page
        # element has focus — often the search box — causing visible corruption.
        # We simulate the human timing of address-bar navigation with a pause,
        # then use page.goto() for the actual navigation.
        if random.random() < 0.40:
            # Simulate the user clicking the address bar and typing the URL
            await asyncio.sleep(random.uniform(0.4, 0.9))
            # Brief delay mimicking keystroke-by-keystroke entry of the domain
            display = url.replace("https://", "").replace("http://", "")
            await asyncio.sleep(len(display) * random.uniform(0.06, 0.12))

        await page.goto(url, wait_until=wait_until)

    # ------------------------------------------------------------------
    # Scrolling
    # ------------------------------------------------------------------

    async def human_scroll(self, page: Page, max_fraction: float = 0.75) -> None:
        """
        Physics-based scrolling that mimics real human patterns:

        - Burst mode: 2-5 quick wheel ticks followed by a reading pause
        - Slow mode: single ticks with longer pauses (reading each section)
        - Backtrack: occasional scroll-up to re-read something
        - Mid-scroll idle: cursor stops moving while eyes scan

        Real scroll telemetry has high variance in velocity and frequent
        direction changes. Uniform step+pause patterns are a bot signature.
        """
        try:
            height = await page.evaluate("document.body.scrollHeight")
        except Exception:
            return

        target  = height * random.uniform(0.35, max_fraction)
        current = 0.0

        while current < target:
            # Decide: burst scroll or slow read-scroll
            if random.random() < 0.35:
                # ── Burst: fast scroll through non-interesting content ──────
                ticks = random.randint(2, 5)
                for _ in range(ticks):
                    step = random.randint(220, 680)
                    await page.mouse.wheel(0, step)
                    current += step
                    await asyncio.sleep(random.uniform(0.04, 0.14))
                # Pause after burst — eyes catching up
                await asyncio.sleep(random.uniform(0.6, 2.2))

            else:
                # ── Slow: reading this section ──────────────────────────────
                step = random.randint(60, 280)
                await page.mouse.wheel(0, step)
                current += step
                await asyncio.sleep(random.uniform(0.25, 1.1))

            # Backtrack — re-reading something above
            if random.random() < self.scroll_back_prob:
                back = random.randint(50, 220)
                await page.mouse.wheel(0, -back)
                current -= back
                await asyncio.sleep(random.uniform(0.4, 1.2))

            # Cursor idle — eyes scanning, hand at rest
            await self.idle_cursor(page)

            # Distraction pause (very rare — phone ping, etc.)
            await self.maybe_distracted()
