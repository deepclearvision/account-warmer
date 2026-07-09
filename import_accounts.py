"""
Account Importer
Reads a CSV file and adds accounts to config/accounts.yaml.
Won't overwrite or duplicate accounts that already exist.

Each imported account automatically gets a corresponding entry in
geelark_accounts.yaml so it appears in the Mobile Phones dashboard tab.
The GeelarK phone is not provisioned here — use the dashboard Provision
button for that. The mobile proxy used is GEELARK_PROXY from warmer.env
(StreamVia rotating mobile proxy).

Usage:
  python import_accounts.py                         (uses accounts_template.csv)
  python import_accounts.py --csv my_accounts.csv   (use a different file)
  python import_accounts.py --list                  (show all current accounts)
  python import_accounts.py --remove acc_001        (remove an account)
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from core.account_store import get_account_store
from core.paths import ACCOUNTS_FILE as CONFIG_FILE
TEMPLATE_FILE = Path(__file__).parent / "accounts_template.csv"

# Columns that must be filled in — all others have defaults
REQUIRED = ["email", "multilogin_profile_id", "proxy"]

# Sensible defaults for optional columns
DEFAULTS = {
    "language":           "en-GB",
    "timezone":           "Europe/London",
    "location":           "London, UK",
    "active_hours_start": "8",
    "active_hours_end":   "22",
    "warmup_start_date":  str(date.today()),
    "strategy":           "",
}

VALID_STRATEGIES = ["standard", "maps_heavy", "light", "pin_prep"]

# Maps location strings to timezone (for auto-fill if user leaves timezone blank)
LOCATION_TIMEZONES = {
    "london":     "Europe/London",
    "manchester": "Europe/London",
    "birmingham": "Europe/London",
    "new york":   "America/New_York",
    "los angeles":"America/Los_Angeles",
    "chicago":    "America/Chicago",
    "sydney":     "Australia/Sydney",
    "melbourne":  "Australia/Melbourne",
    "toronto":    "America/Toronto",
    "paris":      "Europe/Paris",
    "berlin":     "Europe/Berlin",
    "amsterdam":  "Europe/Amsterdam",
    "dubai":      "Asia/Dubai",
    "singapore":  "Asia/Singapore",
    "tokyo":      "Asia/Tokyo",
}


# ------------------------------------------------------------------
# Load / save
# ------------------------------------------------------------------

def load_config() -> dict:
    store = get_account_store()
    accounts = store.get_desktop_accounts()
    return {"accounts": accounts}


def save_config(data: dict) -> None:
    store = get_account_store()
    store.save_desktop_accounts(data.get("accounts", []))


def load_geelark_accounts() -> dict:
    store = get_account_store()
    accounts = store.get_mobile_accounts()
    return {"accounts": accounts}


def save_geelark_accounts(data: dict) -> None:
    store = get_account_store()
    store.save_mobile_accounts(data.get("accounts", []))


# ------------------------------------------------------------------
# Import
# ------------------------------------------------------------------

def import_from_csv(csv_path: Path) -> None:
    if not csv_path.exists():
        print(f"ERROR: File not found: {csv_path}")
        print(f"       Create a CSV using the template: accounts_template.csv")
        sys.exit(1)

    config   = load_config()
    existing = {a["email"] for a in config["accounts"]}
    existing_ids = {a["id"] for a in config["accounts"]}

    gl_config    = load_geelark_accounts()
    gl_existing  = {a["email"] for a in gl_config["accounts"]}

    added   = []
    skipped = []
    errors  = []

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        # Strip whitespace from column headers (common Excel issue)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]

        for row_num, row in enumerate(reader, start=2):
            row = {k.strip(): v.strip() for k, v in row.items() if k}

            # Skip blank rows
            if not any(row.values()):
                continue

            # --- Validate required fields ---
            missing = [r for r in REQUIRED if not row.get(r)]
            if missing:
                errors.append(f"Row {row_num}: missing {missing} — skipped")
                continue

            # --- Skip placeholder rows (template examples) ---
            if "PASTE-PROFILE-ID" in row.get("multilogin_profile_id", ""):
                skipped.append(f"Row {row_num}: {row.get('email')} — still has placeholder ID, skipped")
                continue

            # --- Skip duplicates ---
            if row["email"] in existing:
                skipped.append(f"Row {row_num}: {row['email']} — already exists, skipped")
                continue

            # --- Apply defaults ---
            for col, default in DEFAULTS.items():
                if not row.get(col):
                    row[col] = default

            # --- Auto-fill timezone from location if missing ---
            if not row.get("timezone") or row["timezone"] == DEFAULTS["timezone"]:
                loc_lower = row.get("location", "").lower()
                for key, tz in LOCATION_TIMEZONES.items():
                    if key in loc_lower:
                        row["timezone"] = tz
                        break

            # --- Auto-generate ID if blank ---
            account_id = row.get("id", "").strip()
            if not account_id:
                # Generate next available ID
                n = len(config["accounts"]) + len(added) + 1
                account_id = f"acc_{n:03d}"
                while account_id in existing_ids:
                    n += 1
                    account_id = f"acc_{n:03d}"

            existing_ids.add(account_id)

            # --- Build desktop account entry ---
            account = {
                "id":                    account_id,
                "email":                 row["email"],
                "multilogin_profile_id": row["multilogin_profile_id"],
                "multilogin_folder_id":  row.get("multilogin_folder_id", ""),
                "proxy":                 row["proxy"],
                "timezone":              row["timezone"],
                "location":              row["location"],
                "language":              row["language"],
                "warmup_start_date":     row["warmup_start_date"],
                "active_hours":          [
                    int(row["active_hours_start"]),
                    int(row["active_hours_end"]),
                ],
            }

            # Optional password / totp_secret — needed for desktop + mobile login
            if row.get("password"):
                account["password"] = row["password"]
            if row.get("totp_secret"):
                account["totp_secret"] = row["totp_secret"]

            # Optional geo / category — shared across both platforms
            if row.get("geo_city"):
                account["geo_city"] = row["geo_city"]
            if row.get("neighbourhood"):
                account["neighbourhood"] = row["neighbourhood"]
            if row.get("category"):
                account["category"] = row["category"]

            # Optional strategy
            strategy_raw = row.get("strategy", "").strip()
            if strategy_raw:
                if strategy_raw not in VALID_STRATEGIES:
                    errors.append(f"Row {row_num}: unknown strategy {strategy_raw!r} — skipping strategy, using default")
                else:
                    account["strategy"] = strategy_raw

            # Optional target_businesses — comma-separated list of biz IDs
            biz_raw = row.get("target_businesses", "").strip()
            if biz_raw:
                account["target_businesses"] = [
                    b.strip() for b in biz_raw.split(",") if b.strip()
                ]

            config["accounts"].append(account)
            existing.add(row["email"])
            added.append(account_id)

            # --- Create corresponding GeelarK mobile entry ---
            if row["email"] not in gl_existing:
                gl_entry = {
                    "id":                    account_id,
                    "email":                 row["email"],
                    "mobile_warming_enabled": True,
                    "mobile_setup_done":     False,
                    "geelark_phone_id":      None,
                }
                # Shared credentials
                if row.get("password"):
                    gl_entry["password"] = row["password"]
                if row.get("totp_secret"):
                    gl_entry["totp_secret"] = row["totp_secret"]
                # Shared geo / category
                geo_city = row.get("geo_city", "").strip()
                if not geo_city:
                    loc_lower = row.get("location", "").lower()
                    for key in LOCATION_TIMEZONES:
                        if key in loc_lower:
                            geo_city = key
                            break
                if geo_city:
                    gl_entry["geo_city"] = geo_city
                if row.get("neighbourhood"):
                    gl_entry["neighbourhood"] = row["neighbourhood"]
                if row.get("category"):
                    gl_entry["category"] = row["category"]
                # Shared strategy + target_businesses
                strategy_raw = row.get("strategy", "").strip()
                if strategy_raw and strategy_raw in VALID_STRATEGIES:
                    gl_entry["strategy"] = strategy_raw
                biz_raw = row.get("target_businesses", "").strip()
                if biz_raw:
                    gl_entry["target_businesses"] = [
                        b.strip() for b in biz_raw.split(",") if b.strip()
                    ]
                gl_config["accounts"].append(gl_entry)
                gl_existing.add(row["email"])

    # --- Save and report ---
    if added:
        save_config(config)
        save_geelark_accounts(gl_config)

    print()
    print(f"  Import complete")
    print(f"  -----------------------------")
    if added:
        print(f"  Added ({len(added)}):")
        for a in added:
            print(f"    + {a}")
    if skipped:
        print(f"  Skipped ({len(skipped)}):")
        for s in skipped:
            print(f"    ~ {s}")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(f"    ! {e}")
    if not added and not errors:
        print("  Nothing new to import.")
    print()


# ------------------------------------------------------------------
# List
# ------------------------------------------------------------------

def list_accounts() -> None:
    config   = load_config()
    accounts = config.get("accounts", [])

    if not accounts:
        print("\n  No accounts configured yet.\n")
        return

    print()
    print(f"  {'ID':<12} {'Email':<35} {'Location':<20} {'Start Date':<12} {'Week':<6} {'Strategy'}")
    print(f"  {'-'*12} {'-'*35} {'-'*20} {'-'*12} {'-'*6} {'-'*12}")

    today = date.today()
    for a in accounts:
        start_str = a.get("warmup_start_date", str(today))
        try:
            start = date.fromisoformat(start_str)
            week  = (today - start).days // 7 + 1
        except Exception:
            week = "?"
        strategy = a.get("strategy", "standard")
        print(
            f"  {a['id']:<12} {a['email']:<35} "
            f"{a.get('location',''):<20} {start_str:<12} {week:<6} {strategy}"
        )
    print()


# ------------------------------------------------------------------
# Remove
# ------------------------------------------------------------------

def remove_account(account_id: str) -> None:
    config   = load_config()
    before   = len(config["accounts"])
    config["accounts"] = [a for a in config["accounts"] if a["id"] != account_id]
    after    = len(config["accounts"])

    if before == after:
        print(f"\n  Account '{account_id}' not found.\n")
        return

    save_config(config)
    print(f"\n  Removed: {account_id}\n")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Account Importer")
    parser.add_argument("--csv",    type=str,  default=str(TEMPLATE_FILE), help="Path to CSV file")
    parser.add_argument("--list",   action="store_true", help="List all configured accounts")
    parser.add_argument("--remove", type=str,  help="Remove an account by ID")
    args = parser.parse_args()

    if args.list:
        list_accounts()
    elif args.remove:
        remove_account(args.remove)
    else:
        import_from_csv(Path(args.csv))
