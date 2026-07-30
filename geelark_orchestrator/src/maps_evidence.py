"""
maps_evidence.py — Post-run Maps behavioural verification via uiautomator dump.

After the Maps flow completes, this module:
  1. Dumps the screen accessibility hierarchy to XML
  2. Parses it for evidence of what actually happened
  3. Returns structured evidence: search submitted, business found, interactions present

This closes the "Maps completed != Maps worked" gap.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

from core.geelark_client import _post

log = logging.getLogger("maps_evidence")

DUMP_PATH = "/sdcard/window_dump.xml"


def _dump_screen(phone_id: str) -> str:
    """Run uiautomator dump and return the XML string."""
    # Trigger dump
    r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"uiautomator dump {DUMP_PATH}"})
    out = r.get("output", "")
    err = r.get("error", "")
    if "dumped" not in out.lower() and "hierarchy" not in out.lower():
        log.warning("uiautomator dump unexpected output: %s / %s", out, err)
    # Read the file
    r2 = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"cat {DUMP_PATH}"})
    xml_data = r2.get("output", "")
    if not xml_data.startswith("<"):
        raise RuntimeError(f"uiautomator dump returned non-XML: {xml_data[:200]}")
    return xml_data


def _extract_texts(xml_data: str) -> list[str]:
    """Extract all text attributes from the accessibility hierarchy."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        log.warning("XML parse error: %s", e)
        return []
    texts: list[str] = []
    for node in root.iter("node"):
        t = node.get("text", "").strip()
        if t:
            texts.append(t)
        cd = node.get("content-desc", "").strip()
        if cd and cd != t:
            texts.append(cd)
    return texts


