"""
Activity Log
============
Centralised structured logger for all warming activities (desktop + mobile).

Each event is appended to WarmingData/logs/{account_id}_activity_log.json.

Log entry schema:
  timestamp    ISO-8601 UTC string
  device       "desktop" or "mobile"
  activity_type  e.g. "search", "maps_browse", "business_signal", "email_read",
                     "maps_directions", "youtube", "gmail"
  detail       dict — activity-specific fields:
                 search_term, interaction, business_id, business_name,
                 url, phase, action, destination, …
  screenshot   relative path from LOGS_DIR, or null
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from core.paths import LOGS_DIR

_lock = threading.Lock()


def log_event(
    account_id: str,
    device: str,
    activity_type: str,
    detail: dict | None = None,
    screenshot: str | None = None,
) -> None:
    """
    Append one activity event to the account's activity log.

    Parameters
    ----------
    account_id    Account ID (e.g. "acc_001")
    device        "desktop" or "mobile"
    activity_type Short slug (e.g. "search", "maps_browse", "business_signal")
    detail        Free-form dict with activity-specific info
    screenshot    Relative path from LOGS_DIR (e.g. "screenshots/foo.png"), or None
    """
    entry: dict = {
        "timestamp":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device":        device,
        "activity_type": activity_type,
        "detail":        detail or {},
    }
    if screenshot:
        entry["screenshot"] = screenshot

    log_file = LOGS_DIR / f"{account_id}_activity_log.json"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    with _lock:
        try:
            records: list = json.loads(log_file.read_text(encoding="utf-8")) if log_file.exists() else []
        except Exception:
            records = []
        records.append(entry)
        # Keep last 500 events per account
        if len(records) > 500:
            records = records[-500:]
        try:
            log_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
        except Exception:
            pass  # Never crash the calling activity due to logging failure
