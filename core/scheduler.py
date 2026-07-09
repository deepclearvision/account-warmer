"""
Account Scheduler

Maintains a set of accounts enrolled for continuous warming.
Enrolled accounts are run repeatedly between ACTIVE_HOURS_START (7am) and
ACTIVE_HOURS_END (10pm), with a minimum gap of MIN_SESSION_GAP_MINS between
consecutive sessions for the same account.

Scheduling is fair: the account that waited longest since its last session
is always chosen next. Up to MAX_CONCURRENT sessions can run simultaneously.

On error (non-zero exit from run.py), the account is automatically unenrolled
and its status set to 'error' so the dashboard can show it clearly.

State is persisted to logs/state/scheduler.json so enrolled accounts survive
a server restart (though any in-progress 'running' status is reset to 'waiting'
since the old process is gone).
"""

import subprocess
import sys
import json
import random
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from core.paths import LOGS_DIR, SCHEDULER_STATE_FILE as STATE_FILE, APP_DIR as ROOT

ACTIVE_HOURS_START   = 7    # 7am
ACTIVE_HOURS_END     = 22   # 10pm  (exclusive — won't START new sessions at 22:00+)
MIN_SESSION_GAP_MINS = 180  # 3 hours — absolute floor between sessions for the same account
MAX_CONCURRENT       = 4    # max accounts running simultaneously (raised from 2)
CHECK_INTERVAL_SECS  = 60   # how often the scheduler loop wakes up

# ── Anti-clustering measures ──────────────────────────────────────────────────
# Accounts don't launch the instant they become eligible.  A random jitter
# spreads them out so they don't all start (and therefore finish) together.
LAUNCH_JITTER_MIN_SEC = 120   # 2 minutes
LAUNCH_JITTER_MAX_SEC = 480   # 8 minutes

# When an account is first enrolled (or on server restart), assign it a
# random initial offset so not all accounts become eligible at the same time.
INITIAL_STAGGER_MAX_MINS = 180  # spread over 3 hours

# Per-account gap range applied ON TOP of the absolute MIN_SESSION_GAP_MINS.
# Each account gets a random target gap in this range when its session ends,
# so different accounts naturally run at different cadences.
PER_ACCOUNT_GAP_RANGE_MINS = (180, 720)  # 3-12 hours


_instance: Optional["SchedulerService"] = None


def get_scheduler() -> "SchedulerService":
    global _instance
    if _instance is None:
        _instance = SchedulerService()
    return _instance


