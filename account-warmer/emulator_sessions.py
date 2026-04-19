"""
Emulator Session Automator

Automates GPS location sessions for all accounts using ADB.
Runs through accounts sequentially — one at a time, no simultaneous sessions.

Works with:
  - Android Studio AVD (recommended — full GPS control)
  - Bluestacks with ADB enabled (Settings → Advanced → ADB)
  - LDPlayer with ADB enabled

What it does per account:
  1. Switches to the account's Android user profile
  2. Injects GPS coordinates (home area, generic place, or target business)
  3. Opens Google Maps at those coordinates
  4. Waits a realistic time (location gets logged to Google Timeline)
  5. Optionally searches for the business listing
  6. Closes Maps and moves to the next account

Run daily or every few days — not every hour.

IMEI note:
  IMEI is a device-level identifier — all Android user profiles in one
  emulator instance share the same IMEI. Setup generates a realistic IMEI
  (Luhn-valid, real manufacturer TAC) and shows you where to apply it.
  If you run separate emulator instances, run --setup for each and note
  the different IMEI each generates.

Setup (first time only):
  python emulator_sessions.py --setup

Show current device config (IMEI, user mapping):
  python emulator_sessions.py --show-config

Run all accounts:
  python emulator_sessions.py --all

Run one account:
  python emulator_sessions.py --account acc_001

Check what each account would do today:
  python emulator_sessions.py --all --dry-run
"""

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

# ── Config paths ──────────────────────────────────────────────────────────────
from core.paths import ACCOUNTS_FILE, BUSINESSES_FILE, STATE_DIR
EMU_STATE_FILE = STATE_DIR / "emulator_users.json"

# Android Studio AVD config directory (Windows path)
AVD_DIR = Path.home() / ".android" / "avd"

# ── ADB settings ──────────────────────────────────────────────────────────────
# Bluestacks default ADB port. Android Studio AVD uses 5554, 5556, 5558...
ADB_HOST    = "127.0.0.1"
ADB_PORT    = 5555          # Change to 5554 for Android Studio AVD
ADB_DEVICE  = f"{ADB_HOST}:{ADB_PORT}"

