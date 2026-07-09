"""
Centralised path resolution for Account Warmer.

User data (accounts, logs, state) lives in DATA_DIR — an external directory
that survives software updates. The script code can be deleted and replaced
with a new version without losing any account history or state.

Configure the data directory in warmer.env (in the account-warmer folder):

    WARMER_DATA_DIR=C:\\WarmingData

Defaults to C:\\WarmingData if not set.

Split:
  DATA_DIR   — user data (accounts, businesses, proxies, logs, state)
               This is what you back up. Never deleted on software update.

  APP_DIR    — software config (strategies, schedule, behaviour, goals)
               Ships with each version. Safe to overwrite.
"""

import os
from pathlib import Path

# ── Load warmer.env from the app root ─────────────────────────────────────────

_APP_DIR  = Path(__file__).parent.parent
_env_file = _APP_DIR / "warmer.env"

if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── App directory (changes with each software version) ────────────────────────

APP_DIR    = _APP_DIR
CONFIG_DIR = APP_DIR / "config"

# ── Data directory (user data — never changes between versions) ───────────────

DATA_DIR = Path(os.environ.get("WARMER_DATA_DIR", r"C:\WarmingData"))

# ── User config (editable, lives in DATA_DIR) ─────────────────────────────────

ACCOUNTS_FILE         = DATA_DIR / "accounts.yaml"
BUSINESSES_FILE       = DATA_DIR / "businesses.yaml"
PROXIES_FILE          = DATA_DIR / "proxies.yaml"
PROFILE_AGER_FILE     = DATA_DIR / "profile_ager.yaml"
PIN_WARMER_FILE       = DATA_DIR / "pin_warmer_accounts.yaml"
GEELARK_ACCOUNTS_FILE = DATA_DIR / "geelark_accounts.yaml"
ACCOUNT_BUSINESS_MAP_FILE = DATA_DIR / "account_business_map.yaml"
GEELARK_FLOW_DATA_FILE = DATA_DIR / "geelark_flow_data.yaml"

# ── Logs and state (lives in DATA_DIR) ────────────────────────────────────────

LOGS_DIR             = DATA_DIR / "logs"
STATE_DIR            = DATA_DIR / "logs" / "state"
TOKEN_FILE           = STATE_DIR / "ml_token.json"
SYNC_CACHE_FILE      = STATE_DIR / "ml_sync_cache.json"
SCHEDULER_STATE_FILE = STATE_DIR / "scheduler.json"

# ── App config (ships with the software — strategies, schedules, behaviour) ───

STRATEGIES_FILE = CONFIG_DIR / "strategies.yaml"
SCHEDULE_FILE   = CONFIG_DIR / "schedule.yaml"
BEHAVIOUR_FILE  = CONFIG_DIR / "behaviour.yaml"
ML_CONFIG_FILE  = CONFIG_DIR / "multilogin.yaml"


def ensure_data_dirs() -> None:
    """Create the external data directory structure if it doesn't exist yet."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