class SchedulerService:

    def __init__(self):
        # Persistent state: account_id → entry dict (survives restarts via JSON)
        self._state: dict[str, dict] = {}
        # In-memory running processes: account_id → subprocess.Popen
        self._procs: dict[str, subprocess.Popen] = {}
        # Paused state — persisted so it survives server restarts
        self._paused: bool = True
        # Configurable concurrency limit (persisted in state file)
        self._max_concurrent: int = MAX_CONCURRENT
        self._log = get_logger("scheduler")
        self._load_state()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self._state = data.get("accounts", {})
                # Restore persisted paused state (default True if never saved)
                self._paused = bool(data.get("paused", True))
                # Restore persisted concurrency setting
                self._max_concurrent = int(data.get("max_concurrent", MAX_CONCURRENT))
                now = datetime.now()
                now_iso = now.isoformat()
                # Reset all non-waiting accounts on startup:
                # - "running" → kill the orphaned detached process, reset to waiting
                # - "error"   → reset to waiting so accounts get another chance
                # Assign stagger offsets so accounts don't all become eligible
                # at the same instant (prevents the startup avalanche).
                stagger_idx = 0
                for entry in self._state.values():
                    if entry.get("status") == "running":
                        pid = entry.get("pid")
                        if pid:
                            try:
                                if sys.platform == "win32":
                                    import subprocess as _sp
                                    _sp.run(
                                        ["taskkill", "/PID", str(pid), "/F"],
                                        capture_output=True,
                                    )
                                else:
                                    import os as _os, signal as _signal
                                    _os.kill(pid, _signal.SIGTERM)
                            except Exception:
                                pass
                        entry["status"]      = "waiting"
                    elif entry.get("status") == "error":
                        # Give errored accounts another attempt after restart
                        entry["status"]    = "waiting"
                        entry["error_msg"] = None
                    # Stagger: each account gets a different next-eligible time
                    if entry["status"] == "waiting":
                        stagger_mins = (stagger_idx * 7 + random.randint(0, 15)) % INITIAL_STAGGER_MAX_MINS
                        entry["next_eligible_after"] = (
                            now + timedelta(minutes=stagger_mins)
                        ).isoformat()
                        stagger_idx += 1
                    # Only preserve existing last_run_at — do NOT fabricate one.
                    # Setting it to now would block every account for
                    # MIN_SESSION_GAP_MINS after every server restart.
                    if "last_run_at" not in entry:
                        entry["last_run_at"] = None
            except Exception:
                self._state = {}

    def _save_state(self) -> None:
        try:
            STATE_FILE.write_text(
                json.dumps({
                    "accounts":       self._state,
                    "paused":         self._paused,
                    "max_concurrent": self._max_concurrent,
                    "saved_at":       datetime.now().isoformat(),
                }, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    def enroll(self, account_id: str) -> dict:
        """Enrol an account for continuous scheduling.
        If already enrolled but stopped/errored, resets it back to waiting.
        New accounts get a random stagger offset so they don't all start
        simultaneously."""
        now = datetime.now()
        if account_id not in self._state:
            stagger_mins = random.randint(0, INITIAL_STAGGER_MAX_MINS)
            self._state[account_id] = {
                "status":              "waiting",
                "enrolled_at":         now.isoformat(),
                "last_run_at":         None,
                "last_status":         None,
                "error_msg":           None,
                "next_eligible_after": (
                    now + timedelta(minutes=stagger_mins)
                ).isoformat(),
            }
            self._log.info(
                f"Enrolled {account_id} (stagger +{stagger_mins} min)"
            )
        elif self._state[account_id].get("status") == "error":
            # Clear the error so the scheduler will pick it up again
            self._state[account_id]["status"]    = "waiting"
            self._state[account_id]["error_msg"] = None
            self._state[account_id]["next_eligible_after"] = now.isoformat()
        self._save_state()
        return self._public_entry(account_id)

    def unenroll(self, account_id: str) -> bool:
        """Remove an account from the scheduler. Kills any active session."""
        if account_id not in self._state:
            return False
        self._kill(account_id)
        del self._state[account_id]
        self._save_state()
        return True

    def get_status(self) -> dict:
        """Return public status for all enrolled accounts."""
        self._reap()
        return {
            "paused":         self._paused,
            "max_concurrent": self._max_concurrent,
            "accounts":       {aid: self._public_entry(aid) for aid in self._state},
        }

    def is_enrolled(self, account_id: str) -> bool:
        return account_id in self._state

    def pause(self) -> None:
        """Pause auto-scheduling. In-progress sessions continue to completion."""
        self._paused = True
        self._save_state()

    def resume(self) -> None:
        """Resume auto-scheduling."""
        self._paused = False
        self._save_state()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_max_concurrent(self, n: int) -> None:
        """Change the maximum number of simultaneous browser sessions (1–5)."""
        self._max_concurrent = max(1, min(5, int(n)))
        self._save_state()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    # ── Background loop ────────────────────────────────────────────────────────

    async def run_loop(self) -> None:
        """Asyncio task — runs forever while the FastAPI server is up."""
        while True:
            try:
                self._tick()
            except Exception:
                pass
            await asyncio.sleep(CHECK_INTERVAL_SECS)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _reap(self) -> None:
        """Check all tracked processes for completion; update state."""
        for acc_id in list(self._procs):
            proc = self._procs[acc_id]
            rc   = proc.poll()      # non-blocking; None = still running
            if rc is None:
                continue
            # Process finished
            del self._procs[acc_id]
            if acc_id not in self._state:
                continue
            now = datetime.now()
            self._state[acc_id]["last_run_at"] = now.isoformat()
            if rc == 0:
                # Success — assign a random per-account gap before next run.
                # This is key: different accounts get different gaps (3–12 h),
                # so they naturally drift apart over time instead of clustering.
                gap_mins = random.randint(*PER_ACCOUNT_GAP_RANGE_MINS)
                self._state[acc_id]["next_eligible_after"] = (
                    now + timedelta(minutes=gap_mins)
                ).isoformat()
                self._state[acc_id]["status"]      = "waiting"
                self._state[acc_id]["last_status"] = "ok"
                self._state[acc_id]["error_msg"]   = None
                self._log.debug(
                    f"{acc_id}: session ok — next eligible in {gap_mins} min "
                    f"({gap_mins/60:.1f} h)"
                )
            else:
                # Non-zero exit — mark error and analyse logs for fix proposals.
                # Give a shorter gap so it retries sooner after investigation.
                retry_mins = random.randint(60, 120)
                self._state[acc_id]["next_eligible_after"] = (
                    now + timedelta(minutes=retry_mins)
                ).isoformat()
                self._state[acc_id]["status"]      = "error"
                self._state[acc_id]["last_status"] = "error"
                self._state[acc_id]["error_msg"]   = f"run.py exited with code {rc}"
                self._run_analyser(acc_id)

        # Also clean up any accounts that show 'running' in state but whose
        # PID is no longer alive (can happen if the server restarted mid-session
        # and the kill in _load_state didn't catch them in time).
        for acc_id, entry in self._state.items():
            if entry.get("status") != "running":
                continue
            if acc_id in self._procs:
                continue  # tracked above
            pid = entry.get("pid")
            alive = False
            if pid:
                try:
                    if sys.platform == "win32":
                        import subprocess as _sp
                        r = _sp.run(
                            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                            capture_output=True, text=True,
                        )
                        alive = str(pid) in r.stdout
                    else:
                        import os as _os
                        _os.kill(pid, 0)
                        alive = True
                except Exception:
                    alive = False
            if not alive:
                entry["status"]      = "waiting"
                entry["last_run_at"] = datetime.now().isoformat()

        self._save_state()

    def _run_analyser(self, account_id: str) -> None:
        """Analyse errors for one account after a failed session (non-blocking)."""
        try:
            import threading
            from core.error_analyser import analyse_account
            # Get profile_id from scheduler state if available
            profile_id = self._state.get(account_id, {}).get("profile_id", "")
            threading.Thread(
                target=analyse_account,
                args=(account_id, profile_id),
                daemon=True,
            ).start()
        except Exception:
            pass

    def _tick(self) -> None:
        """One scheduler tick: reap finished processes then start the next eligible account."""
        self._reap()

        # Globally paused — reap only, no new launches
        if self._paused:
            return

        now  = datetime.now()
        hour = now.hour

        # Outside active window — do nothing
        if not (ACTIVE_HOURS_START <= hour < ACTIVE_HOURS_END):
            return

        # Already at concurrent limit (count all browser sessions across every component)
        total_running = len(self._procs)
        try:
            from api.routers.runner import _jobs
            manual_running = sum(1 for j in _jobs.values() if j["status"] == "running")
            total_running += manual_running
        except Exception:
            manual_running = 0
        try:
            from api.routers.trust import _trust_jobs
            trust_running = sum(
                1 for j in _trust_jobs.values()
                if j.get("status") == "running" and j["proc"].poll() is None
            )
            total_running += trust_running
        except Exception:
            trust_running = 0
        try:
            from api.routers.login_check import _login_jobs
            login_running = sum(
                1 for j in _login_jobs.values()
                if j.get("status") == "running" and j["proc"].poll() is None
            )
            total_running += login_running
        except Exception:
            login_running = 0
        if total_running >= self._max_concurrent:
            return

        # Find accounts eligible to run.
        # Eligibility = waited past both:
        #   1. The absolute minimum gap (MIN_SESSION_GAP_MINS)
        #   2. The account's own next_eligible_after timestamp (set when
        #      its last session ended, includes a random per-account gap)
        candidates = []
        skipped_stagger = 0
        skipped_gap = 0
        for acc_id, entry in self._state.items():
            if acc_id in self._procs:
                continue                          # already running
            if entry.get("status") in ("running", "error"):
                continue                          # skip errored accounts

            # Check next_eligible_after (per-account stagger)
            next_eligible = entry.get("next_eligible_after")
            if next_eligible:
                eligible_at = datetime.fromisoformat(next_eligible)
                if now < eligible_at:
                    skipped_stagger += 1
                    continue

            # Check absolute minimum gap
            last = entry.get("last_run_at")
            if last:
                elapsed_mins = (now - datetime.fromisoformat(last)).total_seconds() / 60
                if elapsed_mins < MIN_SESSION_GAP_MINS:
                    skipped_gap += 1
                    continue

            candidates.append((acc_id, entry.get("last_run_at") or ""))

        if not candidates:
            if skipped_stagger or skipped_gap:
                self._log.debug(
                    f"No eligible accounts — "
                    f"{skipped_stagger} still staggered, "
                    f"{skipped_gap} too soon, "
                    f"{total_running}/{self._max_concurrent} running"
                )
            return

        # Prioritise the account that has waited the longest (oldest last_run_at)
        candidates.sort(key=lambda x: x[1])
        chosen_id = candidates[0][0]

        # ── Launch jitter ──────────────────────────────────────────────────
        # Don't launch immediately — add a random delay so accounts that
        # become eligible around the same time don't all start together.
        # This is the single most effective anti-clustering measure.
        jitter_sec = random.randint(LAUNCH_JITTER_MIN_SEC, LAUNCH_JITTER_MAX_SEC)
        eligible_at = datetime.fromisoformat(
            self._state[chosen_id].get("next_eligible_after")
            or self._state[chosen_id].get("last_run_at")
            or datetime.now().isoformat()
        )
        waited_sec = (now - eligible_at).total_seconds()
        if waited_sec < jitter_sec:
            remaining_jitter = jitter_sec - waited_sec
            self._log.debug(
                f"{chosen_id}: eligible but waiting {remaining_jitter:.0f}s "
                f"jitter before launch (waited {waited_sec:.0f}s, "
                f"need {jitter_sec}s)"
            )
            return

        self._log.info(
            f"Spawning {chosen_id} — waited {waited_sec/60:.0f} min, "
            f"{len(candidates)} eligible, {total_running}/{self._max_concurrent} running"
        )
        self._spawn(chosen_id)

    def _spawn(self, account_id: str) -> None:
        """Launch run.py for the given account as a detached subprocess."""
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"{account_id}.log"
        cmd      = [sys.executable, str(ROOT / "run.py"), "--account", account_id]

        kwargs: dict = {"cwd": str(ROOT)}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP |
                subprocess.DETACHED_PROCESS
            )

        try:
            log_fh = open(log_path, "a", encoding="utf-8")
            proc   = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_fh,
                **kwargs,
            )
            log_fh.close()
        except Exception as e:
            self._state[account_id]["status"]    = "error"
            self._state[account_id]["error_msg"] = f"Spawn failed: {e}"
            self._save_state()
            return

        self._procs[account_id]              = proc
        self._state[account_id]["status"]    = "running"
        self._state[account_id]["pid"]       = proc.pid
        self._state[account_id]["error_msg"] = None
        self._save_state()

    def _kill(self, account_id: str) -> None:
        """Terminate any running process for this account."""
        proc = self._procs.pop(account_id, None)
        if not proc:
            return
        try:
            if sys.platform == "win32":
                import signal
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
        except Exception:
            pass

    def _public_entry(self, account_id: str) -> dict:
        entry = self._state.get(account_id, {})
        proc  = self._procs.get(account_id)
        return {
            "account_id":  account_id,
            "status":      entry.get("status", "waiting"),
            "enrolled_at": entry.get("enrolled_at"),
            "last_run_at": entry.get("last_run_at"),
            "last_status": entry.get("last_status"),
            "error_msg":   entry.get("error_msg"),
            "pid":         proc.pid if proc else None,
        }
