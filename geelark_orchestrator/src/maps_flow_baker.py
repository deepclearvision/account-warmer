"""
maps_flow_baker.py — Per-run ephemeral Maps flow with resolver search term + business name baked in.

The existing Maps flow (620892967896350964) is hardcoded for:
  - GPS coords: "51.427671, 0.101348"
  - Search term: "plumber sidcup"
  - Business name: "Sidcup Plumbing & Heating"

This baker exports the template, string-replaces the hardcoded values with the
resolver's actual values, imports as a fresh ephemeral flow, and returns the flow ID.

Combined with gps_flow_baker.py, the full per-run sequence is:
  1. GPS baker: clear storage, set resolver coords, start spoof
  2. Maps baker: set resolver coords (redundant but harmless), search resolver term,
                 find resolver business, fire interactions
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core.geelark_client import GeelarKClient

log = logging.getLogger("maps_flow_baker")

DEFAULT_TEMPLATE_FLOW_ID = "620892967896350964"

# Hardcoded literals inside the template
HARDCODED_GPS = "51.427671, 0.101348"
HARDCODED_SEARCH = "plumber sidcup"
HARDCODED_BUSINESS = "Sidcup Plumbing & Heating"

_TEMPLATE_CACHE_PATH = Path(__file__).with_name(".maps_flow_template.json")


def _get_template(client: GeelarKClient, template_flow_id: str = DEFAULT_TEMPLATE_FLOW_ID) -> dict:
    """Export the Maps flow template and return as dict. Cached to disk."""
    if _TEMPLATE_CACHE_PATH.exists():
        log.debug("Using cached Maps flow template: %s", _TEMPLATE_CACHE_PATH)
        return json.loads(_TEMPLATE_CACHE_PATH.read_text(encoding="utf-8"))

    log.info("Exporting Maps flow template %s from GeelarK...", template_flow_id)
    gal_json = client.export_rpa_flow(template_flow_id)
    data = json.loads(gal_json)
    _TEMPLATE_CACHE_PATH.write_text(gal_json, encoding="utf-8")
    log.info("Template cached to %s", _TEMPLATE_CACHE_PATH)
    return data


def bake_and_import(
    client: GeelarKClient,
    lat: str,
    lng: str,
    search_term: str,
    business_name: str,
    template_flow_id: str = DEFAULT_TEMPLATE_FLOW_ID,
    reuse_flow_id: str | None = None,
    search_mode: str = "discovery",
) -> str:
    """
    Bake resolver values into the Maps flow and import it.

    Args:
        reuse_flow_id: If provided, update this existing flow in-place instead
                       of creating a new one. This prevents flow accumulation.
        search_mode:   "branded" inserts a Back-key step after the suggestion tap
                       to exit the auto-opened route screen and return to results.

    Returns the flow_id string (new or reused).
    """
    template = _get_template(client, template_flow_id)

    # Verify literals are present on the ORIGINAL template before any branded-mode
    # modifications (which remove the hardcoded business-name steps).
    raw_gal_str = json.dumps(template, ensure_ascii=False)
    for literal, label in (
        (HARDCODED_SEARCH, "search term"),
        (HARDCODED_BUSINESS, "business name"),
    ):
        if literal not in raw_gal_str:
            raise RuntimeError(
                f"Template flow {template_flow_id} does not contain expected {label} "
                f"'{literal}'. The template may have changed."
            )

    # Branded-mode tweak: branded terms auto-open the business card after pressing
    # Enter (no results list). Remove the suggestion-tap and the "click business in
    # results list" steps so the flow goes straight: type -> Enter -> card opens.
    if search_mode == "branded":
        contents = template.get("content", {}).get("contents", [])
        tap_idx = None
        wait_after_tap_idx = None
        click_business_idx = None
        wait_after_click_idx = None
        for idx, step in enumerate(contents):
            remark = step.get("config", {}).get("remark", "")
            if remark == "Tap business in suggestions (branded fallback)":
                tap_idx = idx
            elif remark == "Wait after suggestion tap":
                wait_after_tap_idx = idx
            elif remark == "Try click business (top)":
                click_business_idx = idx
            elif remark == "Wait after click attempt":
                wait_after_click_idx = idx
        # Remove all four steps if present (later index first so indices don't shift)
        to_remove = sorted(
            [i for i in (tap_idx, wait_after_tap_idx, click_business_idx, wait_after_click_idx) if i is not None],
            reverse=True,
        )
        for i in to_remove:
            removed_remark = contents[i].get("config", {}).get("remark", "")
            contents.pop(i)
            log.info("Branded mode: removed step at index %d (%s)", i, removed_remark)

    gal_str = json.dumps(template, ensure_ascii=False)

    # Replace with resolver values
    baked_gal_str = gal_str.replace(HARDCODED_SEARCH, search_term)
    baked_gal_str = baked_gal_str.replace(HARDCODED_BUSINESS, business_name)

    # Update title
    parsed = json.loads(baked_gal_str)
    title_tag = f"Maps search (baked {business_name[:20]})"
    parsed["title"] = title_tag
    parsed["desc"] = f"Ephemeral Maps flow — search='{search_term}' business='{business_name}' mode={search_mode}"
    if "content" in parsed and isinstance(parsed["content"], dict):
        parsed["content"]["name"] = title_tag

    baked_gal_json = json.dumps(parsed, ensure_ascii=False)

    if reuse_flow_id:
        log.info("Updating existing Maps flow %s with search='%s' business='%s' mode=%s", reuse_flow_id, search_term, business_name, search_mode)
        flow_id = client.import_rpa_flow(baked_gal_json, flow_id=reuse_flow_id)
        log.info("Maps flow updated in place: id=%s", flow_id)
    else:
        log.info("Importing new ephemeral Maps flow with baked search='%s' business='%s' mode=%s", search_term, business_name, search_mode)
        flow_id = client.import_rpa_flow(baked_gal_json)
        log.info("Ephemeral Maps flow imported: id=%s", flow_id)
    return flow_id
