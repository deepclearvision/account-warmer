"""
Email Send Activity (Self-Warming)
Sends emails to other accounts in your pool.
Each account picks a random peer from the pool as the recipient.
This creates bidirectional email traffic which is the strongest trust signal.
"""

import random
import json
from datetime import datetime, timedelta
from pathlib import Path
from playwright.async_api import Page
from activities.base_activity import BaseActivity

EMAIL_DATA_FILE    = Path(__file__).parent.parent / "data" / "email_bodies.json"
GMAIL_COMPOSE_URL  = "https://mail.google.com/#compose"
GMAIL_URL          = "https://mail.google.com"
# Shared across all accounts — tracks the last time each recipient was emailed
from core.paths import STATE_DIR as _STATE_DIR
_COOLDOWN_FILE = _STATE_DIR / "email_cooldowns.json"
# Minimum minutes between any two accounts emailing the same recipient
_COOLDOWN_MINUTES  = 45


class EmailSendActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict,
                 all_accounts: list, emails_sent_today: int):
        super().__init__(page, account, behaviour_cfg)
        with open(EMAIL_DATA_FILE, encoding="utf-8") as f:
            self._email_data = json.load(f)
        self._email_cfg = behaviour_cfg.get("email", {})
        self._all_accounts = all_accounts
        self._emails_sent_today = emails_sent_today

    async def run(self) -> bool:
        max_daily = self._email_cfg.get("max_sends_per_day", 12)
        if self._emails_sent_today >= max_daily:
            self.log.info(f"Daily send limit reached ({max_daily}). Skipping send activity.")
            return True

        recipient = self._pick_recipient()
        if not recipient:
            self.log.warning("No eligible recipient found. Need at least 2 accounts in pool.")
            return False

        self.log.info(f"Composing email to: {recipient}")
        return await self._send_email(recipient)

    def _pick_recipient(self) -> str | None:
        """
        Pick a random other account from the pool as recipient.
        Skips anyone who received an email (from any account) within the last
        _COOLDOWN_MINUTES minutes — prevents all accounts emailing each other
        simultaneously when sessions run at the same time.
        """
        cooldowns = self._load_cooldowns()
        cutoff    = datetime.now() - timedelta(minutes=_COOLDOWN_MINUTES)

        eligible = [
            a["email"]
            for a in self._all_accounts
            if a["email"] != self.account["email"]
            and self._cooldown_ok(a["email"], cooldowns, cutoff)
        ]
        if not eligible:
            # All contacts on cooldown — pick the least-recently-emailed one as fallback
            others = [
                a["email"] for a in self._all_accounts
                if a["email"] != self.account["email"]
            ]
            if not others:
                return None
            self.log.debug("All recipients on cooldown — using least-recently-emailed")
            return min(others, key=lambda e: cooldowns.get(e, ""))

        return random.choice(eligible)

    @staticmethod
    def _cooldown_ok(email: str, cooldowns: dict, cutoff: datetime) -> bool:
        last_str = cooldowns.get(email)
        if not last_str:
            return True
        try:
            return datetime.fromisoformat(last_str) < cutoff
        except Exception:
            return True

    @staticmethod
    def _load_cooldowns() -> dict:
        try:
            if _COOLDOWN_FILE.exists():
                return json.loads(_COOLDOWN_FILE.read_text())
        except Exception:
            pass
        return {}

    @staticmethod
    def _save_cooldown(email: str) -> None:
        try:
            _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = EmailSendActivity._load_cooldowns()
            data[email] = datetime.now().isoformat()
            _COOLDOWN_FILE.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    async def _send_email(self, to_address: str) -> bool:
        try:
            await self.page.goto(GMAIL_URL, wait_until="domcontentloaded")
            await self.humaniser.pause(2000, 4000)

            # Click Compose
            compose_btn = self.page.locator(
                '[gh="cm"], [data-tooltip="Compose"], [aria-label="Compose"]'
            ).first
            if not await compose_btn.is_visible(timeout=5000):
                self.log.warning("Compose button not found")
                return False

            await self.humaniser.human_click(
                self.page, '[gh="cm"], [data-tooltip="Compose"], [aria-label="Compose"]'
            )
            await self.humaniser.pause(1000, 2500)

            # Fill To field
            to_field = self.page.locator(
                'input[name="to"], textarea[name="to"], [aria-label="To"]'
            ).first
            await to_field.click()
            await self.humaniser.pause(300, 700)
            await to_field.fill(to_address)
            await self.page.keyboard.press("Tab")
            await self.humaniser.pause(500, 1200)

            # Fill Subject
            subject = random.choice(self._email_data["subjects"])
            subject_field = self.page.locator(
                'input[name="subjectbox"], [aria-label="Subject"]'
            ).first
            await subject_field.click()
            await self.humaniser.pause(300, 700)
            for char in subject:
                delay = max(40, min(300, int(random.gauss(100, 40))))
                await subject_field.type(char, delay=delay)
            await self.humaniser.pause(500, 1200)

            # Fill Body
            opener = random.choice(self._email_data["openers"])
            body   = random.choice(self._email_data["bodies"])
            signoff = random.choice(self._email_data["sign_offs"])
            name   = self.account["email"].split("@")[0].split(".")[0].capitalize()
            full_body = f"{opener}\n\n{body}\n\n{signoff}\n{name}"

            body_field = self.page.locator(
                '[aria-label="Message Body"], div[contenteditable="true"][role="textbox"]'
            ).last
            await body_field.click()
            await self.humaniser.pause(400, 1000)

            for char in full_body:
                delay = max(40, min(350, int(random.gauss(115, 45))))
                await body_field.type(char, delay=delay)
                if random.random() < 0.04:
                    import asyncio
                    await asyncio.sleep(random.uniform(0.3, 1.0))

            # Pause before sending (re-reading what we wrote)
            await self.humaniser.pause(3000, 8000)

            # Send
            send_btn = self.page.locator(
                '[data-tooltip="Send"], [aria-label="Send"]'
            ).first
            if await send_btn.is_visible(timeout=3000):
                await send_btn.click()
                await self.humaniser.pause(1500, 3000)
                self.log.info(f"Email sent to {to_address} | subject: {subject!r}")
                EmailSendActivity._save_cooldown(to_address)
                return True
            else:
                self.log.warning("Send button not found after composing")
                await self.page.keyboard.press("Escape")
                return False

        except Exception as e:
            self.log.error(f"Email send failed: {e}")
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False
