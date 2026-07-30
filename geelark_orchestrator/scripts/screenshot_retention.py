#!/usr/bin/env python3
"""
screenshot_retention.py — Keep only the last N days of screenshots.

Run manually or via cron. Example:
    python screenshot_retention.py --days 7
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def cleanup(screenshots_dir: Path, days: int, dry_run: bool = False) -> int:
    if not screenshots_dir.exists():
        print(f"Directory does not exist: {screenshots_dir}")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    kept = 0
    total_bytes = 0

    for p in screenshots_dir.iterdir():
        if not p.is_file():
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            total_bytes += p.stat().st_size
            removed += 1
            if not dry_run:
                p.unlink()
                print(f"  removed: {p.name} ({mtime.isoformat()})")
            else:
                print(f"  would remove: {p.name} ({mtime.isoformat()})")
        else:
            kept += 1

    action = "Would remove" if dry_run else "Removed"
    print(f"{action} {removed} file(s), kept {kept} file(s).")
    if removed:
        print(f"Space freed: {total_bytes / 1024 / 1024:.2f} MB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Screenshot retention cleanup")
    ap.add_argument("--dir", default="logs/screenshots", help="Screenshots directory")
    ap.add_argument("--days", type=int, default=7, help="Keep files newer than N days")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be deleted")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    screenshots_dir = repo_root / args.dir
    return cleanup(screenshots_dir, args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
