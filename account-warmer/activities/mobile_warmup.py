"""
Mobile Warm-Up — Daily Short Sessions on GeelarK Cloud Phones

Runs one 2–5 minute session per phone.  All sessions are strictly sequential
(one phone at a time) because all 5 phones share a single StreamVia mobile
proxy — starting phones simultaneously would give them all the same IP.

Session flow per phone:
  1. Rotate proxy IP (changeipunique=true) — poll until Ready
  2. Start phone
  3. Wait for boot
  4. Refresh GPS location (city coordinates)
  5. Open Google Maps → search a random nearby place → browse 30s
  6. Open YouTube → browse Shorts or search a topic → watch 30–60s
  7. Open Gmail → scroll inbox
  8. Return to home screen
  9. Stop phone
  10. Log session to WarmingData/logs/mobile_sessions.json

Skips any account with mobile_warming_enabled=false or no phone provisioned.
"""

import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mobile_warmup")

# ── Import ADB helpers ────────────────────────────────────────────────────────
from activities.google_login_mobile import (
    _shell, _find_and_tap, _get_window_focus, _rotate_proxy_ip, _CITY_CONFIG,
    _wait_for_foreground_app,
)

LAUNCHER_ACTIVITY = "com.android.launcher3"

# Proxy rotation rate limit: StreamVia enforces a 180s minimum between rotations.
# Track the last rotation time so we never rotate too quickly.
_last_proxy_rotation: float = 0.0
_PROXY_ROTATION_COOLDOWN = 185  # seconds (5s buffer over the 180s limit)

# ── Random content pools ──────────────────────────────────────────────────────

_MAP_SEARCHES = [
    "coffee shops near me",
    "restaurants near me",
    "pharmacies near me",
    "supermarkets near me",
    "gyms near me",
    "parks near me",
    "barbers near me",
    "pizza delivery near me",
    "petrol stations near me",
    "post office near me",
]

# London driving directions pairs — (dest_lat_lon, origin_lat, origin_lon, label)
# dest_lat_lon used in saddr/daddr URL so Maps never falls back to IP/network location.
# Pairs are ~0.5–3 km apart across South/Central/East London.
_DIRECTIONS_PAIRS = [
    # (dest_coords,           origin_lat,  origin_lon,  label)
    ("51.5117,-0.1240",       51.5137,    -0.1337,  ),  # Soho → Covent Garden
    ("51.5178,-0.0823",       51.5228,    -0.0782,  ),  # Shoreditch → Liverpool St
    ("51.4613,-0.1156",       51.4618,    -0.1388,  ),  # Clapham → Brixton
    ("51.5309,-0.1233",       51.5390,    -0.1426,  ),  # Camden → King's Cross
    ("51.5142,-0.1878",       51.5085,    -0.1960,  ),  # Notting Hill → Bayswater
    ("51.4826,-0.0077",       51.5054,    -0.0235,  ),  # Canary Wharf → Greenwich
    ("51.5453,-0.0753",       51.5450,    -0.0553,  ),  # Hackney → Dalston
    ("51.5325,-0.1057",       51.5349,    -0.1029,  ),  # Islington → Angel
    ("51.4959,-0.1002",       51.4736,    -0.0694,  ),  # Peckham → Elephant & Castle
    ("51.4875,-0.1687",       51.4739,    -0.1994,  ),  # Fulham → Chelsea
    ("51.5045,-0.2180",       51.4927,    -0.2237,  ),  # Hammersmith → Shepherd's Bush
    ("51.5196,-0.0613",       51.5269,    -0.0545,  ),  # Bethnal Green → Whitechapel
    ("51.4881,-0.1071",       51.4855,    -0.1234,  ),  # Vauxhall → Kennington
    ("51.5014,-0.0898",       51.4995,    -0.0745,  ),  # Bermondsey → Borough Market
    ("51.5568,-0.1368",       51.5657,    -0.1356,  ),  # Archway → Tufnell Park
    ("51.4959,-0.1427",       51.4875,    -0.1687,  ),  # Chelsea → Victoria
    ("51.5269,-0.0545",       51.5196,    -0.0613,  ),  # Whitechapel → Bethnal Green
    ("51.5642,-0.1066",       51.5453,    -0.0753,  ),  # Dalston → Finsbury Park
    ("51.4732,-0.1227",       51.4613,    -0.1156,  ),  # Brixton → Stockwell
    ("51.5054,-0.0235",       51.4826,    -0.0077,  ),  # Greenwich → Canary Wharf
    # Additional routes
    ("51.5074,-0.1278",       51.5014,    -0.0898,  ),  # Bermondsey → Waterloo
    ("51.4893,-0.1441",       51.4959,    -0.1427,  ),  # Chelsea → Pimlico
    ("51.5476,-0.0891",       51.5642,    -0.1066,  ),  # Finsbury Park → Stoke Newington
    ("51.4627,-0.3053",       51.4736,    -0.2994,  ),  # Ealing → Hanwell
    ("51.5400,-0.1426",       51.5309,    -0.1233,  ),  # King's Cross → Euston
    ("51.4982,-0.1749",       51.5045,    -0.2180,  ),  # Hammersmith → Chiswick
    ("51.5155,-0.0775",       51.5228,    -0.0782,  ),  # Spitalfields → Shoreditch
    ("51.4835,-0.1036",       51.4881,    -0.1071,  ),  # Stockwell → Oval
    ("51.5116,-0.1362",       51.5142,    -0.1878,  ),  # Soho → Paddington
    ("51.4765,-0.0544",       51.4732,    -0.0422,  ),  # Deptford → New Cross
    ("51.5800,-0.1756",       51.5752,    -0.1669,  ),  # Hendon → Brent Cross
    ("51.5500,-0.2800",       51.5613,    -0.2988,  ),  # Wembley → Harrow
]

