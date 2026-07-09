"""
create_maps_template.py — Build a starter Maps search flow with data source baked in.

This creates (or updates) a Geelark RPA flow that includes:
  1. Import Flow Data (our 38 profiles)
  2. Open Google Maps
  3. Tap search bar
  4. Type a random branded search query
  5. Press Enter
  6. Wait / scroll

You open it in rpa.geelark.com and modify the steps however you want.
The variables from the data source are available in every step below it.

Usage:
    python create_maps_template.py
    python create_maps_template.py --shuffle      # re-roll random selections first
    python create_maps_template.py --flow-id ID   # update existing template
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import (
    step_open_app,
    step_wait,
    step_click,
    step_type_text,
    step_press_key,
    step_scroll,
)
from sync_geelark_flow_data import _build_excel_map, load_flow_data

log = logging.getLogger("maps_template")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")

TEMPLATE_TITLE = "Maps Searches - Starter Template"
TEMPLATE_DESC = (
    "Starter template for Google Maps searches across 38 profiles. "
    "Contains the data import step + placeholder Maps automation. "
    "Modify the steps in rpa.geelark.com to match your exact workflow."
)


def _build_template_gal(excel_map: list[dict]) -> dict:
    """Build a GAL flow with data source + placeholder Maps steps."""

    contents = [
        # Step 1: Import the data source
        {
            "type": "exportExcel",
            "name": "Import Flow Data",
            "config": {
                "excelMap": excel_map,
            },
        },
        # Step 2: Open Google Maps
        step_open_app(
            package="com.google.android.apps.maps",
            timeout_ms=30_000,
            remark="Open Google Maps",
        ),
        # Step 3: Wait for load
        step_wait(5000, "Wait for Maps to load"),
        # Step 4: Tap search bar
        step_click(
            selector_type="text",
            selector="Search here",
            search_time_ms=10_000,
            remark="Tap Maps search bar",
        ),
        # Step 5: Type a random branded search query
        step_type_text(
            text="${random_branded_search}",
            selector_type="class",
            selector="android.widget.EditText",
            serial=1,
            clear=True,
            search_time_ms=5_000,
            remark="Enter branded search query",
        ),
        # Step 6: Press Enter to search
        step_press_key("enter", "Submit search"),
        # Step 7: Wait for results
        step_wait(8000, "Wait for search results"),
        # Step 8: Tap first result (try by text)
        step_click(
            selector_type="text",
            selector="Directions",
            search_time_ms=8_000,
            remark="Tap first result (Directions button)",
        ),
        # Step 9: Dwell / scroll
        step_wait(10_000, "Dwell on result page"),
        step_scroll(
            direction="bottom",
            min_px=500,
            max_px=700,
            remark="Scroll down result details",
        ),
        step_wait(5000, "Pause after scroll"),
        # Step 10: Go home
        {"type": "home", "config": {"remark": "Return to home screen"}},
    ]

    return {
        "title": TEMPLATE_TITLE,
        "desc": TEMPLATE_DESC,
        "content": {
            "contentType": "phone",
            "errorType": "skip",
            "isDebug": False,
            "timeOut": "15",
            "contents": contents,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Create a Maps search starter template in Geelark")
    parser.add_argument("--shuffle", action="store_true", help="Re-roll random selections")
    parser.add_argument("--flow-id", help="Update an existing template flow")
    parser.add_argument("--dry-run", action="store_true", help="Build GAL but don't import")
    args = parser.parse_args()

    log.info("Loading flow data …")
    rows = load_flow_data()
    if not rows:
        log.error("No flow data found. Run generate_flow_data.py first.")
        sys.exit(1)

    log.info("Loaded %d rows", len(rows))
    excel_map = _build_excel_map(rows)

    gal_dict = _build_template_gal(excel_map)
    gal_json = json.dumps(gal_dict, ensure_ascii=False)

    if args.dry_run:
        out_path = Path(__file__).parent / "maps_starter_template_gal.json"
        out_path.write_text(gal_json, encoding="utf-8")
        log.info("Dry-run: GAL JSON written to %s", out_path)
        sys.exit(0)

    client = GeelarKClient()

    # Resolve existing flow by title
    flow_id = args.flow_id
    if not flow_id:
        try:
            existing = client.list_rpa_flows()
            for f in existing:
                if f.get("title") == TEMPLATE_TITLE:
                    flow_id = f.get("id")
                    log.info("Found existing template '%s' (id=%s)", TEMPLATE_TITLE, flow_id)
                    break
        except Exception as e:
            log.warning("Could not list flows: %s", e)

    try:
        fid = client.import_rpa_flow(gal_json, flow_id=flow_id)
        action = "updated" if flow_id else "created"
        log.info("Template '%s' %s -- id=%s", TEMPLATE_TITLE, action, fid)
        print(f"\nOK  Template '{TEMPLATE_TITLE}' {action} successfully.")
        print(f"   Flow ID: {fid}")
        print(f"   Rows:    {len(rows)}")
        print(f"\nNext steps:")
        print(f"  1. Open https://rpa.geelark.com")
        print(f"  2. Find the flow '{TEMPLATE_TITLE}'")
        print(f"  3. The 1st step 'Import Flow Data' already contains all 38 profiles")
        print(f"  4. Modify or delete the steps below it to build your automation")
        print(f"  5. Available variables: ${random_branded_search}, ${business_lat}, etc.")
    except Exception as e:
        log.error("Failed to import template: %s", e)
        fallback = Path(__file__).parent / "maps_starter_template_gal.json"
        fallback.write_text(gal_json, encoding="utf-8")
        log.info("Saved GAL JSON to %s for manual import", fallback)
        sys.exit(1)


if __name__ == "__main__":
    main()
