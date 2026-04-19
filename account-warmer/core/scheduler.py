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
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional


from core.paths import LOGS_DIR, SCHEDULER_STATE_FILE as STATE_FILE, APP_DIR as ROOT

ACTIVE_HOURS_START   = 7    # 7am
ACTIVE_HOURS_END     = 22   # 10pm  (exclusive — won't START new sessions at 22:00+)
MIN_SESSION_GAP_MINS = 180  # 3 hours between sessions for the same account
MAX_CONCURRENT       = 2    # max accounts running simultaneously
CHECK_INTERVAL_SECS  = 60   # how often the scheduler loop wakes up


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
        # Always start paused — operator must explicitly resume after every restart
        self._paused: bool = True
        # Configurable concurrency limit (persisted in state file)
        self._max_concurrent: int = MAX_CONCURRENT
        self._load_state()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self._state = data.get("accounts", {})
                # Restore persisted concurrency setting
                self._max_concurrent = int(data.get("max_concurrent", MAX_CONCURRENT))
                now_iso = datetime.now().isoformat()
                # Any account marked 'running' from a previous session is now
                # orphaned (its process is dead). Reset to 'waiting' and stamp
                # last_run_at = now so it must wait the full gap before running
                # again (prevents immediate re-launch on server restart).
                for entry in self._state.values():
                    if entry.get("status") == "running":
                        entry["status"]      = "waiting"
                        entry["last_run_at"] = now_iso
            except Exception:
                self._state = {}

    def _save_state(self) -> None:
        try:
            STATE_FILE.write_text(
                json.dumps({
                    "accounts":       self._state,
                    "max_concurrent": self._max_concurrent,
                    "saved_at":       datetime.now().isoformat(),
                }, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    def enroll(self, account_id: str) -> dict:
        """Enrol an account for continuous scheduling. Idempotent."""
        if account_id not in self._state:
            self._state[account_id] = {
                "status":      "waiting",
                "enrolled_at": datetime.now().isoformat(),
                "last_run_at": None,
                "last_status": None,
                "error_msg":   None,
            }
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

    def resume(self) -> None:
        """Resume auto-scheduling."""
        self._paused = False

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
            self._state[acc_id]["last_run_at"] = datetime.now().isoformat()
            if rc == 0:
                self._state[acc_id]["status"]      = "waiting"
                self._state[acc_id]["last_status"] = "ok"
                self._state[acc_id]["error_msg"]   = None
            else:
                # Non-zero exit — mark error and analyse logs for fix proposals
                self._state[acc_id]["status"]      = "error"
                self._state[acc_id]["last_status"] = "error"
                self._state[acc_id]["error_msg"]   = f"run.py exited with code {rc}"
                self._run_analyser(acc_id)
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
        try:
            from api.routers.runner import _jobs
            manual_running = sum(1 for j in _jobs.values() if j["status"] == "running")
        except Exception:
            manual_running = 0
        try:
            from api.routers.trust import _trust_jobs
            trust_running = sum(
                1 for j in _trust_jobs.values()
                if j.get("status") == "running" and j["proc"].poll() is None
            )
        except Exception:
            trust_running = 0
        try:
            from api.routers.login_check import _login_jobs
            login_running = sum(
                1 for j in _login_jobs.values()
                if j.get("status") == "running" and j["proc"].poll() is None
            )
        except Exception:
            login_running = 0
        if len(self._procs) + manual_running + trust_running + login_running >= self._max_concurrent:
            return

        # Find accounts eligible to run
        candidates = []
        for acc_id, entry in self._state.items():
            if acc_id in self._procs:
                continue                                    # already running
            if entry.get("status") in ("running", "error"):
                continue                                    # skip errored accounts
            last = entry.get("last_run_at")
            if last:
                elapsed_mins = (now - datetime.fromisoformat(last)).total_seconds() / 60
                if elapsed_mins < MIN_SESSION_GAP_MINS:
                    continue                                # too soon
            candidates.append((acc_id, last or ""))

        if not candidates:
            return

        # Prioritise the account that has waited the longest (oldest last_run_at)
        candidates.sort(key=lambda x: x[1])
        self._spawn(candidates[0][0])

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
