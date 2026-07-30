#!/usr/bin/env python3
"""
Generate keywords v3 — three categories with distance-specific nearby points.

Categories:
  1. Discovery (money + business location) — nearby within 2km
  2. Standalone (generic + near me) — nearby within 0.5km
  3. Branded (brand + details) — nearby within 2km

Usage: python generate_keywords_v3.py
"""
import csv
import json
import math
import random
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_data_dir = _repo_root / "data"

CSV_PATH = _data_dir / "accounts_business_mapping.csv"
JSON_PATH = _data_dir / "accounts_business_mapping.json"


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _random_nearby(lat: float, lng: float, radius_km: float) -> tuple[float, float]:
    """Generate a random lat/lng within radius_km of the given point."""
    # Earth's radius in km
    R = 6371.0
    # Convert radius to radians
    radius_rad = radius_km / R
    # Random bearing (0 to 2π)
    bearing = random.uniform(0, 2 * math.pi)
    # Random distance (square root for uniform distribution)
    distance = radius_rad * math.sqrt(random.uniform(0, 1))
    # Current position in radians
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    # New latitude
    lat2 = math.asin(
        math.sin(lat1) * math.cos(distance)
        + math.cos(lat1) * math.sin(distance) * math.cos(bearing)
    )
    # New longitude
    lng2 = lng1 + math.atan2(
        math.sin(bearing) * math.sin(distance) * math.cos(lat1),
        math.cos(distance) - math.sin(lat1) * math.sin(lat2),
    )
    return round(math.degrees(lat2), 6), round(math.degrees(lng2), 6)


def _generate_nearby_points(lat: float, lng: float, radius_km: float, count: int = 10) -> list[str]:
    """Generate 'count' nearby points within radius_km, formatted as 'lat,lng'."""
    points = []
    for _ in range(count):
        n_lat, n_lng = _random_nearby(lat, lng, radius_km)
        points.append(f"{n_lat},{n_lng}")
    return points


# ─── Keyword generators ─────────────────────────────────────────

def generate_discovery_keywords(row: dict) -> list[str]:
    """Discovery: service + business location, within 2km."""
    name = row["business_name"].strip()
    location = row["business_location"].strip()
    location_no_comma = location.replace(",", "").strip()
    area = row.get("business_area", "").strip()

    # Extract main service word from business name
    service_words = ["plumber", "plumbing", "drain", "heating", "boiler", "leak", "toilet"]
    service = "plumber"  # default
    name_lower = name.lower()
    for sw in service_words:
        if sw in name_lower:
            service = sw
            break

    templates = [
        f"{service} {location}",
        f"{service} near {location}",
        f"{service} {location_no_comma}",
        f"{service} near {location_no_comma}",
        f"emergency {service} {location}",
        f"emergency {service} near {location}",
        f"best {service} {location}",
        f"best {service} near {location}",
        f"24 hour {service} {location}",
        f"24 hour {service} near {location}",
        f"cheap {service} {location}",
        f"cheap {service} near {location}",
        f"local {service} {location}",
        f"local {service} near {location}",
        f"gas {service} {location}",
        f"gas {service} near {location}",
        f"reliable {service} {location}",
        f"reliable {service} near {location}",
        f"same day {service} {location}",
        f"same day {service} near {location}",
    ]
    if area and area != location:
        templates.extend([
            f"{service} {area}",
            f"{service} near {area}",
        ])

    seen = set()
    out = []
    for t in templates:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:20]


def generate_standalone_keywords(row: dict) -> list[str]:
    """Standalone: generic service terms with or without 'near me', within 0.5km."""
    name = row["business_name"].strip()
    service_words = ["plumber", "plumbing", "drain", "heating", "boiler", "leak", "toilet"]
    service = "plumber"
    name_lower = name.lower()
    for sw in service_words:
        if sw in name_lower:
            service = sw
            break

    templates = [
        service,
        f"{service} near me",
        f"emergency {service}",
        f"emergency {service} near me",
        f"best {service}",
        f"best {service} near me",
        f"24 hour {service}",
        f"24 hour {service} near me",
        f"cheap {service}",
        f"cheap {service} near me",
        f"local {service}",
        f"local {service} near me",
        f"gas {service}",
        f"gas {service} near me",
        f"boiler repair",
        f"boiler repair near me",
        f"drain unblocking",
        f"drain unblocking near me",
        f"leak detection",
        f"leak detection near me",
        f"toilet repair",
        f"toilet repair near me",
        f"heating engineer",
        f"heating engineer near me",
        f"burst pipe repair",
        f"burst pipe repair near me",
        f"central heating repair",
        f"central heating repair near me",
        f"plumbing company",
        f"plumbing company near me",
    ]
    seen = set()
    out = []
    for t in templates:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:20]


