"""
Generate Geelark Flow Data Sheet

Reads accounts.yaml + businesses.yaml, pairs accounts with businesses
(area-match preferred, round-robin fallback), geocodes addresses,
generates home/work/nearby/on-site coordinates, and writes:
  - WarmingData/geelark_flow_data.yaml
  - WarmingData/geelark_flow_data.csv (exportable)

Run:
  python generate_flow_data.py

Requires: requests (pip install requests)
"""

import sys
import math
import random
import csv
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

try:
    import yaml
except ImportError:
    print("Installing PyYAML...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml", "-q"])
    import yaml

from core.paths import DATA_DIR, ACCOUNTS_FILE, BUSINESSES_FILE

OUTPUT_YAML = DATA_DIR / "geelark_flow_data.yaml"
OUTPUT_CSV  = DATA_DIR / "geelark_flow_data.csv"

# ── Search terms by area ───────────────────────────────────────────────────────

AREA_SEARCH_TERMS = {
    "south_east_london": [
        "plumber near me", "emergency plumber", "boiler repair",
        "local plumber", "heating engineer", "plumbing services",
        "gas safe plumber", "bathroom installation", "leak repair",
        "radiator repair", "toilet repair", "pipe replacement",
        "kitchen plumber", "shower installation", "tap repair",
        "drain unblocking", "central heating", "underfloor heating",
        "plumbing company", "24 hour plumber",
    ],
    "south_west_london": [
        "plumber near me", "emergency plumber", "boiler repair",
        "local plumber", "heating engineer", "plumbing services",
        "gas safe plumber", "bathroom installation", "leak repair",
        "radiator repair", "toilet repair", "pipe replacement",
        "kitchen plumber", "shower installation", "tap repair",
        "drain unblocking", "central heating", "underfloor heating",
        "plumbing company", "24 hour plumber",
    ],
    "east_london": [
        "plumber near me", "emergency plumber", "boiler repair",
        "local plumber", "heating engineer", "plumbing services",
        "gas safe plumber", "bathroom installation", "leak repair",
        "radiator repair", "toilet repair", "pipe replacement",
        "kitchen plumber", "shower installation", "tap repair",
        "drain unblocking", "central heating", "underfloor heating",
        "plumbing company", "24 hour plumber",
    ],
    "north_london": [
        "plumber near me", "emergency plumber", "boiler repair",
        "local plumber", "heating engineer", "plumbing services",
        "gas safe plumber", "bathroom installation", "leak repair",
        "radiator repair", "toilet repair", "pipe replacement",
        "kitchen plumber", "shower installation", "tap repair",
        "drain unblocking", "central heating", "underfloor heating",
        "plumbing company", "24 hour plumber",
    ],
    "south_london": [
        "plumber near me", "emergency plumber", "boiler repair",
        "local plumber", "heating engineer", "plumbing services",
        "gas safe plumber", "bathroom installation", "leak repair",
        "radiator repair", "toilet repair", "pipe replacement",
        "kitchen plumber", "shower installation", "tap repair",
        "drain unblocking", "central heating", "underfloor heating",
        "plumbing company", "24 hour plumber",
    ],
    "sidcup": [
        "plumber sidcup", "emergency plumber sidcup", "boiler repair sidcup",
        "local plumber sidcup", "heating engineer sidcup", "plumbing services sidcup",
        "gas safe plumber sidcup", "bathroom installation sidcup", "leak repair sidcup",
        "radiator repair sidcup", "toilet repair sidcup", "pipe replacement sidcup",
        "kitchen plumber sidcup", "shower installation sidcup", "tap repair sidcup",
        "drain unblocking sidcup", "central heating sidcup", "underfloor heating sidcup",
        "plumbing company sidcup", "24 hour plumber sidcup",
    ],
    "surrey": [
        "plumber epsom", "emergency plumber epsom", "boiler repair epsom",
        "local plumber surrey", "heating engineer surrey", "plumbing services surrey",
        "gas safe plumber surrey", "bathroom installation surrey", "leak repair surrey",
        "radiator repair surrey", "toilet repair surrey", "pipe replacement surrey",
        "kitchen plumber surrey", "shower installation surrey", "tap repair surrey",
        "drain unblocking surrey", "central heating surrey", "underfloor heating surrey",
        "plumbing company surrey", "24 hour plumber surrey",
    ],
    "coventry": [
        "plumber coventry", "emergency plumber coventry", "boiler repair coventry",
        "local plumber coventry", "heating engineer coventry", "plumbing services coventry",
        "gas safe plumber coventry", "bathroom installation coventry", "leak repair coventry",
        "radiator repair coventry", "toilet repair coventry", "pipe replacement coventry",
        "kitchen plumber coventry", "shower installation coventry", "tap repair coventry",
        "drain unblocking coventry", "central heating coventry", "underfloor heating coventry",
        "plumbing company coventry", "24 hour plumber coventry",
    ],
    "birmingham": [
        "plumber birmingham", "emergency plumber birmingham", "boiler repair birmingham",
        "local plumber birmingham", "heating engineer birmingham", "plumbing services birmingham",
        "gas safe plumber birmingham", "bathroom installation birmingham", "leak repair birmingham",
        "radiator repair birmingham", "toilet repair birmingham", "pipe replacement birmingham",
        "kitchen plumber birmingham", "shower installation birmingham", "tap repair birmingham",
        "drain unblocking birmingham", "central heating birmingham", "underfloor heating birmingham",
        "plumbing company birmingham", "24 hour plumber birmingham",
    ],
    "midlands": [
        "plumber hinckley", "emergency plumber hinckley", "boiler repair hinckley",
        "local plumber leicestershire", "heating engineer leicestershire", "plumbing services leicestershire",
        "gas safe plumber leicestershire", "bathroom installation leicestershire", "leak repair leicestershire",
        "radiator repair leicestershire", "toilet repair leicestershire", "pipe replacement leicestershire",
        "kitchen plumber leicestershire", "shower installation leicestershire", "tap repair leicestershire",
        "drain unblocking leicestershire", "central heating leicestershire", "underfloor heating leicestershire",
        "plumbing company leicestershire", "24 hour plumber leicestershire",
    ],
    "london": [
        "plumber london", "emergency plumber london", "boiler repair london",
        "local plumber london", "heating engineer london", "plumbing services london",
        "gas safe plumber london", "bathroom installation london", "leak repair london",
        "radiator repair london", "toilet repair london", "pipe replacement london",
        "kitchen plumber london", "shower installation london", "tap repair london",
        "drain unblocking london", "central heating london", "underfloor heating london",
        "plumbing company london", "24 hour plumber london",
    ],
    "default": [
        "plumber near me", "emergency plumber", "boiler repair",
        "local plumber", "heating engineer", "plumbing services",
        "gas safe plumber", "bathroom installation", "leak repair",
        "radiator repair", "toilet repair", "pipe replacement",
        "kitchen plumber", "shower installation", "tap repair",
        "drain unblocking", "central heating", "underfloor heating",
        "plumbing company", "24 hour plumber",
    ],
}


