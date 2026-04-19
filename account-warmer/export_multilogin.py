"""
Multilogin Profile Exporter

Fetches all profiles from Multilogin and generates a fully pre-filled
accounts_template_exported.csv ready to import with import_accounts.py.

What gets auto-filled:
  - multilogin_profile_id  (from Multilogin API)
  - multilogin_folder_id   (from Multilogin API)
  - id                     (auto-generated: acc_001, acc_002, ...)
  - email                  (taken from the Multilogin profile name)
  - proxy                  (from the Multilogin profile)
  - location               (geolocated through the proxy — e.g. London, GB)
  - timezone               (geolocated through the proxy — e.g. Europe/London)
  - language               (derived from country code)
  - warmup_start_date      (today's date)

The only column you may want to edit manually afterwards:
  - target_businesses      (optional — comma-separated business IDs)

Usage:
  python export_multilogin.py

Tip: Name your profiles in Multilogin after the Gmail address
(e.g. "john.doe@gmail.com") so email is filled in automatically.
"""

import csv
import json
import sys
from datetime import date
from pathlib import Path

import requests
import yaml

OUTPUT_CSV = Path(__file__).parent / "accounts_template_exported.csv"

CSV_HEADERS = [
    "id", "email", "multilogin_profile_id", "multilogin_folder_id", "proxy",
    "timezone", "location", "language",
    "warmup_start_date", "active_hours_start", "active_hours_end",
    "target_businesses", "strategy",
]

# Country code → language tag
_COUNTRY_LANG = {
    "GB": "en-GB", "US": "en-US", "AU": "en-AU", "CA": "en-CA",
    "IE": "en-IE", "NZ": "en-NZ", "ZA": "en-ZA",
    "DE": "de-DE", "AT": "de-AT", "CH": "de-CH",
    "FR": "fr-FR", "BE": "fr-BE",
    "ES": "es-ES", "MX": "es-MX", "AR": "es-AR",
    "IT": "it-IT", "PT": "pt-PT", "BR": "pt-BR",
    "NL": "nl-NL", "PL": "pl-PL", "SE": "sv-SE",
    "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI",
    "RU": "ru-RU", "JP": "ja-JP", "CN": "zh-CN",
    "KR": "ko-KR", "IN": "en-IN", "AE": "ar-AE",
}


def _fetch_profiles() -> list:
    """Fetch all profiles using the shared auth module."""
    from core.multilogin_auth import list_profiles
    return list_profiles()


# ── Profile parsing ────────────────────────────────────────────────────────────

def _parse_profile(profile: dict) -> dict:
    """Extract useful fields from a Multilogin profile dict."""

    profile_id = (
        profile.get("id")
        or profile.get("uuid")
        or profile.get("profileId")
        or ""
    )

    folder_id = (
        profile.get("folder_id")
        or profile.get("folderId")
        or ""
    )

    # Profile name — used as the email address
    name = (profile.get("name") or "").strip()

    # Proxy — build URL string and keep raw dict for geolocation
    proxy_str  = ""
    proxy_dict = profile.get("proxy") or profile.get("proxyConfig") or {}
    if isinstance(proxy_dict, dict):
        ptype = proxy_dict.get("type", "http").lower()
        host  = proxy_dict.get("host") or ""
        port  = proxy_dict.get("port") or ""
        user  = proxy_dict.get("username") or ""
        pwd   = proxy_dict.get("password") or ""
        if host and port:
            if user and pwd:
                proxy_str = f"{ptype}://{user}:{pwd}@{host}:{port}"
            else:
                proxy_str = f"{ptype}://{host}:{port}"

    return {
        "profile_id": profile_id,
        "folder_id":  folder_id,
        "email":      name,
        "proxy":      proxy_str,
        "proxy_dict": proxy_dict,
    }


# ── Proxy geolocation ──────────────────────────────────────────────────────────

def _parse_proxy_username_location(proxy_str: str) -> dict:
    """
    Multilogin mobile proxy usernames encode location, e.g.:
      ...country-gb-region-england-city-birmingham-sid-...
    Parse these as a fallback when IP geolocation fails.
    """
    empty = {"city": "", "country": "", "countryCode": "", "timezone": ""}
    if not proxy_str:
        return empty
    try:
        # Extract the username part from the proxy URL
        at_idx = proxy_str.rfind("@")
        if at_idx == -1:
            return empty
        userinfo = proxy_str[:at_idx].split("//", 1)[-1]
        username = userinfo.split(":")[0].lower()

        # Parse key-value pairs separated by hyphens: country-gb-city-london-...
        parts = username.split("-")
        kv = {}
        i = 0
        while i < len(parts) - 1:
            kv[parts[i]] = parts[i + 1]
            i += 2

        country_code = kv.get("country", "").upper()
        city         = kv.get("city", "").title()

        # Map country code to timezone (rough defaults)
        _CC_TZ = {
            "GB": "Europe/London", "US": "America/New_York",
            "AU": "Australia/Sydney", "CA": "America/Toronto",
            "DE": "Europe/Berlin", "FR": "Europe/Paris",
            "NL": "Europe/Amsterdam", "ES": "Europe/Madrid",
            "IT": "Europe/Rome", "PL": "Europe/Warsaw",
            "SE": "Europe/Stockholm", "NO": "Europe/Oslo",
            "AE": "Asia/Dubai", "SG": "Asia/Singapore",
        }
        timezone = _CC_TZ.get(country_code, "")

        return {
            "city":        city,
            "country":     "",
            "countryCode": country_code,
            "timezone":    timezone,
        }
    except Exception:
        return empty