# Path to adb.exe — update if yours is in a different location
ADB_PATHS = [
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",                                      # Bluestacks
    r"C:\LDPlayer\LDPlayer9\adb.exe",                                                    # LDPlayer
    r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
    str(Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe"),  # Android Studio
    "adb",                                                                               # If in PATH
]

# Mock GPS app broadcast — install one of these on each Android user profile.
# The app receives an ADB broadcast to set the mock location.
# Recommended: "Mock Locations" by Lexa (free, Play Store)
MOCK_GPS_APPS = {
    "lexa":    "com.lexa.fakegps",           # Fake GPS Location — lexa
    "mock":    "ru.gavrikov.mocklocations",   # Mock Locations — Gavrikov (recommended)
    "fly":     "com.incorporateapps.fakegps.fre",
}
GPS_APP = "mock"   # Change to whichever you install

# How long to stay at each location (seconds) — varies per visit type
VISIT_DURATIONS = {
    "home":     (180, 420),    # 3-7 min — just being home
    "generic":  (240, 540),    # 4-9 min — popped to the shops
    "business": (300, 720),    # 5-12 min — at the target business
}

# Generic nearby places to visit during warming (adds to naturalness)
# Coordinates are approximate — script offsets them slightly per session
GENERIC_LOCATION_TYPES = ["supermarket", "coffee shop", "pharmacy", "park"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_adb() -> str:
    for path in ADB_PATHS:
        try:
            result = subprocess.run(
                [path, "version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError(
        "ADB not found. Install Android Studio SDK platform-tools or Bluestacks.\n"
        "Or update ADB_PATHS in emulator_sessions.py with the correct path."
    )


def adb(*args, timeout: int = 15) -> str:
    """Run an ADB command, return stdout. Raises on failure."""
    adb_path = _find_adb()
    cmd = [adb_path, "-s", ADB_DEVICE] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0 and result.stderr:
        raise RuntimeError(f"ADB error: {result.stderr.strip()}")
    return result.stdout.strip()


def adb_shell(*args, timeout: int = 15) -> str:
    return adb("shell", *args, timeout=timeout)


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_accounts() -> list:
    return _load_yaml(ACCOUNTS_FILE).get("accounts", [])


def _load_businesses() -> dict:
    if not BUSINESSES_FILE.exists():
        return {}
    return {
        b["id"]: b
        for b in _load_yaml(BUSINESSES_FILE).get("businesses", [])
    }


def _load_emu_state() -> dict:
    if EMU_STATE_FILE.exists():
        with open(EMU_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_emu_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(EMU_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _load_biz_state(account_id: str, biz_id: str) -> dict:
    path = STATE_DIR / f"{account_id}_biz_{biz_id}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _offset_coords(lat: float, lng: float,
                   min_m: int = 30, max_m: int = 400) -> tuple[float, float]:
    """Offset coordinates by a random distance in a random direction."""
    import math
    distance = random.uniform(min_m, max_m)
    bearing  = random.uniform(0, 360)
    R = 6_371_000
    lat_r    = math.radians(lat)
    d_r      = distance / R
    b_r      = math.radians(bearing)
    new_lat  = math.asin(math.sin(lat_r) * math.cos(d_r) +
                         math.cos(lat_r) * math.sin(d_r) * math.cos(b_r))
    new_lng  = math.radians(lng) + math.atan2(
        math.sin(b_r) * math.sin(d_r) * math.cos(lat_r),
        math.cos(d_r) - math.sin(lat_r) * math.sin(new_lat)
    )
    return round(math.degrees(new_lat), 6), round(math.degrees(new_lng), 6)


# ── Location decision ─────────────────────────────────────────────────────────

def decide_location(account: dict, businesses: dict) -> dict:
    """
    Decide what location to visit today based on warming week and business phases.
    Returns dict with keys: type, lat, lng, label, business_id (optional)
    """
    from core.geolocation import CITY_CENTRES, coords_for_account

    start_str     = account.get("warmup_start_date", str(date.today()))
    start         = date.fromisoformat(start_str)
    weeks_elapsed = max(0, (date.today() - start).days // 7)
    account_id    = account["id"]

    # Check business signal phases
    target_ids = account.get("target_businesses", [])
    for biz_id in target_ids:
        biz = businesses.get(biz_id)
        if not biz or not biz.get("lat"):
            continue
        biz_state = _load_biz_state(account_id, biz_id)

        # Phase 2 complete + appointment date passed = visit the business
        if (biz_state.get("phase_2_complete")
                and not biz_state.get("emulator_visit_done")):
            appt_str  = biz_state.get("appointment_date", "")
            if appt_str and date.today() >= date.fromisoformat(appt_str):
                lat, lng = _offset_coords(
                    float(biz["lat"]), float(biz["lng"]), 30, 200
                )
                return {
                    "type":        "business",
                    "lat":         lat,
                    "lng":         lng,
                    "label":       biz["name"],
                    "business_id": biz_id,
                    "account_id":  account_id,
                }

    # Week 1-2: just home area
    if weeks_elapsed < 2:
        coords = coords_for_account(account, min_metres=100, max_metres=1500)
        if coords:
            return {
                "type":  "home",
                "lat":   coords[0],
                "lng":   coords[1],
                "label": f"Home area ({account.get('location', '')})",
            }

    # Week 2-4: generic local place
    if weeks_elapsed < 4:
        coords = coords_for_account(account, min_metres=300, max_metres=2000)
        if coords:
            place = random.choice(GENERIC_LOCATION_TYPES)
            return {
                "type":  "generic",
                "lat":   coords[0],
                "lng":   coords[1],
                "label": f"Local {place}",
            }

    # Week 4+: mix of home and business
    coords = coords_for_account(account, min_metres=100, max_metres=1500)
    if coords:
        return {
            "type":  "home",
            "lat":   coords[0],
            "lng":   coords[1],
            "label": f"Home area ({account.get('location', '')})",
        }

    return None


# ── Android user management ────────────────────────────────────────────────────

def get_android_users() -> dict:
    """Return {user_id: user_name} from the emulator."""
    try:
        output = adb_shell("pm", "list", "users")
        users  = {}
        for line in output.splitlines():
            # Format: "  UserInfo{10:acc_001:10} running"
            if "UserInfo{" in line:
                inner = line.split("{")[1].split("}")[0]
                parts = inner.split(":")
                if len(parts) >= 2:
                    user_id   = parts[0].strip()
                    user_name = parts[1].strip()
                    users[user_id] = user_name
        return users
    except Exception as e:
        print(f"  Could not list Android users: {e}")
        return {}


def switch_user(android_user_id: str) -> bool:
    """Switch to an Android user profile. Returns True on success."""
    try:
        adb_shell("am", "switch-user", android_user_id)
        time.sleep(3)  # Give Android time to switch
        return True
    except Exception as e:
        print(f"  User switch failed: {e}")
        return False


# ── GPS injection ──────────────────────────────────────────────────────────────

def set_gps(lat: float, lng: float) -> bool:
    """
    Inject GPS coordinates into the emulator.

    Method 1: Android Studio AVD telnet console (most reliable for AVD)
    Method 2: ADB broadcast to mock GPS app (for Bluestacks/LDPlayer)
    """

    # Method 1: AVD console (works for Android Studio emulators on port 5554)
    try:
        import telnetlib
        tn = telnetlib.Telnet("127.0.0.1", 5554, timeout=5)
        tn.read_until(b"OK", timeout=3)
        tn.write(f"geo fix {lng} {lat}\n".encode())
        tn.read_until(b"OK", timeout=3)
        tn.close()
        print(f"  GPS set via AVD console: {lat}, {lng}")
        return True
    except Exception:
        pass

    # Method 2: Mock GPS app broadcast (for Bluestacks/LDPlayer)
    app_pkg = MOCK_GPS_APPS.get(GPS_APP, MOCK_GPS_APPS["mock"])
    try:
        adb_shell(
            "am", "broadcast",
            "-a", f"{app_pkg}.SET_LOCATION",
            "--ef", "lat", str(lat),
            "--ef", "lng", str(lng),
            "-n", f"{app_pkg}/.MockLocationReceiver"
        )
        print(f"  GPS set via mock app broadcast: {lat}, {lng}")
        return True
    except Exception:
        pass

    # Method 3: Direct LocationManager via shell (requires root or development build)
    try:
        adb_shell(
            "am", "broadcast",
            "-a", "android.intent.action.BOOT_COMPLETED"
        )
        # Inject using content provider method
        adb_shell(
            "cmd", "location", "inject-nlp-location",
            f"--latitude={lat}", f"--longitude={lng}", "--accuracy=15"
        )
        print(f"  GPS set via cmd location: {lat}, {lng}")
        return True
    except Exception as e:
        print(f"  All GPS methods failed: {e}")
        return False


# ── Maps interaction ───────────────────────────────────────────────────────────

def open_maps_at_location(lat: float, lng: float, label: str) -> None:
    """Open Google Maps centred on the target coordinates."""
    try:
        # Open Maps with a geo URI — centres on coords
        adb_shell(
            "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", f"geo:{lat},{lng}?q={lat},{lng}({label.replace(' ', '+')})",
            "-n", "com.google.android.apps.maps/com.google.android.maps.MapsActivity"
        )
        print(f"  Maps opened at: {label}")
    except Exception as e:
        print(f"  Maps open failed: {e}")


def interact_with_maps(location: dict, duration_seconds: int) -> None:
    """
    Simulate natural Maps interaction during the location visit.
    Occasional taps and scrolls to show the app was actively used.
    """
    elapsed  = 0
    interval = random.randint(25, 55)  # Seconds between interactions

    while elapsed < duration_seconds:
        wait = min(interval, duration_seconds - elapsed)
        time.sleep(wait)
        elapsed += wait

        if elapsed >= duration_seconds:
            break

        # Occasional random interaction (tap, scroll)
        action = random.choice(["tap_center", "swipe_up", "swipe_down", "none"])
        if action == "tap_center":
            adb_shell("input", "tap",
                      str(random.randint(300, 700)),
                      str(random.randint(400, 900)))
        elif action == "swipe_up":
            adb_shell("input", "swipe", "540", "800", "540", "400", "600")
        elif action == "swipe_down":
            adb_shell("input", "swipe", "540", "400", "540", "800", "600")

        interval = random.randint(20, 50)

    # Close Maps when done
    try:
        adb_shell("input", "keyevent", "KEYCODE_HOME")
    except Exception:
        pass


def record_emulator_visit(location: dict) -> None:
    """Mark that the emulator visit for this business has been done."""
    biz_id     = location.get("business_id")
    account_id = location.get("account_id")
    if not biz_id or not account_id:
        return
    state_path = STATE_DIR / f"{account_id}_biz_{biz_id}.json"
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {}
    state["emulator_visit_done"] = True
    state["emulator_visit_date"] = str(date.today())
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── Per-account session ────────────────────────────────────────────────────────

def run_account_session(account: dict, businesses: dict,
                        emu_state: dict, dry_run: bool = False) -> bool:
    account_id = account["id"]
    print(f"\n{'─'*50}")
    print(f"  Account: {account_id} ({account['email']})")

    # Get the Android user ID for this account
    user_id = emu_state.get("user_map", {}).get(account_id)
    if not user_id:
        print(f"  No Android user mapped for {account_id}.")
        print(f"  Run: python emulator_sessions.py --setup")
        return False

    # Decide what location to visit
    location = decide_location(account, businesses)
    if not location:
        print(f"  No location to visit today for {account_id}")
        return True

    visit_type = location["type"]
    lat        = location["lat"]
    lng        = location["lng"]
    label      = location["label"]
    duration   = random.randint(*VISIT_DURATIONS[visit_type])

    print(f"  Location: {label}  ({visit_type})")
    print(f"  GPS:      {lat}, {lng}")
    print(f"  Duration: {duration}s ({duration//60}m {duration%60}s)")

    if dry_run:
        print(f"  [DRY RUN] Would switch to user {user_id} and run session")
        return True

    # Switch Android user
    print(f"  Switching to Android user: {user_id}")
    if not switch_user(user_id):
        return False
    time.sleep(2)

    # Inject GPS
    if not set_gps(lat, lng):
        print(f"  GPS injection failed — session aborted")
        return False
    time.sleep(3)  # Let the GPS settle

    # Open Maps and interact
    open_maps_at_location(lat, lng, label)
    time.sleep(4)  # Let Maps load
    print(f"  Staying at location for {duration}s …")
    interact_with_maps(location, duration)

    # Record completion if this was a business visit
    if visit_type == "business":
        record_emulator_visit(location)
        print(f"  Business visit recorded for: {label}")

    print(f"  Session complete: {account_id}")
    return True


# ── IMEI management ────────────────────────────────────────────────────────────

def _get_or_create_device_imei(emu_state: dict) -> tuple[str, str, str]:
    """
    Return the device IMEI from state, generating one if not yet assigned.
    Returns (imei, manufacturer, model).
    """
    if emu_state.get("device_imei"):
        return (
            emu_state["device_imei"],
            emu_state.get("device_manufacturer", "Unknown"),
            emu_state.get("device_model",        "Unknown"),
        )

    from core.imei import generate_imei
    imei, manufacturer, model = generate_imei()
    emu_state["device_imei"]          = imei
    emu_state["device_manufacturer"]  = manufacturer
    emu_state["device_model"]         = model
    return imei, manufacturer, model


def _patch_avd_imei(avd_name: str, imei: str) -> bool:
    """
    Write the IMEI into an Android Studio AVD hardware config file.
    The emulator must be stopped before running this.
    Returns True if the file was found and patched.
    """
    ini_path = AVD_DIR / f"{avd_name}.avd" / "hardware-qemu.ini"
    if not ini_path.exists():
        return False

    content = ini_path.read_text(encoding="utf-8")
    lines   = content.splitlines()
    found   = False
    patched = []
    for line in lines:
        if line.startswith("hw.gsmModem.imei"):
            patched.append(f"hw.gsmModem.imei = {imei}")
            found = True
        else:
            patched.append(line)
    if not found:
        patched.append(f"hw.gsmModem.imei = {imei}")

    ini_path.write_text("\n".join(patched) + "\n", encoding="utf-8")
    return True


def _list_avd_names() -> list[str]:
    """Return the names of all AVDs found in the Android SDK directory."""
    if not AVD_DIR.exists():
        return []
    return [p.stem for p in AVD_DIR.iterdir() if p.is_dir() and p.suffix == ".avd"]


def _print_imei_instructions(imei: str, manufacturer: str, model: str) -> None:
    """Print platform-specific instructions for applying the device IMEI."""
    print()
    print("  ─── Apply this IMEI to your emulator ─────────────────────────────")
    print(f"  IMEI:   {imei}")
    print(f"  Device: {manufacturer} {model}")
    print()
    print("  IMPORTANT: IMEI is device-level — all user profiles on one emulator")
    print("  instance share it. This is normal; real shared devices work the same.")
    print()
    print("  ── Android Studio AVD ─────────────────────────────────────────────")
    avd_names = _list_avd_names()
    if avd_names:
        print(f"  AVDs found: {', '.join(avd_names)}")
        print("  To auto-patch (stop the emulator first):")
        print(f"    python emulator_sessions.py --patch-avd <avd-name>")
    else:
        print("  No AVDs detected at ~/.android/avd/")
    print()
    print("  Manual: edit  ~/.android/avd/<name>.avd/hardware-qemu.ini")
    print(f"  Set:    hw.gsmModem.imei = {imei}")
    print("  Restart the emulator after saving.")
    print()
    print("  ── Bluestacks ─────────────────────────────────────────────────────")
    print("  Option A — Bluestacks settings (easiest):")
    print("    1. Open Bluestacks")
    print("    2. Settings → Phone → IMEI")
    print(f"    3. Enter: {imei}")
    print("    4. Click Change and restart Bluestacks")
    print()
    print("  Option B — Config file (if settings menu absent):")
    print("    Close Bluestacks, then edit:")
    print(r"    C:\ProgramData\BlueStacks_nxt\Engine\<instance>\Android.bstk.in")
    print("    Look for 'imei' and replace the value.")
    print()
    print("  ── LDPlayer ───────────────────────────────────────────────────────")
    print("  LDPlayer Multi-Instance Manager → right-click instance → Properties")
    print(f"  → Phone → IMEI → enter: {imei}")
    print("  ──────────────────────────────────────────────────────────────────")
    print()


def run_patch_avd(avd_name: str) -> None:
    """Patch the IMEI into an AVD config file (stop the emulator first)."""
    emu_state = _load_emu_state()
    imei, manufacturer, model = _get_or_create_device_imei(emu_state)
    _save_emu_state(emu_state)

    print(f"\n  Patching AVD: {avd_name}")
    if _patch_avd_imei(avd_name, imei):
        print(f"  IMEI set to {imei} ({manufacturer} {model})")
        print("  Restart the emulator for the change to take effect.")
    else:
        ini_path = AVD_DIR / f"{avd_name}.avd" / "hardware-qemu.ini"
        print(f"  AVD config not found: {ini_path}")
        print("  Available AVDs:", _list_avd_names() or "none")
    print()


def run_show_config() -> None:
    """Print the current device IMEI and account-to-user mapping."""
    emu_state = _load_emu_state()
    accounts  = _load_accounts()

    print()
    print("  ─── Emulator Configuration ────────────────────────────────────────")

    imei = emu_state.get("device_imei")
    if imei:
        mfr   = emu_state.get("device_manufacturer", "?")
        model = emu_state.get("device_model",        "?")
        print(f"  Device IMEI:   {imei}")
        print(f"  Device model:  {mfr} {model}")
    else:
        print("  Device IMEI:   not assigned yet (run --setup)")

    user_map = emu_state.get("user_map", {})
    if user_map:
        print()
        print("  Account → Android user mapping:")
        acc_lookup = {a["id"]: a["email"] for a in accounts}
        for acc_id, uid in user_map.items():
            email = acc_lookup.get(acc_id, "?")
            print(f"    {acc_id} ({email}) → Android user {uid}")
    else:
        print("  No accounts mapped yet (run --setup)")

    avd_names = _list_avd_names()
    if avd_names:
        print()
        print(f"  AVDs detected: {', '.join(avd_names)}")
        if imei:
            print(f"  Patch an AVD:  python emulator_sessions.py --patch-avd <name>")

    print()


# ── Setup ──────────────────────────────────────────────────────────────────────

def run_setup() -> None:
    """
    Interactive setup — maps account IDs to Android user IDs.
    Run this once after creating your Android user profiles.
    """
    print("\n  Emulator Session Setup")
    print("  ─────────────────────────────────────────────")
    print()

    # Try to connect to emulator
    print(f"  Connecting to emulator at {ADB_DEVICE} …")
    try:
        subprocess.run(
            [_find_adb(), "connect", ADB_DEVICE],
            capture_output=True, timeout=10
        )
        android_users = get_android_users()
    except Exception as e:
        print(f"  Could not connect: {e}")
        print(f"  Make sure the emulator is running and ADB is enabled.")
        return

    if not android_users:
        print("  No Android users found.")
        return

    print(f"  Android users found:")
    for uid, name in android_users.items():
        print(f"    User ID {uid}: {name}")
    print()

    accounts = _load_accounts()
    emu_state = _load_emu_state()
    user_map  = emu_state.get("user_map", {})

    print("  Map each account to an Android user ID.")
    print("  (Press Enter to skip an account)\n")

    for account in accounts:
        account_id = account["id"]
        email      = account["email"]
        current    = user_map.get(account_id, "not mapped")
        print(f"  {account_id} ({email})  — currently: {current}")
        uid = input(f"  Android user ID for {account_id}: ").strip()
        if uid:
            user_map[account_id] = uid
        print()

    emu_state["user_map"] = user_map

    # Generate device IMEI if not already assigned
    imei, manufacturer, model = _get_or_create_device_imei(emu_state)
    if not _load_emu_state().get("device_imei"):
        print(f"  New device IMEI generated: {imei}  ({manufacturer} {model})")

    _save_emu_state(emu_state)
    print(f"  Setup saved. {len(user_map)} accounts mapped.")
    print()

    # Summary
    print("  Current mapping:")
    for acc_id, uid in user_map.items():
        name = android_users.get(uid, "unknown")
        print(f"    {acc_id} → Android user {uid} ({name})")
    print()

    # Print IMEI application instructions
    _print_imei_instructions(imei, manufacturer, model)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Emulator Session Automator")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--all",         action="store_true", help="Run all accounts")
    group.add_argument("--account",     type=str,            help="Run a specific account")
    group.add_argument("--setup",       action="store_true", help="Map accounts to Android users and assign IMEI")
    group.add_argument("--show-config", action="store_true", help="Display IMEI and user mapping")
    group.add_argument("--patch-avd",   type=str, metavar="AVD_NAME",
                       help="Patch the assigned IMEI into an Android Studio AVD config file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run, no action")
    parser.add_argument("--port",    type=int,            help=f"ADB port (default: {ADB_PORT})")
    args = parser.parse_args()

    if args.port:
        global ADB_DEVICE
        ADB_DEVICE = f"{ADB_HOST}:{args.port}"

    if args.setup:
        run_setup()
        return

    if args.show_config:
        run_show_config()
        return

    if args.patch_avd:
        run_patch_avd(args.patch_avd)
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    accounts   = _load_accounts()
    businesses = _load_businesses()
    emu_state  = _load_emu_state()

    if not emu_state.get("user_map"):
        print("\n  No accounts mapped yet. Run setup first:")
        print("  python emulator_sessions.py --setup\n")
        sys.exit(1)

    # Connect ADB
    if not args.dry_run:
        try:
            adb_exe = _find_adb()
            subprocess.run(
                [adb_exe, "connect", ADB_DEVICE],
                capture_output=True, timeout=10
            )
            print(f"  ADB connected to {ADB_DEVICE}")
        except Exception as e:
            print(f"  ADB connection failed: {e}")
            sys.exit(1)

    if args.account:
        account = next((a for a in accounts if a["id"] == args.account), None)
        if not account:
            print(f"  Account not found: {args.account}")
            sys.exit(1)
        run_account_session(account, businesses, emu_state, dry_run=args.dry_run)

    elif args.all:
        success = 0
        for account in accounts:
            ok = run_account_session(
                account, businesses, emu_state, dry_run=args.dry_run
            )
            if ok:
                success += 1
            # Gap between accounts — don't rush
            if account != accounts[-1]:
                gap = random.randint(15, 45)
                print(f"  Waiting {gap}s before next account …")
                if not args.dry_run:
                    time.sleep(gap)

        print(f"\n  Done — {success}/{len(accounts)} sessions completed.\n")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
