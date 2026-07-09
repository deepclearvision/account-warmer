"""
Google Drive & Docs Activity

Four modes:

  setup (week 1, runs once):
    Creates an initial set of personal documents and one spreadsheet.
    Turns an empty Drive into one that looks used — shopping list, to-do,
    notes, and a monthly budget spreadsheet.

  browse (weeks 3+, ongoing):
    Opens Drive, navigates the file list, opens a random existing document,
    reads it by scrolling, then closes it. Pure engagement.

  edit_doc (weeks 3+, occasional):
    Opens an existing document created during setup and makes a small realistic
    edit — adds a list item, appends a note, ticks something off.

  create_doc (weeks 5+, occasional):
    Creates a brand new document with fresh personal content.
    Adds variety to the Drive over time.
"""

import random
import json
import asyncio
from pathlib import Path
from playwright.async_api import Page

from activities.base_activity import BaseActivity
from core.orchestrator import STATE_DIR

DRIVE_DATA_FILE = Path(__file__).parent.parent / "data" / "drive_data.json"
DRIVE_URL       = "https://drive.google.com"
DOCS_NEW_URL    = "https://docs.google.com/document/create"
SHEETS_NEW_URL  = "https://docs.google.com/spreadsheets/create"


class DriveActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict,
                 mode: str = "browse"):
        """
        mode: "setup" | "browse" | "edit_doc" | "create_doc"
        """
        super().__init__(page, account, behaviour_cfg)
        with open(DRIVE_DATA_FILE, encoding="utf-8") as f:
            self._data = json.load(f)
        self._mode       = mode
        self._state_file = STATE_DIR / f"{account['id']}_drive.json"
        self._state      = self._load_state()

    async def run(self) -> bool:
        # If setup already done, fall back gracefully
        if self._mode == "setup" and self._state.get("setup_complete"):
            self.log.info("Drive setup already complete — switching to edit_doc")
            self._mode = "edit_doc"

        # Can't edit if nothing created yet
        if self._mode == "edit_doc" and not self._state.get("doc_urls"):
            self.log.info("No docs to edit yet — switching to browse")
            self._mode = "browse"

        self.log.info(f"Drive session | mode={self._mode}")

        try:
            if self._mode == "setup":
                await self._run_setup()
            elif self._mode == "browse":
                await self._browse_drive()
            elif self._mode == "edit_doc":
                await self._edit_existing_doc()
            elif self._mode == "create_doc":
                await self._create_new_doc()
            else:
                self.log.warning(f"Unknown drive mode: {self._mode}")
                return False

            self._save_state()
            return True

        except Exception as e:
            self.log.error(f"Drive session failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Setup — runs once
    # ------------------------------------------------------------------

    async def _run_setup(self) -> None:
        """
        Create the initial Drive files — spread across multiple sessions (1-2 files
        each) rather than all at once. Real people don't create 5 documents in a row
        on their first day.
        State tracks a pending queue so each session continues from where it left off.
        """
        if "setup_queue" not in self._state:
            self._state["setup_queue"] = self._build_setup_queue()

        queue = self._state["setup_queue"]
        if not queue:
            self.log.info("Drive setup already complete — switching to browse")
            self._state["setup_complete"] = True
            await self._browse_drive()
            return

        batch_size = random.randint(1, 2)
        batch      = queue[:batch_size]
        self._state["setup_queue"] = queue[batch_size:]

        self.log.info(
            f"Drive setup: creating {len(batch)} file(s) "
            f"({len(queue) - len(batch)} remaining after this session)"
        )

        for spec in batch:
            if spec["kind"] == "doc":
                url = await self._create_google_doc(
                    title   = spec["title"],
                    content = spec["content"],
                )
                if url:
                    self._state.setdefault("doc_urls", {})[spec["doc_type"]] = url
                    self._state.setdefault("doc_types", {})[url] = spec["doc_type"]
            elif spec["kind"] == "sheet":
                url = await self._create_google_sheet(
                    title   = spec["title"],
                    headers = spec["headers"],
                    rows    = spec["rows"],
                )
                if url:
                    self._state.setdefault("sheet_urls", {})[spec["sheet_type"]] = url
            await self.humaniser.pause(5000, 12000)

        if not self._state["setup_queue"]:
            self._state["setup_complete"] = True
            self.log.info(
                f"Drive setup complete — "
                f"{len(self._state.get('doc_urls', {}))} docs, "
                f"{len(self._state.get('sheet_urls', {}))} sheets"
            )
        else:
            self.log.info(
                f"Drive setup batch done. "
                f"{len(self._state['setup_queue'])} file(s) remain for future sessions."
            )

    def _build_setup_queue(self) -> list:
        """Build a randomised list of all files to create during setup."""
        queue = []
        setup_cfg = self._data["setup"]

        for doc_type in setup_cfg["docs_to_create"]:
            doc_data = self._data["documents"].get(doc_type)
            if doc_data:
                queue.append({
                    "kind":     "doc",
                    "doc_type": doc_type,
                    "title":    doc_data["title"],
                    "content":  random.choice(doc_data["content"]),
                })

        for sheet_type in setup_cfg["sheets_to_create"]:
            sheet_data = self._data["spreadsheets"].get(sheet_type)
            if sheet_data:
                queue.append({
                    "kind":       "sheet",
                    "sheet_type": sheet_type,
                    "title":      sheet_data["title"],
                    "headers":    sheet_data["headers"],
                    "rows":       sheet_data["rows"],
                })

        random.shuffle(queue)
        return queue

    # ------------------------------------------------------------------
    # Browse Drive
    # ------------------------------------------------------------------

    async def _browse_drive(self) -> None:
        self.log.info("Browsing Google Drive")
        await self.page.goto(DRIVE_URL, wait_until="domcontentloaded", timeout=20000)
        await self.humaniser.pause(2000, 4000)
        await self._dismiss_consent()

        # Scroll through the file list
        await self.humaniser.human_scroll(self.page, max_fraction=0.5)
        await self.humaniser.pause(1500, 3500)

        # If we have saved doc URLs, open one and read it
        doc_urls = self._state.get("doc_urls", {})
        sheet_urls = self._state.get("sheet_urls", {})
        all_urls = list(doc_urls.values()) + list(sheet_urls.values())

        if all_urls:
            url = random.choice(all_urls)
            await self._open_and_read_file(url)
        else:
            # Just browse Drive itself
            await self.humaniser.human_scroll(self.page, max_fraction=0.7)
            await self.humaniser.reading_pause()

        # Maybe use the search bar in Drive
        if random.random() < 0.30:
            await self._search_in_drive()

    async def _open_and_read_file(self, url: str) -> None:
        """Open a document URL and read/scroll through it."""
        self.log.debug(f"Opening file: {url}")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await self.humaniser.pause(2000, 4000)

        # Scroll through the document
        await self.humaniser.human_scroll(self.page, max_fraction=0.8)
        await self.humaniser.reading_pause()

        # Maybe scroll back to top
        if random.random() < 0.30:
            await self.page.keyboard.press("Control+Home")
            await self.humaniser.pause(1000, 2500)

    async def _search_in_drive(self) -> None:
        """Use the Drive search bar to look for a file."""
        terms = ["shopping", "notes", "budget", "to do", "list"]
        term  = random.choice(terms)
        try:
            search_box = self.page.locator(
                'input[aria-label*="Search"], input[placeholder*="Search"]'
            ).first
            if await search_box.is_visible(timeout=3000):
                await search_box.click()
                await self.humaniser.pause(400, 900)
                for char in term:
                    delay = max(50, min(250, int(random.gauss(100, 35))))
                    await search_box.type(char, delay=delay)
                await self.page.keyboard.press("Enter")
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(2000, 4000)
                await self.humaniser.human_scroll(self.page, max_fraction=0.4)
                self.log.debug(f"Searched Drive for: {term!r}")
        except Exception as e:
            self.log.debug(f"Drive search failed: {e}")

    # ------------------------------------------------------------------
    # Edit an existing document
    # ------------------------------------------------------------------

    async def _edit_existing_doc(self) -> None:
        doc_urls  = self._state.get("doc_urls", {})
        if not doc_urls:
            self.log.info("No docs available to edit")
            return

        # Pick a random doc
        doc_type = random.choice(list(doc_urls.keys()))
        url      = doc_urls[doc_type]
        self.log.info(f"Editing doc: {doc_type!r}")

        await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await self.humaniser.pause(2000, 5000)

        # Read through it first
        await self.humaniser.human_scroll(self.page, max_fraction=0.7)
        await self.humaniser.reading_pause()

        # Make a small, appropriate edit based on doc type
        if doc_type in ("shopping_list",):
            await self._append_to_doc(
                random.choice(self._data["edits"]["new_list_items"])
            )
        elif doc_type == "to_do":
            # Either tick something off or add a new item
            if random.random() < 0.5:
                await self._tick_off_todo()
            else:
                await self._append_to_doc(
                    random.choice(self._data["edits"]["new_todo_items"])
                )
        elif doc_type == "notes":
            await self._append_to_doc(
                random.choice(self._data["edits"]["additions"])
            )
        else:
            await self._append_to_doc(
                random.choice(self._data["edits"]["additions"])
            )

        # Wait for autosave indicator
        await self.humaniser.pause(3000, 6000)
        self.log.info(f"Edit complete on: {doc_type!r}")

    async def _append_to_doc(self, text: str) -> None:
        """Click at the end of the document and type new text."""
        try:
            # Click the document body
            doc_body = self.page.locator('.kix-appview-editor, .docs-texteventtarget-iframe').first

            # Move focus to end of doc
            await self.page.keyboard.press("Control+End")
            await self.humaniser.pause(500, 1200)

            # Type the new content
            for char in text:
                delay = max(40, min(300, int(random.gauss(105, 38))))
                await self.page.keyboard.type(char)
                await asyncio.sleep(delay / 1000)
                if random.random() < 0.03:
                    await asyncio.sleep(random.uniform(0.4, 1.0))

            self.log.debug(f"Appended to doc: {text[:40]!r}…")
        except Exception as e:
            self.log.debug(f"Append failed: {e}")

    async def _tick_off_todo(self) -> None:
        """Find a [ ] item and change it to [x]."""
        try:
            await self.page.keyboard.press("Control+Home")
            await self.humaniser.pause(400, 900)

            # Use find & replace to tick off first unchecked item
            await self.page.keyboard.press("Control+h")
            await self.humaniser.pause(1000, 2000)

            find_field = self.page.locator(
                'input[aria-label="Find"], input[placeholder*="Find"]'
            ).first
            replace_field = self.page.locator(
                'input[aria-label="Replace with"], input[placeholder*="Replace"]'
            ).last

            if await find_field.is_visible(timeout=3000):
                await find_field.click()
                await find_field.fill("[ ]")
                await self.humaniser.pause(500, 1000)
                await replace_field.click()
                await replace_field.fill("[x]")
                await self.humaniser.pause(500, 1000)

                # Replace only first occurrence
                replace_one_btn = self.page.locator(
                    'button:has-text("Replace"), button[aria-label*="Replace"]'
                ).first
                if await replace_one_btn.is_visible(timeout=2000):
                    await replace_one_btn.click()
                    await self.humaniser.pause(500, 1200)

            # Close find/replace
            await self.page.keyboard.press("Escape")
            await self.humaniser.pause(400, 800)
            self.log.debug("Ticked off a to-do item")

        except Exception as e:
            self.log.debug(f"Tick off failed: {e}")
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Create a new document (ongoing activity)
    # ------------------------------------------------------------------

    async def _create_new_doc(self) -> None:
        # Pick a doc type that doesn't already exist, or personal/notes
        existing_types = set(self._state.get("doc_urls", {}).keys())
        all_types      = list(self._data["documents"].keys())
        new_types      = [t for t in all_types if t not in existing_types]

        doc_type  = random.choice(new_types) if new_types else "personal"
        doc_data  = self._data["documents"].get(doc_type, self._data["documents"]["notes"])
        title     = doc_data["title"]
        content   = random.choice(doc_data["content"])

        self.log.info(f"Creating new doc: {title!r} (type: {doc_type})")
        url = await self._create_google_doc(title=title, content=content)

        if url:
            self._state.setdefault("doc_urls", {})[doc_type] = url
            self._state.setdefault("doc_types", {})[url]     = doc_type
            self.log.info(f"New doc created: {url}")

    # ------------------------------------------------------------------
    # Core: create Google Doc
    # ------------------------------------------------------------------

    async def _create_google_doc(self, title: str, content: str) -> str | None:
        """Create a new Google Doc, set its title, type content, return URL."""
        try:
            await self.page.goto(DOCS_NEW_URL, wait_until="domcontentloaded", timeout=20000)
            await self.humaniser.pause(3000, 6000)

            # Rename the document
            await self._set_doc_title(title)
            await self.humaniser.pause(1500, 3000)

            # Click into the body and type content
            await self._click_doc_body()
            await self.humaniser.pause(800, 1800)
            await self._type_doc_content(content)

            # Wait for autosave
            await self.humaniser.pause(3000, 6000)

            doc_url = self.page.url
            self.log.info(f"Doc created: {title!r} → {doc_url}")
            return doc_url

        except Exception as e:
            self.log.error(f"Doc creation failed for {title!r}: {e}")
            return None

    async def _set_doc_title(self, title: str) -> None:
        """Click the document title field and rename it."""
        try:
            title_field = self.page.locator(
                'div.docs-title-input-label-inner, input.docs-title-input, '
                'span.docs-title-widget-editable[aria-label*="title" i]'
            ).first

            if not await title_field.is_visible(timeout=5000):
                # Try clicking where the title area usually is
                await self.page.click('.docs-title-outer', timeout=3000)
                await self.humaniser.pause(500, 1000)
                title_field = self.page.locator('input.docs-title-input').first

            await title_field.click()
            await self.humaniser.pause(300, 700)
            await title_field.click(click_count=3)
            await self.humaniser.pause(200, 500)

            for char in title:
                delay = max(50, min(280, int(random.gauss(105, 38))))
                await title_field.type(char, delay=delay)

            await self.page.keyboard.press("Enter")
            await self.humaniser.pause(500, 1200)
            self.log.debug(f"Set doc title: {title!r}")
        except Exception as e:
            self.log.debug(f"Title set failed: {e}")

    async def _click_doc_body(self) -> None:
        """Click into the document body to start typing."""
        try:
            # Google Docs embeds an iframe for the editor
            frame = self.page.frame_locator('.docs-texteventtarget-iframe').first
            if frame:
                try:
                    await frame.locator("body").click(timeout=3000)
                    return
                except Exception:
                    pass

            # Fallback: click the main editor area
            editor = self.page.locator('.kix-page-content-wrapper, .docs-editor').first
            if await editor.is_visible(timeout=3000):
                await editor.click()
        except Exception as e:
            self.log.debug(f"Doc body click failed: {e}")
            # Last resort: press Escape then click somewhere central
            await self.page.keyboard.press("Escape")
            await self.page.mouse.click(600, 400)

    async def _type_doc_content(self, content: str) -> None:
        """Type content into the document with human timing."""
        lines = content.split("\n")
        for line_idx, line in enumerate(lines):
            for char in line:
                delay = max(40, min(300, int(random.gauss(100, 38))))
                await self.page.keyboard.type(char)
                await asyncio.sleep(delay / 1000)

                # Occasional brief pause mid-sentence
                if char in (".", ",", "!") and random.random() < 0.08:
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                # Occasional longer pause (thinking)
                if random.random() < 0.02:
                    await asyncio.sleep(random.uniform(1.0, 3.0))

            if line_idx < len(lines) - 1:
                await self.page.keyboard.press("Enter")
                # Pause between lines
                await asyncio.sleep(random.uniform(0.1, 0.4))

        self.log.debug(f"Typed {len(content)} chars into doc")

    # ------------------------------------------------------------------
    # Core: create Google Sheet
    # ------------------------------------------------------------------

    async def _create_google_sheet(self, title: str, headers: list, rows: list) -> str | None:
        """Create a new Google Sheet with headers and data rows."""
        try:
            await self.page.goto(SHEETS_NEW_URL, wait_until="domcontentloaded", timeout=20000)
            await self.humaniser.pause(3000, 6000)

            # Rename the spreadsheet
            await self._set_doc_title(title)
            await self.humaniser.pause(1500, 3000)

            # Click cell A1
            await self._click_sheet_cell("A1")
            await self.humaniser.pause(800, 1800)

            # Type headers across row 1
            for i, header in enumerate(headers):
                await self._type_cell_value(header)
                if i < len(headers) - 1:
                    await self.page.keyboard.press("Tab")
                    await asyncio.sleep(random.uniform(0.2, 0.6))
            await self.page.keyboard.press("Enter")
            await self.humaniser.pause(500, 1200)

            # Type data rows
            for row in rows:
                # Go to start of next row (column A)
                await self.page.keyboard.press("Home")
                await self.humaniser.pause(200, 500)
                for i, cell_val in enumerate(row):
                    await self._type_cell_value(cell_val)
                    if i < len(row) - 1:
                        await self.page.keyboard.press("Tab")
                        await asyncio.sleep(random.uniform(0.15, 0.5))
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(random.uniform(0.3, 0.8))

            # Wait for autosave
            await self.humaniser.pause(3000, 6000)

            sheet_url = self.page.url
            self.log.info(f"Sheet created: {title!r} → {sheet_url}")
            return sheet_url

        except Exception as e:
            self.log.error(f"Sheet creation failed for {title!r}: {e}")
            return None

    async def _click_sheet_cell(self, cell_ref: str) -> None:
        """Click on a cell using the Name Box (e.g. A1)."""
        try:
            name_box = self.page.locator(
                '.cell-input, input[aria-label*="cell" i], '
                '.goog-flat-menu-button-caption'
            ).first
            if await name_box.is_visible(timeout=3000):
                await name_box.click()
                await name_box.click(click_count=3)
                await name_box.type(cell_ref)
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(0.3)
        except Exception as e:
            self.log.debug(f"Cell click failed: {e}")

    async def _type_cell_value(self, value: str) -> None:
        """Type a value into the current cell."""
        for char in value:
            delay = max(40, min(200, int(random.gauss(80, 28))))
            await self.page.keyboard.type(char)
            await asyncio.sleep(delay / 1000)

    # ------------------------------------------------------------------
    # Consent
    # ------------------------------------------------------------------

    async def _dismiss_consent(self) -> None:
        """Dismiss Google Drive consent dialogs."""
        await self.dismiss_google_consent()

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
