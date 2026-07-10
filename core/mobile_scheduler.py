"""
Mobile Schedule Scheduler

Maintains a set of mobile accounts enrolled for daily schedule-driven warmup.
Each enrolled account runs exactly one batch-mode session per day during active
hours (7am-10pm). The script to run is determined by the account's schedule_day
counter using a monthly pattern.

Sessions are strictly sequential (one phone at a time — shared mobile proxy).
Fair scheduling: the account that waited longest since its last session is
chosen next.

State is persisted to logs/state/mobile_schedule.json so enrolled accounts
survive a server restart.  Unlike the desktop scheduler (which spawns sub-
processes), session execution runs in a background thread and acquires the
same `_warmup_semaphore` used by manual warmup calls.
"""

import asyncio
import json
import random
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from core.paths import MOBILE_SCHEDULER_STATE_FILE as STATE_FILE

ACTIVE_HOURS_START = 7
ACTIVE_HOURS_END   = 22
CHECK_INTERVAL_SECS = 120  # mobile sessions take 3-6 min; poll every 2 min

_instance: Optional["MobileScheduler"] = None


def get_mobile_scheduler() -> "MobileScheduler":
    global _instance
    if _instance is None:
        _instance = MobileScheduler()
    return _instance


def _day_to_script(day: int) -> str:
    """
    Monthly schedule pattern (days 1+).

      Day  1-2:  local_discovery   (initial warming)
      Day  3:    money_kw          (first business interaction)
      Day  4-10: local_discovery
      Day 11:    brand_1km
      Day 12-20: local_discovery
      Day 21:    brand_1km
      Day 22-30: local_discovery

    After day 30 (month 2+): daily local_discovery + brand_1km every 10th day.
    Total in month 1: 1 Money KW, 2 Brand 1km, 27 Local Discovery.
    """
    cycle = ((day - 1) % 30) + 1  # map day 31 → day 1, day 32 → day 2, etc.
    if day <= 30:
        if cycle == 3:
            return "money_kw"
        if cycle in (11, 21):
            return "brand_1km"
        return "local_discovery"
    # Post month 1
    if cycle % 10 == 0:
        return "brand_1km"
    return "local_discovery"