_YOUTUBE_SEARCHES = [
    "london street food",
    "manchester city highlights",
    "uk news today",
    "recipe pasta",
    "funny moments football",
    "london vlog",
    "best restaurants london",
    "morning workout routine",
    "travel uk",
    "cooking at home",
    "premier league goals",
    "london weather forecast",
    "how to make sourdough",
    "british sitcom clips",
    "chelsea fc highlights",
]

_GOOGLE_SEARCHES = [
    # Food & Drink
    "best pizza near me",
    "best indian restaurant near me",
    "best chinese takeaway london",
    "best sushi restaurant london",
    "best burger place london",
    "best ramen london",
    "best sunday roast london",
    "best brunch london",
    "best afternoon tea london",
    "best vegan restaurant london",
    "best coffee shops london",
    "best espresso martini london",
    "best cocktail bars shoreditch",
    "best pub near me",
    "best gastropub london",
    "best fish and chips london",
    "best kebab near me",
    "best curry house near me",
    "best bakery london",
    "best dim sum london",
    "best tapas london",
    "best thai restaurant london",
    "best korean bbq london",
    "best pizza shoreditch",
    "best italian restaurant london",
    "best steak restaurant london",
    "best noodles near me",
    "best fried chicken near me",
    "best pho london",
    "best wine bar london",
    # Shopping
    "what time does sainsbury's close",
    "what time does tesco open",
    "what time does boots close",
    "primark opening times",
    "john lewis nearest to me",
    "marks and spencer near me",
    "waitrose near me",
    "asda near me",
    "lidl near me",
    "aldi near me",
    "ikea london opening times",
    "next clothing sale uk",
    "nike store london",
    "adidas store london",
    "apple store london nearest",
    "currys pc world near me",
    "argos near me opening times",
    "halfords near me",
    "b&q near me",
    "homebase near me",
    "waterstones london",
    "hmv london",
    "tk maxx near me",
    "sports direct near me",
    "decathlon london",
    # Transport
    "how to get to heathrow from central london",
    "tube map pdf",
    "london overground timetable",
    "gatwick express timetable",
    "national rail journey planner",
    "oyster card top up online",
    "how much is a daily travelcard london",
    "elizabeth line stations",
    "dlr map london",
    "bus from victoria to waterloo",
    "cheapest uber alternative london",
    "how long does it take to walk from london bridge to tower bridge",
    "parking near o2 arena",
    "congestion charge zone map",
    "ulez checker",
    "cycle hire near me",
    "night tube map",
    "heathrow terminal 5 airlines",
    "stansted express time",
    "thameslink timetable",
    # Weather & News
    "weather in london this week",
    "weather forecast london 10 days",
    "bbc weather london",
    "uk news today",
    "the guardian latest news",
    "bbc news uk",
    "sky news headlines",
    "times newspaper uk",
    "evening standard london",
    "metro uk news",
    # Things to Do
    "things to do in london this weekend",
    "free things to do in london",
    "museums in london free",
    "london eye ticket prices",
    "tower of london tickets",
    "tate modern opening times",
    "natural history museum london",
    "victoria and albert museum",
    "british museum opening times",
    "science museum london",
    "national gallery london",
    "southbank events this week",
    "barbican events london",
    "comedy club london",
    "escape rooms london",
    "bowling near me",
    "go karting near me london",
    "laser tag near me",
    "trampoline park near me",
    "adventure golf near me",
    "cinemas near me",
    "odeon listings london",
    "vue cinema near me",
    "picturehouse cinema london",
    "theatre shows london",
    "west end shows london",
    "hamilton tickets london",
    "les miserables london",
    "phantom of the opera london",
    "wicked musical london",
    # Health & NHS
    "nhs urgent care near me",
    "nhs 111 online",
    "gp near me accepting patients",
    "dentist near me nhs",
    "optician near me",
    "pharmacy open now near me",
    "walk in centre near me",
    "covid booster where to get it",
    "how to register with a gp",
    "how to get a sick note uk",
    "nhs app how to use",
    "blood test near me",
    "hospital near me",
    # Finance & Admin
    "what is the minimum wage uk 2024",
    "how long does passport renewal take uk",
    "dvla driving licence renewal",
    "how to apply for universal credit",
    "council tax bands london",
    "stamp duty calculator uk",
    "how to check national insurance number",
    "self assessment tax return deadline",
    "hmrc contact number",
    "how to open a bank account uk",
    "best savings account uk 2024",
    "best credit card uk cashback",
    "mortgage calculator uk",
    "help to buy scheme uk",
    "lifetime isa rules",
    "how to claim ppi",
    "energy bills help uk",
    "ofgem price cap",
    # Travel
    "cheap flights to barcelona",
    "cheap flights to amsterdam",
    "cheap flights to rome",
    "cheap flights to dubai",
    "cheap flights to new york",
    "best hotels in paris",
    "airbnb london",
    "booking.com deals",
    "skyscanner cheapest days to fly",
    "easyjet hand luggage allowance",
    "ryanair check in online",
    "british airways seat selection",
    "euro tunnel prices",
    "ferry to france calais",
    "travel insurance uk",
    "esta for usa from uk",
    "do i need a visa for turkey uk passport",
    "spain travel requirements uk",
    "europe travel insurance",
    "best time to visit japan",
    # Sport & Fitness
    "premier league fixtures",
    "premier league table",
    "arsenal fixtures",
    "chelsea fc fixtures",
    "tottenham fixtures",
    "man city results",
    "man united news",
    "liverpool fc latest",
    "championship table",
    "fa cup results",
    "rugby six nations fixtures",
    "wimbledon tickets",
    "formula 1 schedule 2024",
    "golf uk open 2024",
    "boxing fights uk 2024",
    "gym near me",
    "crossfit near me london",
    "swimming pool near me",
    "yoga classes near me",
    "pilates near me",
    "personal trainer london",
    "park run near me",
    "5k training plan for beginners",
    "how to lose weight quickly",
    "best protein powder uk",
    # Home & Garden
    "national trust places near london",
    "garden centre near me",
    "b&q compost",
    "best lawn mower uk 2024",
    "how to fix a leaking tap",
    "best boiler brand uk",
    "how to bleed a radiator",
    "loft conversion cost london",
    "extension cost london",
    "best estate agents london",
    "rightmove london",
    "zoopla london",
    "how much is my house worth",
    "best interior designers london",
    "ikea kitchen planner",
    # Tech & Entertainment
    "how to cancel netflix uk",
    "best vpn uk 2024",
    "iphone 15 review",
    "samsung galaxy s24 vs iphone",
    "best laptop uk 2024",
    "best broadband deals uk",
    "sky tv packages",
    "virgin media deals",
    "spotify student discount",
    "amazon prime day 2024",
    "best smart tv uk",
    "nintendo switch deals uk",
    "ps5 restock uk",
    "xbox game pass games list",
    "best podcast apps uk",
    # Recipes & Cooking
    "how to make sourdough bread",
    "best pasta carbonara recipe",
    "easy chicken curry recipe",
    "how to make naan bread",
    "best chocolate cake recipe uk",
    "how to cook a perfect steak",
    "vegetarian recipes quick",
    "how to make risotto",
    "easy biscuit recipe",
    "how to make yorkshire pudding",
    "beef stew slow cooker recipe",
    "how to make homemade pizza",
    "best chilli con carne recipe",
    "how to make crumpets",
    "fish pie recipe uk",
    # Misc Everyday
    "post office near me opening times",
    "how to send a parcel royal mail",
    "royal mail tracking",
    "hermes parcel tracking",
    "dpd delivery tracking",
    "how to recycle near me",
    "tip near me opening times",
    "dry cleaner near me",
    "car wash near me",
    "petrol station near me cheapest",
    "dog groomer near me",
    "vet near me",
    "locksmith near me 24 hour",
    "electrician near me",
    "plumber near me emergency",
    "cleaning service near me",
    "removal company london",
    "storage unit near me",
    "how to dispute a parking ticket",
    "how to report a pothole uk",
]

