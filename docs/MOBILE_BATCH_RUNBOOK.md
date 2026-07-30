# Mobile Batch Runbook — Daily Unattended Mobile Warm-Up

## 1. Overview

The daily mobile batch runner warms GeelarK cloud-phone Google accounts
automatically — one session per account, per day — with no manual
intervention required.

**Key properties:**

| Property | Detail |
|---|---|
| Concurrency | One phone at a time (all phones share a single StreamVia mobile proxy) |
| Per-account session | Follows the monthly schedule (`_day_to_script`) by default |
| Schedule (default) | **Most days:** `local_discovery` — Maps "near me" search + optional YouTube (25% before / 35% after / 10% Chrome after / 30% none). **Day 3:** `money_kw` — 2 commercial keyword searches + optional YouTube (50%) or Chrome (20%). **Days 11, 21:** `brand_1km` — named business navigation with GPS variation (40% home / 25% work / 25% near business / 10% area fallback) + optional YouTube (30% before / 40% after) |
| Testing mode | `--activity` flag forces a single activity on every account (skips the schedule) |
| Proxy | Rotated before every account (185 s cooldown respected) |
| Occupied phones | Skipped safely — see §6 |

**What it does NOT do:**

- It does **not** run Gmail or Google Search activities as part of the daily schedule (use the dashboard or `--activity` testing flag for those).
- It does **not** start multiple phones at once.

---

## 2. Files created or modified in this task

| File | Change |
|---|---|
| `activities/mobile_location_setup.py` | **New.** Shared GPS + permission helper (`ensure_location_and_permissions`, `set_gps_near_business`, `choose_business_location`) |
| `activities/mobile_warmup.py` | **Modified.** Three batch scripts updated: `_batch_local_discovery` now randomises YouTube before/after Maps; `_batch_money_kw` adds optional YouTube/Chrome after keyword searches; `_batch_brand_1km` varies GPS per run (home/work/business/area) and adds YouTube before/after. Added `run_selected_warmup`, `run_warmup_activity`, backward-compatible `run_warmup_session` alias. Added occupied-phone skip logic. Reduced IP settling from 60 s to 5–15 s. |
| `daily_mobile_batch_runner.py` | **New.** Standalone daemon-capable daily batch runner. Default mode follows the monthly schedule via `run_warmup_session`. `--activity` flag overrides for testing single activities. Supports `--once`, `--daemon`, `--account`, `--status`, `--pc` CLI flags. |
| `tray.py` | **Modified.** Added `Start Daily Mobile Batch` / `Stop Daily Mobile Batch` menu items, amber icon for batch-only state, safety note, Exit stops both server and batch. |
| `install_daily_mobile_task.ps1` | **New.** PowerShell script to install/update the `AccountWarmerDailyMobile` Windows Scheduled Task. |
| `docs/MOBILE_BATCH_RUNBOOK.md` | **New.** This file. |

---

## 3. Manual testing commands

All commands are run from the project root:

```
C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced
```

### Test a single account with the monthly schedule (default)

```
python daily_mobile_batch_runner.py --account gl_001
```

Runs whatever the `_day_to_script` schedule prescribes for today.

### Test a single account with a forced activity

```
python daily_mobile_batch_runner.py --account gl_001 --activity youtube
python daily_mobile_batch_runner.py --account gl_001 --activity maps_directions
python daily_mobile_batch_runner.py --account gl_001 --activity maps_browse
python daily_mobile_batch_runner.py --account gl_001 --activity maps+youtube
```

The `--activity` flag overrides the schedule — use it for testing specific
activity types.  Valid choices: `maps_browse`, `maps_directions`, `youtube`,
`maps+youtube`, `gmail`, `google_search`.

### Run one full batch cycle (schedule mode)

```
python daily_mobile_batch_runner.py --once
```

Runs every enabled account through the monthly schedule, sequentially.

### Run one full batch cycle (testing mode — forced activity)

```
python daily_mobile_batch_runner.py --once --activity youtube
```

