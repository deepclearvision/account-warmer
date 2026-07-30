#!/usr/bin/env python3
"""Generate branded and money keywords for every business in the mapping."""
import csv
import json
import random
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_data_dir = _repo_root / "data"
_docs_dir = _repo_root / "account-warmer" / "docs"
_docs_dir.mkdir(parents=True, exist_ok=True)

CSV_PATH = _data_dir / "accounts_business_mapping.csv"
JSON_PATH = _data_dir / "accounts_business_mapping.json"


def generate_branded_keywords(row: dict) -> list[str]:
    """20 variations of exact business name + address."""
    name = row["business_name"].strip()
    addr = row["business_address"].strip()
    loc = row["business_location"].strip()
    area = row["business_area"].strip()
    parts = addr.split(",")
    street = parts[0].strip() if parts else addr
    postcode = parts[-1].strip() if len(parts) > 1 else ""
    short_postcode = " ".join(postcode.split()[:2]) if postcode else ""

    templates = [
        name,
        f"{name} {street}",
        f"{name} {loc}",
        f"{name} {area}",
        f"{name} {postcode}",
        f"{name} near me",
        f"{name} reviews",
        f"{name} phone number",
        f"{name} contact",
        f"{name} opening hours",
        f"{name} emergency",
        f"{name} 24 hour",
        f"{name} London",
        f"{name} {short_postcode}",
        f"{name} plumber",
        f"{name} plumbing",
        f"{name} services",
        f"{name} {street} {loc}",
        f"{name} {area} plumber",
        f"{name} Google Maps",
    ]
    # Ensure exactly 20 unique
    seen = set()
    out = []
    for t in templates:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    while len(out) < 20:
        suffix = random.choice(["reviews", "near me", "phone", "emergency", "24hr", "London", "plumber", "plumbing"])
        candidate = f"{name} {suffix}"
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out[:20]


def generate_money_keywords(row: dict) -> list[str]:
    """20 generic service queries for trade + area."""
    name = row["business_name"].strip()
    loc = row["business_location"].strip()
    area = row["business_area"].strip()
    addr = row["business_address"].strip()
    parts = addr.split(",")
    street = parts[0].strip() if parts else addr
    postcode = parts[-1].strip() if len(parts) > 1 else ""
    short_postcode = " ".join(postcode.split()[:2]) if postcode else ""

    templates = [
        f"emergency plumber {loc}",
        f"best plumber near {loc}",
        f"24 hour plumber {area}",
        f"cheap plumber {street}",
        f"plumber near me {area}",
        f"emergency plumbing {loc}",
        f"local plumber {area}",
        f"plumbing services {loc}",
        f"gas plumber {area}",
        f"boiler repair {loc}",
        f"drain unblocking {area}",
        f"heating engineer {loc}",
        f"plumber {postcode}",
        f"reliable plumber {area}",
        f"same day plumber {loc}",
        f"emergency plumber London",
        f"plumbing company {area}",
        f"burst pipe repair {loc}",
        f"central heating repair {area}",
        f"toilet repair {loc}",
    ]
    seen = set()
    out = []
    for t in templates:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    # Fill to 20 if any duplicates were removed
    extras = [
        f"affordable plumber {area}",
        f"plumber {short_postcode}",
        f"emergency plumber {street}",
        f"commercial plumber {loc}",
        f"residential plumbing {area}",
        f"plumber reviews {loc}",
        f"top rated plumber {area}",
        f"plumber call out {loc}",
        f"after hours plumber {area}",
        f"plumbing maintenance {loc}",
    ]
    for e in extras:
        e = e.strip()
        if e not in seen and len(out) < 20:
            seen.add(e)
            out.append(e)
    return out[:20]


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Generate keywords
    for row in rows:
        row["branded_keywords"] = "|".join(generate_branded_keywords(row))
        row["money_keywords"] = "|".join(generate_money_keywords(row))

    # Write updated CSV
    fieldnames = list(rows[0].keys())
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Build JSON
    json_out = {}
    for row in rows:
        email = row["account_email"]
        json_out[email] = {
            "account_id": row["account_id"],
            "geelark_phone_id": row["geelark_phone_id"],
            "multilogin_profile_id": row["multilogin_profile_id"],
            "business_id": row["business_id"],
            "business_name": row["business_name"],
            "business_address": row["business_address"],
            "business_location": row["business_location"],
            "business_area": row["business_area"],
            "business_lat": float(row["business_lat"]),
            "business_lng": float(row["business_lng"]),
            "business_goal": row["business_goal"],
            "business_share_link": row["business_share_link"],
            "home_lat": float(row["home_lat"]),
            "home_lng": float(row["home_lng"]),
            "home_address": row["home_address"],
            "work_lat": float(row["work_lat"]),
            "work_lng": float(row["work_lng"]),
            "work_address": row["work_address"],
            "nearby_points": [p.strip() for p in row["nearby_points"].split("|") if p.strip()] if row.get("nearby_points") else [],
            "onsite_points": [p.strip() for p in row["onsite_points"].split("|") if p.strip()] if row.get("onsite_points") else [],
            "branded_keywords": row["branded_keywords"].split("|"),
            "money_keywords": row["money_keywords"].split("|"),
        }

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    # Write schema doc
    schema_md = _docs_dir / "account_mapping_schema.md"
    schema_md.write_text(ACCOUNT_MAPPING_SCHEMA, encoding="utf-8")

    # Write logging schema doc
    logging_md = _docs_dir / "logging_schema.md"
    logging_md.write_text(LOGGING_SCHEMA, encoding="utf-8")

    print(f"Updated CSV: {CSV_PATH}")
    print(f"Updated JSON: {JSON_PATH}")
    print(f"Schema docs: {schema_md}, {logging_md}")
    print(f"Businesses processed: {len(rows)}")