def get_search_terms(area: str) -> list[str]:
    return AREA_SEARCH_TERMS.get(area, AREA_SEARCH_TERMS["default"])


BRANDED_SEARCH_TEMPLATES = [
    "{name}",
    "{name} {location}",
    "{name} opening hours",
    "{name} reviews",
    "{name} phone number",
    "{name} contact",
    "{name} services",
    "{name} prices",
    "{name} facebook",
    "{name} instagram",
    "{name} twitter",
    "{name} linkedin",
    "{name} website",
    "{name} address",
    "{name} near me",
    "{name} emergency",
    "{name} book appointment",
    "{name} directions",
    "{name} {address}",
    "best {name} reviews",
]

LOCAL_KEYWORD_TEMPLATES = [
    "emergency plumber {area}",
    "leak detection {area}",
    "blocked drain {area}",
    "plumber {area}",
    "local plumber {area}",
    "plumbing services {area}",
    "gas safe plumber {area}",
    "boiler repair {area}",
    "central heating {area}",
    "bathroom installation {area}",
    "shower installation {area}",
    "tap repair {area}",
    "toilet repair {area}",
    "radiator repair {area}",
    "pipe replacement {area}",
    "drain unblocking {area}",
    "underfloor heating {area}",
    "water leak {area}",
    "burst pipe {area}",
    "24 hour plumber {area}",
    "cheap plumber {area}",
    "best plumber {area}",
    "plumber near me {area}",
    "heating engineer {area}",
    "kitchen plumber {area}",
    "plumbing company {area}",
    "emergency plumbing {area}",
    "commercial plumber {area}",
    "residential plumber {area}",
]