class MobileScheduler:
    """Daily mobile warmup scheduler — one script per account per day."""

    def __init__(self):
        self._state: dict[str, dict] = {}     # account_id → entry
        self._paused: bool = True
        self._running: bool = False           # in-memory guard — only one phone at a time
        self._log = get_logger("mobile-sched")
        self._load_state()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            self._state = data.get("accounts", {})
            self._paused = bool(data.get("paused", True))
            # Reset "running" → "waiting" on startup (old session died with the server)
            now_iso = datetime.now().isoformat()
            for entry in self._state.values():
                if entry.get("status") == "running":
                    entry["status"] = "waiting"
                elif entry.get("status") == "error":
                    entry["status"]    = "waiting"
                    entry["error_msg"] = None
            self._log.info("MobileScheduler loaded: %d accounts, paused=%s",
                           len(self._state), self._paused)
        else:
            self._log.info("MobileScheduler: no state file — fresh start")

    def _save_state(self) -> None:
        data = {
            "accounts": self._state,
            "paused":    self._paused,
            "saved_at":  datetime.now().isoformat(),
        }
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Public API ─────────────────────────────────────────────────────────────

    def enroll(self, account_id: str) -> dict:
        """Add an account to the mobile schedule. Idempotent."""
        today_str = str(date.today())
        if account_id in self._state:
            # Already enrolled — make sure it's in a usable state
            entry = self._state[account_id]
            if entry.get("status") == "error":
                entry["status"]    = "waiting"
                entry["error_msg"] = None
                self._save_state()
            return {"enrolled": account_id, "already": True}

        self._state[account_id] = {
            "status":         "waiting",
            "schedule_day":   1,
            "last_run_date":  None,
            "last_script":    None,
            "enrolled_at":    datetime.now().isoformat(),
            "last_run_at":    None,
            "error_msg":      None,
        }
        self._save_state()
        self._log.info("Enrolled %s (schedule_day=1)", account_id)
        return {"enrolled": account_id, "already": False}

    def unenroll(self, account_id: str) -> bool:
        """Remove an account from the schedule."""
        if account_id not in self._state:
            return False
        del self._state[account_id]
        self._save_state()
        self._log.info("Unenrolled %s", account_id)
        return True

    def is_enrolled(self, account_id: str) -> bool:
        return account_id in self._state

    def pause(self) -> None:
        self._paused = True
        self._save_state()

    def resume(self) -> None:
        self._paused = False
        self._save_state()

    @property
    def paused(self) -> bool:
        return self._paused

    def get_status(self) -> dict:
        """Return public summary for the dashboard."""
        today = str(date.today())
        accounts = {}
        for aid, entry in self._state.items():
            sd = entry.get("schedule_day", 1)
            accounts[aid] = {
                "status":       entry.get("status"),
                "schedule_day": sd,
                "today_script": _day_to_script(sd) if entry.get("last_run_date") != today else "done",
                "last_run_date": entry.get("last_run_date"),
                "last_script":   entry.get("last_script"),
                "enrolled_at":   entry.get("enrolled_at"),
                "last_run_at":   entry.get("last_run_at"),
                "error_msg":     entry.get("error_msg"),
            }
        return {"paused": self._paused, "running": self._running,
                "accounts": accounts}

    def get_raw_state(self) -> dict:
        """Return the raw state dict (for session runner to mutate)."""
        return self._state

    def record_session_complete(self, account_id: str, script: str) -> None:
        """Called by the session runner after a successful session."""
        entry = self._state.get(account_id)
        if not entry:
            return
        now = datetime.now()
        entry["status"]        = "waiting"
        entry["schedule_day"]  = entry.get("schedule_day", 1) + 1
        entry["last_run_date"] = str(date.today())
        entry["last_script"]   = script
        entry["last_run_at"]   = now.isoformat()
        entry["error_msg"]     = None
        self._save_state()

    def record_session_error(self, account_id: str, error: str) -> None:
        """Called by the session runner after a failed session."""
        entry = self._state.get(account_id)
        if not entry:
            return
        entry["status"]    = "error"
        entry["error_msg"] = error
        entry["last_run_at"] = datetime.now().isoformat()
        self._save_state()

    # ── Background loop ────────────────────────────────────────────────────────

    async def run_loop(self) -> None:
        """Asyncio task — runs forever while FastAPI is up."""
        while True:
            try:
                self._tick()
            except Exception:
                pass
            await asyncio.sleep(CHECK_INTERVAL_SECS)

    # ── Internal tick logic ────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._paused:
            return
        if self._running:
            return  # a session is already in progress

        hour = datetime.now().hour
        if not (ACTIVE_HOURS_START <= hour < ACTIVE_HOURS_END):
            return  # outside active hours — don't start new sessions

        candidates = self._get_eligible()
        if not candidates:
            return

        # Fair pick: account that waited longest (oldest last_run_at, or
        # oldest enrolled_at if never run)
        def sort_key(item):
            entry = item[1]
            return entry.get("last_run_at") or entry.get("enrolled_at") or ""

        chosen_id = min(candidates, key=sort_key)[0]

        self._running = True
        self._state[chosen_id]["status"] = "running"
        self._save_state()

        t = threading.Thread(target=self._run_session_thread, args=(chosen_id,),
                             daemon=True)
        t.start()

    def _get_eligible(self) -> list:
        """Return [(account_id, entry), ...] for accounts ready to warm today."""
        today = str(date.today())
        eligible = []
        for aid, entry in self._state.items():
            if entry.get("status") != "waiting":
                continue
            if entry.get("last_run_date") == today:
                continue  # already done today
            eligible.append((aid, entry))
        return eligible

    def _run_session_thread(self, account_id: str) -> None:
        """Background thread — one session per phone, sequential via semaphore."""
        try:
            from core.account_store import get_account_store
            store   = get_account_store()
            mobile  = next((a for a in store.get_mobile_accounts()
                           if a["id"] == account_id), None)
            if not mobile:
                self._log.warning("Account %s not found for mobile session", account_id)
                self._running = False
                return

            # Acquire the warmup semaphore (same gate as manual warmup calls)
            import api.routers.mobile as _mobile_mod
            sem = _mobile_mod._warmup_semaphore
            sem.acquire()

            try:
                from activities.mobile_warmup import run_mobile_schedule_session
                from core.paths import LOGS_DIR
                log_file = LOGS_DIR / "mobile_sessions.json"

                result = run_mobile_schedule_session(
                    account=mobile,
                    log_file=log_file,
                    progress_callback=None,
                    schedule_state=self._state,
                )

                script = result.get("script", "?")
                if result.get("success"):
                    self.record_session_complete(account_id, script)
                    self._log.info("%s: %s session OK (day %d, duration %.0fs)",
                                   account_id, script,
                                   self._state.get(account_id, {}).get("schedule_day", 0),
                                   result.get("duration_s", 0))
                else:
                    self.record_session_error(account_id,
                                              result.get("error", "unknown error"))
                    self._log.warning("%s: session failed — %s",
                                     account_id, result.get("error"))
            finally:
                sem.release()
        except Exception as e:
            self._log.error("%s: session thread error: %s", account_id, e)
            self.record_session_error(account_id, str(e)[:200])
        finally:
            self._running = False
