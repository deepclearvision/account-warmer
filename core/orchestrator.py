"""
Orchestrator
Controls the warm-up schedule for a single account:
- Determines which week of warming the account is in
- Decides how many and which activities to run today
- Respects active_hours (no activity while the user "sleeps")
- Tracks per-day email send counts to avoid hitting limits
"""

import asyncio
import random
import json
import os
from datetime import datetime, date
from pathlib import Path
import yaml

from core.profile_manager import ProfileSession
from core.logger import get_logger


from core.paths import (
    SCHEDULE_FILE, STRATEGIES_FILE, BEHAVIOUR_FILE,
    BUSINESSES_FILE, STATE_DIR, LOGS_DIR,
    ACCOUNT_BUSINESS_MAP_FILE,
)


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_businesses() -> dict:
    """Return a dict of business_id -> business config."""
    if not BUSINESSES_FILE.exists():
        return {}
    data = _load_yaml(BUSINESSES_FILE)
    return {b["id"]: b for b in data.get("businesses", [])}


def _load_account_business_map() -> dict:
    """Return a dict of account_id -> list of business_ids from the mapping file."""
    if not ACCOUNT_BUSINESS_MAP_FILE.exists():
        return {}
    data = _load_yaml(ACCOUNT_BUSINESS_MAP_FILE)
    return {
        m["account_id"]: m.get("business_ids", [])
        for m in data.get("mappings", [])
        if m.get("account_id")
    }


def _get_week_config(schedule: dict, weeks_elapsed: int) -> dict:
    if weeks_elapsed < 2:
        return schedule["weeks_1_2"]
    elif weeks_elapsed < 4:
        return schedule["weeks_3_4"]
    elif weeks_elapsed < 6:
        return schedule["weeks_5_6"]
    else:
        return schedule["weeks_7_plus"]