def generate_branded_keywords(row: dict) -> list[str]:
    """Branded: brand + address/timings/services/phone, within 2km."""
    name = row["business_name"].strip()
    addr = row["business_address"].strip()
    parts = addr.split(",")
    street = parts[0].strip() if parts else addr
    postcode = parts[-1].strip() if len(parts) > 1 else ""
    location = row["business_location"].strip()

    templates = [
        name,
        f"{name} near me",
        f"{name} reviews",
        f"{name} phone number",
        f"{name} contact",
        f"{name} opening hours",
        f"{name} emergency",
        f"{name} 24 hour",
        f"{name} services",
        f"{name} {street}",
        f"{name} {postcode}",
        f"{name} {location}",
        f"{name} Google Maps",
        f"{name} directions",
        f"{name} website",
        f"{name} call",
        f"{name} cheap",
        f"{name} best",
        f"{name} local",
        f"{name} address",
        f"{name} location",
        f"{name} plumber",
        f"{name} plumbing",
        f"{name} reviews near me",
        f"{name} phone",
    ]
    seen = set()
    out = []
    for t in templates:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    while len(out) < 20:
        suffix = random.choice(["reviews", "near me", "phone", "emergency", "24hr", "plumber", "plumbing", "local", "cheap", "best", "address", "directions", "contact"])
        candidate = f"{name} {suffix}"
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out[:20]


# ─── Main ───────────────────────────────────────────────────────

def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        bl = float(row["business_lat"])
        b_ln = float(row["business_lng"])

        # Generate keywords
        row["discovery_keywords"] = "|".join(generate_discovery_keywords(row))
        row["standalone_keywords"] = "|".join(generate_standalone_keywords(row))
        row["branded_keywords"] = "|".join(generate_branded_keywords(row))

        # Generate categorized nearby points
        row["nearby_points_discovery"] = "|".join(_generate_nearby_points(bl, b_ln, 2.0, 12))
        row["nearby_points_standalone"] = "|".join(_generate_nearby_points(bl, b_ln, 0.5, 12))
        row["nearby_points_branded"] = "|".join(_generate_nearby_points(bl, b_ln, 2.0, 12))

    # Preserve existing fieldnames and add new ones
    existing_fields = list(rows[0].keys())
    new_fields = [
        "discovery_keywords", "standalone_keywords", "branded_keywords",
        "nearby_points_discovery", "nearby_points_standalone", "nearby_points_branded",
    ]
    fieldnames = existing_fields + [f for f in new_fields if f not in existing_fields]

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # JSON output
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
            "business_area": row.get("business_area", ""),
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
            "nearby_points": [p.strip() for p in row.get("nearby_points", "").split("|") if p.strip()],
            "onsite_points": [p.strip() for p in row.get("onsite_points", "").split("|") if p.strip()],
            "discovery_keywords": row["discovery_keywords"].split("|"),
            "standalone_keywords": row["standalone_keywords"].split("|"),
            "branded_keywords": row["branded_keywords"].split("|"),
            "nearby_points_discovery": row["nearby_points_discovery"].split("|"),
            "nearby_points_standalone": row["nearby_points_standalone"].split("|"),
            "nearby_points_branded": row["nearby_points_branded"].split("|"),
        }

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    print(f"Updated CSV: {CSV_PATH}")
    print(f"Updated JSON: {JSON_PATH}")
    print(f"Businesses processed: {len(rows)}")
    print("\nColumns added:")
    print("  - discovery_keywords (service + location)")
    print("  - standalone_keywords (generic + near me)")
    print("  - branded_keywords (brand + details)")
    print("  - nearby_points_discovery (12 points, ≤2km)")
    print("  - nearby_points_standalone (12 points, ≤0.5km)")
    print("  - nearby_points_branded (12 points, ≤2km)")


if __name__ == "__main__":
    main()
