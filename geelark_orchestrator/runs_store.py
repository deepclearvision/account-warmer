"""
runs_store.py — Persist runs + outcomes to SQLite (queryable history from day one).

PURE-ish: only touches a local SQLite file. No Geelark, no network.

Three things this gives you:
  1. record_run()      — save a resolved plan BEFORE triggering (inputs + seed).
  2. finish_run()      — fill in run health AFTER execution (status, duration, etc).
  3. record_outcome()  — your manual verdict, at run-level OR profile-level.

Plus the two queries that matter:
  - history(profile_key)     — "what has this phone done?" (the re-run decision view)
  - success_profile()        — descriptive aggregation over winning runs

Everything is reproducible: the seed is stored, so any run can be replayed by
feeding it back to resolver.resolve(profile, seed=...).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunsStore:
    def __init__(self, db_path: str, schema_path: str | None = None):
        self.db_path = db_path
        first_time = not os.path.exists(db_path) or os.path.getsize(db_path) == 0
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        if first_time and schema_path:
            with open(schema_path, encoding="utf-8") as f:
                self.conn.executescript(f.read())
            self.conn.commit()
        else:
            # Migration: add GPS evidence columns if missing (Section 9.12)
            self._migrate_runs_table()

    def _migrate_runs_table(self) -> None:
        """Add evidence columns if missing (Section 9.12)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(runs)")}
        if "gps_verified" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN gps_verified INTEGER DEFAULT 0")
        if "gps_dumpsys_coords" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN gps_dumpsys_coords TEXT")
        if "maps_task_status" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN maps_task_status INTEGER")
        if "maps_foreground" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN maps_foreground TEXT")
        if "search_submitted" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN search_submitted INTEGER DEFAULT 0")
        if "business_found" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN business_found INTEGER DEFAULT 0")
        if "business_name_matched" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN business_name_matched TEXT")
        if "interactions_present" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN interactions_present TEXT")
        if "card_opened" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN card_opened INTEGER DEFAULT 0")
        if "interaction_ip" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN interaction_ip TEXT")
        self.conn.commit()


    # ---- profiles (optional convenience: mirror CSV into the DB) ----------
    def upsert_profile(self, p) -> None:
        self.conn.execute(
            """INSERT INTO profiles
               (profile_key, geelark_profile_id, business_name, home_lat, home_lng,
                business_lat, business_lng, nearby_points, nearby_points_backup,
                onsite_points, branded_keywords, search_keywords, business_goal,
                profile_verdict, provisioned, maps_verified)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(profile_key) DO UPDATE SET
                 geelark_profile_id=excluded.geelark_profile_id,
                 business_name=excluded.business_name,
                 provisioned=excluded.provisioned,
                 maps_verified=excluded.maps_verified""",
            (
                p.profile_key, p.geelark_profile_id, p.business_name,
                p.home.lat, p.home.lng, p.business.lat, p.business.lng,
                "|".join(c.as_token() for c in p.nearby_points),
                "|".join(c.as_token() for c in p.nearby_points_backup),
                "|".join(c.as_token() for c in p.onsite_points),
                "|".join(p.branded_keywords), "|".join(p.search_keywords),
                p.business_goal, p.profile_verdict,
                int(p.provisioned), int(p.maps_verified),
            ),
        )
        self.conn.commit()

    # ---- runs -------------------------------------------------------------
    def record_run(self, run_plan) -> None:
        """Save the resolved plan BEFORE triggering the flow."""
        self.conn.execute(
            """INSERT INTO runs
               (run_id, profile_key, created_at, rng_seed, gps_lat, gps_lng,
                search_term_used, branded_term_used, fallback_taken, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_plan.run_id, run_plan.profile_key, _ts(), run_plan.rng_seed,
                run_plan.gps_lat, run_plan.gps_lng, run_plan.search_term,
                run_plan.branded_term,
                ",".join(run_plan.fallbacks_taken) if run_plan.fallbacks_taken else None,
                "skipped_data_error" if run_plan.abort else "pending",
            ),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, status: str, duration_seconds: float | None = None,
                   interactions_fired: str | None = None, failed_step: str | None = None,
                   screenshot_url: str | None = None,
                   gps_verified: int = 0, gps_dumpsys_coords: str | None = None,
                   maps_task_status: int | None = None, maps_foreground: str | None = None,
                   search_submitted: int = 0, business_found: int = 0,
                   business_name_matched: str | None = None,
                   card_opened: int = 0,
                   interactions_present: str | None = None,
                   interaction_ip: str | None = None) -> None:
        """Fill in run health AFTER execution (from the Geelark result + uiautomator evidence)."""
        self.conn.execute(
            """UPDATE runs SET status=?, duration_seconds=?, interactions_fired=?,
               failed_step=?, screenshot_url=?, finished_at=?, gps_verified=?,
               gps_dumpsys_coords=?, maps_task_status=?, maps_foreground=?,
               search_submitted=?, business_found=?, business_name_matched=?,
               card_opened=?, interactions_present=?, interaction_ip=? WHERE run_id=?""",
            (status, duration_seconds, interactions_fired, failed_step,
             screenshot_url, _ts(), int(gps_verified), gps_dumpsys_coords,
             maps_task_status, maps_foreground,
             int(search_submitted), int(business_found), business_name_matched,
             int(card_opened), interactions_present, interaction_ip, run_id),
        )
        self.conn.commit()

    # ---- outcomes (your manual verdict) ----------------------------------
    def record_outcome(self, profile_key: str, verdict: str, run_id: str | None = None,
                       ranking_observed: str | None = None, notes: str | None = None,
                       recorded_by: str = "operator") -> str:
        """
        run_id=None  -> a PROFILE-level verdict ("this business worked overall").
        run_id set   -> a verdict on one specific run.
        """
        outcome_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO outcomes
               (outcome_id, profile_key, run_id, verdict, ranking_observed,
                notes, recorded_by, recorded_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (outcome_id, profile_key, run_id, verdict, ranking_observed,
             notes, recorded_by, _ts()),
        )
        if run_id is None:
            self.conn.execute(
                "UPDATE profiles SET profile_verdict=? WHERE profile_key=?",
                (verdict, profile_key),
            )
        self.conn.commit()
        return outcome_id

    # ---- queries ----------------------------------------------------------
    def history(self, profile_key: str) -> list[dict]:
        """'What has this phone done?' -- the re-run decision view."""
        rows = self.conn.execute(
            """SELECT created_at, status, search_term_used, gps_lat, gps_lng,
                      interactions_fired, duration_seconds, fallback_taken, run_id
               FROM runs WHERE profile_key=? ORDER BY created_at DESC""",
            (profile_key,),
        ).fetchall()
        return [dict(r) for r in rows]

    def success_profile(self) -> dict:
        """Descriptive aggregation over runs whose linked verdict is 'success'."""
        row = self.conn.execute(
            """SELECT COUNT(*) AS winning_runs,
                      AVG(r.duration_seconds) AS avg_duration
               FROM runs r JOIN outcomes o ON o.run_id = r.run_id
               WHERE o.verdict='success'"""
        ).fetchone()
        return dict(row) if row else {}

    def previous_interaction_ip(self, profile_key: str) -> str | None:
        """Return the most recent non-null interaction_ip for this profile."""
        row = self.conn.execute(
            """SELECT interaction_ip FROM runs
               WHERE profile_key=? AND interaction_ip IS NOT NULL
               ORDER BY created_at DESC LIMIT 1""",
            (profile_key,),
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.conn.close()
