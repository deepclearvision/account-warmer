"""
sync_geelark_flow_data.py — Push flow data into Geelark as an exportExcel data source.

Usage:
    python sync_geelark_flow_data.py
    python sync_geelark_flow_data.py --shuffle  # re-roll random selections
    python sync_geelark_flow_data.py --flow-id 619331580585836946  # update existing

This creates (or updates) a Geelark RPA flow whose only job is to expose every
account's data as GAL variables via exportExcel. You then open rpa.geelark.com,
find the flow, and build the actual automation steps beneath the export step.

Random-selection columns (re-shuffled each run):
  ${random_nearby_point}    — one lat,lng from nearby_points
  ${random_onsite_point}    — one lat,lng from onsite_points
  ${random_branded_search}  — one query from branded_searches
  ${random_local_search}    — one query from local_searches
  ${random_search_term}     — one query from search_terms
"""

import argparse
import json
import logging
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder
from core.flow_data import load_flow_data
from core.paths import DATA_DIR

log = logging.getLogger("sync_flow_data")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")

OUTPUT_FLOW_TITLE = "Flow Data Source - Maps Searches"
OUTPUT_FLOW_DESC = (
    "Auto-generated data source for ~38 Geelark profiles. "
    "Contains account info, business coords, nearby/onsite points, and search terms. "
    "Use variables like ${random_nearby_point}, ${branded_searches}, etc. in your flow steps."
)


def _normalise_row(row: dict) -> dict:
    """Ensure every derived/computed field exists as a plain string."""
    r = dict(row)

    # Build coord strings from lat/lng if missing
    for prefix in ("business", "home", "work"):
        lat = r.get(f"{prefix}_lat")
        lng = r.get(f"{prefix}_lng")
        coord_key = f"{prefix}_coords"
        if (not r.get(coord_key)) and lat is not None and lng is not None:
            r[coord_key] = f"{lat},{lng}"
        elif not r.get(coord_key):
            r[coord_key] = ""

    # Convert nearby_points list-of-dicts -> pipe-separated "lat,lng" strings
    nearby = r.get("nearby_points", [])
    if isinstance(nearby, list):
        r["nearby_points"] = "|".join(f"{p['lat']},{p['lng']}" for p in nearby if isinstance(p, dict) and "lat" in p and "lng" in p)
    else:
        r["nearby_points"] = str(nearby or "")

    # Convert onsite_points list-of-dicts -> pipe-separated "lat,lng" strings
    onsite = r.get("onsite_points", [])
    if isinstance(onsite, list):
        r["onsite_points"] = "|".join(f"{p['lat']},{p['lng']}" for p in onsite if isinstance(p, dict) and "lat" in p and "lng" in p)
    else:
        r["onsite_points"] = str(onsite or "")

    # Convert string-list fields -> pipe-separated
    for key in ("search_terms", "branded_searches", "local_searches"):
        val = r.get(key, [])
        if isinstance(val, list):
            r[key] = "|".join(str(v) for v in val)
        else:
            r[key] = str(val or "")

    return r


def _pick_random_pipe_field(row: dict, key: str) -> str:
    """Pick one random item from a pipe-separated string field."""
    val = row.get(key, "")
    if not val:
        return ""
    items = [v.strip() for v in str(val).split("|") if v.strip()]
    return random.choice(items) if items else ""


def _build_excel_map(rows: list[dict]) -> list[dict]:
    """Convert flow-data rows into Geelark exportExcel excelMap format."""

    # Normalise every row so derived fields exist as plain strings
    normalised = [_normalise_row(r) for r in rows]

    # Define columns in the order they appear in the Geelark editor
    columns = [
        ("account_id",            "account_id"),
        ("account_email",         "account_email"),
        ("geelark_phone_id",      "geelark_phone_id"),
        ("account_password",      "account_password"),
        ("account_totp",          "account_totp"),
        ("multilogin_profile_id", "multilogin_profile_id"),
        ("proxy",                 "proxy"),
        ("geo_city",              "geo_city"),
        ("strategy",              "strategy"),
        ("business_id",           "business_id"),
        ("business_name",         "business_name"),
        ("business_address",      "business_address"),
        ("business_location",     "business_location"),
        ("business_area",         "business_area"),
        ("business_share_link",   "business_share_link"),
        ("business_goal",         "business_goal"),
        ("business_coords",       "business_coords"),
        ("business_lat",          "business_lat"),
        ("business_lng",          "business_lng"),
        ("home_coords",           "home_coords"),
        ("home_lat",              "home_lat"),
        ("home_lng",              "home_lng"),
        ("home_address",          "home_address"),
        ("work_coords",           "work_coords"),
        ("work_lat",              "work_lat"),
        ("work_lng",              "work_lng"),
        ("work_address",          "work_address"),
        ("nearby_points",         "nearby_points"),
        ("onsite_points",         "onsite_points"),
        ("search_terms",          "search_terms"),
        ("branded_searches",      "branded_searches"),
        ("local_searches",        "local_searches"),
        # Random-selection convenience columns
        ("random_nearby_point",   None),   # computed
        ("random_onsite_point",   None),   # computed
        ("random_branded_search", None),   # computed
        ("random_local_search",   None),   # computed
        ("random_search_term",    None),   # computed
    ]

    excel_map: list[dict] = []
    for col_name, src_key in columns:
        values: list = []
        for row in normalised:
            if src_key is not None:
                values.append(str(row.get(src_key, "")))
            else:
                if col_name == "random_nearby_point":
                    values.append(_pick_random_pipe_field(row, "nearby_points"))
                elif col_name == "random_onsite_point":
                    values.append(_pick_random_pipe_field(row, "onsite_points"))
                elif col_name == "random_branded_search":
                    values.append(_pick_random_pipe_field(row, "branded_searches"))
                elif col_name == "random_local_search":
                    values.append(_pick_random_pipe_field(row, "local_searches"))
                elif col_name == "random_search_term":
                    values.append(_pick_random_pipe_field(row, "search_terms"))
                else:
                    values.append("")
        excel_map.append({"key": col_name, "value": values})

    return excel_map


