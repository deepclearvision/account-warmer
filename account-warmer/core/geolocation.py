"""
Geolocation Helper

Provides coordinates for browser geolocation spoofing via Playwright.

Two use cases:

  1. Business proximity  — when running business signal or Maps review
     activities, spoof the browser location to be near the business.
     Coordinates come from the business entry in businesses.yaml.

  2. Account home area  — for all other sessions, spoof location to a
     random point within ~2km of the account's configured city centre.
     This means every Maps interaction shows the account as being in
     the right city even without a specific business target.

Google Maps requests the browser's geolocation when you open it.
By granting it with realistic coordinates we create a direct location
signal tied to the Maps session — much stronger than just IP alone.

The offset randomisation is important:
  - Don't spoof exactly ON the business (too precise, looks fake)
  - Don't spoof more than ~500m away (weakens the signal)
  - Vary it slightly each session (real people aren't stationary)
"""

import random
import math
from typing import Optional


# Approximate city centre coordinates for common locations.
# The script picks a random point within ~2km of these for general sessions.
def list_cities() -> list[dict]:
    """Return sorted list of cities for the dashboard picker."""
    return sorted(
        [{"key": k, "label": k.replace("new york", "New York").replace("los angeles", "Los Angeles").title(),
          "lat": v[0], "lng": v[1]}
         for k, v in CITY_CENTRES.items()],
        key=lambda x: x["label"]
    )


CITY_CENTRES = {
    "london":       (51.5074, -0.1278),
    "manchester":   (53.4808, -2.2426),
    "birmingham":   (52.4862, -1.8904),
    "liverpool":    (53.4084, -2.9916),
    "leeds":        (53.8008, -1.5491),
    "sheffield":    (53.3811, -1.4701),
    "bristol":      (51.4545, -2.5879),
    "edinburgh":    (55.9533, -3.1883),
    "glasgow":      (55.8642, -4.2518),
    "cardiff":      (51.4816, -3.1791),
    "new york":     (40.7128, -74.0060),
    "los angeles":  (34.0522, -118.2437),
    "chicago":      (41.8781, -87.6298),
    "houston":      (29.7604, -95.3698),
    "toronto":      (43.6532, -79.3832),
    "sydney":       (-33.8688, 151.2093),
    "melbourne":    (-37.8136, 144.9631),
    "paris":        (48.8566, 2.3522),
    "berlin":       (52.5200, 13.4050),
    "amsterdam":    (52.3676, 4.9041),
    "dubai":        (25.2048, 55.2708),
    "singapore":    (1.3521, 103.8198),
    "tokyo":        (35.6762, 139.6503),
}


def _offset_coords(lat: float, lng: float,
                   min_metres: int = 50,
                   max_metres: int = 400) -> tuple[float, float]:
    """
    Return coordinates offset by a random amount between min and max metres.
    Uses a uniform random bearing and distance to ensure the offset isn't
    directionally biased.
    """
    distance = random.uniform(min_metres, max_metres)
    bearing  = random.uniform(0, 360)

    # Earth radius in metres
    R = 6_371_000

    lat_r = math.radians(lat)
    lng_r = math.radians(lng)
    d_r   = distance / R
    b_r   = math.radians(bearing)

    new_lat_r = math.asin(
        math.sin(lat_r) * math.cos(d_r) +
        math.cos(lat_r) * math.sin(d_r) * math.cos(b_r)
    )
    new_lng_r = lng_r + math.atan2(
        math.sin(b_r) * math.sin(d_r) * math.cos(lat_r),
        math.cos(d_r) - math.sin(lat_r) * math.sin(new_lat_r)
    )

    return round(math.degrees(new_lat_r), 6), round(math.degrees(new_lng_r), 6)


def coords_for_business(business: dict,
                        min_metres: int = 30,
                        max_metres: int = 300) -> Optional[tuple[float, float]]:
    """
    Return spoofed coordinates near the business.
    Returns None if the business has no saved coordinates.
    """
    lat = business.get("lat")
    lng = business.get("lng")
    if not lat or not lng:
        return None
    return _offset_coords(float(lat), float(lng), min_metres, max_metres)


def coords_for_account(account: dict,
                       min_metres: int = 200,
                       max_metres: int = 2000) -> Optional[tuple[float, float]]:
    """
    Return spoofed coordinates near the account's configured city.
    Used for general browsing sessions — puts the account in the right city
    without tying it to a specific business.
    """
    location = account.get("location", "").lower()
    for city_key, centre in CITY_CENTRES.items():
        if city_key in location:
            return _offset_coords(centre[0], centre[1], min_metres, max_metres)
    return None


def coords_accuracy() -> float:
    """
    Return a realistic GPS accuracy value in metres.
    Real devices report between 5m (good GPS) and 65m (urban/indoor).
    """
    return random.uniform(8, 55)