# ── ADB helpers ───────────────────────────────────────────────────────────────

def _press_home(phone_id: str) -> None:
    _shell(phone_id, "input keyevent KEYCODE_HOME")
    time.sleep(2)


def _open_app(phone_id: str, package: str, acc_id: str = "") -> None:
    _shell(phone_id, f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    _wait_for_foreground_app(phone_id, package, timeout=12, acc_id=acc_id)


def _swipe_down(phone_id: str) -> None:
    _shell(phone_id, "input swipe 540 400 540 1200 700")
    time.sleep(1)


def _press_back(phone_id: str, times: int = 1) -> None:
    for _ in range(times):
        _shell(phone_id, "input keyevent KEYCODE_BACK")
        time.sleep(1)


def _type_and_search(phone_id: str, text: str) -> None:
    """Type into the currently-focused search box and press Enter."""
    escaped = text.replace(" ", "%s")
    _shell(phone_id, f"input text {escaped}")
    time.sleep(0.8)
    _shell(phone_id, "input keyevent 66")  # Enter
    time.sleep(3)


def _refresh_gps(phone_id: str, account: dict, acc_id: str) -> None:
    """
    Set GPS location via GeelarK API.

    Priority order:
      1. home_lat / home_lng from account (set by generate-home-addresses) — most precise
      2. geo_area from AREAS dict (area-centre with random jitter)
      3. geo_city fallback via ADB

    A small jitter is always applied so exact coordinates vary each session.
    """
    from set_phone_area import AREAS, area_coords, set_phone_gps

    # Priority 1: home or work coords (65% home / 35% work when both available)
    home_lat = account.get("home_lat")
    home_lng = account.get("home_lng")
    work_lat = account.get("work_lat")
    work_lng = account.get("work_lng")

    use_work = work_lat and work_lng and random.random() < 0.35
    if use_work:
        base_lat, base_lng, loc_label = work_lat, work_lng, "work"
    elif home_lat and home_lng:
        base_lat, base_lng, loc_label = home_lat, home_lng, "home"
    else:
        base_lat = base_lng = None

    if base_lat and base_lng:
        lat = base_lat + random.uniform(-0.0005, 0.0005)
        lon = base_lng + random.uniform(-0.0007, 0.0007)
        ok = set_phone_gps(phone_id, lat, lon)
        if ok:
            log.info("[%s] GPS set to %s location (%.4f, %.4f)", acc_id, loc_label, lat, lon)
            return
        log.warning("[%s] GPS API failed for %s coords, trying geo_area", acc_id, loc_label)

    # Priority 2: geo_area centre with area-radius jitter
    area_key = account.get("geo_area")
    if area_key and area_key in AREAS:
        lat, lon = area_coords(area_key, jitter=True)
        name = AREAS[area_key][0]
        ok = set_phone_gps(phone_id, lat, lon)
        if ok:
            log.info("[%s] GPS set via API: %s (%.4f, %.4f)", acc_id, name, lat, lon)
            return
        log.warning("[%s] GPS API failed, falling back to ADB", acc_id)

    # Fallback: city-level ADB method
    geo_city = (account.get("geo_city") or "london").lower()
    cfg = _CITY_CONFIG.get(geo_city, _CITY_CONFIG["london"])
    _, lat, lon = cfg
    _shell(phone_id, "settings put secure location_mode 3")
    _shell(phone_id,
           f"am startservice -n com.android.location.fused/.FusedLocationService "
           f"--ef latitude {lat} --ef longitude {lon}")
    log.info("[%s] GPS set via ADB fallback: lat=%.4f lon=%.4f (city=%s)", acc_id, lat, lon, geo_city)


# ── Route cache — avoids re-querying OSRM for the same pair ──────────────────
_route_cache: dict[str, list[tuple[float, float]]] = {}


def _fetch_route_waypoints(
    origin_lat: float, origin_lon: float,
    dest_lat: float,   dest_lon: float,
) -> list[tuple[float, float]]:
    """
    Fetch road-following waypoints from the OSRM public routing API.

    Returns a list of (lat, lon) tuples that trace actual London roads between
    origin and destination.  Results are cached in-process so repeated sessions
    on the same route pair don't make redundant HTTP calls.

    Falls back to an empty list on any network/parse error — callers should
    handle that by falling back to linear interpolation.
    """
    import requests

    # Round to 4 dp for cache key (~11 m precision — plenty for our jittered origins)
    cache_key = f"{origin_lat:.4f},{origin_lon:.4f};{dest_lat:.4f},{dest_lon:.4f}"
    if cache_key in _route_cache:
        return _route_cache[cache_key]

    # OSRM expects coordinates as lon,lat (not lat,lon)
    url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{origin_lon:.6f},{origin_lat:.6f};{dest_lon:.6f},{dest_lat:.6f}"
        f"?geometries=geojson&overview=full"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # GeoJSON coordinates are [lon, lat] — swap to (lat, lon)
        raw = data["routes"][0]["geometry"]["coordinates"]
        waypoints = [(round(lat, 6), round(lon, 6)) for lon, lat in raw]
        _route_cache[cache_key] = waypoints
        log.info("OSRM route fetched: %d waypoints for %s", len(waypoints), cache_key)
        return waypoints
    except Exception as exc:
        log.warning("OSRM fetch failed (%s) — will use linear fallback", exc)
        return []


def _sample_waypoints(
    waypoints: list[tuple[float, float]], n: int
) -> list[tuple[float, float]]:
    """Return n evenly-spaced points sampled from a waypoint list."""
    if len(waypoints) <= n:
        return waypoints
    indices = [int(round(i * (len(waypoints) - 1) / (n - 1))) for i in range(n)]
    return [waypoints[i] for i in indices]


def _animate_gps_drive(
    phone_id: str,
    acc_id: str,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    steps: int = 12,
) -> None:
    """
    Animate GPS along real London roads from origin to destination.

    Queries the OSRM public routing API for the actual road geometry, samples
    `steps` evenly-spaced points from the route polyline, and pushes a GPS
    update at each one — mimicking a genuine car journey through London streets.

    A tiny GPS jitter (±2 m) is applied at each step so the trace doesn't look
    pixel-perfect.  Falls back to linear interpolation if OSRM is unavailable.

    Timing: steps × 4–7 s  ≈  50–85 s total.
    """
    from set_phone_area import set_phone_gps

    # Try to get road-following waypoints from OSRM
    waypoints = _fetch_route_waypoints(origin_lat, origin_lon, dest_lat, dest_lon)

    if waypoints:
        sampled = _sample_waypoints(waypoints, steps + 1)
        log.info("[%s] GPS road drive: %d road waypoints → sampling %d steps",
                 acc_id, len(waypoints), len(sampled))
    else:
        # Linear fallback — straight line with jitter
        log.info("[%s] GPS drive (linear fallback): (%.4f,%.4f) → (%.4f,%.4f)",
                 acc_id, origin_lat, origin_lon, dest_lat, dest_lon)
        sampled = [
            (
                origin_lat + (dest_lat - origin_lat) * i / steps,
                origin_lon + (dest_lon - origin_lon) * i / steps,
            )
            for i in range(steps + 1)
        ]

    for i, (lat, lon) in enumerate(sampled):
        # Small GPS jitter — real phones never report perfectly exact coordinates
        lat += random.uniform(-0.00002, 0.00002)   # ±~2 m
        lon += random.uniform(-0.00003, 0.00003)
        ok = set_phone_gps(phone_id, round(lat, 6), round(lon, 6))
        if not ok:
            log.warning("[%s] GPS step %d/%d failed", acc_id, i + 1, len(sampled))
        if i < len(sampled) - 1:
            time.sleep(random.uniform(4.0, 7.0))

    log.info("[%s] GPS arrived at destination (%.4f, %.4f)", acc_id, dest_lat, dest_lon)


# ── Warm-up activities ────────────────────────────────────────────────────────

def _warmup_maps(phone_id: str, acc_id: str, log_acc_id: str = "") -> bool:
    """Open Maps, search a nearby place, open its detail card, scroll reviews/photos."""
    log.info("[%s] Maps warm-up …", acc_id)
    try:
        _open_app(phone_id, "com.google.android.apps.maps", acc_id)

        # 40% chance: tap the My Location button (bottom-right blue dot) to centre
        # the map on the device GPS.  Mirrors natural user behaviour and ensures
        # Google sees the location pin is active for this session.
        if random.random() < 0.4:
            time.sleep(random.uniform(1.5, 3.0))  # brief pause before tapping
            _shell(phone_id, "input tap 1010 1650")
            log.info("[%s] Maps: tapped My Location button", acc_id)
            time.sleep(random.uniform(1.5, 2.5))

        # Tap search bar
        tapped = _find_and_tap(phone_id, ["Search here", "Search Google Maps", "Search Maps"])
        if not tapped:
            log.warning("[%s] Maps: search bar not found", acc_id)
            _press_home(phone_id)
            return False
        time.sleep(2)

        # Type a random search
        query = random.choice(_MAP_SEARCHES)
        log.info("[%s] Maps search: %s", acc_id, query)
        if log_acc_id:
            try:
                from core.activity_log import log_event
                log_event(log_acc_id, "mobile", "maps_browse", {"search_term": query})
            except Exception:
                pass
        _type_and_search(phone_id, query)
        time.sleep(4)

        # Swipe up to reveal results bottom sheet
        _swipe_down(phone_id)
        time.sleep(2)

        # Tap the first result to open its place detail card
        tapped = _find_and_tap(phone_id, ["Directions", "Website", "Call", "Save", "Share", "Open"])
        time.sleep(3)

        if tapped:
            # Scroll through the place detail (reviews, photos, info)
            dwell = random.randint(20, 45)
            log.info("[%s] Browsing place detail for %ds", acc_id, dwell)
            scrolls = dwell // 8
            for _ in range(scrolls):
                _swipe_down(phone_id)
                time.sleep(random.uniform(2.5, 5.0))
            # Scroll back up naturally
            _shell(phone_id, "input swipe 540 400 540 1200 500")
            time.sleep(2)
        else:
            # Just dwell on the results list
            time.sleep(random.randint(15, 25))

        _press_home(phone_id)
        log.info("[%s] Maps warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Maps warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _wait_for_phone_ready(phone_id: str, acc_id: str, timeout: int = 60) -> bool:
    """
    Poll until the phone accepts shell commands (i.e. fully booted).

    Uses adaptive intervals — starts at 1 s to catch fast booters, backs off
    to 5 s once the phone has been slow for a while.  Replaces the fixed 5 s
    grid which wastes 4 s on every attempt for phones that boot in 8–12 s.
    """
    # Adaptive intervals: 1 s × 5, then 3 s × 5, then 5 s for the remainder
    intervals = [1, 1, 1, 1, 1, 3, 3, 3, 3, 3]
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        ok, _ = _shell(phone_id, "echo ready")
        if ok:
            return True
        interval = intervals[attempt] if attempt < len(intervals) else 5
        attempt += 1
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    log.warning("[%s] Phone did not become ready within %ds", acc_id, timeout)
    return False


def _warmup_maps_directions(phone_id: str, acc_id: str, account: dict | None = None,
                             log_acc_id: str = "") -> bool:
    """
    Open a ~1-min driving route in Google Maps navigation.

    Route selection priority:
      1. home/work → associated business (if account has target_businesses with lat/lng) — 50% chance
      2. Random London pair from _DIRECTIONS_PAIRS fallback
    """
    log.info("[%s] Maps directions warm-up ...", acc_id)
    try:
        if not _wait_for_phone_ready(phone_id, acc_id, timeout=60):
            log.warning("[%s] Phone not ready — skipping directions", acc_id)
            return False

        dest_coords = None
        origin_lat = origin_lon = None
        route_label = "random"
        dest_biz_name = None  # set when routing to a named business

        # Try to route from home/work to an associated business
        if account and random.random() < 0.5:
            target_ids = account.get("target_businesses") or []
            if target_ids:
                try:
                    import yaml
                    from core.paths import DATA_DIR
                    biz_file = DATA_DIR / "businesses.yaml"
                    biz_data = yaml.safe_load(biz_file.read_text(encoding="utf-8")) if biz_file.exists() else {}
                    businesses = (biz_data or {}).get("businesses", [])
                    biz_map = {b["id"]: b for b in businesses}
                    routable = [
                        b for bid in target_ids
                        if (b := biz_map.get(bid)) and b.get("lat") and b.get("lng")
                    ]
                    if routable:
                        biz = random.choice(routable)
                        dest_coords = f"{biz['lat']},{biz['lng']}"
                        dest_biz_name = biz.get("name")

                        # Origin: work (35%) or home (65%)
                        use_work = (account.get("work_lat") and account.get("work_lng")
                                    and random.random() < 0.35)
                        if use_work:
                            origin_lat = account["work_lat"] + random.uniform(-0.0003, 0.0003)
                            origin_lon = account["work_lng"] + random.uniform(-0.0005, 0.0005)
                            route_label = f"work → {biz.get('name', dest_coords)}"
                        elif account.get("home_lat") and account.get("home_lng"):
                            origin_lat = account["home_lat"] + random.uniform(-0.0003, 0.0003)
                            origin_lon = account["home_lng"] + random.uniform(-0.0005, 0.0005)
                            route_label = f"home → {biz.get('name', dest_coords)}"
                except Exception as exc:
                    log.debug("[%s] Business routing lookup failed: %s", acc_id, exc)

        # Fallback to random London pair
        if not dest_coords:
            dest_coords, origin_lat, origin_lon = random.choice(_DIRECTIONS_PAIRS)

        # Parse destination lat/lon for GPS animation
        dest_parts = dest_coords.split(",")
        dest_lat = float(dest_parts[0])
        dest_lon = float(dest_parts[1])

        if log_acc_id:
            try:
                from core.activity_log import log_event
                log_event(log_acc_id, "mobile", "maps_directions", {
                    "route": route_label,
                    "destination": dest_biz_name or dest_coords,
                    "dest_lat": dest_lat,
                    "dest_lng": dest_lon,
                })
            except Exception:
                pass

        # Set GPS to origin before opening Maps so the starting pin is accurate
        from set_phone_area import set_phone_gps
        set_phone_gps(phone_id, round(origin_lat, 6), round(origin_lon, 6))
        time.sleep(1)

        _shell(phone_id, "am force-stop com.google.android.apps.maps")
        time.sleep(2)

        nav_uri = (
            f"https://maps.google.com/maps"
            f"?saddr={origin_lat},{origin_lon}"
            f"&daddr={dest_coords}"
            f"&dirflg=d"
        )
        _shell(phone_id,
               f"am start -a android.intent.action.VIEW "
               f"-d \"{nav_uri}\" "
               f"com.google.android.apps.maps")
        _wait_for_foreground_app(phone_id, "com.google.android.apps.maps", timeout=12, acc_id=acc_id)

        # Animate GPS along the route in the background while Maps is visible
        anim_thread = threading.Thread(
            target=_animate_gps_drive,
            args=(phone_id, acc_id, origin_lat, origin_lon, dest_lat, dest_lon),
            daemon=True,
        )
        anim_thread.start()

        dwell = random.randint(45, 90)
        log.info("[%s] Viewing route (%s) for %ds ...", acc_id, route_label, dwell)
        time.sleep(dwell)

        # Wait for animation to finish (it takes ~35–55 s; usually done by now)
        anim_thread.join(timeout=90)

        # Hold GPS at destination and actively browse Maps — stacks Location History
        # visit with real app engagement at those coordinates.
        dest_dwell = random.randint(60, 90)
        log.info("[%s] GPS at destination — opening Maps to browse for %ds", acc_id, dest_dwell)
        try:
            _shell(phone_id, "am force-stop com.google.android.apps.maps")
            time.sleep(1)
            if dest_biz_name:
                # Search for the business by name — strongest signal (GPS + Maps search)
                search_query = dest_biz_name.replace(" ", "%s")
                _shell(phone_id,
                       f"am start -a android.intent.action.VIEW "
                       f"-d \"geo:{dest_lat},{dest_lon}?q={search_query}\" "
                       f"com.google.android.apps.maps")
                log.info("[%s] Maps: searching for business '%s' at destination", acc_id, dest_biz_name)
            else:
                # No named business — open Maps centred on destination coords
                _shell(phone_id,
                       f"am start -a android.intent.action.VIEW "
                       f"-d \"geo:{dest_lat},{dest_lon}?z=16\" "
                       f"com.google.android.apps.maps")
            _wait_for_foreground_app(phone_id, "com.google.android.apps.maps", timeout=10, acc_id=acc_id)
            time.sleep(3)

            # Scroll/browse for the dwell period
            elapsed = 4
            while elapsed < dest_dwell:
                _swipe_down(phone_id)
                pause = random.uniform(4.0, 8.0)
                time.sleep(pause)
                elapsed += pause + 0.5
        except Exception as e:
            log.warning("[%s] Destination Maps browse failed: %s — falling back to sleep", acc_id, e)
            time.sleep(max(0, dest_dwell - 5))

        # Return GPS to origin (home/work) before next activity
        set_phone_gps(phone_id, round(origin_lat, 6), round(origin_lon, 6))
        time.sleep(1)

        _press_home(phone_id)
        log.info("[%s] Maps directions warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Maps directions warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _warmup_youtube(phone_id: str, acc_id: str, log_acc_id: str = "") -> bool:
    """Open YouTube, search for a topic, open a video, watch with randomised dwell and interactions."""
    log.info("[%s] YouTube warm-up …", acc_id)
    try:
        _open_app(phone_id, "com.google.android.youtube", acc_id)

        # Tap search icon
        tapped = _find_and_tap(phone_id, ["Search", "Search YouTube"])
        time.sleep(2)

        if tapped:
            query = random.choice(_YOUTUBE_SEARCHES)
            log.info("[%s] YouTube search: %s", acc_id, query)
            if log_acc_id:
                try:
                    from core.activity_log import log_event
                    log_event(log_acc_id, "mobile", "youtube", {"search_term": query})
                except Exception:
                    pass
            _type_and_search(phone_id, query)
            time.sleep(4)

            # Tap the first video result (not Shorts — prefer regular videos)
            tapped_video = _find_and_tap(phone_id, ["Watch", "Play"])
            if not tapped_video:
                # Fall back to tapping anything in results
                _find_and_tap(phone_id, ["Shorts", "views", "ago"])
            time.sleep(4)
        else:
            # Already on home feed — scroll down once then tap something
            _swipe_down(phone_id)
            time.sleep(2)
            _find_and_tap(phone_id, ["Watch", "views", "ago", "Shorts"])
            time.sleep(3)

        # Watch for a randomised duration (30–90s) with mid-watch interactions
        watch_time = random.randint(30, 90)
        log.info("[%s] Watching for %ds", acc_id, watch_time)

        # Pause partway through and interact (tap progress bar, scroll comments briefly)
        first_segment = watch_time // 3
        time.sleep(first_segment)

        # Tap screen once (shows playback controls) — natural viewer behaviour
        _shell(phone_id, "input tap 540 960")
        time.sleep(random.uniform(1.5, 3.0))

        # Continue watching
        time.sleep(watch_time - first_segment)

        # Optionally scroll down to peek at comments (50% chance)
        if random.random() < 0.5:
            _swipe_down(phone_id)
            time.sleep(random.uniform(3.0, 6.0))
            # Scroll back up
            _shell(phone_id, "input swipe 540 400 540 1200 500")
            time.sleep(2)

        _press_home(phone_id)
        log.info("[%s] YouTube warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] YouTube warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _warmup_gmail(phone_id: str, acc_id: str, log_acc_id: str = "") -> bool:
    """Open Gmail, scroll inbox, open the first email, read it, then return."""
    log.info("[%s] Gmail warm-up …", acc_id)
    if log_acc_id:
        try:
            from core.activity_log import log_event
            log_event(log_acc_id, "mobile", "gmail", {"action": "scroll inbox, open email"})
        except Exception:
            pass
    try:
        _open_app(phone_id, "com.google.android.gm", acc_id)

        # Scroll down through inbox to simulate reading
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))

        # Tap the first visible email to open it
        tapped = _find_and_tap(phone_id, ["Inbox", "Primary", "Promotions", "Updates", "Social"])
        # The above tries to tap a tab which won't open an email — try tapping by position instead
        # Emails are typically at y~400-600 range in the list
        _shell(phone_id, "input tap 540 420")
        time.sleep(4)

        # Scroll through the email body
        _swipe_down(phone_id)
        time.sleep(random.uniform(3.0, 6.0))
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 4.0))

        # Go back to inbox
        _press_back(phone_id)
        time.sleep(2)

        _press_home(phone_id)
        log.info("[%s] Gmail warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Gmail warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _warmup_google_search(phone_id: str, acc_id: str, log_acc_id: str = "") -> bool:
    """Open Chrome → google.co.uk → search a query → scroll results → tap a result."""
    log.info("[%s] Google Search warm-up …", acc_id)
    try:
        # Open Chrome (wait until actually in foreground before proceeding)
        _shell(phone_id,
               "am start -a android.intent.action.VIEW "
               "-d 'https://www.google.co.uk' com.android.chrome")
        _wait_for_foreground_app(phone_id, "chrome", timeout=15, acc_id=acc_id)

        # Handle Chrome first-run welcome screen ("Welcome to Chrome" → Accept & continue)
        if _find_and_tap(phone_id, ["Accept & continue"]):
            log.info("[%s] Accepted Chrome terms of service.", acc_id)
            time.sleep(3)
            # Chrome may then offer to sign in — skip it
            _find_and_tap(phone_id, ["No thanks", "Skip", "Not now"])
            time.sleep(2)

        # Tap the search bar / address bar
        _find_and_tap(phone_id, ["Search or type URL", "Search Google or type a URL",
                                  "google.co.uk", "Address bar"])
        time.sleep(2)

        # Type search query
        query = random.choice(_GOOGLE_SEARCHES)
        log.info("[%s] Google search: %s", acc_id, query)
        if log_acc_id:
            try:
                from core.activity_log import log_event
                log_event(log_acc_id, "mobile", "google_search", {"search_term": query})
            except Exception:
                pass
        _type_and_search(phone_id, query)
        time.sleep(4)

        # Scroll results page
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))

        # Tap a result — try to find organic result links by position
        # Results typically start around y=500; tap into that region
        _shell(phone_id, "input tap 540 600")
        time.sleep(5)

        # Scroll the page briefly
        _swipe_down(phone_id)
        time.sleep(random.uniform(4.0, 8.0))

        # Go back to results
        _press_back(phone_id)
        time.sleep(3)

        _press_home(phone_id)
        log.info("[%s] Google Search warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Google Search warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


# ── Session logger ────────────────────────────────────────────────────────────

def _log_session(log_file: Path, acc_id: str, steps: list, duration_s: float,
                 ip: str) -> None:
    """Append a session record to mobile_sessions.json."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    records: list = []
    if log_file.exists():
        try:
            records = json.loads(log_file.read_text(encoding="utf-8"))
        except Exception:
            records = []
    records.append({
        "account_id":  acc_id,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "ip":          ip,
        "steps_done":  steps,
        "duration_s":  round(duration_s, 1),
    })
    log_file.write_text(json.dumps(records, indent=2), encoding="utf-8")


# ── Main session runner ───────────────────────────────────────────────────────

def run_warmup_session(account: dict, log_file: Path, progress_callback=None) -> dict:
    """
    Run a single 2–5 min warm-up session for one GeelarK account.

    Args:
        account:  dict from geelark_accounts.yaml
        log_file: Path to mobile_sessions.json

    Returns:
        dict with keys: success, steps_done, duration_s, ip, error
    """
    from core.geelark_client import GeelarKClient

    acc_id   = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id")
    result   = {"success": False, "steps_done": [], "duration_s": 0.0, "ip": "", "error": ""}

    if not phone_id:
        result["error"] = "No phone_id — provision the phone first"
        return result

    if not account.get("mobile_warming_enabled", True):
        log.info("[%s] mobile_warming_enabled=false — skipping", acc_id)
        result["error"] = "warming disabled"
        return result

    t_start = time.time()
    client  = GeelarKClient()

    # Resolve desktop account ID (acc_xxx) by matching email in accounts.yaml
    # Mobile sessions are keyed gl_xxx; activity log uses acc_xxx for dashboard display
    log_acc_id = acc_id  # fallback to gl_id
    email = account.get("email", "").lower()
    if email:
        try:
            import yaml
            from core.paths import ACCOUNTS_FILE
            if ACCOUNTS_FILE.exists():
                data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
                for a in data.get("accounts", []):
                    if a.get("email", "").lower() == email:
                        log_acc_id = a["id"]
                        break
        except Exception:
            pass

    # 0. Pre-flight health check — skip broken phones immediately
    health = client.check_phone_health(phone_id)
    if not health["healthy"]:
        result["error"] = f"Phone health check failed: {health['reason']}"
        log.warning("[%s] Skipping warmup — phone unhealthy: %s", acc_id, health["reason"])
        return result

    # 1. Rotate proxy IP (enforce 180s cooldown between rotations)
    global _last_proxy_rotation
    elapsed = time.time() - _last_proxy_rotation
    if elapsed < _PROXY_ROTATION_COOLDOWN:
        wait = _PROXY_ROTATION_COOLDOWN - elapsed
        log.info("[%s] Proxy cooldown — waiting %.0fs before rotating …", acc_id, wait)
        time.sleep(wait)
    log.info("[%s] Rotating proxy IP …", acc_id)
    ip = _rotate_proxy_ip()
    _last_proxy_rotation = time.time()
    result["ip"] = ip

    # 2. Start phone
    log.info("[%s] Starting phone …", acc_id)
    try:
        viewer_url = client.start_phone(phone_id) or ""
        result["viewer_url"] = viewer_url
        if progress_callback:
            progress_callback({"phase": "running", "viewer_url": viewer_url})
    except Exception as e:
        result["error"] = f"Failed to start phone: {e}"
        return result

    # 3. Wait for phone to fully boot (replaces hardcoded sleep)
    log.info("[%s] Waiting for phone to boot …", acc_id)
    if not _wait_for_phone_ready(phone_id, acc_id, timeout=90):
        log.warning("[%s] Phone did not boot in 90s — proceeding anyway", acc_id)

    # 4. Refresh GPS
    try:
        _refresh_gps(phone_id, account, acc_id)
        result["steps_done"].append("gps_refresh")
    except Exception as e:
        log.warning("[%s] GPS refresh failed: %s", acc_id, e)

    # 5. Warm-up activities — randomise order each session for variety
    activity_fns = [
        ("maps",             lambda: _warmup_maps(phone_id, acc_id, log_acc_id)),
        ("maps_directions",  lambda: _warmup_maps_directions(phone_id, acc_id, account, log_acc_id)),
        ("youtube",          lambda: _warmup_youtube(phone_id, acc_id, log_acc_id)),
        ("gmail",            lambda: _warmup_gmail(phone_id, acc_id, log_acc_id)),
        ("google_search",    lambda: _warmup_google_search(phone_id, acc_id, log_acc_id)),
    ]
    random.shuffle(activity_fns)

    for step_name, fn in activity_fns:
        try:
            ok = fn()
            if ok:
                result["steps_done"].append(step_name)
            else:
                log.warning("[%s] %s returned False", acc_id, step_name)
        except Exception as e:
            log.warning("[%s] %s activity raised: %s", acc_id, step_name, e)
        time.sleep(random.uniform(2.0, 4.0))

    # 7. Stop phone — retry up to 3× and verify it actually stopped
    stopped = False
    for attempt in range(1, 4):
        try:
            client.stop_phone(phone_id)
        except Exception as e:
            log.warning("[%s] stop_phone attempt %d failed: %s", acc_id, attempt, e)
        # Poll status to confirm (GeelarK sometimes returns OK but keeps phone running)
        time.sleep(5)
        try:
            statuses = client.get_phone_status([phone_id])
            st = statuses[0].get("status") if statuses else -1
            if st in (2, 3):  # 2=Stopped, 3=Stopping/Expired
                log.info("[%s] Phone stopped (attempt %d, status=%d).", acc_id, attempt, st)
                stopped = True
                break
            log.warning("[%s] Phone still running after stop attempt %d (status=%d) — retrying",
                        acc_id, attempt, st)
        except Exception as e:
            log.warning("[%s] Could not verify phone status after stop: %s", acc_id, e)
            break
    if not stopped:
        log.error("[%s] Phone %s could NOT be confirmed stopped after 3 attempts — manual check needed",
                  acc_id, phone_id)

    result["duration_s"] = round(time.time() - t_start, 1)
    result["success"]    = len(result["steps_done"]) >= 2

    # 8. Log session
    try:
        _log_session(log_file, acc_id, result["steps_done"], result["duration_s"], ip)
    except Exception as e:
        log.warning("[%s] Session log write failed: %s", acc_id, e)

    log.info("[%s] Session complete. Steps: %s  Duration: %.0fs  IP: %s",
             acc_id, result["steps_done"], result["duration_s"], ip)
    return result


def run_all_warmup_sessions(accounts: list, log_file: Path, progress_callback=None) -> list:
    """
    Run warm-up sessions for all enabled accounts, strictly sequentially.
    Never starts two phones at the same time — all share one mobile proxy.
    """
    results = []
    enabled = [a for a in accounts
               if a.get("geelark_phone_id") and a.get("mobile_warming_enabled", True)]

    if not enabled:
        log.info("No accounts with mobile_warming_enabled=true and a phone_id.")
        return results

    log.info("Starting mobile warm-up for %d account(s): %s",
             len(enabled), [a["id"] for a in enabled])

    for acc in enabled:
        r = run_warmup_session(acc, log_file, progress_callback=progress_callback)
        results.append({"account_id": acc["id"], **r})
        # Pause between phones — proxy rate limit is 180s; sessions typically run 2-5min
        # so by the time we get to the next phone we're well within the window.
        time.sleep(5)

    ok     = sum(1 for r in results if r["success"])
    failed = len(results) - ok
    log.info("Mobile warm-up complete. OK: %d  Failed/partial: %d", ok, failed)
    return results
