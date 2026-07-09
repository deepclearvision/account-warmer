"""
setup_new_instance.py - First-run setup for a new Account Warmer instance.

Handles everything needed to go from a fresh code copy to a running instance:
  1. Creates WarmingData directory structure
  2. Initialises empty YAML data files (accounts, proxies, businesses, geelark)
  3. Prompts for PC_ID if not set
  4. Validates Python, dependencies, and service connectivity
  5. Reports what still needs manual configuration

Usage:
  python setup_new_instance.py              # Interactive setup
  python setup_new_instance.py --pc pc_2    # Non-interactive: set PC_ID
  python setup_new_instance.py --check      # Validate only, don't create files
  python setup_new_instance.py --reset      # Wipe WarmingData to fresh state
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure we can import from account-warmer
sys.path.insert(0, str(Path(__file__).parent))

# Load warmer.env early so DATA_DIR is resolved correctly
_env_file = Path(__file__).parent / "warmer.env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from core.paths import DATA_DIR, LOGS_DIR, STATE_DIR, ensure_data_dirs
from core.paths import (
    ACCOUNTS_FILE, GEELARK_ACCOUNTS_FILE, GEELARK_FLOW_DATA_FILE,
    BUSINESSES_FILE, PROXIES_FILE, TOKEN_FILE, SCHEDULER_STATE_FILE,
)


def header(text: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"  {text}")
    print(f"{'-' * 60}")


def ok(text: str) -> None:
    print(f"  [OK]  {text}")


def warn(text: str) -> None:
    print(f"  [WARN] {text}")


def fail(text: str) -> None:
    print(f"  [FAIL] {text}")


def info(text: str) -> None:
    print(f"     {text}")


# -- Step 1: Create directory structure ----------------------------------------

def create_directory_structure() -> bool:
    header("Step 1: Directory Structure")
    try:
        ensure_data_dirs()
        ok(f"DATA_DIR  = {DATA_DIR}")
        ok(f"LOGS_DIR  = {LOGS_DIR}")
        ok(f"STATE_DIR = {STATE_DIR}")
        return True
    except Exception as e:
        fail(f"Could not create directories: {e}")
        return False


# -- Step 2: Initialise empty YAML files ---------------------------------------

EMPTY_ACCOUNTS_YAML = "# Account Warmer - Desktop Accounts\n# Each entry = one Google account warmed via Multilogin browser.\naccounts: []\n"
EMPTY_GEELARK_YAML  = "# Account Warmer - Mobile Accounts\n# Each entry = one Google account on a GeelarK cloud phone.\naccounts: []\n"
EMPTY_PROXIES_YAML  = "# Account Warmer - Proxy Pool\n# Assign proxies to accounts via the Proxies tab.\nproxies: []\n"
EMPTY_BUSINESSES_YAML = "# Account Warmer - Business Targets\n# Used by maps_browse, maps_review, and business_signal activities.\nbusinesses: []\n"

def init_empty_files(reset: bool = False) -> bool:
    header("Step 2: Data Files")

    files: list[tuple[Path, str, str]] = [
        (ACCOUNTS_FILE,         "Desktop accounts",          EMPTY_ACCOUNTS_YAML),
        (GEELARK_ACCOUNTS_FILE, "Mobile accounts",           EMPTY_GEELARK_YAML),
        (PROXIES_FILE,          "Proxy pool",                EMPTY_PROXIES_YAML),
        (BUSINESSES_FILE,       "Business targets",          EMPTY_BUSINESSES_YAML),
    ]

    all_ok = True
    for path, label, template in files:
        if path.exists() and not reset:
            ok(f"{label}: {path.name} (already exists - skipped)")
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(template, encoding="utf-8")
            if reset:
                ok(f"{label}: {path.name} (reset to empty)")
            else:
                ok(f"{label}: {path.name} (created)")
        except Exception as e:
            fail(f"{label}: {e}")
            all_ok = False

    # Flow data - just remove if resetting, otherwise leave alone
    if reset and GEELARK_FLOW_DATA_FILE.exists():
        GEELARK_FLOW_DATA_FILE.unlink()
        ok("Flow data: deleted (will be regenerated)")

    # Clean logs on reset
    if reset:
        import shutil
        for d in [LOGS_DIR, STATE_DIR]:
            if d.exists():
                shutil.rmtree(d)
                d.mkdir(parents=True, exist_ok=True)
        ok("Logs and state: cleared")

    return all_ok


# -- Step 3: PC_ID configuration -----------------------------------------------

def configure_pc_id(pc_id: str = "") -> bool:
    header("Step 3: PC Identity")

    current = os.environ.get("PC_ID", "").strip()

    if pc_id:
        new_id = pc_id.strip()
    elif current:
        info(f"Current PC_ID = '{current}'")
        choice = input("  Change it? (y/N): ").strip().lower()
        if choice in ("y", "yes"):
            new_id = input("  New PC_ID: ").strip()
        else:
            new_id = current
    else:
        info("PC_ID identifies this computer to the scheduler.")
        info("Only accounts whose 'category' field matches PC_ID are warmed.")
        info("Leave blank to run ALL accounts (single-computer setup).")
        new_id = input("  PC_ID for this computer (e.g. pc_1): ").strip()

    if new_id != current:
        # Update warmer.env
        env_path = Path(__file__).parent / "warmer.env"
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("PC_ID="):
                new_lines.append(f"PC_ID={new_id}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"\nPC_ID={new_id}")
        env_path.write_text("\n".join(new_lines), encoding="utf-8")
        os.environ["PC_ID"] = new_id or ""
        ok(f"PC_ID set to '{new_id or '(blank - runs all accounts)'}'")
    else:
        ok(f"PC_ID = '{current or '(blank)'}' (unchanged)")

    return True


# -- Step 4: Validate Python & dependencies ------------------------------------

REQUIRED_PACKAGES = [
    ("yaml",       "pyyaml"),
    ("playwright", "playwright"),
    ("fastapi",    "fastapi"),
    ("uvicorn",    "uvicorn"),
    ("pydantic",   "pydantic"),
    ("requests",   "requests"),
    ("numpy",      "numpy"),
    ("PIL",        "pillow"),
    ("anthropic",  "anthropic"),
    ("pyotp",      "pyotp"),
]

def validate_dependencies() -> bool:
    header("Step 4: Dependencies")
    all_ok = True
    for module, package in REQUIRED_PACKAGES:
        try:
            __import__(module)
            ok(f"{package}")
        except ImportError:
            fail(f"{package} - pip install {package}")
            all_ok = False
    return all_ok


# -- Step 5: Service connectivity ----------------------------------------------

def check_service_connectivity() -> bool:
    header("Step 5: Service Connectivity (best-effort)")

    all_ok = True

    # Multilogin
    try:
        import requests as _r
        resp = _r.get("http://localhost:45001/api/v2/profile", timeout=5)
        if resp.status_code in (200, 401):
            ok("Multilogin API (port 45001) - reachable")
        else:
            warn(f"Multilogin API returned {resp.status_code}")
    except Exception:
        warn("Multilogin API - not reachable (is Multilogin X running?)")
        all_ok = False

    # GeelarK
    api_key = os.environ.get("GEELARK_API_KEY", "")
    if api_key:
        try:
            import requests as _r2
            from core.geelark_client import _post
            bal = _post("/open/v1/balance", {})
            if bal:
                ok(f"GeelarK API - connected (balance response received)")
            else:
                warn("GeelarK API - connected but balance check failed")
        except Exception as e:
            warn(f"GeelarK API - not reachable ({e})")
            all_ok = False
    else:
        warn("GeelarK API - GEELARK_API_KEY not set")

    return all_ok


# -- Step 6: Summary -----------------------------------------------------------

def print_summary(pc_id: str = "") -> None:
    header("Setup Complete - Summary")

    pc = os.environ.get("PC_ID", "").strip()
    print(f"""
  Data directory:    {DATA_DIR}
  PC_ID:             {pc or '(all accounts)'}
  Desktop accounts:  {ACCOUNTS_FILE}
  Mobile accounts:   {GEELARK_ACCOUNTS_FILE}
  Proxies:           {PROXIES_FILE}
  Businesses:        {BUSINESSES_FILE}
  Logs:              {LOGS_DIR}

  Next steps:
