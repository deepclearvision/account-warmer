# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Production Safety Rules

The **live production warmer** runs from a separate directory: `C:\Users\Administrator\Desktop\Warmer-Production\`

This directory (`AccountWarmer-Deploy`) is the **development copy** only.

- **Do NOT** run `python tray.py`, `python api/main.py`, or `uvicorn` — these bind to port 8000 which production already owns.
- **Do NOT** kill processes on port 8000.
- **Do NOT** modify anything in `logs/state/` — that is live scheduler state.
- You MAY freely edit `.py` files, `config/`, `data/`, `static/`, `api/`, `core/`, `activities/`.
- To test in isolation: use `--dry-run` where supported.

## Deploying to Production

1. Stop production warmer (tray icon → Stop Server)
2. Copy code files (NOT `logs/` or `WarmingData/`) from this directory into `Warmer-Production\account-warmer\`
3. Restart production warmer (tray icon → Start Server)

## Running the Application

All commands run from `account-warmer/`:

```bash
# Install dependencies
pip install -r requirements.txt

# Tray launcher (production mode — opens dashboard at http://localhost:8000)
python tray.py

# CLI runners
python run.py --all                          # Run all accounts
python run.py --account acc_001              # Run single account
python run.py --all --dry-run                # Simulate without opening browser
python run.py --account acc_001 --week 3     # Override week number for testing

# Strategy-aware CLI
python warmer.py --list                      # List accounts
python warmer.py --strategies                # Show available strategies
python warmer.py --assign acc_001 maps_heavy # Assign strategy to account
python warmer.py --run --dry-run             # Dry-run all accounts

# Utility scripts
python import_accounts.py --csv my_accounts.csv   # Batch import from CSV
python import_accounts.py --list                  # List current accounts
python add_business.py                            # Add business target
python set_proxies.py                             # Configure proxies
python pin_warmer.py --all                        # Specialized pin warming
python profile_ager.py                            # Age profiles
python login_check_run.py                         # Verify logins
python trust_run.py                               # Check trust scores
```

## Architecture

### Directory Layout

```
AccountWarmer-Deploy/
└── account-warmer/        # All application code lives here
    ├── core/              # Engine: orchestrator, profile manager, humaniser
    ├── activities/        # Pluggable action modules (search, maps, email, etc.)
    ├── api/               # FastAPI server + REST routers
    ├── config/            # YAML configuration (accounts, schedule, strategies)
    ├── data/              # Content templates (search terms, email bodies, etc.)
    ├── static/            # Single-page web dashboard (index.html)
    ├── tray.py            # System tray launcher / process manager
    ├── run.py             # Core CLI runner
    └── warmer.py          # Strategy-aware CLI wrapper
```

User data (accounts, logs) lives outside the code tree:
```
WarmingData/               # Persists across code updates — never delete
├── accounts.yaml          # Live account definitions
├── businesses.yaml        # Business targets
├── proxies.yaml
└── logs/
    ├── state/             # Scheduler state, JWT token cache, trust results
    ├── combined.log
    └── {account_id}.log
```

### How a Session Works

`orchestrator.py` drives each session:
1. Loads account from `accounts.yaml`, computes week number from `warmup_start_date`
2. Selects a strategy (`config/strategies.yaml`) and the matching week-band from `config/schedule.yaml`
3. Randomly picks N activities weighted by `activity_weights`
4. For each activity: `profile_manager.py` launches the Multilogin browser profile (via CDP), Playwright connects, the activity runs, then the profile stops
5. `humaniser.py` wraps all browser interactions with log-normal timing, typos, reading pauses, and scroll backtracking

### Core Modules

| File | Role |
|------|------|
| `core/orchestrator.py` | Session logic: week band → activity selection → execution loop |
| `core/profile_manager.py` | Multilogin API client; injects proxy + geolocation; returns Playwright page |
| `core/humaniser.py` | Anti-detection: variable delays, typing speed, typos, scroll behavior |
| `core/scheduler.py` | Background job runner for API-triggered scheduled sessions |
| `core/claude_analyser.py` + `core/fix_engine.py` | Claude API integration for error analysis and auto-repair |
| `api/main.py` | FastAPI app; runs token refresh loop; registers all routers |
| `api/deps.py` | Shared helpers: YAML I/O, file locks, token status |

### Activity System

All activities extend `BaseActivity` (`activities/base_activity.py`) and implement `async def run(self) -> bool`. To add a new activity:
1. Create `activities/my_activity.py` extending `BaseActivity`
2. Register it in `orchestrator.py`
3. Add it to the relevant week bands in `config/schedule.yaml` and/or `config/strategies.yaml`

### Key Configuration Files

| File | Purpose |
|------|---------|
| `warmer.env` | `WARMER_DATA_DIR` (path to WarmingData) and `ANTHROPIC_API_KEY` |
| `config/accounts.yaml` | Account list with email, multilogin_profile_id, proxy, timezone, strategy, warmup_start_date |
| `config/schedule.yaml` | Week bands (weeks_1_2, weeks_3_4, etc.) defining actions_per_day, allowed_activities, activity_weights |
| `config/strategies.yaml` | Named presets (standard, maps_heavy, light, pin_prep) referencing schedule week bands |
| `config/behaviour.yaml` | Timing parameters for humaniser (delays, typing speed, typo probability, reading pauses) |
| `config/businesses.yaml` | Business targets with lat/lon for maps activities |

### Mobile Phones (GeelarK) — 1:1 with Accounts

Every Google account has a corresponding GeelarK cloud Android phone. These are two parallel systems:

| Dashboard Tab | Data File | Platform |
|---|---|---|
| Accounts | `WarmingData/accounts.yaml` | Multilogin desktop Chrome |
| Mobile Phones | `WarmingData/geelark_accounts.yaml` | GeelarK cloud Android |

**Rule: one account = one mobile phone, always.**

When accounts are imported via `import_accounts.py`, a matching entry is automatically written to `geelark_accounts.yaml`. The GeelarK phone itself is not created until you click **Provision** in the Mobile Phones dashboard tab.

**All mobile phones share a single rotating mobile proxy** — the StreamVia proxy set in `warmer.env` as `GEELARK_PROXY`. This is assigned automatically at provision time. Do not assign ISP or rotating residential proxies to mobile phones.

**Mobile setup flow (once per account):**
1. `import_accounts.py` → creates `geelark_accounts.yaml` entry automatically
2. Dashboard → Mobile Phones → **Provision** → spins up GeelarK cloud phone
3. Dashboard → **Login** → automated Google login via TOTP
4. Dashboard → **Setup** → installs Maps/Gmail/YouTube, enables location history, Local Guides enrolment
5. Dashboard → **Run All** → daily warmup (sequential, one phone at a time)

**Proxy assignment for new accounts:**
- Desktop (Multilogin): assign one unique Decodo ISP proxy per account (ports 10001–10050; next free port is 10039)
- Mobile (GeelarK): all share `GEELARK_PROXY` (StreamVia rotating mobile) — no per-account proxy needed

### API Endpoints (FastAPI on port 8000)

Routers in `api/routers/`: `accounts`, `proxies`, `multilogin`, `businesses`, `runner`, `scheduler`, `trust`, `fixes`, `logs`, `login_check`, `mobile`. The web dashboard (`static/index.html`) is the primary UI.
