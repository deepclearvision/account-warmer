"""
Account Warmer — Deploy Script
==============================
Stop the server, back up the current code, sync new code from a source folder,
and restart. Built to be safety-critical and boring: clear, linear logic with
loud summaries at every step.

Usage
-----
    python deploy.py --source <path> [options]

    python deploy.py --source E:\\account-warmer --dry-run      # preview only
    python deploy.py --source E:\\account-warmer --yes          # deploy, no prompt
    python deploy.py --source E:\\account-warmer --mode wait     # drain sessions first
    python deploy.py --rollback deploy_backups\\2026-07-08_1530  # restore a backup

What it NEVER touches (hardcoded blocklist)
-------------------------------------------
    warmer.env, .env, ml_token*.json, config/multilogin.yaml,
    logs/, WarmingData/, WarmingData2/,
    __pycache__/, .venv/, venv/, .git/, *.log, *.bak

These hold secrets, per-machine config, and user data that must survive a
deploy. The blocklist is applied to sync, backup, and rollback alike — so no
code path can clobber them.

Server lifecycle
----------------
The dashboard runs as a uvicorn process (started by tray.py, which also has a
watchdog that auto-restarts it on unexpected exit). This script stops the
server by killing whatever holds the configured port. On restart it first waits
for the tray watchdog to bring the server back with the new code; if nothing
comes back (no tray running) it launches `python api/main.py` itself.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

# Ensure console output never crashes on legacy code pages (Windows cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Config ─────────────────────────────────────────────────────────────────────

TARGET_DIR   = Path(__file__).resolve().parent          # the live account-warmer/
BACKUP_ROOT  = TARGET_DIR / "deploy_backups"
HEALTH_GRACE = 25     # seconds to let the tray watchdog restart the server
HEALTH_TOTAL = 90     # total seconds to wait for the server to become healthy
DRAIN_MAX    = 300    # max seconds to wait for running sessions in --mode wait

# Paths that are NEVER synced, backed up, or overwritten. Matched against every
# path segment (so "logs" blocks logs/ anywhere) and, for glob entries, against
# the file name.
BLOCKLIST = [
    "warmer.env", ".env", "ml_token.json", "ml_token_cache.json",
    "multilogin.yaml",
    "logs", "WarmingData", "WarmingData2",
    "__pycache__", ".venv", "venv", ".git",
    "deploy_backups",
    "*.log", "*.bak", "*.pyc",
]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _read_port() -> str:
    """Read SERVER_PORT from the target warmer.env (fallback 8000)."""
    env = TARGET_DIR / "warmer.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("SERVER_PORT") and "=" in line:
                return line.partition("=")[2].strip()
    return os.environ.get("SERVER_PORT", "8000")


PORT     = _read_port()
BASE_URL = f"http://127.0.0.1:{PORT}"


def is_blocklisted(rel_path: str) -> bool:
    """True if a project-relative path matches the safety blocklist."""
    parts = Path(rel_path).parts
    name  = Path(rel_path).name
    for entry in BLOCKLIST:
        if "*" in entry:
            if fnmatch(name, entry):
                return True
        elif entry in parts:
            return True
    return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: Path):
    """Yield project-relative paths of non-blocklisted files under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        # Prune blocklisted directories in place so os.walk skips them
        dirnames[:] = [d for d in dirnames
                       if not is_blocklisted(str(rel_dir / d))]
        for fn in filenames:
            rel = str(rel_dir / fn) if str(rel_dir) != "." else fn
            if not is_blocklisted(rel):
                yield rel


# ── Scan ─────────────────────────────────────────────────────────────────────

def scan(source_dir: Path, target_dir: Path) -> dict:
    """Compare source and target; return {'add','modify','delete'} rel-path lists."""
    src_files = set(_iter_files(source_dir))
    tgt_files = set(_iter_files(target_dir))

    add, modify = [], []
    for rel in sorted(src_files):
        s = source_dir / rel
        t = target_dir / rel
        if not t.exists():
            add.append(rel)
        elif s.stat().st_size != t.stat().st_size or _sha256(s) != _sha256(t):
            modify.append(rel)

    delete = sorted(tgt_files - src_files)
    return {"add": add, "modify": modify, "delete": delete}


