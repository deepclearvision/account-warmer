"""
download_profile_photos.py — Download AI-generated face photos for account profiles.

Fetches images from thispersondoesnotexist.com (free, no account needed).
Each request returns a unique AI-generated face. Images are saved to
account-warmer/data/profile_photos/.

Usage:
    python download_profile_photos.py          # download until 50 photos exist
    python download_profile_photos.py --count 30
    python download_profile_photos.py --list   # show what's already downloaded
"""

import argparse
import time
from pathlib import Path

import requests

PHOTOS_DIR = Path(__file__).parent / "data" / "profile_photos"
DEFAULT_COUNT = 50
SOURCE_URL = "https://thispersondoesnotexist.com/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def list_photos() -> list[Path]:
    if not PHOTOS_DIR.exists():
        return []
    return sorted(PHOTOS_DIR.glob("face_*.jpg"))


def download_photos(target_count: int) -> None:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    existing = list_photos()
    already = len(existing)
    needed = target_count - already

    if needed <= 0:
        print(f"Already have {already} photos in {PHOTOS_DIR} — nothing to do.")
        return

    print(f"Downloading {needed} photo(s) to {PHOTOS_DIR}")
    # Next index based on highest existing number to avoid collisions
    next_idx = already + 1
    if existing:
        last_name = existing[-1].stem  # e.g. "face_042"
        try:
            next_idx = int(last_name.split("_")[1]) + 1
        except (IndexError, ValueError):
            pass

    downloaded = 0
    for i in range(needed):
        idx = next_idx + i
        dest = PHOTOS_DIR / f"face_{idx:03d}.jpg"
        try:
            resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            if not resp.content[:3] == b"\xff\xd8\xff":
                print(f"  [{i+1}/{needed}] Warning: response doesn't look like JPEG — saving anyway")
            dest.write_bytes(resp.content)
            downloaded += 1
            print(f"  [{downloaded}/{needed}] Saved {dest.name} ({len(resp.content)//1024} KB)")
        except Exception as e:
            print(f"  [{i+1}/{needed}] ERROR: {e}")

        if i < needed - 1:
            time.sleep(1.2)  # polite delay between requests

    print(f"\nDone. {downloaded}/{needed} photos downloaded. Total: {len(list_photos())}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download AI face photos for account profiles")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"Target total number of photos (default: {DEFAULT_COUNT})")
    parser.add_argument("--list", action="store_true", help="List existing photos and exit")
    args = parser.parse_args()

    if args.list:
        photos = list_photos()
        if not photos:
            print(f"No photos found in {PHOTOS_DIR}")
        else:
            print(f"{len(photos)} photo(s) in {PHOTOS_DIR}:")
            for p in photos:
                print(f"  {p.name}  ({p.stat().st_size // 1024} KB)")
        return

    download_photos(args.count)


if __name__ == "__main__":
    main()
