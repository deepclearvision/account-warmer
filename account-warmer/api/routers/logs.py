"""
Logs router — list and read log files from the logs/ directory.
"""

from fastapi import APIRouter, HTTPException

from api.deps import LOGS_DIR

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def list_logs():
    """Return a list of all .log files available."""
    if not LOGS_DIR.exists():
        return []
    return sorted(f.name for f in LOGS_DIR.glob("*.log"))


@router.get("/{name}")
def get_log(name: str, lines: int = 200):
    """
    Return the last N lines of a log file.
    Only .log files in the logs/ directory are accessible.
    """
    # Sanitise: no path traversal, only .log extension
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Invalid log file name")
    if not name.endswith(".log"):
        raise HTTPException(400, "Only .log files are accessible")

    log_file = LOGS_DIR / name
    if not log_file.exists():
        return {"lines": [], "exists": False, "total_lines": 0}

    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return {
            "lines":       [ln.rstrip() for ln in all_lines[-lines:]],
            "exists":      True,
            "total_lines": len(all_lines),
        }
    except Exception as e:
        raise HTTPException(500, str(e))