def generate_branded_searches(name: str, address: str, location: str) -> list[str]:
    """Return ~20 branded search queries for a specific business."""
    city = location.split(",")[0].strip() if "," in location else location
    short_address = address.split(",")[0].strip() if "," in address else address
    searches = []
    for tmpl in BRANDED_SEARCH_TEMPLATES:
        q = tmpl.replace("{name}", name).replace("{location}", city).replace("{address}", short_address)
        searches.append(q)
    return searches


def generate_local_searches(area: str, location: str) -> list[str]:
    """Return ~20-30 local keyword searches with area substituted."""
    city = location.split(",")[0].strip() if "," in location else location
    area_label = area.replace("_", " ").title()
    searches = []
    for tmpl in LOCAL_KEYWORD_TEMPLATES:
        q = tmpl.replace("{area}", city)
        searches.append(q)
    return searches


# ── Geocoding ──────────────────────────────────────────────────────────────────

import re
import time

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
_geocode_cache: dict[str, tuple[float, float]] = {}

_UK_POSTCODE_RE = re.compile(r"([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})", re.IGNORECASE)


def _extract_postcode(address: str) -> str | None:
    m = _UK_POSTCODE_RE.search(address)
    return m.group(1).replace(" ", "").upper() if m else None


def geocode_postcodes_io(postcode: str) -> tuple[float, float] | None:
    """Use postcodes.io for UK postcodes — free, no key, fast."""
    try:
        resp = requests.get(
            f"{_POSTCODES_IO_URL}/{postcode}",
            headers={"User-Agent": "AccountWarmer/1.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == 200 and data.get("result"):
                lat = data["result"]["latitude"]
                lng = data["result"]["longitude"]
                return float(lat), float(lng)
    except Exception:
        pass
    return None


def geocode_maps_share_link(share_link: str) -> tuple[float, float] | None:
    """
    Follow a Google Maps short link redirect and extract coordinates
    from the final URL (e.g. .../@51.5007,-0.1246,...).
    """
    try:
        resp = requests.get(
            share_link,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
            allow_redirects=True,
        )
        final_url = resp.url
        # Look for @lat,lng pattern
        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", final_url)
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:
        pass
    return None


def geocode(address: str, share_link: str = "", fallback_name: str = "") -> tuple[float, float] | None:
    """Multi-strategy geocoder: postcode -> share link -> Nominatim."""
    cache_key = address or share_link or fallback_name
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    # Strategy 1: UK postcode via postcodes.io
    postcode = _extract_postcode(address)
    if postcode:
        coords = geocode_postcodes_io(postcode)
        if coords:
            _geocode_cache[cache_key] = coords
            return coords

    # Strategy 2: Google Maps share link redirect
    if share_link:
        coords = geocode_maps_share_link(share_link)
        if coords:
            _geocode_cache[cache_key] = coords
            return coords

    # Strategy 3: Nominatim with backoff
    time.sleep(1.5)
    for q in (address, fallback_name):
        if not q:
            continue
        try:
            resp = requests.get(
                _NOMINATIM_URL,
                params={"q": q, "format": "json", "limit": 1},
                headers={"User-Agent": "AccountWarmer/1.0"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                _geocode_cache[cache_key] = (lat, lon)
                return lat, lon
        except Exception:
            continue
    return None


# ── Coordinate helpers ───────────────────────────────────────────────────────

def _offset_meters(lat: float, lng: float, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    """Offset lat/lng by distance (meters) and bearing (degrees)."""
    R = 6_371_000
    lat_r = math.radians(lat)
    lng_r = math.radians(lng)
    d_r = distance_m / R
    b_r = math.radians(bearing_deg)
    new_lat = math.asin(
        math.sin(lat_r) * math.cos(d_r) +
        math.cos(lat_r) * math.sin(d_r) * math.cos(b_r)
    )
    new_lng = lng_r + math.atan2(
        math.sin(b_r) * math.sin(d_r) * math.cos(lat_r),
        math.cos(d_r) - math.sin(lat_r) * math.sin(new_lat)
    )
    return round(math.degrees(new_lat), 6), round(math.degrees(new_lng), 6)


def generate_nearby_points(lat: float, lng: float, count: int = 20, max_m: float = 1609) -> list[dict]:
    """Generate `count` random lat/lng points within `max_m` meters."""
    points = []
    for i in range(count):
        dist = random.uniform(50, max_m)
        bear = random.uniform(0, 360)
        p_lat, p_lng = _offset_meters(lat, lng, dist, bear)
        points.append({
            "id": f"nearby_{i+1}",
            "lat": p_lat,
            "lng": p_lng,
            "distance_m": round(dist, 1),
            "bearing": round(bear, 1),
        })
    return points


def generate_onsite_points(lat: float, lng: float, count: int = 10) -> list[dict]:
    """Generate `count` points very close to the location (0-30m)."""
    points = []
    for i in range(count):
        dist = random.uniform(0, 30)
        bear = random.uniform(0, 360)
        p_lat, p_lng = _offset_meters(lat, lng, dist, bear)
        points.append({
            "id": f"onsite_{i+1}",
            "lat": p_lat,
            "lng": p_lng,
            "distance_m": round(dist, 1),
            "bearing": round(bear, 1),
        })
    return points


def generate_home_work(lat: float, lng: float, area: str) -> tuple[dict, dict]:
    """Generate a home and work address near the business area."""
    # Home: 0.5 - 2 miles away, random direction
    home_dist = random.uniform(800, 3200)
    home_bear = random.uniform(0, 360)
    home_lat, home_lng = _offset_meters(lat, lng, home_dist, home_bear)
    home = {
        "lat": home_lat,
        "lng": home_lng,
        "distance_m": round(home_dist, 1),
        "address": f"Residential address near {area}",
    }

    # Work: 0.3 - 1.5 miles away, different direction
    work_dist = random.uniform(500, 2400)
    work_bear = (home_bear + random.uniform(90, 270)) % 360
    work_lat, work_lng = _offset_meters(lat, lng, work_dist, work_bear)
    work = {
        "lat": work_lat,
        "lng": work_lng,
        "distance_m": round(work_dist, 1),
        "address": f"Work address near {area}",
    }
    return home, work


# ── Pairing logic ─────────────────────────────────────────────────────────────

def pair_accounts_businesses(accounts: list[dict], businesses: list[dict]) -> list[tuple[dict, dict]]:
    """
    Pair each account with one business.
    Strategy:
      1. Area match: account geo_area (from geelark_accounts) matches business area
      2. City match: account geo_city matches business area loosely
      3. Round-robin fallback for unmatched
    """
    # Build account -> geo_area lookup from geelark_accounts.yaml
    gl_file = DATA_DIR / "geelark_accounts.yaml"
    geo_areas: dict[str, str] = {}
    if gl_file.exists():
        gl_data = yaml.safe_load(gl_file.read_text(encoding="utf-8")) or {}
        for gl_acc in gl_data.get("accounts", []):
            email = gl_acc.get("email", "").lower()
            area = gl_acc.get("geo_area", "")
            if email and area:
                geo_areas[email] = area

    unpaired_accs = list(accounts)
    unpaired_bizs = list(businesses)
    pairs: list[tuple[dict, dict]] = []
    paired_biz_ids: set[str] = set()

    # Pass 1: strict area match
    for acc in list(unpaired_accs):
        email = acc.get("email", "").lower()
        acc_area = geo_areas.get(email, acc.get("geo_city", "")).lower().replace(" ", "_")
        matched_biz = None
        for biz in unpaired_bizs:
            biz_area = biz.get("area", "").lower().replace(" ", "_")
            if acc_area and biz_area and acc_area == biz_area:
                matched_biz = biz
                break
        if matched_biz:
            pairs.append((acc, matched_biz))
            unpaired_accs.remove(acc)
            unpaired_bizs.remove(matched_biz)

    # Pass 2: loose area match (partial)
    for acc in list(unpaired_accs):
        email = acc.get("email", "").lower()
        acc_area = geo_areas.get(email, acc.get("geo_city", "")).lower()
        matched_biz = None
        for biz in unpaired_bizs:
            biz_area = biz.get("area", "").lower()
            if acc_area and biz_area and (acc_area in biz_area or biz_area in acc_area):
                matched_biz = biz
                break
        if matched_biz:
            pairs.append((acc, matched_biz))
            unpaired_accs.remove(acc)
            unpaired_bizs.remove(matched_biz)

    # Pass 3: round-robin for remaining
    for acc in list(unpaired_accs):
        if unpaired_bizs:
            biz = unpaired_bizs.pop(0)
        else:
            # All businesses used — rotate back through all of them
            biz = businesses[len(pairs) % len(businesses)]
        pairs.append((acc, biz))

    return pairs


# ── Main generator ─────────────────────────────────────────────────────────────

def generate():
    print("Loading accounts and businesses...")
    acc_data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    biz_data = yaml.safe_load(BUSINESSES_FILE.read_text(encoding="utf-8")) or {}

    accounts = acc_data.get("accounts", [])
    businesses = biz_data.get("businesses", [])

    if not accounts:
        print("No accounts found.")
        return
    if not businesses:
        print("No businesses found.")
        return

    print(f"Loaded {len(accounts)} accounts, {len(businesses)} businesses.")
    print("Pairing accounts with businesses...")
    pairs = pair_accounts_businesses(accounts, businesses)

    rows = []
    for acc, biz in pairs:
        acc_id = acc["id"]
        biz_id = biz["id"]
        area = biz.get("area", "default")
        biz_address = biz.get("address", "")

        print(f"  [{acc_id}] -> {biz_id} ({biz.get('name')}) — geocoding...")

        # Geocode business address
        biz_lat, biz_lng = None, None
        share_link = biz.get("share_link", "")
        fallback = f"{biz.get('name', '')}, {biz.get('location', '')}"
        coords = geocode(biz_address, share_link=share_link, fallback_name=fallback)
        if coords:
            biz_lat, biz_lng = coords

        if biz_lat is None:
            print(f"    WARNING: Could not geocode {biz_id}, using random London coords.")
            biz_lat, biz_lng = 51.5074 + random.uniform(-0.1, 0.1), -0.1278 + random.uniform(-0.1, 0.1)

        # Generate home/work
        home, work = generate_home_work(biz_lat, biz_lng, area)

        # Generate nearby and on-site points
        nearby = generate_nearby_points(biz_lat, biz_lng, count=20, max_m=1609)
        onsite = generate_onsite_points(biz_lat, biz_lng, count=10)

        # Search terms
        search_terms = get_search_terms(area)

        # Branded searches (specific to this business)
        branded_searches = generate_branded_searches(
            biz.get("name", ""), biz_address, biz.get("location", "")
        )

        # Local keyword searches (area-based)
        local_searches = generate_local_searches(area, biz.get("location", ""))

        # Find geelark phone info
        gl_file = DATA_DIR / "geelark_accounts.yaml"
        phone_id = ""
        gl_password = ""
        gl_totp = ""
        if gl_file.exists():
            gl_data = yaml.safe_load(gl_file.read_text(encoding="utf-8")) or {}
            for gl_acc in gl_data.get("accounts", []):
                if gl_acc.get("email", "").lower() == acc.get("email", "").lower():
                    phone_id = str(gl_acc.get("geelark_phone_id", ""))
                    gl_password = gl_acc.get("password", "")
                    gl_totp = gl_acc.get("totp_secret", "")
                    break

        row = {
            "account_id": acc_id,
            "account_email": acc.get("email", ""),
            "geelark_phone_id": phone_id,
            "account_password": gl_password,
            "account_totp": gl_totp,
            "multilogin_profile_id": acc.get("multilogin_profile_id", ""),
            "proxy": acc.get("proxy", ""),
            "geo_city": acc.get("geo_city", ""),
            "strategy": acc.get("strategy", ""),
            "business_id": biz_id,
            "business_name": biz.get("name", ""),
            "business_address": biz_address,
            "business_location": biz.get("location", ""),
            "business_area": area,
            "business_share_link": biz.get("share_link", ""),
            "business_goal": biz.get("goal", ""),
            "business_lat": biz_lat,
            "business_lng": biz_lng,
            "home_lat": home["lat"],
            "home_lng": home["lng"],
            "home_address": home["address"],
            "work_lat": work["lat"],
            "work_lng": work["lng"],
            "work_address": work["address"],
            "nearby_points": nearby,
            "onsite_points": onsite,
            "search_terms": search_terms,
            "branded_searches": branded_searches,
            "local_searches": local_searches,
            "generated_at": datetime.now().isoformat(),
        }
        rows.append(row)

    # Write YAML
    print(f"\nWriting {OUTPUT_YAML}...")
    OUTPUT_YAML.write_text(
        yaml.safe_dump({"flows": rows}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # Write CSV (lat,lng combined in single cells, points pipe-separated)
    print(f"Writing {OUTPUT_CSV}...")
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        headers = [
            "account_id", "account_email", "geelark_phone_id", "account_password", "account_totp",
            "multilogin_profile_id", "proxy", "geo_city", "strategy",
            "business_id", "business_name", "business_address", "business_location",
            "business_area", "business_share_link", "business_goal",
            "business_coords", "home_coords", "home_address",
            "work_coords", "work_address",
            "nearby_points", "onsite_points",
            "search_terms", "branded_searches", "local_searches",
        ]
        writer.writerow(headers)

        for row in rows:
            nearby_pipe = "|".join(f"{p['lat']},{p['lng']}" for p in row["nearby_points"])
            onsite_pipe = "|".join(f"{p['lat']},{p['lng']}" for p in row["onsite_points"])
            csv_row = [
                row["account_id"],
                row["account_email"],
                row["geelark_phone_id"],
                row["account_password"],
                row["account_totp"],
                row["multilogin_profile_id"],
                row["proxy"],
                row["geo_city"],
                row["strategy"],
                row["business_id"],
                row["business_name"],
                row["business_address"],
                row["business_location"],
                row["business_area"],
                row["business_share_link"],
                row["business_goal"],
                f"{row['business_lat']},{row['business_lng']}",
                f"{row['home_lat']},{row['home_lng']}",
                row["home_address"],
                f"{row['work_lat']},{row['work_lng']}",
                row["work_address"],
                nearby_pipe,
                onsite_pipe,
                "|".join(row["search_terms"]),
                "|".join(row["branded_searches"]),
                "|".join(row["local_searches"]),
            ]
            writer.writerow(csv_row)

    print("\nDone!")
    print(f"  YAML: {OUTPUT_YAML}")
    print(f"  CSV:  {OUTPUT_CSV}")
    print(f"  Rows: {len(rows)}")


if __name__ == "__main__":
    generate()