class AccountOrchestrator:

    def __init__(self, account: dict, all_accounts: list, dry_run: bool = False,
                 force_week: int | None = None, force_activity: str | None = None):
        self.account        = account
        self.all_accounts   = all_accounts
        self.dry_run        = dry_run
        self.force_week     = force_week
        self.force_activity = force_activity
        self.log            = get_logger(account["id"])
        # Strategy resolution: strategies.yaml → schedule.yaml fallback
        strategy = account.get("strategy", "standard")
        if strategy and strategy != "standard" and STRATEGIES_FILE.exists():
            all_strategies = _load_yaml(STRATEGIES_FILE)
            resolved = all_strategies.get(strategy)
            if resolved is None:
                self.log.warning(f"Unknown strategy {strategy!r} — falling back to standard schedule")
                self.schedule = _load_yaml(SCHEDULE_FILE)
            else:
                self.schedule = resolved
        else:
            self.schedule = _load_yaml(SCHEDULE_FILE)
        self.behaviour      = _load_yaml(BEHAVIOUR_FILE)
        self._all_businesses = _load_businesses()
        self._account_biz_map = _load_account_business_map()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_file    = STATE_DIR / f"{account['id']}.json"
        self._state         = self._load_state()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run_session(self) -> None:
        account_id = self.account["id"]

        if not self._within_active_hours():
            self.log.info("Outside active hours — skipping session.")
            return

        weeks_elapsed = self._weeks_since_start()
        if self.force_week is not None:
            weeks_elapsed = self.force_week

        week_cfg = _get_week_config(self.schedule, weeks_elapsed)
        self.log.info(
            f"Week {weeks_elapsed + 1} — phase: {week_cfg['label']} — "
            f"account: {account_id}"
        )

        # Maybe skip today entirely (only on first session of the day)
        sessions_today = self._count_sessions_today()
        skip_prob = week_cfg.get("skip_day_probability", 0.10)
        if sessions_today == 0 and random.random() < skip_prob and self.force_week is None:
            self.log.info("Random day off — no activity today.")
            return

        # Choose activities for this session
        activities = self._choose_activities(week_cfg)

        # Inject business signal activity if this account has target businesses
        # and the account is past week 1 (don't start signals on day 1)
        if weeks_elapsed >= 1:
            biz_activity = self._get_pending_business_signal()
            if biz_activity and not any(a.startswith("business_signal:") for a in activities):
                # Insert business signal naturally mid-session
                insert_at = random.randint(0, len(activities))
                activities.insert(insert_at, f"business_signal:{biz_activity}")

        if not activities:
            self.log.info("No activities selected for this session.")
            return

        self.log.info(f"Planned activities: {activities}")

        if self.dry_run:
            self.log.info("[DRY RUN] Would have executed: " + str(activities))
            return

        # Determine if a business is being targeted this session
        # (so profile_manager can spoof geolocation near that business)
        pending_biz_id = self._get_pending_business_signal()
        active_business = (
            self._all_businesses.get(pending_biz_id) if pending_biz_id else None
        )

        # ── Session recording setup ─────────────────────────────────────────
        _session_start = datetime.now()
        _session_acts  = []
        _session_ok    = True

        try:
            # Run the session inside a Multilogin profile
            async with ProfileSession(
                profile_id  = self.account["multilogin_profile_id"],
                folder_id   = self.account.get("multilogin_folder_id"),
                account_id  = account_id,
                timeout     = self.schedule.get("multilogin", {}).get("start_timeout_seconds", 60),
                account     = self.account,
                business    = active_business,
            ) as page:
                emails_sent_today = self._state.get("emails_sent_today", {}).get(
                    str(date.today()), 0
                )

                for activity_name in activities:
                    if page.is_closed():
                        self.log.warning("Browser page closed unexpectedly — ending session early")
                        break

                    self.log.info(f"Running activity: {activity_name}")
                    success = await self._run_activity(
                        activity_name, page, emails_sent_today
                    )
                    _session_acts.append(activity_name)
                    # Record the activity in the structured activity log.
                    # search and business_signal log their own richer events from within
                    # the activity itself (with search term / phase / business detail).
                    _skip_log = activity_name.startswith("business_signal:") or activity_name == "search"
                    if not _skip_log:
                        try:
                            from core.activity_log import log_event as _log_ev
                            _log_ev(
                                account_id    = account_id,
                                device        = "desktop",
                                activity_type = activity_name,
                                detail        = {"success": success},
                            )
                        except Exception:
                            pass
                    if not success:
                        _session_ok = False

                    if activity_name == "email_send" and success:
                        emails_sent_today += 1
                        self._record_email_sent()

                    if activity_name != activities[-1]:
                        await asyncio.sleep(random.uniform(10, 45))

                cooldown = random.uniform(
                    *self.schedule.get("session_cooldown_seconds", [30, 120])
                )
                self.log.info(f"Session cooldown: {cooldown:.0f}s")
                await asyncio.sleep(cooldown)

        except Exception:
            _session_ok = False
            raise
        finally:
            self._record_session(_session_start, _session_acts, _session_ok)

    # ------------------------------------------------------------------
    # Activity runner
    # ------------------------------------------------------------------

    async def _run_activity(self, name: str, page, emails_sent_today: int) -> bool:
        from activities.search      import SearchActivity
        from activities.email_read  import EmailReadActivity
        from activities.email_send  import EmailSendActivity
        from activities.browse      import BrowseActivity

        try:
            if name == "search":
                return await SearchActivity(page, self.account, self.behaviour).run()
            elif name == "email_read":
                return await EmailReadActivity(page, self.account, self.behaviour).run()
            elif name == "email_send":
                return await EmailSendActivity(
                    page, self.account, self.behaviour,
                    self.all_accounts, emails_sent_today
                ).run()
            elif name == "browse":
                return await BrowseActivity(page, self.account, self.behaviour).run()
            elif name == "newsletter_signup":
                from activities.newsletter_signup import NewsletterSignupActivity
                return await NewsletterSignupActivity(page, self.account, self.behaviour).run()
            elif name == "maps_browse":
                from activities.maps import MapsActivity
                return await MapsActivity(page, self.account, self.behaviour).run()
            elif name == "calendar_setup":
                from activities.calendar import CalendarActivity
                return await CalendarActivity(page, self.account, self.behaviour, mode="setup").run()
            elif name == "calendar_browse":
                from activities.calendar import CalendarActivity
                return await CalendarActivity(page, self.account, self.behaviour, mode="browse").run()
            elif name == "calendar_add":
                from activities.calendar import CalendarActivity
                return await CalendarActivity(page, self.account, self.behaviour, mode="add_event").run()
            elif name == "drive_setup":
                from activities.drive import DriveActivity
                return await DriveActivity(page, self.account, self.behaviour, mode="setup").run()
            elif name == "drive_browse":
                from activities.drive import DriveActivity
                return await DriveActivity(page, self.account, self.behaviour, mode="browse").run()
            elif name == "drive_edit":
                from activities.drive import DriveActivity
                return await DriveActivity(page, self.account, self.behaviour, mode="edit_doc").run()
            elif name == "drive_create":
                from activities.drive import DriveActivity
                return await DriveActivity(page, self.account, self.behaviour, mode="create_doc").run()
            elif name.startswith("business_signal:"):
                biz_id = name.split(":", 1)[1]
                return await self._run_business_signal(page, biz_id)
            else:
                self.log.warning(f"Unknown activity: {name}")
                return False
        except Exception as e:
            self.log.error(f"Activity {name!r} raised an exception: {e}")
            return False

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------

    def _get_target_businesses(self) -> list[str]:
        """Return merged target businesses from inline config + mapping file."""
        inline = self.account.get("target_businesses", []) or []
        mapped = self._account_biz_map.get(self.account["id"], [])
        # Preserve order: inline first, then mapped extras, deduplicated
        seen = set()
        result = []
        for biz_id in inline + mapped:
            if biz_id not in seen:
                seen.add(biz_id)
                result.append(biz_id)
        return result

    def _get_pending_business_signal(self) -> str | None:
        """
        Return the business_id that needs a signal run this session, or None.
        Only one business signal runs per session. Prioritises incomplete phases.
        """
        from core.orchestrator import STATE_DIR
        target_ids = self._get_target_businesses()
        if not target_ids:
            return None

        for biz_id in target_ids:
            biz = self._all_businesses.get(biz_id)
            if not biz:
                continue
            state_file = STATE_DIR / f"{self.account['id']}_biz_{biz_id}.json"
            if not state_file.exists():
                return biz_id  # Phase 1 not started yet

            try:
                with open(state_file, encoding="utf-8") as f:
                    biz_state = json.load(f)
            except Exception:
                return biz_id

            if not biz_state.get("phase_1_complete"):
                return biz_id
            if not biz_state.get("phase_2_complete"):
                # Don't run phase 2 same day as phase 1
                p1_date = biz_state.get("phase_1_date", "")
                if p1_date and p1_date != str(date.today()):
                    return biz_id
            if biz_state.get("phase_2_complete") and not biz_state.get("phase_3_complete"):
                appt_date_str = biz_state.get("appointment_date")
                if appt_date_str:
                    appt_date = date.fromisoformat(appt_date_str)
                    if date.today() >= appt_date:
                        p2_date = biz_state.get("phase_2_date", "")
                        if p2_date and p2_date != str(date.today()):
                            return biz_id
        return None

    async def _run_business_signal(self, page, biz_id: str) -> bool:
        """Run the business signal activity for the given business ID."""
        from activities.business_signal import BusinessSignalActivity
        biz = self._all_businesses.get(biz_id)
        if not biz:
            self.log.warning(f"Business not found: {biz_id}")
            return False
        self.log.info(f"Running business signal for: {biz['name']!r}")
        activity = BusinessSignalActivity(page, self.account, self.behaviour, biz)
        return await activity.run()

    # Mobile step name → desktop activity name mapping
    _MOBILE_TO_DESKTOP = {
        "maps":             "maps_browse",
        "maps_directions":  "maps_browse",
        "google_search":    "search",
        "gmail":            "email_read",
        "youtube":          "browse",
    }

    def _get_mobile_done_today(self) -> set[str]:
        """
        Return desktop activity names already performed today on this account's
        paired GeelarK phone.  Delegates to AccountStore which encapsulates all
        the file I/O.
        """
        from core.account_store import get_account_store
        account_email = self.account.get("email", "")
        done = get_account_store().get_mobile_activities_done_today(account_email)
        if done:
            self.log.info(f"[mobile-coord] Mobile already did today: {done}")
        return done

    def _choose_activities(self, week_cfg: dict) -> list[str]:
        if self.force_activity:
            if self.force_activity == "business_signal":
                # Resolve to the pending business (or force the first configured one)
                pending = self._get_pending_business_signal()
                if pending:
                    return [f"business_signal:{pending}"]
                target_ids = self._get_target_businesses()
                if target_ids:
                    return [f"business_signal:{target_ids[0]}"]
                self.log.warning("No target businesses configured for this account")
                return []
            return [self.force_activity]

        allowed   = week_cfg.get("allowed_activities", ["search"])
        weights_d = dict(week_cfg.get("activity_weights", {a: 1.0 for a in allowed}))

        # De-prioritise activities already done on the paired mobile phone today
        mobile_done = self._get_mobile_done_today()
        if mobile_done:
            for act in mobile_done:
                if act in weights_d:
                    weights_d[act] = weights_d[act] * 0.3

        # Real users have a PURPOSE for each session rather than random activity salads.
        # Pick a session focus then draw mostly from that group.
        focus_pools = {
            "search_focus":  ["search", "browse"],
            "email_focus":   ["email_read", "email_send", "calendar_browse", "calendar_add"],
            "maps_focus":    ["maps_browse"],
            "productivity":  ["drive_browse", "drive_edit", "drive_create",
                              "calendar_browse", "calendar_add", "email_read"],
            "mixed":         allowed,
        }
        focus_type = random.choices(
            list(focus_pools.keys()),
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
            k=1
        )[0]
        # Only keep focus activities that are unlocked this week
        focus_allowed = [a for a in focus_pools[focus_type] if a in allowed]
        if not focus_allowed:
            focus_allowed = allowed  # nothing in focus pool is unlocked yet

        num_actions_range = week_cfg.get("actions_per_day", [1, 3])
        num_actions = random.randint(*num_actions_range)
        # Cap per-session activities so warming is split into smaller slots
        max_per_session = week_cfg.get("max_session_activities")
        if max_per_session and num_actions > max_per_session:
            num_actions = max_per_session

        chosen  = []
        seen    = set()
        attempts = 0
        while len(chosen) < num_actions and attempts < num_actions * 4:
            # 70% draw from focus pool, 30% from full allowed list (occasional detour)
            pool = focus_allowed if random.random() < 0.70 else allowed
            pool_weights = [weights_d.get(a, 0.1) for a in pool]
            pick = random.choices(pool, weights=pool_weights, k=1)[0]
            # Only one email_send / email_read / browse per session
            if pick in ("email_send", "email_read", "browse") and pick in seen:
                attempts += 1
                continue
            chosen.append(pick)
            seen.add(pick)
            attempts += 1

        # Shuffle so searches and email are interleaved (more natural)
        random.shuffle(chosen)
        return chosen

    def _weeks_since_start(self) -> int:
        start_str = self.account.get("warmup_start_date", str(date.today()))
        start     = date.fromisoformat(start_str)
        delta     = (date.today() - start).days
        return max(0, delta // 7)

    def _within_active_hours(self) -> bool:
        active = self.account.get("active_hours", [7, 23])
        hour   = datetime.now().hour  # Uses local machine time; adjust if running across timezones
        return active[0] <= hour <= active[1]

    # ------------------------------------------------------------------
    # State persistence (tracks email sends per day)
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

    def _record_email_sent(self) -> None:
        key = str(date.today())
        if "emails_sent_today" not in self._state:
            self._state["emails_sent_today"] = {}
        self._state["emails_sent_today"][key] = (
            self._state["emails_sent_today"].get(key, 0) + 1
        )
        self._save_state()

    def _record_session(self, start: datetime, activities: list, success: bool) -> None:
        """Persist a session record for time-warmed and last-action reporting."""
        end = datetime.now()
        record = {
            "start":      start.isoformat(),
            "end":        end.isoformat(),
            "duration_s": int((end - start).total_seconds()),
            "activities": activities,
            "success":    success,
        }
        session_file = STATE_DIR / f"{self.account['id']}_sessions.json"
        try:
            history = json.loads(session_file.read_text()) if session_file.exists() else []
        except Exception:
            history = []
        history.append(record)
        history = history[-200:]  # keep last 200 sessions
        session_file.write_text(json.dumps(history, indent=2))

    def _count_sessions_today(self) -> int:
        """Count how many sessions have run today for this account."""
        session_file = STATE_DIR / f"{self.account['id']}_sessions.json"
        try:
            history = json.loads(session_file.read_text()) if session_file.exists() else []
        except Exception:
            return 0
        today_str = str(date.today())
        return sum(1 for s in history if s.get("start", "").startswith(today_str))
