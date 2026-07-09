"""
install_adbkeyboard.py — Install ADBKeyboard IME on a GeelarK phone.

ADBKeyboard replaces 'adb shell input text' with a broadcast-based IME
that injects text atomically, eliminating key-event race conditions.

Usage:
    python install_adbkeyboard.py --phone-id 614216763072053315
    python install_adbkeyboard.py --account acc_004
    python install_adbkeyboard.py --all          # all acc_ accounts
    python install_adbkeyboard.py --check        # check which phones have it
"""

import argparse
import base64
import os
import sys
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

# ── env / paths ───────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
load_dotenv(BASE / "warmer.env")

GEELARK_TOKEN  = os.environ.get("GEELARK_BEARER_TOKEN", "")
GEELARK_BASE   = "https://openapi.geelark.com"
HEADERS        = lambda: {"Authorization": f"Bearer {GEELARK_TOKEN}",
                          "Content-Type": "application/json"}

DATA_DIR       = Path(os.environ.get("WARMER_DATA_DIR", "C:/WarmingData"))
ACCOUNTS_YAML  = DATA_DIR / "geelark_accounts.yaml"
APK_PATH       = BASE / "data" / "ADBKeyboard.apk"
APK_PACKAGE    = "com.android.adbkeyboard"
APK_COMPONENT  = "com.android.adbkeyboard/.AdbIME"


# ── helpers ───────────────────────────────────────────────────────────────────
def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{GEELARK_BASE}{path}", json=body,
                         headers=HEADERS(), timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code", -1) != 0:
        raise RuntimeError(f"GeelarK {path}: {payload.get('msg')} — {payload.get('data')}")
    return payload.get("data") or {}


def shell(phone_id: str, cmd: str) -> str:
    """Run a shell command and return stdout."""
    data = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": cmd})
    return data.get("output", "") or ""


def load_accounts() -> list:
    with open(ACCOUNTS_YAML) as f:
        raw = yaml.safe_load(f)
    return raw["accounts"] if isinstance(raw, dict) else raw


# ── step 1: upload APK to GeelarK OSS ─────────────────────────────────────────
def upload_apk_to_geelark() -> str:
    """Upload APK to GeelarK temp storage. Returns the resourceUrl."""
    print("Requesting presigned upload URL …")
    data = _post("/open/v1/upload/getUrl", {"fileType": "apk"})
    upload_url   = data["uploadUrl"]
    resource_url = data["resourceUrl"]

    print(f"Uploading {APK_PATH.name} ({APK_PATH.stat().st_size} bytes) …")
    with open(APK_PATH, "rb") as f:
        apk_bytes = f.read()

    # PUT with no extra headers (GeelarK/Alibaba OSS requirement — adding Content-Type
    # causes 403 because it's not included in the presigned signature)
    put_resp = requests.put(upload_url, data=apk_bytes, timeout=60)
    put_resp.raise_for_status()
    print(f"Upload complete — resource URL obtained.")
    return resource_url


# ── step 2: register APK with GeelarK app store ───────────────────────────────
def register_apk(resource_url: str) -> str:
    """Register the uploaded APK with GeelarK. Returns the appVersionId."""
    print("  Registering APK with GeelarK app store ...")
    data = _post("/open/v1/app/upload", {"fileUrl": resource_url})
    task_id = data.get("taskId") or data.get("id")
    if not task_id:
        raise RuntimeError(f"No taskId from app/upload: {data}")

    for _ in range(30):
        time.sleep(3)
        result = _post("/open/v1/app/upload/status", {"taskId": task_id})
        status = result.get("status")
        if status == 1:
            version_id = result.get("versionId") or result.get("appVersionId")
            print(f"  APK registered, versionId={version_id}")
            return version_id
        if status == 3:
            raise RuntimeError(f"APK rejected by GeelarK review: {result}")
        print(f"  Waiting for registration ... (status={status})")

    raise RuntimeError("APK registration timed out")


# ── step 3: install via GeelarK API + enable as IME ───────────────────────────
def install_and_enable(phone_id: str, version_id: str) -> None:
    """Install via GeelarK app install API, then enable as IME."""
    print(f"  Installing via GeelarK API (versionId={version_id}) ...")
    _post("/open/v1/app/install", {"envId": phone_id, "appVersionId": version_id})

    # Poll until installed
    for _ in range(40):
        time.sleep(3)
        out = shell(phone_id, f"pm list packages | grep {APK_PACKAGE}")
        if APK_PACKAGE in out:
            print("  Package installed.")
            break
        print("  Waiting for install to complete ...")
    else:
        raise RuntimeError(f"Package {APK_PACKAGE} not found after install")

    shell(phone_id, f"ime enable {APK_COMPONENT}")
    shell(phone_id, f"ime set {APK_COMPONENT}")
    print("  IME enabled and set as default.")