""")
    steps = [
        "1. Import accounts:  python import_accounts.py --csv your_file.csv",
        "                     (or use the dashboard Accounts → Import CSV)",
        "2. Assign proxies:   python set_proxies.py",
        "                     (or use the dashboard Proxies tab)",
        "3. Start warming:    python tray.py",
        "                     (dashboard at http://localhost:8000)",
    ]
    if pc:
        steps.insert(1, f"   Make sure imported accounts have category = '{pc}'")
    for s in steps:
        print(f"  {s}")
    print()


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="First-run setup for Account Warmer instance",
    )
    parser.add_argument("--pc",    type=str,  help="Set PC_ID (computer label)")
    parser.add_argument("--check", action="store_true", help="Validate only, no file creation")
    parser.add_argument("--reset", action="store_true", help="Wipe WarmingData to fresh empty state")
    args = parser.parse_args()

    print()
    print("  Account Warmer - New Instance Setup")
    print("  " + "=" * 38)

    if args.check:
        validate_dependencies()
        check_service_connectivity()
        return

    if args.reset:
        print("\n  [WARNING] This will DELETE all data in WarmingData!")
        print(f"     {DATA_DIR}")
        confirm = input("\n  Type 'yes' to confirm: ").strip()
        if confirm.lower() != "yes":
            print("  Aborted.")
            return
        create_directory_structure()
        init_empty_files(reset=True)
        print("\n  WarmingData has been reset to a clean state.")
        return

    # Full setup
    create_directory_structure()
    init_empty_files(reset=False)
    configure_pc_id(args.pc)
    validate_dependencies()
    check_service_connectivity()
    print_summary(args.pc)


if __name__ == "__main__":
    main()