### Show last session per account

```
python daily_mobile_batch_runner.py --status
```

### Filter accounts by PC category (multi-machine setups)

```
python daily_mobile_batch_runner.py --once --pc PC2
```

---

## 4. Daemon operation

### Start the daemon directly

```
python daily_mobile_batch_runner.py --daemon
```

The daemon runs one batch cycle immediately (following the monthly schedule),
computes the next run time (random minute within 08:00–18:00 tomorrow),
sleeps until then, and repeats indefinitely.  It logs progress to both the
console and `WarmingData\logs\daily_mobile_batch.log`.

To run in testing mode with a forced activity:

```
python daily_mobile_batch_runner.py --daemon --activity youtube
```

### Start from the tray app

1. Right-click the Account Warmer tray icon (green/grey/amber circle).
2. Click **Start Daily Mobile Batch**.
3. The icon turns amber while the batch is running (if the server is not also running).

### Stop the daemon

- **If started from CLI:** press `Ctrl+C`.
- **If started from tray:** right-click the tray icon → **Stop Daily Mobile Batch**.

The daemon also handles `SIGINT` / `SIGTERM` gracefully — it finishes the
current cycle before exiting.

---

## 5. Windows Scheduled Task

### Install (or update) the scheduled task

```
powershell -File install_daily_mobile_task.ps1
```

With custom time:

```
powershell -File install_daily_mobile_task.ps1 -RandomHour 10 -RandomMinute 30
```

This creates a task named `AccountWarmerDailyMobile` that runs
`daily_mobile_batch_runner.py --daemon` once per day at a random minute
between 09:00 and 17:00 (or the custom hour/minute provided).

The task is configured as:

