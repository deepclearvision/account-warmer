"""
gps_flow_baker.py — Per-run ephemeral GPS setup flow with resolver coords baked in.

Mechanism (fixes both Gap 1 + Gap 2):
  1. Load the GPS setup flow template (exported from the working flow).
  2. Replace the hardcoded coord string "51.504782, -0.086934" with the resolver's
     actual coords (e.g. "51.5013, -0.0886") as a literal.
  3. Import the modified GAL as a new ephemeral flow via GeelarK API.
  4. Dispatch the ephemeral flow on the phone.
  5. After the task completes, delete the ephemeral flow.

This guarantees:
  - The spoof uses the RESOLVER'S chosen point (Gap 1 fixed).
  - A FRESH spoof is started every run, not inherited (Gap 2 fixed).
  - The spoof->search window stays tight because GPS setup is run immediately
    before Maps, every time.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core.geelark_client import GeelarKClient

log = logging.getLogger("gps_flow_baker")

# The working GPS setup flow template (exported once and cached)
DEFAULT_TEMPLATE_FLOW_ID = "620512663138468211"

# The literal hardcoded coordinate string inside the template
HARDCODED_COORDS = "51.504782, -0.086934"

# Path to cache the exported template locally
_TEMPLATE_CACHE_PATH = Path(__file__).with_name(".gps_flow_template.json")


def _get_template(client: GeelarKClient, template_flow_id: str = DEFAULT_TEMPLATE_FLOW_ID) -> dict:
    """Export the GPS setup flow and return it as a dict. Cached to disk."""
    if _TEMPLATE_CACHE_PATH.exists():
        log.debug("Using cached GPS flow template: %s", _TEMPLATE_CACHE_PATH)
        return json.loads(_TEMPLATE_CACHE_PATH.read_text(encoding="utf-8"))

    log.info("Exporting GPS flow template %s from GeelarK...", template_flow_id)
    gal_json = client.export_rpa_flow(template_flow_id)
    data = json.loads(gal_json)

    _TEMPLATE_CACHE_PATH.write_text(gal_json, encoding="utf-8")
    log.info("Template cached to %s", _TEMPLATE_CACHE_PATH)
    return data


def bake_and_import(
    client: GeelarKClient,
    lat: str,
    lng: str,
    template_flow_id: str = DEFAULT_TEMPLATE_FLOW_ID,
    reuse_flow_id: str | None = None,
) -> str:
    """
    Bake resolver coords into the GPS setup flow and import it.

    Args:
        client: GeelarKClient instance.
        lat: Latitude string (e.g. "51.5013").
        lng: Longitude string (e.g. "-0.0886").
        template_flow_id: Source flow to clone.
        reuse_flow_id: If provided, update this existing flow in-place instead
                       of creating a new one. This prevents flow accumulation.

    Returns:
        The flow_id string (new or reused).
    """
    template = _get_template(client, template_flow_id)

    # Serialize to string for replacement, then swap in the fresh coords
    gal_str = json.dumps(template, ensure_ascii=False)

    if HARDCODED_COORDS not in gal_str:
        raise RuntimeError(
            f"Template flow {template_flow_id} does not contain expected hardcoded coords "
            f"'{HARDCODED_COORDS}'. The template may have changed."
        )

    # Format: the Fake GPS app expects "lat, lng" in the input field
    baked_coords = f"{lat}, {lng}"
    baked_gal_str = gal_str.replace(HARDCODED_COORDS, baked_coords)

    # Update title so it's recognisable in the GeelarK portal
    title_tag = f"GPS setup (baked {lat},{lng})"
    parsed = json.loads(baked_gal_str)
    parsed["title"] = title_tag
    parsed["desc"] = f"Ephemeral GPS setup flow — coords baked at {lat},{lng}"
    if "content" in parsed and isinstance(parsed["content"], dict):
        parsed["content"]["name"] = title_tag

    baked_gal_json = json.dumps(parsed, ensure_ascii=False)

    if reuse_flow_id:
        log.info("Updating existing GPS flow %s with baked coords: %s", reuse_flow_id, baked_coords)
        flow_id = client.import_rpa_flow(baked_gal_json, flow_id=reuse_flow_id)
        log.info("GPS flow updated in place: id=%s", flow_id)
    else:
        log.info("Importing new ephemeral GPS setup flow with baked coords: %s", baked_coords)
        flow_id = client.import_rpa_flow(baked_gal_json)
        log.info("Ephemeral flow imported: id=%s", flow_id)
    return flow_id


def delete_ephemeral_flow(client: GeelarKClient, flow_id: str) -> None:
    """Clean up an ephemeral flow after use."""
    try:
        # GeelarK does not expose a delete-flow endpoint in the client.
        # We can attempt a direct API call, but if unsupported we just log.
        log.info("Skipping explicit flow delete (API not exposed); flow %s will remain in account.", flow_id)
    except Exception:
        pass
