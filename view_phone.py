"""
view_phone.py — Open a GeelarK cloud phone viewer in your browser.

Usage:
  python view_phone.py --account gl_001        Start phone + open viewer
  python view_phone.py --account gl_001 --screenshot   Take a screenshot instead
  python view_phone.py --account gl_001 --stop         Stop the phone
  python view_phone.py --list                          Show all phones + status
"""

import argparse
import subprocess
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Bootstrap warmer.env
_env = Path(__file__).parent / "warmer.env"
if _env.exists():
    import os
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger("view_phone")

import yaml
from core.geelark_client import GeelarKClient

DATA_FILE = Path(os.environ.get("WARMER_DATA_DIR", r"C:\WarmingData")) / "geelark_accounts.yaml"


def _load_accounts() -> list:
    data = yaml.safe_load(DATA_FILE.read_text(encoding="utf-8")) or {}
    return data.get("accounts", [])


def _open_in_browser(url: str) -> None:
    """Open a URL in the default Windows browser. Uses PowerShell to handle & in URLs."""
    try:
        subprocess.Popen(
            ["powershell", "-Command", f'Start-Process "{url}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Opened in browser: %s", url[:100])
    except Exception as e:
        log.error("Could not open browser: %s", e)
        print(f"\nOpen this URL manually:\n  {url}\n")


def cmd_view(account_id: str) -> None:
    accounts = _load_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        log.error("Account %r not found.", account_id)
        sys.exit(1)

    phone_id = acc.get("geelark_phone_id")
    if not phone_id:
        log.error("No phone_id for account %s.", account_id)
        sys.exit(1)

    client = GeelarKClient()
    log.info("Starting phone %s (%s) ...", account_id, acc.get("email", ""))
    try:
        viewer_url = client.start_phone(phone_id)
    except RuntimeError as e:
        msg = str(e)
        # Phone may already be running — try to get viewer URL from status
        if "occupied" in msg.lower() or "running" in msg.lower() or "started" in msg.lower():
            log.info("Phone already running. Fetching viewer URL ...")
            viewer_url = _get_viewer_url_running(client, phone_id)
        else:
            log.error("Failed to start phone: %s", e)
            sys.exit(1)

    if not viewer_url:
        log.error("No viewer URL returned. Is the phone already open in another tab?")
        sys.exit(1)

    log.info("Phone starting. Opening viewer ...")
    _open_in_browser(viewer_url)
    print(f"\nViewer URL (copy if browser didn't open):\n  {viewer_url}\n")


def _get_viewer_url_running(client: GeelarKClient, phone_id: str) -> str:
    """Try to get viewer URL for an already-running phone via the list endpoint."""
    try:
        from core.geelark_client import _post
        result = _post("/open/v1/phone/list", {"page": 1, "pageSize": 100})
        items = result.get("items") or result.get("list") or []
        for item in items:
            if str(item.get("id", "")) == str(phone_id):
                return item.get("url") or item.get("viewUrl") or ""
    except Exception as e:
        log.warning("Could not fetch viewer URL from list: %s", e)
    return ""


def cmd_screenshot(account_id: str) -> None:
    accounts = _load_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        log.error("Account %r not found.", account_id)
        sys.exit(1)

    phone_id = acc.get("geelark_phone_id")
    if not phone_id:
        log.error("No phone_id for account %s.", account_id)
        sys.exit(1)

    client = GeelarKClient()
    log.info("Taking screenshot of %s ...", account_id)
    img = client.take_screenshot(phone_id)
    if not img:
        log.error("Screenshot failed — is the phone running?")
        sys.exit(1)

    out = Path(__file__).parent / f"screenshot_{account_id}_{int(time.time())}.png"
    out.write_bytes(img)
    log.info("Saved: %s", out)

    # Open the screenshot in the default image viewer
    subprocess.Popen(["powershell", "-Command", f'Start-Process "{out}"'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_stop(account_id: str) -> None:
    accounts = _load_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        log.error("Account %r not found.", account_id)
        sys.exit(1)

    phone_id = acc.get("geelark_phone_id")
    client = GeelarKClient()
    client.stop_phone(phone_id)
    log.info("Phone %s stopped.", account_id)


def cmd_list() -> None:
    accounts = _load_accounts()
    print(f"\n{'ID':<10}  {'Email':<36}  {'Phone ID':<20}  {'Login':<8}  {'Setup'}")
    print("-" * 90)
    for a in accounts:
        verified = "YES" if a.get("login_verified") else "no"
        setup    = "YES" if a.get("mobile_setup_done") else "no"
        print(f"{a.get('id',''):<10}  {a.get('email',''):<36}  "
              f"{a.get('geelark_phone_id',''):<20}  {verified:<8}  {setup}")
    print()


def main():
    import os
    parser = argparse.ArgumentParser(description="View or control a GeelarK cloud phone")
    parser.add_argument("--account", metavar="ID", help="Account ID (e.g. gl_001)")
    parser.add_argument("--screenshot", action="store_true", help="Take a screenshot")
    parser.add_argument("--stop", action="store_true", help="Stop the phone")
    parser.add_argument("--list", action="store_true", help="List all accounts and status")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.account and args.screenshot:
        cmd_screenshot(args.account)
    elif args.account and args.stop:
        cmd_stop(args.account)
    elif args.account:
        cmd_view(args.account)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
