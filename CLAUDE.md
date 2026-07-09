# Claude Code — Account Warmer Project Guide

## CRITICAL: This Is the Live Server

This directory (`AccountWarmer-Deploy-Enhanced\account-warmer\`) **is the live production server**. There is no separate dev copy. Every edit here affects the running application.

- **Python / API changes** → restart required: right-click tray icon → Stop Server → Start Server
- **`static/index.html` only** → hard refresh in browser (Ctrl+Shift+R), no restart needed
- **Config / data file changes** → may need restart depending on what reads them

## Production Safety Rules

- **Do NOT** kill processes on port 8000 — that is the live server.
- **Do NOT** modify anything in `logs/state/` — that is live scheduler state.
- You MAY freely edit `.py` files, `config/`, `data/`, `static/`, `api/`, `core/`, `activities/`.
- Use `--dry-run` to test scripts in isolation without affecting live state.
- After making code changes, tell the user a restart is needed and what to restart.

## Project Overview

Account Warmer is a Python-based automation platform that simulates realistic human activity on Google accounts to build trust and age profiles. It operates two parallel warming systems:

1. **Desktop warming** via Multilogin (Chrome browser profiles with unique ISP proxies)
2. **Mobile warming** via GeelarK (cloud Android phones with a shared rotating mobile proxy)

Each Google account has exactly one desktop profile and one mobile phone. Activities are strategy-driven, scheduled fairly, and wrapped with anti-detection behaviour.

## Architecture

### Directory Layout

```
AccountWarmer-Deploy/
└── account-warmer/              # All application code
    ├── core/                    # Engine modules
    │   ├── orchestrator.py      # Session lifecycle: week band → activities → execution
    │   ├── profile_manager.py   # Multilogin API client; CDP launch; proxy injection
    │   ├── humaniser.py         # Anti-detection: delays, typos, mouse curves, scrolls
    │   ├── scheduler.py         # Background fair scheduler for auto-sessions
    │   ├── error_analyser.py    # Known error pattern matching
    │   ├── fix_engine.py        # Auto-fix handlers (proxy clear, token refresh, etc.)
    │   ├── claude_analyser.py   # Sends unknown errors to Claude API for diagnosis
    │   ├── multilogin_auth.py   # JWT token management with cross-process locking
    │   ├── proxy_guard.py       # IP leak verification
    │   ├── geelark_client.py    # GeelarK REST API wrapper
    │   ├── geelark_flow_builder.py  # GAL JSON flow construction for RPA
    │   ├── paths.py             # APP_DIR / DATA_DIR / LOGS_DIR constants
    │   └── activity_log.py      # Structured event logging
    ├── activities/              # Pluggable activity modules
    │   ├── base_activity.py     # Abstract base with logging, cookie dismissal, screenshots
    │   ├── search.py            # Google search with result clicking and internal link following
    │   ├── maps.py              # Google Maps navigation, directions, reviews, photos
    │   ├── business_signal.py   # Three-phase staged customer journey (Discovery → Intent → Post-visit)
    │   ├── email.py             # Gmail inbox reading and occasional reply drafting
    │   ├── youtube.py           # YouTube watch, like, subscribe, comment
    │   ├── mobile_warmup.py   # GeelarK phone daily warmup (Maps, Gmail, YouTube, searches)
    │   ├── google_login_mobile.py   # Pure ADB Google login for Android 10 (no RPA)
    │   └── geelark_setup.py     # Phone provisioning, app installation, Local Guides enrolment
    ├── api/                     # FastAPI server and REST routers
    │   ├── main.py              # FastAPI app; lifespan: token refresh + orphan cleanup + resume
    │   ├── deps.py              # Shared helpers: YAML I/O, file locks, token status
    │   └── routers/             # Endpoint modules
    │       ├── accounts.py      # CRUD, CSV import, profile import, home/work address gen
    │       ├── mobile.py        # Phone provision, login, setup, warmup, screenshot
    │       ├── runner.py        # Manual session spawn, business signal queue, bulk run
    │       ├── scheduler.py     # Enrol/unenrol, pause/resume, concurrency config
    │       ├── trust.py         # Trust score checks and history
    │       ├── fixes.py         # Fix application and status
    │       ├── logs.py          # Log tailing and search
    │       ├── multilogin.py    # Token status, profile sync
    │       ├── proxies.py       # Proxy test, assign, push-to-ML
    │       ├── businesses.py    # Business target CRUD
    │       ├── login_check.py   # Login verification jobs
    │       └── fixes.py         # (duplicate reference — see above)
    ├── config/                  # YAML configuration
    │   ├── accounts.yaml        # Account definitions (email, profile_id, proxy, strategy, etc.)
    │   ├── strategies.yaml      # Named presets (standard, maps_heavy, light, pin_prep)
    │   ├── schedule.yaml        # Week bands (weeks_1_2, weeks_3_4, etc.) with activity weights
    │   ├── behaviour.yaml       # Humaniser timing parameters
    │   ├── businesses.yaml      # Business targets with lat/lon
    │   ├── goals.yaml           # Goal-driven configs (reviews_uk, pindrop_uk, gmb_creation)
    │   └── settings.yaml        # Shared credentials (Multilogin, GeelarK, Decodo, StreamVia)
    ├── data/                    # Content templates
    │   ├── search_terms.json    # 1000+ terms across 12 categories (general, local, shopping, etc.)
    │   ├── email_bodies.json    # Template email subjects and bodies for reply drafting
    │   └── youtube_videos.json  # Curated video pool for YouTube activity
    ├── static/
    │   └── index.html           # Single-page web dashboard (primary UI)
    ├── tray.py                  # System tray launcher / process manager
    ├── run.py                   # Core CLI runner (used by scheduler and manual runs)
    ├── warmer.py                # Strategy-aware CLI wrapper
    ├── import_accounts.py       # Batch CSV import (auto-creates geelark_accounts.yaml entries)
    ├── pin_warmer.py            # Pin readiness checker + "Add a Missing Place" automation
    ├── ml_setup_run.py          # Multilogin profile setup utility
    ├── mobile_login_run.py      # Standalone mobile login runner
    ├── add_business.py          # Interactive business target addition
    ├── set_proxies.py           # Proxy configuration utility
    ├── login_check_run.py       # Standalone login verification
    ├── trust_run.py             # Standalone trust score checker
    ├── requirements.txt         # Python dependencies
    └── warmer.env               # WARMER_DATA_DIR path + ANTHROPIC_API_KEY (NEVER commit)
```

User data (accounts, logs, state) lives **outside** the code tree:

```
WarmingData/                     # Persists across code updates — never delete
├── accounts.yaml
├── geelark_accounts.yaml        # 1:1 mapping with accounts.yaml entries
├── businesses.yaml
├── proxies.yaml
└── logs/
    ├── state/                   # Scheduler JSON, biz-signal state, mobile session logs
    ├── combined.log
    ├── screenshots/
    └── {account_id}.log
```

### Data Directory Split

`core/paths.py` defines two roots:
- `APP_DIR` — where the code lives (`account-warmer/`)
- `DATA_DIR` — where user data lives (from `WARMER_DATA_DIR` env var, usually `WarmingData/`)

All YAML state and log files write to `DATA_DIR`. All static assets and code imports read from `APP_DIR`.

## Session Lifecycle (Desktop)

`core/orchestrator.py` drives each session:

1. **Load account** from `accounts.yaml`, compute week number from `warmup_start_date`
2. **Select strategy** (`config/strategies.yaml`) and matching week-band from `config/schedule.yaml`
3. **Choose activities** — weighted random selection from `activity_weights`, with a 70/30 focus/random split
4. **Launch profile** — `profile_manager.py` starts Multilogin profile via local API (`launcher.mlx.yt:45001`), connects Playwright over CDP, applies geolocation spoofing, blocks media streams, verifies proxy exit IP
5. **Run activities** — each activity extends `BaseActivity` and implements `async def run(self) -> bool`
6. **Stop profile** — profile stops automatically via `ProfileSession` context manager
7. **Log events** — structured events written via `core/activity_log.py`

### Mobile Coordination

`_get_mobile_done_today()` in `orchestrator.py` reads `mobile_sessions.json` and de-prioritises activities already performed on the paired GeelarK phone that day.

## Strategy and Goal System

### Strategies

Defined in `config/strategies.yaml`. Each references a week band in `config/schedule.yaml`:

| Strategy | Description |
|----------|-------------|
| `standard` | Balanced mix of search, maps, email, YouTube |
| `maps_heavy` | Higher weight on Maps and business signals |
| `light` | Fewer actions per day, shorter sessions |
| `pin_prep` | Focus on reviews, photos, and Local Guides points |

### Goals

Defined in `config/goals.yaml`. Goal configs override strategy selection and include emulator thresholds:

| Goal | Duration | Emulator trigger |
|------|----------|------------------|
| `reviews_uk` | ~8 weeks | Add at week 6, 2-week burst |
| `pindrop_uk` | ~10 weeks | Add at week 8, 2-week burst |
| `phone_edit_uk` | ~7 weeks | Add at week 5, 2-week burst |
| `gmb_creation` | ~12 weeks | Add at week 10, 2-week burst |

## Activity System

All activities extend `BaseActivity` (`activities/base_activity.py`). Key capabilities inherited:
- Structured logging via `self.log`
- Cookie/GDPR banner dismissal (`dismiss_cookie_banner()`)
- Screenshot capture on failure
- Access to `self.page` (Playwright Page), `self.account`, and `self.humaniser`

### Desktop Activities

| File | What it does |
|------|-------------|
| `search.py` | Google search, result clicking, page 2 browsing, snippet hovering |
| `maps.py` | Maps navigation, business listing views, photos, reviews, directions |
| `business_signal.py` | Three-phase staged customer journey (see below) |
| `email.py` | Gmail inbox reading, occasional reply drafting |
| `youtube.py` | Video watch, like, subscribe, comment |

### Business Signal Activity (Three-Phase Journey)

`activities/business_signal.py` builds believable customer history before leaving a review:

1. **Discovery** — Search → view listing → browse photos/reviews → get directions
2. **Intent** — Return visit → click website → dwell on site
3. **Post-visit** — Leave review (triggered after 24h+ delay)

State is tracked per `account_id` + `business_id` in `logs/state/{account_id}_biz_{biz_id}.json`. Phase transitions happen automatically based on timestamps.

## Mobile Subsystem (GeelarK)

### One Account = One Phone

Every Google account has a corresponding GeelarK cloud Android phone. Parallel systems:

| Dashboard Tab | Data File | Platform |
|---|---|---|
| Accounts | `WarmingData/accounts.yaml` | Multilogin desktop Chrome |
| Mobile Phones | `WarmingData/geelark_accounts.yaml` | GeelarK cloud Android |

When accounts are imported via `import_accounts.py`, a matching entry is automatically written to `geelark_accounts.yaml`. The GeelarK phone itself is not created until you click **Provision** in the Mobile Phones dashboard tab.

### Proxy Assignment

- **Desktop (Multilogin)**: one unique Decodo ISP proxy per account (ports 10001–10050)
- **Mobile (GeelarK)**: all phones share the single rotating StreamVia mobile proxy (`GEELARK_PROXY` in `warmer.env`)

### Mobile Setup Flow (once per account)

1. `import_accounts.py` → creates `geelark_accounts.yaml` entry automatically
2. Dashboard → Mobile Phones → **Provision** → spins up GeelarK cloud phone
3. Dashboard → **Login** → automated Google login via TOTP
4. Dashboard → **Setup** → installs Maps/Gmail/YouTube, enables location history, Local Guides enrolment
5. Dashboard → **Run All** → daily warmup (sequential, one phone at a time)

### Pure ADB Login (Android 10)

Because GeelarK's built-in RPA requires Android 11+, the project implements a complete Google login flow via ADB shell commands on Android 10 (SM-G9650):

- `activities/google_login_mobile.py` drives `MinuteMaidActivity` with uiautomator XML parsing
- Screens classified: email → password → "Try another way" → method list → TOTP entry → post-login consent
- Includes proxy rotation via `_rotate_proxy_ip()` and device locale configuration
- Three smart ADB polling helpers for real-time screen detection (no GAL flows on Android 10)

### GeelarK API Client

`core/geelark_client.py` wraps the GeelarK REST API:
- Phone lifecycle: create, start, stop, delete, rename
- Screenshot capture
- Custom RPA flow execution
- Task querying

`core/geelark_flow_builder.py` constructs GAL (GeelarK Automation Language) JSON flows programmatically.

## Scheduler and Global Concurrency

### Fair Scheduler

`core/scheduler.py` maintains enrolled accounts and runs them continuously:
- **Active hours**: 07:00–22:00 (won't start new sessions at 22:00+)
- **Minimum gap**: 3 hours between sessions for the same account
- **Fair selection**: the account that has waited longest since its last session is chosen next
- **Max concurrent**: configurable (default 2, range 1–5)
- **State persistence**: `logs/state/scheduler.json` survives restarts
- **On startup**: kills orphaned processes, resets "error" statuses to "waiting"

### Global Concurrency Limit

The scheduler, manual runner, trust checks, and login checks **all share one limit**. The scheduler's `_tick()` explicitly counts running processes across all four systems:

```python
len(self._procs) + manual_running + trust_running + login_running >= self._max_concurrent
```

This prevents overwhelming Multilogin with simultaneous browser launches.

## Error Analysis and Auto-Fix

### Error Analyser

`core/error_analyser.py` scans logs for known patterns:
- `proxy_error` — proxy unreachable or blocked
- `profile_wont_start` — Multilogin profile launch failure
- `stale_token` — expired or invalid JWT
- `wrong_page` — landed on unexpected page
- `not_logged_in` — Google session expired

Unknown errors are queued with `confidence: "pending_ai"` for Claude API analysis.

### Claude Analyser

`core/claude_analyser.py` sends unknown error logs to the Claude API for diagnosis. Results are cached and fed into the fix engine.

### Fix Engine

`core/fix_engine.py` applies approved fixes:

| Fix | Action |
|-----|--------|
| `_clear_proxy` | Removes proxy from account config |
| `_stop_profile` | Force-stops Multilogin profile via API |
| `_force_token_refresh` | Deletes `ml_token.json` to trigger re-auth |
| `_flag_relogin` | Sets `needs_relogin=True` and pauses the account |
| `_pause_account` | Pauses scheduler enrolment |

## Token Auto-Refresh

`api/main.py` lifespan includes a background loop (`_token_refresh_loop`) that:
- Refreshes the Multilogin JWT ~10 minutes before expiry
- Prevents parallel subprocess sign-in races that trigger 429 rate limits
- On failure, waits 2 minutes before retrying

## Startup Cleanup

`api/main.py` lifespan also runs two cleanup tasks on every startup:

1. **`_stop_orphaned_phones()`** — queries GeelarK for phones still in "Running" or "Starting" state and stops them (prevents burned proxy/billing credits after a crash)
2. **`_resume_mobile_warmup_if_needed()`** — re-triggers mobile warmup for accounts that didn't complete a session today (only between 07:00–22:00)

## Key Files and Their Responsibilities

| File | Why it matters |
|------|---------------|
| `core/orchestrator.py` | Main session driver — week calculation, activity selection, execution loop |
| `core/profile_manager.py` | Browser lifecycle — start/stop Multilogin, CDP connect, proxy injection |
| `core/humaniser.py` | Anti-detection layer — all timing and mouse behaviour |
| `core/scheduler.py` | Background auto-scheduler with fair queueing |
| `api/main.py` | FastAPI lifespan — token refresh, orphan cleanup, mobile resume |
| `api/routers/runner.py` | Manual session launch + business signal queue |
| `api/routers/mobile.py` | Phone provision, login, setup, warmup endpoints |
| `activities/business_signal.py` | Three-phase review pipeline |
| `activities/google_login_mobile.py` | Pure ADB login for Android 10 |
| `core/paths.py` | Central constants for APP_DIR, DATA_DIR, LOGS_DIR, STATE_FILE |

## Common Tasks

### Adding a New Activity

1. Create `activities/my_activity.py` extending `BaseActivity`
2. Implement `async def run(self) -> bool`
3. Register in `core/orchestrator.py` (`_activity_map`)
4. Add to relevant week bands in `config/schedule.yaml` and/or `config/strategies.yaml`

### Adding a New API Endpoint

1. Add route in the appropriate `api/routers/*.py` file
2. The router is already included in `api/main.py`

### Changing Scheduler Timing

Edit constants at the top of `core/scheduler.py`:
- `ACTIVE_HOURS_START` / `ACTIVE_HOURS_END`
- `MIN_SESSION_GAP_MINS`
- `MAX_CONCURRENT`

### Changing Humaniser Behaviour

Edit `config/behaviour.yaml` — values are read at activity init time.

### Changing Search Term Pools

Edit `data/search_terms.json` — categories are dynamically discovered at runtime.

### Testing in Isolation

```bash
# Dry-run a single account (no browser)
python run.py --account acc_001 --dry-run

# Dry-run strategy-aware runner
python warmer.py --run --dry-run

# Test proxy assignment
python set_proxies.py --test
```

## Environment and Credentials

Sensitive values live in two places (never commit them):
- `warmer.env` — `WARMER_DATA_DIR` and `ANTHROPIC_API_KEY`
- `config/settings.yaml` — Multilogin, GeelarK, Decodo, StreamVia credentials

The dashboard reads `config/settings.yaml` and passes credentials to each module so subprocesses don't need their own copies.