def _extract_resource_ids(xml_data: str) -> list[str]:
    """Extract all resource-id attributes."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []
    return [n.get("resource-id", "").strip() for n in root.iter("node") if n.get("resource-id", "").strip()]


# Common words that cause false positives when matched alone
_STOPWORDS = {"the", "a", "an", "of", "and", "in", "at", "on", "to", "for", "by", "with", "&"}


def _matches_business_name(texts: list[str], business_name: str) -> bool:
    """Check if business name (or significant substring) appears in UI text.

    Avoids false positives from common words like "The" by requiring
    at least two consecutive non-common words to match.
    """
    if not business_name:
        return False

    # 1. Exact full match
    for t in texts:
        if business_name in t:
            return True

    # 2. Build a substring of significant (non-stopword) words
    words = [w for w in business_name.lower().split() if w not in _STOPWORDS]
    if len(words) >= 2:
        partial = " ".join(words[:3])  # up to first 3 significant words
        for t in texts:
            if partial in t.lower():
                return True
    elif len(words) == 1:
        # Single significant word: require exact word match (not substring)
        w = words[0]
        for t in texts:
            if re.search(rf"\b{re.escape(w)}\b", t, re.IGNORECASE):
                return True

    return False


def _check_card_opened(texts: list[str]) -> bool:
    """
    Detect whether the business knowledge panel / card is actually open.

    The card shows action buttons (Photos, Reviews, Directions, Save, Share,
    Call, Website) and business metadata (rating, address, hours, About).
    We require at least two distinct card indicators to avoid false positives
    from route screens or result lists that also mention the business name.
    """
    all_text_lower = " ".join(texts).lower()
    indicators: set[str] = set()

    # Action buttons visible on the card
    for label in ("Photos", "Reviews", "Directions", "Save", "Share", "Call", "Website"):
        for t in texts:
            if label.lower() == t.lower():
                indicators.add(label)
                break

    # Card-specific metadata (not present on pure route screens)
    for meta in ("about", "updates", "hours", "address", "phone", "rating"):
        if meta in all_text_lower:
            indicators.add(meta)

    return len(indicators) >= 2


def _check_interactions(texts: list[str], log_interactions: list[str] | None = None) -> list[str]:
    """
    Return which interaction evidence is present on screen, merged with any
    log-derived interactions (e.g. execution logs showing successful clicks).

    We report POST-CLICK screen evidence where available, and supplement with
    log evidence for interactions that navigate away and return (e.g. Share
    sheet opened then dismissed with Back, which won't leave Share text on
    the final screen).
    """
    all_text_lower = " ".join(texts).lower()

    # If we're still on the raw search-results list, no card was opened.
    if "about these results" in all_text_lower and "search this area" in all_text_lower:
        return []

    found: set[str] = set()

    # Photos clicked → photo gallery shows "Image result" or "posted by"
    if "image result" in all_text_lower or "view photo map" in all_text_lower or "posted by" in all_text_lower:
        found.add("Photos")

    # Reviews clicked → review list shows "Write a review" or review sorting
    if "write a review" in all_text_lower:
        found.add("Reviews")

    # Directions clicked → route screen shows a Start button and actual route detail
    has_start_button = any(
        t.lower() in ("start", "start navigation", "navigate")
        for t in texts
    )
    has_route_detail = (
        "min" in all_text_lower
        or "best route" in all_text_lower
        or "km" in all_text_lower
        or "mi" in all_text_lower
    )
    if has_start_button and has_route_detail:
        found.add("Directions")

    # Save clicked → button text changes to "Saved" or a confirmation appears
    if "saved" in all_text_lower:
        found.add("Save")

    # Share clicked → share sheet opens (look for "Share via" or similar)
    if any("share" in t.lower() and "via" in t.lower() for t in texts):
        found.add("Share")

    # Call clicked → phone dialer opens; weak indicator, only counts if present
    if any(t.lower() in ("dial", "call") for t in texts):
        found.add("Call")

    # Merge log-derived interactions (e.g. execution logs proving the click succeeded)
    if log_interactions:
        found.update(log_interactions)

    # Require at least 2 distinct interactions to count.
    if len(found) < 2:
        return []
    return sorted(found)


def _check_search_submitted(texts: list[str], resource_ids: list[str]) -> bool:
    """
    Heuristic: search was submitted if screen is NOT showing the initial empty search state.
    We look for:
      - "Search here" text (if present, search likely NOT submitted)
      - Result indicators like business names, "Results", "Reviews", etc.
    """
    all_text = " ".join(texts).lower()
    # If "search here" is the dominant text, likely not submitted
    if all_text.count("search here") >= 1 and len(texts) < 10:
        return False
    # If we see result-related text, search was likely submitted
    result_indicators = ["results", "reviews", "photos", "directions", "call", "website"]
    for indicator in result_indicators:
        if indicator in all_text:
            return True
    # If we see multiple business-like names, search likely submitted
    if len([t for t in texts if len(t) > 5]) > 3:
        return True
    return False


def snapshot_screen(phone_id: str, business_name: str,
                    log_interactions: list[str] | None = None) -> dict[str, Any]:
    """
    Fast single-screen evidence snapshot (no package extraction to keep it quick).
    """
    xml_data = _dump_screen(phone_id)
    texts = _extract_texts(xml_data)
    resource_ids = _extract_resource_ids(xml_data)
    return {
        "search_submitted": _check_search_submitted(texts, resource_ids),
        "business_found": _matches_business_name(texts, business_name),
        "card_opened": _check_card_opened(texts),
        "interactions_present": _check_interactions(texts, log_interactions),
    }


def gather_maps_evidence(phone_id: str, business_name: str,
                        log_interactions: list[str] | None = None) -> dict[str, Any]:
    """
    Gather post-run Maps behavioural evidence.

    Returns:
        {
            "search_submitted": bool,
            "business_found": bool,
            "card_opened": bool,
            "business_name_matched": str | None,
            "interactions_present": list[str],
            "all_texts": list[str],
            "screen_package": str | None,
        }
    """
    xml_data = _dump_screen(phone_id)
    texts = _extract_texts(xml_data)
    resource_ids = _extract_resource_ids(xml_data)

    # Get package from root
    screen_package = None
    try:
        root = ET.fromstring(xml_data)
        for node in root.iter("node"):
            pkg = node.get("package", "").strip()
            if pkg:
                screen_package = pkg
                break
    except ET.ParseError:
        pass

    search_submitted = _check_search_submitted(texts, resource_ids)
    interactions_present = _check_interactions(texts, log_interactions)

    # If execution logs prove >=2 interactions were fired, the card was
    # necessarily opened and the business was found, even if the final
    # screen has navigated away (e.g. Back from share sheet).
    log_proves_card = bool(log_interactions and len(log_interactions) >= 2)

    business_found = _matches_business_name(texts, business_name) or log_proves_card
    card_opened = _check_card_opened(texts) or log_proves_card

    # Find exact matched text for logging
    business_name_matched = None
    if business_found:
        sig_words = [w for w in business_name.lower().split() if w not in _STOPWORDS]
        for t in texts:
            if business_name in t:
                business_name_matched = t
                break
            if sig_words:
                partial = " ".join(sig_words[:3])
                if partial in t.lower():
                    business_name_matched = t
                    break
                if len(sig_words) == 1:
                    if re.search(rf"\b{re.escape(sig_words[0])}\b", t, re.IGNORECASE):
                        business_name_matched = t
                        break

    return {
        "search_submitted": search_submitted,
        "business_found": business_found,
        "card_opened": card_opened,
        "business_name_matched": business_name_matched,
        "interactions_present": interactions_present,
        "all_texts": texts,
        "screen_package": screen_package,
    }


def classify_run_status(
    gps_verified: bool,
    evidence: dict[str, Any],
    maps_task_status: int,
) -> tuple[str, str | None]:
    """
    Classify the overall run status based on GPS + Maps evidence.

    Returns (status, failed_step).
    """
    if not gps_verified:
        return "gps_failed", "gps_not_verified"

    if maps_task_status != 3:
        return "maps_failed", f"maps_task_status_{maps_task_status}"

    if not evidence["search_submitted"]:
        return "maps_search_failed", "search_not_submitted"

    # Strong signal: >=2 distinct interaction buttons fired (from screen + logs)
    # proves the card was open and the business was found, even if the final
    # screen has navigated away (e.g. Back from share sheet -> map view).
    interactions = evidence.get("interactions_present") or []
    if len(interactions) >= 2:
        return "success", None

    if not evidence["business_found"]:
        return "maps_business_not_found", "business_not_found"

    if not evidence["card_opened"]:
        return "maps_card_not_opened", "business_card_not_opened"

    if not interactions:
        return "maps_no_interactions", "no_interaction_buttons_present"

    return "success", None