def print_scan(changes: dict) -> None:
    print(f"\n  Changes to apply (source → live):")
    print(f"    + add    : {len(changes['add'])}")
    print(f"    ~ modify : {len(changes['modify'])}")
    print(f"    - delete : {len(changes['delete'])}")
    for label, key in (("+", "add"), ("~", "modify"), ("-", "delete")):
        for rel in changes[key]:
            print(f"      {label} {rel}")


# ── Server control ──────────────────────────────────────────────────────────

def health_ok(timeout: float = 2.0) -> bool:
    """True if the server answers on its port (any non-5xx response counts)."""
    try:
        req = urllib.request.Request(BASE_URL + "/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500          # 4xx still means the server is up
    except Exception:
        return False


def _pid_on_port() -> str | None:
    """Return the PID listening on PORT, or None."""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True,
                           text=True, timeout=10)
        for line in r.stdout.splitlines():
            if f":{PORT}" in line and "LISTENING" in line:
                return line.strip().split()[-1]
    except Exception as e:
        print(f"    ! port scan failed: {e}")
    return None


def _kill_port() -> bool:
    """Kill the process holding PORT. Returns True if something was killed."""
    pid = _pid_on_port()
    if not pid:
        return False
    print(f"    stopping server — killing PID {pid} on port {PORT}")
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                       capture_output=True, timeout=10)
    except Exception as e:
        print(f"    ! taskkill failed: {e}")
        return False
    return True


def drain_sessions() -> None:
    """--mode wait: pause the scheduler and wait for running sessions to finish."""
    print("  Draining active sessions (--mode wait)…")
    try:
        urllib.request.urlopen(urllib.request.Request(
            BASE_URL + "/api/scheduler/pause", method="POST"), timeout=5)
        print("    scheduler paused — no new sessions will start")
    except Exception as e:
        print(f"    ! could not pause scheduler (continuing): {e}")

    import json
    deadline = time.time() + DRAIN_MAX
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE_URL + "/api/scheduler/status", timeout=5) as r:
                data = json.loads(r.read().decode())
            running = [aid for aid, e in data.get("accounts", {}).items()
                       if e.get("status") == "running"]
        except Exception:
            running = []
        if not running:
            print("    no running sessions — safe to proceed")
            return
        print(f"    {len(running)} session(s) still running: {', '.join(running)} — waiting…")
        time.sleep(10)
    print("    ! drain timed out — proceeding anyway (sessions will be killed)")


def stop_server(mode: str) -> None:
    if mode == "wait" and health_ok():
        drain_sessions()
    _kill_port()
    # Confirm it's down
    for _ in range(10):
        if not health_ok(timeout=1):
            print("    server stopped")
            return
        time.sleep(0.5)
    print("    ! server still responding after stop attempt")