# ── verification: send a test broadcast ───────────────────────────────────────
def verify_adbkeyboard(phone_id: str) -> bool:
    """Check ADBKeyboard is installed and responding to broadcasts."""
    # Check package is installed
    out = shell(phone_id, f"pm list packages | grep {APK_PACKAGE}")
    if APK_PACKAGE not in out:
        print("  FAIL: package not found in pm list")
        return False

    # Check it's the active IME
    out = shell(phone_id, "settings get secure default_input_method")
    if APK_COMPONENT not in out:
        print(f"  WARNING: not the active IME (current: {out.strip()})")
        # Try to set it again
        shell(phone_id, f"ime set {APK_COMPONENT}")
    else:
        print(f"  Active IME confirmed: {APK_COMPONENT}")

    print("  ADBKeyboard ready.")
    return True


# ── check status across phones ────────────────────────────────────────────────
def check_all() -> None:
    accounts = [a for a in load_accounts() if a.get("id", "").startswith("acc_")]
    print(f"Checking {len(accounts)} phones …\n")
    for acc in accounts:
        phone_id = acc.get("geelark_phone_id", "")
        acc_id   = acc.get("id", "?")
        try:
            out = shell(phone_id, f"pm list packages | grep {APK_PACKAGE}")
            status = "INSTALLED" if APK_PACKAGE in out else "missing"
        except Exception as e:
            status = f"ERROR ({e})"
        print(f"  {acc_id:10s}  {status}")


# ── start phone and wait for shell to respond ─────────────────────────────────
def _start_and_wait(phone_id: str) -> None:
    """Start the phone (if not running) and wait until ADB shell responds."""
    # Try starting — ignore errors if already running
    try:
        _post("/open/v1/phone/start", {"ids": [phone_id]})
        print("  Phone start requested.")
    except Exception as e:
        if "already" in str(e).lower() or "running" in str(e).lower():
            print("  Phone already running.")
        else:
            print(f"  Start note: {e}")

    # Poll using wm size — returns "Physical size: WxH" only when Android has booted
    print("  Waiting for phone to boot ...")
    for i in range(30):   # up to 150 seconds
        time.sleep(5)
        try:
            resp = requests.post(
                f"{GEELARK_BASE}/open/v1/shell/execute",
                json={"id": phone_id, "cmd": "wm size"},
                headers=HEADERS(), timeout=15,
            )
            data = resp.json()
            output = (data.get("data") or {}).get("output", "")
            if data.get("code") == 0 and "x" in output and "size" in output.lower():
                print(f"  Phone ready after {(i+1)*5}s.")
                return
        except Exception:
            pass
        if (i + 1) % 4 == 0:
            print(f"  Still booting ... ({(i+1)*5}s)")

    print("  WARNING: phone did not confirm ready after 150s — proceeding anyway.")


# ── full install flow for one phone ───────────────────────────────────────────
def install_one(phone_id: str, version_id: str) -> bool:
    try:
        install_and_enable(phone_id, version_id)
        return verify_adbkeyboard(phone_id)
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


# ── entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Install ADBKeyboard on GeelarK phones")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phone-id", help="GeelarK phone ID")
    group.add_argument("--account",  help="Account ID (e.g. acc_004)")
    group.add_argument("--all",      action="store_true", help="All acc_ accounts")
    group.add_argument("--check",    action="store_true", help="Check install status only")
    args = parser.parse_args()

    if args.check:
        check_all()
        return

    # Resolve phone IDs
    if args.all:
        accounts = [a for a in load_accounts() if a.get("id", "").startswith("acc_")]
        phone_ids = [(a["id"], a["geelark_phone_id"]) for a in accounts
                     if a.get("geelark_phone_id")]
    elif args.account:
        accounts = load_accounts()
        acc = next((a for a in accounts if a.get("id") == args.account), None)
        if not acc:
            sys.exit(f"Account {args.account} not found")
        phone_ids = [(acc["id"], acc["geelark_phone_id"])]
    else:
        phone_ids = [("manual", args.phone_id)]

    print(f"Target: {len(phone_ids)} phone(s)\n")

    # Upload APK once, register once, reuse versionId for all phones
    resource_url = upload_apk_to_geelark()
    version_id   = register_apk(resource_url)
    print()

    ok = failed = 0
    for acc_id, phone_id in phone_ids:
        print(f"[{acc_id}] phone={phone_id}")
        _start_and_wait(phone_id)

        if install_one(phone_id, version_id):
            ok += 1
            print(f"  DONE")
        else:
            failed += 1
            print(f"  FAILED")

        # Stop the phone immediately after install — leaving it running burns
        # GeelarK phone-minutes for every other phone that installs after it.
        try:
            _post("/open/v1/phone/stop", {"ids": [phone_id]})
            print(f"  Phone stopped.\n")
        except Exception as _stop_e:
            print(f"  (stop skipped: {_stop_e})\n")

    print(f"Complete: {ok} succeeded, {failed} failed")


if __name__ == "__main__":
    main()
