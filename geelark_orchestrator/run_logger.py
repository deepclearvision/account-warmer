"""
run_logger.py — Write run records in two formats so problems are easy to feed back.

PURE-ish: only touches the local filesystem (a logs/ folder). No Geelark.

Two outputs, same data, different jobs:
  1. logs/runs.log   — human-readable. Glance at it, skim what happened.
  2. logs/runs.jsonl — one JSON object per line (JSON Lines). THIS is the one to
                       send back when something's wrong: it contains the full
                       resolved plan + seed, so the exact run can be reproduced.

Reproducing a run from a JSONL line:
    seed + profile_key is enough — re-run dry_run.py with --seed <seed> on the
    same profile and you get the identical plan.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from dataclasses import asdict


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def log_plan(run_plan, log_dir: str = "logs", extra: dict | None = None) -> None:
    """
    Append one run to both logs. `extra` lets callers attach context
    (e.g. {"phase": "dry_run"} or {"status": "failed", "failed_step": "..."}).
    """
    record = asdict(run_plan)
    record["logged_at"] = _ts()
    if extra:
        record.update(extra)

    jsonl_path = os.path.join(log_dir, "runs.jsonl")
    text_path = os.path.join(log_dir, "runs.log")
    _ensure_dir(jsonl_path)

    # 1. machine-readable, one line per run
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 2. human-readable
    with open(text_path, "a", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write(f"{record['logged_at']}  run_id={run_plan.run_id}\n")
        f.write(run_plan.summary() + "\n")
        f.write("decision trail:\n")
        for d in run_plan.decisions:
            f.write(f"  - {d}\n")
        if extra:
            f.write(f"context: {json.dumps(extra)}\n")
        f.write("\n")


def make_feedback_packet(run_id: str, log_dir: str = "logs") -> str:
    """
    Pull the JSONL record(s) for one run_id into a single string you can paste
    straight back into a chat. This is the 'send this when it breaks' helper.
    """
    jsonl_path = os.path.join(log_dir, "runs.jsonl")
    if not os.path.exists(jsonl_path):
        return f"(no log file at {jsonl_path})"
    matches = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("run_id") == run_id:
                matches.append(json.dumps(rec, indent=2, ensure_ascii=False))
    if not matches:
        return f"(no record found for run_id {run_id})"
    header = (
        "=== FEEDBACK PACKET (Layer 1: resolver decision) ===\n"
        "Paste this back to debug. It includes the seed, so the exact run is\n"
        "reproducible. If the problem was on the PHONE, also attach the Geelark\n"
        "execution log + screenshot for the same run_id (Layer 2).\n\n"
    )
    return header + "\n".join(matches)
