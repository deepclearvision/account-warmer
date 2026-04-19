"""
Email Read Activity
Opens Gmail, reads unread emails, and optionally replies.
Also detects and clicks newsletter confirmation links automatically.
This is the highest-trust activity for warming Gmail accounts.
"""

import random
import json
from pathlib import Path
from playwright.async_api import Page
from activities.base_activity import BaseActivity

EMAIL_DATA_FILE  = Path(__file__).parent.parent / "data" / "email_bodies.json"
NEWSLETTER_FILE  = Path(__file__).parent.parent / "data" / "newsletters.json"
GMAIL_URL        = "https://mail.google.com"


class EmailReadActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict):
        super().__init__(page, account, behaviour_cfg)
        with open(EMAIL_DATA_FILE, encoding="utf-8") as f:
            self._email_data = json.load(f)
        with open(NEWSLETTER_FILE, encoding="utf-8") as f:
            nl_data = json.load(f)
        self._confirm_patterns = nl_data.get("confirmation_patterns", [])
        self._email_cfg = behaviour_cfg.get("email", {})

    async def run(self) -> bool:
        self.log.info("Starting email read session")
        try:
            await self.humaniser.navigate(self.page, GMAIL_URL)
            await self.humaniser.pause(2000, 4000)

            # Make sure we're on the inbox
            await self._ensure_inbox()

            # Read some emails
            await self._read_unread_emails()

            # 25% of sessions: briefly browse Sent or All Mail (real user behaviour)
            if random.random() < 0.25:
                await self._browse_secondary_folder()

            return True

        except Exception as e:
            self.log.error(f"Email read session failed: {e}")
            return False

    async def _ensure_inbox(self) -> None:
        """Navigate to inbox if not already there."""
        try:
            inbox_link = self.page.locator('a[href*="#inbox"], [data-tooltip="Inbox"]').first
            if await inbox_link.is_visible(timeout=3000):
                await self.humaniser.human_click(self.page, 'a[href*="#inbox"], [data-tooltip="Inbox"]')
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1500, 3000)
        except Exception:
            pass

    async def _read_unread_emails(self) -> None:
        """Find unread emails and read them."""
        open_prob   = self._email_cfg.get("open_and_read_probability", 0.80)
        reply_prob  = self._email_cfg.get("reply_probability", 0.60)
        star_prob   = self._email_cfg.get("star_probability", 0.10)
        read_range  = self._email_cfg.get("email_read_seconds", [8, 45])

        # Find unread email rows (bold text in Gmail)
        unread = self.page.locator('tr.zA.zE')
        count = await unread.count()
        self.log.info(f"Found {count} unread emails")

        # Scroll the inbox first — real users scan before opening
        await self.humaniser.human_scroll(self.page, max_fraction=0.3)
        await self.humaniser.pause(1500, 3500)

        if count == 0:
            self.log.info("No unread emails — scrolling inbox briefly")
            await self.humaniser.human_scroll(self.page, max_fraction=0.5)
            await self.humaniser.reading_pause()
            return

        # Read a variable number of emails per session
        read_range = self._email_cfg.get("emails_per_session", [3, 8])
        to_read = min(count, random.randint(*read_range))
        for i in range(to_read):
            if random.random() > open_prob:
                continue

            try:
                email_row = unread.nth(i)
                sender = await email_row.locator('span[email]').first.inner_text(timeout=2000)
                self.log.debug(f"Opening email from: {sender}")

                await email_row.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1000, 2500)

                # Scroll through the email
                await self.humaniser.human_scroll(self.page, max_fraction=0.8)
                read_time = random.uniform(*read_range)
                self.log.debug(f"Reading email for {read_time:.0f}s")
                await self.humaniser.reading_pause()

                # Check if this is a newsletter confirmation — handle it first
                is_confirmation = await self._handle_confirmation_if_needed()

                if not is_confirmation:
                    # Maybe star it
                    if random.random() < star_prob:
                        await self._star_email()

                    # Maybe reply (only to peer emails, not newsletters)
                    if random.random() < reply_prob:
                        await self._reply_to_email()

                # Go back to inbox
                await self.humaniser.pause(1500, 3500)
                await self.page.go_back()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1000, 2500)

            except Exception as e:
                self.log.debug(f"Error reading email #{i}: {e}")
                try:
                    await self.humaniser.navigate(self.page, GMAIL_URL)
                    await self.humaniser.pause(1500, 3000)
                except Exception:
                    pass

    async def _handle_confirmation_if_needed(self) -> bool:
        """
        Detect newsletter confirmation emails and click the confirm link.
        Returns True if this was a confirmation email (so we skip reply logic).
        """
        try:
            # Get the email subject
            subject_el = self.page.locator('h2.hP').first
            subject = await subject_el.inner_text(timeout=2000)
            subject_lower = subject.lower()

            is_confirmation = any(
                p in subject_lower for p in self._confirm_patterns
            )
            if not is_confirmation:
                return False

            self.log.info(f"Detected confirmation email: {subject!r}")

            # Pause to "read" it before clicking
            await self.humaniser.pause(4000, 10000)

            # Find the confirmation link — look for the most prominent link in the email body
            # Confirmation emails typically have one big CTA button/link
            confirm_link = self.page.locator(
                'a:has-text("Confirm"), '
                'a:has-text("Verify"), '
                'a:has-text("Activate"), '
                'a:has-text("Complete"), '
                'a:has-text("Yes, subscribe"), '
                'a:has-text("Click here to confirm")'
            ).first

            if not await confirm_link.is_visible(timeout=3000):
                # Fallback: find any link inside the email body
                confirm_link = self.page.locator(
                    'div.a3s a[href*="confirm"], '
                    'div.a3s a[href*="verify"], '
                    'div.a3s a[href*="subscribe"], '
                    'div.a3s a[href*="activate"]'
                ).first

            if await confirm_link.is_visible(timeout=2000):
                href = await confirm_link.get_attribute("href")
                self.log.info(f"Clicking confirmation link: {href[:60] if href else 'unknown'}…")
                await self.humaniser.pause(1000, 3000)
                await confirm_link.click()

                # Wait for the confirmation page to load
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                await self.humaniser.pause(3000, 7000)
                await self.humaniser.human_scroll(self.page, max_fraction=0.5)

                self.log.info("Newsletter confirmed successfully")

                # Navigate back to Gmail
                await self.humaniser.pause(2000, 5000)
                await self.humaniser.navigate(self.page, GMAIL_URL)
                await self.humaniser.pause(1500, 3000)
                return True
            else:
                self.log.debug("Confirmation link not found in email")
                return True  # Still treat as confirmation, just skip reply

        except Exception as e:
            self.log.debug(f"Confirmation check failed: {e}")
            return False

    async def _star_email(self) -> None:
        try:
            star = self.page.locator('[aria-label="Not starred"], [aria-label="Starred"]').first
            if await star.is_visible(timeout=2000):
                await self.humaniser.pause(500, 1500)
                await star.click()
                self.log.debug("Starred email")
        except Exception:
            pass

    async def _reply_to_email(self) -> None:
        """Compose a short natural reply."""
        try:
            # Click Reply button
            reply_btn = self.page.locator(
                '[data-tooltip="Reply"], [aria-label*="Reply"]'
            ).first
            if not await reply_btn.is_visible(timeout=3000):
                return

            await self.humaniser.pause(2000, 5000)
            await reply_btn.click()
            await self.humaniser.pause(1500, 3000)

            # Type a reply
            reply_text = random.choice(self._email_data["replies"])
            sign_off   = random.choice(self._email_data["sign_offs"])
            # Get the account's first name from email address
            name = self.account["email"].split("@")[0].split(".")[0].capitalize()
            full_reply = f"{reply_text}\n\n{sign_off}\n{name}"

            compose_box = self.page.locator(
                '[aria-label="Message Body"], div[contenteditable="true"]'
            ).last
            await compose_box.click()
            await self.humaniser.pause(500, 1200)

            # Type reply character by character
            for char in full_reply:
                delay = max(40, min(350, int(random.gauss(110, 45))))
                await compose_box.type(char, delay=delay)
                if random.random() < 0.03:
                    import asyncio
                    await asyncio.sleep(random.uniform(0.3, 0.8))

            await self.humaniser.pause(1500, 4000)

            # Send
            send_btn = self.page.locator(
                '[data-tooltip="Send"], [aria-label="Send"]'
            ).first
            if await send_btn.is_visible(timeout=3000):
                await send_btn.click()
                await self.humaniser.pause(1000, 2500)
                self.log.info("Reply sent")
            else:
                # Discard if send button not found
                await self.page.keyboard.press("Escape")

        except Exception as e:
            self.log.debug(f"Reply failed: {e}")
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass

    async def _browse_secondary_folder(self) -> None:
        """Browse Sent or All Mail folder briefly."""
        try:
            folders = [
                ('a[href*="#sent"]',    '[data-tooltip="Sent"]',    "Sent"),
                ('a[href*="#all"]',     '[data-tooltip="All Mail"]', "All Mail"),
            ]
            sel_main, sel_tooltip, label = random.choice(folders)
            link = self.page.locator(f'{sel_main}, {sel_tooltip}').first
            if await link.count() > 0 and await link.is_visible(timeout=3000):
                await self.humaniser.pause(2000, 4000)
                await link.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.humaniser.pause(1500, 3000)
                await self.humaniser.human_scroll(self.page, max_fraction=0.4)
                await self.humaniser.reading_pause()
                self.log.debug(f"Browsed {label} folder")
                # Go back to inbox
                await self.humaniser.pause(1000, 2500)
                inbox = self.page.locator('a[href*="#inbox"], [data-tooltip="Inbox"]').first
                if await inbox.count() > 0:
                    await inbox.click()
                    await self.page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            self.log.debug(f"Secondary folder browse failed: {e}")