ACCOUNT_MAPPING_SCHEMA = """# Account-Business Mapping Schema

## Source Files

- `data/accounts_business_mapping.csv` — canonical flat-file master mapping.
- `data/accounts_business_mapping.json` — derived JSON for programmatic access.

## Columns

| Column | Type | Description |
|--------|------|-------------|
| `account_id` | string | Internal account identifier (e.g. `acc_048`) |
| `account_email` | string | Google account email |
| `geelark_phone_id` | string | GeelarK cloud phone ID |
| `multilogin_profile_id` | string | Multilogin desktop browser profile UUID |
| `business_id` | string | Internal business identifier |
| `business_name` | string | Exact registered business name on Google Maps |
| `business_address` | string | Full street address |
| `business_location` | string | Human-readable area (e.g. `Southwark, London`) |
| `business_area` | string | Normalised area tag (e.g. `south_east_london`) |
| `business_lat` | float | Business latitude |
| `business_lng` | float | Business longitude |
| `business_goal` | string | Campaign goal: `review` or `edit` |
| `business_share_link` | string | Short Google Maps share URL |
| `home_lat` | float | Home location latitude |
| `home_lng` | float | Home location longitude |
| `home_address` | string | Home address description |
| `work_lat` | float | Work location latitude |
| `work_lng` | float | Work location longitude |
| `work_address` | string | Work address description |
| `nearby_points` | pipe-separated list | ~20 nearby lat,lng points for warm-up GPS |
| `onsite_points` | pipe-separated list | ~10 very close lat,lng points for on-site simulation |
| **branded_keywords** | pipe-separated list | 20 variations of the exact business name + address modifiers |
| **money_keywords** | pipe-separated list | 20 generic trade + area search queries |

### Keyword columns (new)

- `branded_keywords`: generated from `business_name`, `business_address`, `business_location`, `business_area`. Includes natural variations like "reviews", "phone number", "near me", "London", postcode.
- `money_keywords`: generated from trade (`plumber`/`plumbing`) + location modifiers. Includes emergency, 24-hour, repair, and area-specific queries.

Both are stored pipe-separated in CSV and as arrays in JSON.
"""

LOGGING_SCHEMA = """# Run History Logging Schema

## Storage

- Primary: `logs/run_history.jsonl` — append-only JSON Lines.
- Per-run directories: `geelark_orchestrator/scripts/live_test_<account>_<timestamp>/` — screenshots + UI dumps.

## JSONL Record Schema

Every execution appends one line with the following fields:

```json
{
  "timestamp": "2026-06-07T17:08:51.329586",
  "script_name": "gps_maps_live_test_v3.py",
  "run_id": "20260607_170606",
  "phone_id": "614216903581237315",
  "account_email": "oakleighoneal9987@gmail.com",
  "business_id": "biz_001",
  "attempt": 1,
  "started_ok": true,
  "overall_status": "GPS_MAPS_LIVE_TEST",
  "keyword_type": "money",
  "keyword_used": "emergency plumber Southwark",
  "provisioning_gates": {
    "gate_1_phone_running": true,
    "gate_2_developer_options": true,
    "gate_3_mock_app_set": true,
    "gate_4_overlay_service": true,
    "gate_5_google_account": true,
    "gate_6_proxy_routing": true,
    "gate_7_ip_freshness": true
  },
  "gps_test": {
    "target_lat": 51.501512,
    "target_lng": -0.071167,
    "mock_success": true,
    "dumpsys_mock_ok": true,
    "dumpsys_mock_info": "51.501509,-0.071168"
  },
  "maps_test": {
    "search_term": "emergency plumber Southwark",
    "maps_search_result": "FOUND",
    "business_found_text": "Southwark Plumbers",
    "screenshot_path": "...",
    "ui_dump_path": "..."
  },
  "interactions": {
    "scrolls": 0,
    "taps": 0,
    "detail_page": false,
    "share_sheet": false
  },
  "errors": [],
  "live_view_url": "https://console.geelark.com/...",
  "duration_seconds": 120
}
```

## Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO-8601 | Run completion time |
| `script_name` | string | Script that produced the record |
| `run_id` | string | Human-readable run identifier (timestamp) |
| `phone_id` | string | GeelarK phone ID |
| `account_email` | string | Google account email |
| `business_id` | string | Business identifier |
| `attempt` | int | Retry attempt number |
| `started_ok` | bool | Phone started successfully |
| `overall_status` | string | High-level outcome label |
| `keyword_type` | string | `branded` or `money` |
| `keyword_used` | string | Exact search term sent to Maps |
| `provisioning_gates` | object | Boolean results for all 7 pre-run checks |
| `gps_test` | object | GPS mock target, actual, and success flags |
| `maps_test` | object | Search result, found text, evidence paths |
| `interactions` | object | Interaction heuristics (scrolls, taps, detail page) |
| `errors` | string[] | Any error messages encountered |
| `live_view_url` | string | GeelarK live-view URL returned by `start_phone` |
| `duration_seconds` | int | Total run wall-clock time |
"""

if __name__ == "__main__":
    main()