def start_server() -> bool:
    """
    Bring the server back. First give the tray watchdog a grace window to
    restart it with the new code; if nothing comes up, launch it directly.
    Returns True once healthy.
    """
    print("  Restarting server…")
    deadline = time.time() + HEALTH_TOTAL
    launched = False
    grace_until = time.time() + HEALTH_GRACE

    while time.time() < deadline:
        if health_ok(timeout=1):
            print(f"    server healthy on {BASE_URL}")
            return True
        # After the grace window, start it ourselves (no tray watchdog running)
        if not launched and time.time() > grace_until:
            print("    no watchdog restart detected — launching api/main.py directly")
            _kill_port()  # clear any half-bound zombie
            main_py = TARGET_DIR / "api" / "main.py"
            try:
                subprocess.Popen(
                    [sys.executable, str(main_py)],
                    cwd=str(TARGET_DIR),
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except Exception as e:
                print(f"    ! failed to launch server: {e}")
                return False
            launched = True
        time.sleep(2)

    print(f"    ! server did not become healthy within {HEALTH_TOTAL}s")
    return False


# ── Backup / sync / rollback ──────────────────────────────────────────────────

def create_backup(target_dir: Path) -> Path:
    """Copy the current (non-blocklisted) code tree to a timestamped backup."""
    stamp   = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest    = BACKUP_ROOT / stamp
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for rel in _iter_files(target_dir):
        src = target_dir / rel
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    print(f"  Backup created: {dest}  ({count} files)")
    return dest


def sync_files(source_dir: Path, target_dir: Path, changes: dict) -> None:
    """Apply add/modify/delete to the target, respecting the blocklist."""
    for rel in changes["add"] + changes["modify"]:
        if is_blocklisted(rel):          # defence in depth
            continue
        src = source_dir / rel
        dst = target_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for rel in changes["delete"]:
        if is_blocklisted(rel):
            continue
        try:
            (target_dir / rel).unlink()
        except FileNotFoundError:
            pass
    total = len(changes["add"]) + len(changes["modify"]) + len(changes["delete"])
    print(f"  Synced {total} change(s) "
          f"({len(changes['add'])} added, {len(changes['modify'])} modified, "
          f"{len(changes['delete'])} deleted)")


def rollback(backup_dir: Path, target_dir: Path) -> bool:
    """Restore the target code tree from a backup (blocklist-respecting mirror)."""
    if not backup_dir.exists():
        print(f"  ! backup not found: {backup_dir}")
        return False
    print(f"  Rolling back from {backup_dir} …")
    backup_files = set(_iter_files(backup_dir))
    # Restore every backed-up file
    for rel in backup_files:
        src = backup_dir / rel
        dst = target_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    # Remove code files that were added after the backup was taken
    for rel in _iter_files(target_dir):
        if rel not in backup_files and not is_blocklisted(rel):
            try:
                (target_dir / rel).unlink()
            except FileNotFoundError:
                pass
    print(f"  Restored {len(backup_files)} file(s) from backup")
    return True


# ── Git helpers ──────────────────────────────────────────────────────────────

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in TARGET_DIR. Returns CompletedProcess."""
    return subprocess.run(
        ["git"] + list(args), cwd=str(TARGET_DIR),
        capture_output=True, text=True, check=check,
    )


def _is_git_repo() -> bool:
    """True if TARGET_DIR is a git repository with a remote 'origin'."""
    try:
        r = _git("rev-parse", "--show-toplevel", check=False)
        if r.returncode != 0:
            return False
        r2 = _git("remote", "get-url", "origin", check=False)
        return r2.returncode == 0
    except Exception:
        return False


def git_status() -> int:
    """Print current branch, latest tag, clean/dirty status."""
    if not _is_git_repo():
        print("  Not a git repository (or no 'origin' remote configured).")
        return 1

    branch = _git("branch", "--show-current").stdout.strip()
    dirty  = _git("status", "--short").stdout.strip()
    latest_tag = _git("describe", "--tags", "--abbrev=0", check=False).stdout.strip() or "none"
    last_log   = _git("log", "-1", "--format=%h %s (%ar)").stdout.strip()

    print("=" * 48)
    print(f"  Git repo: {_git('remote', 'get-url', 'origin').stdout.strip()}")
    print(f"  Branch:   {branch}")
    print(f"  Latest tag: {latest_tag}")
    print(f"  Last commit: {last_log}")
    print(f"  Working tree: {'DIRTY (uncommitted changes)' if dirty else 'clean'}")
    if dirty:
        print("  Changes:")
        for line in dirty.splitlines()[:20]:
            print(f"    {line}")
    print("=" * 48)
    return 0


def _git_deploy(ref: str, mode: str, do_backup: bool, dry_run: bool, skip_prompt: bool) -> int:
    """Deploy from git by checking out a tag/branch then restarting."""
    if not _is_git_repo():
        print("  ! Not a git repo with an origin remote. Run 'git init' and add a remote first.")
        return 1

    # Resolve the ref
    try:
        _git("fetch", "origin")
    except Exception as e:
        print(f"  ! git fetch failed: {e}")
        return 1

    # Resolve 'latest' → the most recent tag, otherwise use the ref as-is.
    checkout_ref = ref
    if ref == "latest":
        r = _git("describe", "--tags", "--abbrev=0", "origin/main", check=False)
        if r.returncode != 0:
            print("  ! No tags found on origin/main — can't resolve 'latest'")
            return 1
        checkout_ref = r.stdout.strip()
        print(f"  latest → {checkout_ref}")

    # What tag/branch are we on right now?
    current = _git("describe", "--tags", "--always", check=False).stdout.strip()
    print(f"  Current: {current}  →  Deploying: {checkout_ref}")

    if current == checkout_ref:
        print("  Already on that version — nothing to deploy.")
        return 0

    if dry_run:
        # Show what files differ without changing anything
        print(f"\n  DRY-RUN — files that would change ({checkout_ref} vs current):")
        diff = _git("diff", "--name-status", f"{current}..{checkout_ref}", check=False)
        for line in diff.stdout.strip().splitlines():
            print(f"    {line}")
        return 0

    if not skip_prompt:
        ans = input(f"\nCheckout {checkout_ref} and restart the server? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Aborted."); return 1

    stop_server(mode)
    backup_dir = None
    if do_backup:
        backup_dir = create_backup(TARGET_DIR)

    try:
        r = _git("checkout", checkout_ref, check=False)
        if r.returncode != 0:
            print(f"  ! git checkout failed: {r.stderr}")
            if backup_dir:
                print("  Attempting automatic rollback…")
                rollback(backup_dir, TARGET_DIR)
            start_server()
            return 2
        print(f"  Checked out {checkout_ref}")
    except Exception as e:
        print(f"  ! git checkout error: {e}")
        if backup_dir:
            rollback(backup_dir, TARGET_DIR)
        start_server()
        return 2

    healthy = start_server()
    if not healthy and backup_dir:
        print("  ! server unhealthy after deploy — rolling back…")
        stop_server("defer")
        rollback(backup_dir, TARGET_DIR)
        healthy = start_server()

    print("\n" + "=" * 68)
    if healthy:
        print(f"  Deploy complete — {checkout_ref} is live." +
              (f"  Backup: {backup_dir}" if backup_dir else ""))
    else:
        print("  ! Deploy finished but the server is NOT healthy. Check logs.")
    print("=" * 68)
    return 0 if healthy else 2


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stop → backup → sync → restart the Account Warmer server.")
    sub = parser.add_subparsers(dest="command", help="Sub-command")

    # ── `deploy` sub-command (Git-based) ─────────────────────────────────────
    p_deploy = sub.add_parser("deploy", help="Deploy a version from the git remote")
    p_deploy.add_argument("--version", default="latest",
                          help="Tag or branch to deploy (default: latest tag)")
    p_deploy.add_argument("--mode", choices=["wait", "defer"], default="wait",
                          help="Restart mode (default: wait)")
    p_deploy.add_argument("--no-backup", action="store_true",
                          help="Skip the pre-deploy backup")
    p_deploy.add_argument("--yes", action="store_true",
                          help="Skip the confirmation prompt")
    p_deploy.add_argument("--dry-run", action="store_true",
                          help="Show what files would change; make no changes")

    # ── `git-status` sub-command ─────────────────────────────────────────────
    sub.add_parser("git-status", help="Show git branch, tags, and tree status")

    # ── Legacy flags (backward-compatible, no sub-command) ────────────────────
    parser.add_argument("--source", help="Folder to deploy code from (e.g. a USB path)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change; make no changes")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip the pre-deploy backup (backup is ON by default)")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt")
    parser.add_argument("--mode", choices=["wait", "defer"], default="defer",
                        help="'wait' drains running sessions first; 'defer' stops immediately")
    parser.add_argument("--rollback", metavar="BACKUP_DIR",
                        help="Restore code from a backup directory and restart")

    args = parser.parse_args()

    # ── Git sub-commands ─────────────────────────────────────────────────────
    if args.command == "git-status":
        return git_status()

    if args.command == "deploy":
        return _git_deploy(
            ref=args.version, mode=args.mode, do_backup=not args.no_backup,
            dry_run=args.dry_run, skip_prompt=args.yes,
        )

    # ── Legacy: --source / --rollback ────────────────────────────────────────
    print("=" * 68)
    print(f"  Account Warmer deploy  —  target: {TARGET_DIR}")
    print(f"  Port: {PORT}   Mode: {args.mode}   "
          f"Backup: {'off' if args.no_backup else 'on'}"
          f"{'   DRY-RUN' if args.dry_run else ''}")
    print("=" * 68)

    # ── Rollback path ─────────────────────────────────────────────────────────
    if args.rollback:
        backup_dir = Path(args.rollback)
        if not backup_dir.is_absolute():
            backup_dir = (BACKUP_ROOT / args.rollback) if not backup_dir.exists() else backup_dir
        if not args.yes:
            ans = input(f"\nRestore code from {backup_dir}? This overwrites live code. [y/N] ")
            if ans.strip().lower() not in ("y", "yes"):
                print("Aborted."); return 1
        stop_server(args.mode)
        ok = rollback(backup_dir, TARGET_DIR)
        if not ok:
            return 1
        healthy = start_server()
        print("\n  Rollback complete." if healthy else "\n  ! Rollback done but server unhealthy.")
        return 0 if healthy else 2

    # ── Deploy path ───────────────────────────────────────────────────────────
    if not args.source:
        parser.error("--source is required (or use --rollback)")
    source_dir = Path(args.source).resolve()
    if not source_dir.exists():
        print(f"  ! source not found: {source_dir}"); return 1
    if not (source_dir / "api" / "main.py").exists():
        print(f"  ! source does not look like an account-warmer folder "
              f"(no api/main.py): {source_dir}"); return 1
    if source_dir == TARGET_DIR:
        print("  ! source and target are the same folder — nothing to deploy."); return 1

    changes = scan(source_dir, TARGET_DIR)
    print_scan(changes)

    if not (changes["add"] or changes["modify"] or changes["delete"]):
        print("\n  Already up to date — nothing to do.")
        return 0

    if args.dry_run:
        print("\n  DRY-RUN — no changes made.")
        return 0

    if not args.yes:
        ans = input(f"\nApply these changes and restart the server? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Aborted."); return 1

    # Stop → backup → sync → restart, with automatic rollback on sync failure.
    stop_server(args.mode)

    backup_dir = None
    if not args.no_backup:
        backup_dir = create_backup(TARGET_DIR)

    try:
        sync_files(source_dir, TARGET_DIR, changes)
    except Exception as e:
        print(f"  ! sync failed: {e}")
        if backup_dir:
            print("  Attempting automatic rollback…")
            rollback(backup_dir, TARGET_DIR)
        start_server()
        return 2

    healthy = start_server()
    if not healthy and backup_dir:
        print("  ! server unhealthy after deploy — rolling back…")
        stop_server("defer")
        rollback(backup_dir, TARGET_DIR)
        healthy = start_server()

    print("\n" + "=" * 68)
    if healthy:
        print("  Deploy complete — server is up." +
              (f"  Backup: {backup_dir}" if backup_dir else ""))
    else:
        print("  ! Deploy finished but the server is NOT healthy. Check logs.")
    print("=" * 68)
    return 0 if healthy else 2


if __name__ == "__main__":
    sys.exit(main())
