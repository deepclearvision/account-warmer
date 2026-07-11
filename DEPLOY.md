# Account Warmer — Deployment Guide

## Quick Deploy (git)

After changes are committed and pushed from the source PC:

```powershell
cd <account-warmer-folder>
git fetch origin --tags
git checkout -f vX.Y.Z
```

Restart the server via tray icon. Done.

Protected files (`warmer.env`, `config/settings.yaml`, `config/multilogin.yaml`, `config/proxies.yaml`, `logs/`, `WarmingData/`) are in `.gitignore` and never overwritten.

## Version Numbering

| Change type | Version bump | Example |
|---|---|---|
| Bug fixes only | Patch | 1.2.3 → 1.2.4 |
| New features | Minor | 1.2.3 → 1.3.0 |
| Breaking changes | Major | 1.2.3 → 2.0.0 |

## Commit Message Convention

```
type: short description (max 72 chars)

Detailed explanation of what changed and why. Any relevant context
for someone reading this 3 months from now.

Co-Authored-By: Claude <noreply@anthropic.com>
```

Types: `fix:` | `feat:` | `chore:` | `docs:`

## Full Release Checklist

- [ ] All changes tested on dev PC
- [ ] `git status` — no unexpected modified files
- [ ] `git add` all changed files
- [ ] Commit with descriptive message
- [ ] Update `version.txt`
- [ ] `git add version.txt && git commit -m "chore: bump version to X.Y.Z"`
- [ ] `git tag vX.Y.Z -m "vX.Y.Z — summary"`
- [ ] `git push origin master --tags`
- [ ] On target PC: `git fetch origin --tags && git checkout -f vX.Y.Z`
- [ ] Restart server on target PC
- [ ] Verify: dashboard loads, accounts appear, tabs work

## USB-Based Deploy (no network)

If the target PC has no internet access:

### PC 2 (source) — copy to USB
```powershell
robocopy "C:\Users\Administrator\Desktop\AccountWarmer-Instance-2\account-warmer" "E:\deploy-pc1\account-warmer" /E /XD __pycache__ deploy_backups .git logs /XF *.pyc *.log
```

### PC 1 (target) — deploy from USB
```powershell
cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer"
python deploy.py --source E:\deploy-pc1\account-warmer --dry-run
python deploy.py --source E:\deploy-pc1\account-warmer --mode wait --yes
```

## Rollback

```powershell
cd <account-warmer-folder>
git checkout -f v1.2.2   # previous known-good version
# Restart server
```

Or if using deploy.py:
```powershell
python deploy.py --rollback deploy_backups\2026-07-09_1430xxxx --yes
```

## Blocklist (never synced)

These files are in `.gitignore` and excluded from `deploy.py` sync:

- `warmer.env` — PC-specific environment (port, PC_ID, API keys)
- `.env`
- `ml_token*.json` — Multilogin auth tokens
- `config/multilogin.yaml`, `config/settings.yaml`, `config/proxies.yaml` — credentials
- `config/accounts.yaml` — user data
- `logs/` — runtime logs and state
- `WarmingData/`, `WarmingData2/` — user data directories
- `__pycache__/`, `.venv/`, `.git/`
- `deploy_backups/`
- `*.log`, `*.bak`, `*.apk`, `*.apkm`, `*.b64`

## Multi-PC Configuration

| Setting | PC 2 (dev) | PC 1 (production) |
|---|---|---|
| Port | 8001 | 8000 |
| PC_ID | pc_2 | pc_1 |
| Data dir | C:\WarmingData2 | C:\WarmingData |
| Decodo proxy range | 10051-10100 | 10001-10050 |
| GeelarK subscription | 100 phones | Shared |