def _build_gal_data_source(excel_map: list[dict], title: str, desc: str) -> dict:
    """Build a minimal GAL flow dict containing only an exportExcel step."""
    return {
        "title": title,
        "desc": desc,
        "content": {
            "contentType": "phone",
            "errorType": "skip",
            "isDebug": False,
            "timeOut": "30",
            "contents": [
                {
                    "type": "exportExcel",
                    "name": "Import Flow Data",
                    "config": {
                        "excelMap": excel_map,
                    },
                },
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Sync flow data into Geelark exportExcel data source")
    parser.add_argument("--shuffle", action="store_true", help="Re-roll random selections")
    parser.add_argument("--flow-id", help="Update existing Geelark flow instead of creating new")
    parser.add_argument("--dry-run", action="store_true", help="Build GAL but don't import")
    args = parser.parse_args()

    log.info("Loading flow data from %s", DATA_DIR / "geelark_flow_data.yaml")
    rows = load_flow_data()
    if not rows:
        log.error("No flow data found. Run generate_flow_data.py first.")
        sys.exit(1)

    log.info("Loaded %d rows", len(rows))

    if args.shuffle:
        log.info("Shuffling random selections …")

    excel_map = _build_excel_map(rows)

    # Log sample of what each column contains
    log.info("Columns exported to Geelark:")
    for col in excel_map:
        sample = col["value"][0] if col["value"] else ""
        log.info("  %-25s  (%d rows)  e.g. %s", col["key"], len(col["value"]), sample[:60])

    gal_dict = _build_gal_data_source(excel_map, OUTPUT_FLOW_TITLE, OUTPUT_FLOW_DESC)
    gal_json = json.dumps(gal_dict, ensure_ascii=False)

    if args.dry_run:
        out_path = DATA_DIR / "geelark_data_source_gal.json"
        out_path.write_text(gal_json, encoding="utf-8")
        log.info("Dry-run: GAL JSON written to %s", out_path)
        sys.exit(0)

    client = GeelarKClient()

    # Resolve existing flow by title if no --flow-id given
    flow_id = args.flow_id
    if not flow_id:
        try:
            existing = client.list_rpa_flows()
            for f in existing:
                if f.get("title") == OUTPUT_FLOW_TITLE:
                    flow_id = f.get("id")
                    log.info("Found existing flow '%s' (id=%s)", OUTPUT_FLOW_TITLE, flow_id)
                    break
        except Exception as e:
            log.warning("Could not list existing flows: %s", e)

    try:
        fid = client.import_rpa_flow(gal_json, flow_id=flow_id)
        action = "updated" if flow_id else "created"
        log.info("Flow '%s' %s — id=%s", OUTPUT_FLOW_TITLE, action, fid)
        print(f"\nOK  Flow '{OUTPUT_FLOW_TITLE}' {action} successfully.")
        print(f"   Flow ID: {fid}")
        print(f"   Rows:    {len(rows)}")
        print(f"\nNext steps:")
        print(f"  1. Open https://rpa.geelark.com")
        print(f"  2. Find the flow '{OUTPUT_FLOW_TITLE}'")
        print(f"  3. Add your automation steps below 'Import Flow Data'")
        print(f"  4. Use variables like ${{random_nearby_point}}, ${{branded_searches}}, etc.")
    except Exception as e:
        log.error("Failed to import flow: %s", e)
        # Save JSON locally for manual import
        fallback = DATA_DIR / "geelark_data_source_gal.json"
        fallback.write_text(gal_json, encoding="utf-8")
        log.info("Saved GAL JSON to %s for manual import", fallback)
        sys.exit(1)


if __name__ == "__main__":
    main()