def _geolocate(proxy_str: str) -> dict:
    """
    Connect through the proxy and return geo info from ip-api.com.
    Falls back to parsing the proxy username if the IP lookup fails.
    Returns a dict with keys: city, country, countryCode, timezone.
    """
    empty = {"city": "", "country": "", "countryCode": "", "timezone": ""}
    if not proxy_str:
        return empty
    try:
        resp = requests.get(
            "http://ip-api.com/json?fields=status,city,country,countryCode,timezone",
            proxies={"http": proxy_str, "https": proxy_str},
            timeout=15,
        )
        data = resp.json()
        if data.get("status") == "success":
            return {
                "city":        data.get("city", ""),
                "country":     data.get("country", ""),
                "countryCode": data.get("countryCode", ""),
                "timezone":    data.get("timezone", ""),
            }
    except Exception:
        pass

    # IP lookup failed — fall back to proxy username parsing
    return _parse_proxy_username_location(proxy_str)


# ── Existing account IDs ───────────────────────────────────────────────────────

def _load_existing_ids() -> set:
    from core.paths import ACCOUNTS_FILE as accounts_file
    if not accounts_file.exists():
        return set()
    with open(accounts_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        a.get("multilogin_profile_id", "")
        for a in data.get("accounts", [])
    }


# ── CSV generation ─────────────────────────────────────────────────────────────

def _generate_csv(profiles_raw: list) -> None:
    existing_ids = _load_existing_ids()
    today        = str(date.today())

    parsed   = [_parse_profile(p) for p in profiles_raw]
    parsed   = [p for p in parsed if p["profile_id"]]
    new_only = [p for p in parsed if p["profile_id"] not in existing_ids]

    if not parsed:
        print("  No profiles with usable IDs found in the API response.")
        print("  Raw sample:", json.dumps(profiles_raw[:2], indent=2))
        return

    total = len(parsed)
    new   = len(new_only)
    print(f"\n  Found {total} profile(s) — {new} not yet in accounts.yaml\n")

    if not new_only:
        print("  All profiles are already in accounts.yaml — nothing to export.")
        return

    # Geolocate each proxy
    print("  Geolocating proxies", end="", flush=True)
    geo_results = []
    for p in new_only:
        print(".", end="", flush=True)
        geo_results.append(_geolocate(p["proxy"]))
    print(" done\n")

    rows = []
    for i, (p, geo) in enumerate(zip(new_only, geo_results), start=1):
        name = p["email"].strip()

        # Account ID: use name if it looks like acc_XXX, else sequential
        if name.lower().startswith("acc_") and len(name) <= 10:
            acc_id = name.lower()
        else:
            acc_id = f"acc_{i:03d}"

        # Location string: "City, CountryCode"  e.g. "London, GB"
        if geo["city"] and geo["countryCode"]:
            location = f"{geo['city']}, {geo['countryCode']}"
        elif geo["country"]:
            location = geo["country"]
        else:
            location = ""

        timezone = geo["timezone"] or "Europe/London"
        language = _COUNTRY_LANG.get(geo["countryCode"], "en-GB")

        rows.append({
            "id":                    acc_id,
            "email":                 name,
            "multilogin_profile_id": p["profile_id"],
            "multilogin_folder_id":  p["folder_id"],
            "proxy":                 p["proxy"],
            "timezone":              timezone,
            "location":              location,
            "language":              language,
            "warmup_start_date":     today,
            "active_hours_start":    "8",
            "active_hours_end":      "22",
            "target_businesses":     "",
            "strategy":              "",   # fill in before importing: standard, maps_heavy, light, pin_prep
        })

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Exported {len(rows)} profile(s) to:")
    print(f"  {OUTPUT_CSV}\n")
    print("  Review the CSV, set email and strategy (standard/maps_heavy/light/pin_prep), then run:")
    print("  python import_accounts.py accounts_template_exported.csv\n")

    # Preview
    print(f"  {'ID':<10} {'Email':<35} {'Location':<20} {'Timezone'}")
    print("  " + "-" * 90)
    for row in rows:
        print(
            f"  {row['id']:<10} {row['email']:<35} "
            f"{row['location']:<20} {row['timezone']}"
        )
    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print()
    print("  Multilogin Profile Exporter")
    print("  " + "-" * 50)
    print()

    try:
        print("  Fetching profiles from Multilogin ...")
        profiles = _fetch_profiles()
        print("  Connected successfully.")
        _generate_csv(profiles)
    except FileNotFoundError as e:
        print(f"\n  {e}\n")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n  Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