| Setting | Value |
|---|---|
| Run as | Current user, interactive logon only |
| Privileges | Highest |
| Multiple instances | Ignore new (won't stack if a run is still in progress) |
| Battery | Allowed to start on battery, won't stop if going on battery |

If the task already exists, running the installer again updates the trigger
time — it does **not** create a duplicate.

### View the task

```
taskschd.msc
```

Navigate to **Task Scheduler Library** → `AccountWarmerDailyMobile`.

### Run the task immediately (test)

```powershell
Start-ScheduledTask -TaskName "AccountWarmerDailyMobile"
```

### Remove the task

```powershell
Unregister-ScheduledTask -TaskName "AccountWarmerDailyMobile" -Confirm:$false
```

### ⚠ Conflict warning

If the **tray app** (`tray.py`) is also managing the batch runner via its
`Start Daily Mobile Batch` menu item, **disable the scheduled task** to
avoid two batch runners starting simultaneously.  The installer prints a
warning if it detects `tray.py` running.

---

## 6. Social-media phone creation safety

It is **safe to manually open another GeelarK phone** and create
social-media accounts while the batch runner is active.

**How this works:**

1. Before each account's session, the batch runner checks the phone's
   GeelarK status.
2. If the phone is already **Running** (status 0) or **Starting** (status 1),
   or the GeelarK API returns error **43021** (phone in use), the batch
   runner **logs a warning and skips that account**.
3. The batch runner moves on to the next account — the manually-opened
   phone is never touched.

**Where this is documented:**

- The tray app's right-click menu includes the note:
  > *"Safe to create social accounts manually — occupied phones are skipped automatically."*
- The batch runner logs every skip with the reason.

**What to watch for:**

- If you keep a phone open for an extended period, its corresponding account
  will simply miss its daily warmup session.  This is fine — the next day's
  run will pick it up again.
- Do **not** open the same phone that the batch runner is about to use.  The
  status check runs immediately before `start_phone`, but there is a small
  race window.  If you need to use a specific phone, click **Stop Daily
  Mobile Batch** in the tray first.

---

## 7. Expected daily behaviour

### Monthly schedule (default mode)

The runner calls `run_warmup_session()` which delegates to `_day_to_script()`:

| Schedule day | Script | What happens |
|---|---|---|
| 1–2 | `local_discovery` | Maps "near me" search + browse listing + optional YouTube (25% before / 35% after / 10% Chrome after / 30% none) |
| 3 | `money_kw` | 2 commercial-keyword Google searches + optional YouTube (50%) or Chrome (20%) follow-up |
| 4–10 | `local_discovery` | Same as days 1–2 |
| 11 | `brand_1km` | Named business navigation with GPS variation (40% home / 25% work / 25% near business / 10% area) + optional YouTube (30% before / 40% after) |
| 12–20 | `local_discovery` | Same as days 1–2 |
| 21 | `brand_1km` | Same as day 11 (with independent GPS and YouTube rolls) |
| 22–30 | `local_discovery` | Same as days 1–2 |
| 31+ | `local_discovery` daily + `brand_1km` every 10th day | |

### GPS variation (brand_1km)

Each `brand_1km` run picks a different GPS strategy randomly:
- **40%**: home address (`home_lat`/`home_lng` with ±0.0005 jitter)
- **25%**: work address (`work_lat`/`work_lng` with ±0.0005 jitter)
- **25%**: near the target business (200 m jitter via `set_gps_near_business`)
- **10%**: area/city fallback (`ensure_location_and_permissions`)

This means two `brand_1km` runs per month almost always use different GPS bases.

### Per-account session flow (schedule mode)

`run_warmup_session()` handles the full lifecycle internally:

```
1. Check phone health
2. Check phone not occupied → skip if busy
3. Rotate proxy IP
4. Start phone via GeelarK API
5. Wait for boot (up to 90 s)
6. Refresh GPS (area-based)
7. Determine today's script from schedule_day
8. Run batch script (local_discovery / money_kw / brand_1km)
9. Take proof screenshot
10. Stop phone (3-attempt verified stop)
11. Log to WarmingData/logs/mobile_sessions.json
```

### Timing

| Phase | Typical duration |
|---|---|
| Proxy rotation + settling | 30–120 s |
| Phone start + boot | 20–90 s |
| local_discovery | 40–120 s |
| money_kw | 45–90 s |
| brand_1km | 60–180 s |
| Phone stop | 5–20 s |
| **Per account (average)** | **3–8 minutes** |

For N accounts, a full batch cycle takes approximately **N × 5 minutes**.

### Log files

| File | Contents |
|---|---|
| `WarmingData/logs/mobile_sessions.json` | Per-session records: account_id, timestamp, ip, activity, duration_s, steps_done, success, location_label |
| `WarmingData/logs/daily_mobile_batch.log` | Detailed batch runner logs: starts, stops, choices, results, errors |
| `WarmingData/logs/tray.log` | Tray app events: batch start/stop, server start/stop, crashes |

### Checking status

```
python daily_mobile_batch_runner.py --status
```

Prints a table of the last session for each account:

```
Account      Timestamp              Activity           IP               Dur    OK    Location
gl_001       2026-07-30 09:23       youtube            185.220.xxx.xx    78s    ✓    business: The Red Lion
gl_002       2026-07-30 09:28       maps_directions    185.220.xxx.yy   145s    ✓    home
gl_003       2026-07-30 09:33       maps_browse        185.220.xxx.zz    62s    ✓    area: shoreditch
```

---

## 8. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| All accounts skipped as "occupied" | Another batch runner or manual session already running | Check Task Manager for `python daily_mobile_batch_runner.py` processes |
| Proxy rotation fails | StreamVia rate limit or API down | Wait 5 minutes and retry |
| Phone won't boot | GeelarK billing / phone expired | Check GeelarK dashboard |
| Activity fails on all accounts | ADBKeyboard not installed, Maps/YouTube not on phone | Run phone setup via dashboard Mobile Phones tab |
| Batch runner not starting from scheduled task | Python not in PATH | Specify full path to `python.exe` in the scheduled task action |
| Two batch runners running | Both tray + scheduled task active | Disable one — use either tray **or** scheduled task, not both |
