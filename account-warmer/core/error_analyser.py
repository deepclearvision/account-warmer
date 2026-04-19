"""
Error Analyser

Scans account log files for errors after sessions complete, classifies them
into known types, and generates fix proposals stored in STATE_DIR/pending_fixes.json.

Unknown errors are flagged for Claude API analysis (core/claude_analyser.py).

Called automatically by the scheduler after a failed session, and available
as a manual trigger via POST /api/fixes/analyse.
"""

import json
import re
import uuid
from datetime import datetime, date
from pathlib import Path

from core.paths import LOGS_DIR, STATE_DIR

# ── Error pattern definitions ─────────────────────────────────────────────────

_PATTERNS = [
    {
        "type":     "proxy_error",
        "regex":    re.compile(
            r"GET_PROXY_CONNECTION_IP_ERROR|couldn't get proxy connection ip|"
            r"PROXY_CONNECTION_ERROR|ProxyError|407 Proxy",
            re.IGNORECASE
        ),
        "summary":  "Proxy connection failed",
        "fix_desc": "Clear the proxy assignment so the account is flagged for proxy reassignment.",
        "action":   "clear_proxy",
        "confidence": "high",
    },
    {
        "type":     "profile_wont_start",
        "regex":    re.compile(
            r"Could not start Multilogin profile|Read timed out.*45001|"
            r"launcher\.mlx\.yt.*timed out",
            re.IGNORECASE
        ),
        "summary":  "Multilogin profile failed to start",
        "fix_desc": "Force-stop the profile via the Multilogin API so the next session can start it cleanly.",
        "action":   "stop_profile",
        "confidence": "high",
    },
    {
        "type":     "stale_token",
        "regex":    re.compile(r"Auth token expired", re.IGNORECASE),
        "summary":  "Multilogin auth token expired repeatedly",
        "fix_desc": "Delete the cached token file so it is fully refreshed on the next run.",
        "action":   "force_token_refresh",
        "confidence": "high",
    },
    {
        "type":     "wrong_page",
        "regex":    re.compile(
            r"Locator\.click.*Timeout.*exceeded.*\n.*locator resolved to.*(?!google)",
            re.IGNORECASE | re.DOTALL
        ),
        "summary":  "Browser landed on wrong page (not Google)",
        "fix_desc": "Force-stop the profile so stale tabs are cleared on the next session start.",
        "action":   "stop_profile",
        "confidence": "medium",
    },
    {
        "type":     "not_logged_in",
        "regex":    re.compile(r"not_logged_in|login_status.*not_logged_in", re.IGNORECASE),
        "summary":  "Account is not logged in to Google",
        "fix_desc": "Pause warming for this account and flag it as requiring manual re-login.",
        "action":   "flag_relogin",
        "confidence": "high",
    },
]

FIXES_FILE = STATE_DIR / "pending_fixes.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_fixes() -> list:
    if not FIXES_FILE.exists():
        return []
    try:
        return json.loads(FIXES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_fixes(fixes: list) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FIXES_FILE.write_text(json.dumps(fixes, indent=2), encoding="utf-8")


def _already_pending(fixes: list, account_id: str, error_type: str) -> bool:
    """Avoid duplicate proposals for the same account+error already waiting."""
    return any(
        f["account_id"] == account_id
        and f["error_type"] == error_type
        and f["status"] == "pending"
        for f in fixes
    )


def _read_today_errors(account_id: str) -> list[str]:
    """Return all ERROR lines from today in the account's log file."""
    log_file = LOGS_DIR / f"{account_id}.log"
    if not log_file.exists():
        return []
    today = date.today().isoformat()
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return [l for l in lines if today in l and " ERROR " in l]
    except Exception:
        return []


def _read_recent_errors(account_id: str, last_n_lines: int = 200) -> str:
    """Return the last N lines of the account log as a single string."""
    log_file = LOGS_DIR / f"{account_id}.log"
    if not log_file.exists():
        return ""
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-last_n_lines:])
    except Exception:
        return ""


# ── Main analyser ─────────────────────────────────────────────────────────────

def analyse_account(account_id: str, profile_id: str = "") -> list[dict]:
    """
    Scan one account's log for today's errors and generate fix proposals.
    Returns list of new fix dicts added (empty if nothing new found).
    """
    error_lines = _read_today_errors(account_id)
    if not error_lines:
        return []

    fixes     = _load_fixes()
    new_fixes = []
    full_log  = "\n".join(error_lines)
    matched_types = set()

    for pattern in _PATTERNS:
        if pattern["type"] in matched_types:
            continue
        if not pattern["regex"].search(full_log):
            continue
        if _already_pending(fixes, account_id, pattern["type"]):
            continue

        # Count occurrences to include in summary
        count = sum(1 for l in error_lines if pattern["regex"].search(l))
        fix = {
            "id":            str(uuid.uuid4())[:8],
            "account_id":    account_id,
            "profile_id":    profile_id,
            "error_type":    pattern["type"],
            "error_summary": f"{pattern['summary']} ({count}× today)",
            "error_lines":   [l for l in error_lines if pattern["regex"].search(l)][-5:],
            "proposed_fix":  pattern["fix_desc"],
            "fix_action":    {"type": pattern["action"], "account_id": account_id,
                              "profile_id": profile_id},
            "confidence":    pattern["confidence"],
            "created_at":    datetime.now().isoformat(),
            "status":        "pending",
        }
        fixes.append(fix)
        new_fixes.append(fix)
        matched_types.add(pattern["type"])

    # If errors exist but no pattern matched → queue for Claude analysis
    unmatched = [
        l for l in error_lines
        if not any(p["regex"].search(l) for p in _PATTERNS)
    ]
    if unmatched and not _already_pending(fixes, account_id, "unknown"):
        fix = {
            "id":            str(uuid.uuid4())[:8],
            "account_id":    account_id,
            "profile_id":    profile_id,
            "error_type":    "unknown",
            "error_summary": f"Unrecognised error ({len(unmatched)} line(s) today) — awaiting AI analysis",
            "error_lines":   unmatched[-10:],
            "proposed_fix":  None,   # filled in by claude_analyser
            "fix_action":    None,
            "confidence":    "pending_ai",
            "created_at":    datetime.now().isoformat(),
            "status":        "pending",
        }
        fixes.append(fix)
        new_fixes.append(fix)

    if new_fixes:
        _save_fixes(fixes)

    return new_fixes


def analyse_all(accounts: list[dict]) -> int:
    """Analyse all accounts and return total number of new fixes generated."""
    total = 0
    for acc in accounts:
        new = analyse_account(
            acc["id"],
            profile_id=acc.get("multilogin_profile_id", ""),
        )
        total += len(new)
    return total


def get_pending_fixes() -> list[dict]:
    return [f for f in _load_fixes() if f["status"] == "pending"]


def get_all_fixes() -> list[dict]:
    return _load_fixes()


def update_fix_status(fix_id: str, status: str) -> bool:
    fixes = _load_fixes()
    for f in fixes:
        if f["id"] == fix_id:
            f["status"]     = status
            f["updated_at"] = datetime.now().isoformat()
            _save_fixes(fixes)
            return True
    return False
