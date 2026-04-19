"""
set_phone_area.py — Assign a home area to each GeelarK cloud phone.

Each phone gets a named London neighbourhood (or other city area) that acts as
its "home base". GPS is set to that area (with a small random offset each session)
so all Maps activity, directions, and location history appears consistent.

Usage:
  python set_phone_area.py --list-areas              Show all available areas
  python set_phone_area.py --list                    Show current area per account
  python set_phone_area.py --set gl_001 shoreditch   Assign area to one account
  python set_phone_area.py --set-all                 Interactive: assign area to every account
  python set_phone_area.py --apply gl_001            Push GPS to the phone right now (phone must be running)
  python set_phone_area.py --apply-all               Push GPS to all running phones
"""

import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_env = Path(__file__).parent / "warmer.env"
if _env.exists():
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import yaml
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger("set_phone_area")

DATA_FILE = Path(os.environ.get("WARMER_DATA_DIR", r"C:\WarmingData")) / "geelark_accounts.yaml"

# ── London area definitions ────────────────────────────────────────────────────
# Each area: (display_name, centre_lat, centre_lon, radius_m)
# Radius is used to randomise GPS slightly each session — keeps it natural.

AREAS = {
    # ── Central ───────────────────────────────────────────────────────────────
    "soho":             ("Soho",                51.5137, -0.1337, 300),
    "covent_garden":    ("Covent Garden",        51.5117, -0.1240, 300),
    "fitzrovia":        ("Fitzrovia",            51.5194, -0.1396, 300),
    "holborn":          ("Holborn",              51.5174, -0.1200, 300),
    "city":             ("City of London",       51.5155, -0.0922, 400),
    "aldgate":          ("Aldgate",              51.5141, -0.0755, 300),
    "mayfair":          ("Mayfair",              51.5097, -0.1478, 300),
    "marylebone":       ("Marylebone",           51.5223, -0.1547, 350),
    "westminster":      ("Westminster",          51.4994, -0.1273, 350),
    "lambeth":          ("Lambeth",              51.4979, -0.1175, 300),

    # ── Inner East ────────────────────────────────────────────────────────────
    "shoreditch":       ("Shoreditch",           51.5228, -0.0782, 400),
    "bethnal_green":    ("Bethnal Green",        51.5269, -0.0545, 350),
    "hackney":          ("Hackney",              51.5450, -0.0553, 400),
    "dalston":          ("Dalston",              51.5453, -0.0753, 350),
    "canary_wharf":     ("Canary Wharf",         51.5054, -0.0235, 400),
    "stratford":        ("Stratford",            51.5416, -0.0038, 400),
    "stoke_newington":  ("Stoke Newington",      51.5609, -0.0723, 300),
    "hackney_wick":     ("Hackney Wick",         51.5475,  0.0177, 300),
    "bow":              ("Bow",                  51.5277, -0.0182, 350),
    "mile_end":         ("Mile End",             51.5251, -0.0334, 300),
    "poplar":           ("Poplar",               51.5089, -0.0163, 300),

    # ── Outer East ────────────────────────────────────────────────────────────
    "leyton":           ("Leyton",               51.5552, -0.0054, 400),
    "leytonstone":      ("Leytonstone",          51.5645,  0.0080, 350),
    "walthamstow":      ("Walthamstow",          51.5842, -0.0197, 450),
    "forest_gate":      ("Forest Gate",          51.5451,  0.0335, 350),
    "east_ham":         ("East Ham",             51.5323,  0.0503, 400),
    "ilford":           ("Ilford",               51.5593,  0.0741, 450),
    "barking":          ("Barking",              51.5396,  0.0814, 450),
    "romford":          ("Romford",              51.5754,  0.1831, 500),
    "dagenham":         ("Dagenham",             51.5440,  0.1492, 450),
    "wanstead":         ("Wanstead",             51.5719,  0.0300, 350),
    "woodford":         ("Woodford",             51.6074,  0.0226, 400),

    # ── Inner North ───────────────────────────────────────────────────────────
    "islington":        ("Islington",            51.5349, -0.1029, 350),
    "camden":           ("Camden",               51.5390, -0.1426, 400),
    "kings_cross":      ("King's Cross",         51.5309, -0.1233, 300),
    "highbury":         ("Highbury",             51.5524, -0.0988, 350),
    "archway":          ("Archway",              51.5657, -0.1356, 300),
    "finsbury_park":    ("Finsbury Park",        51.5642, -0.1066, 350),
    "crouch_end":       ("Crouch End",           51.5765, -0.1210, 300),
    "hornsey":          ("Hornsey",              51.5869, -0.1200, 350),
    "stamford_hill":    ("Stamford Hill",        51.5716, -0.0738, 300),

    # ── Outer North ───────────────────────────────────────────────────────────
    "wood_green":       ("Wood Green",           51.5968, -0.1090, 400),
    "tottenham":        ("Tottenham",            51.5882, -0.0708, 450),
    "edmonton":         ("Edmonton",             51.6174, -0.0690, 450),
    "enfield":          ("Enfield",              51.6522, -0.0808, 500),
    "muswell_hill":     ("Muswell Hill",         51.5905, -0.1432, 300),
    "east_finchley":    ("East Finchley",        51.5892, -0.1689, 350),
    "barnet":           ("Barnet",               51.6445, -0.2060, 500),
    "hendon":           ("Hendon",               51.5879, -0.2244, 400),
    "chingford":        ("Chingford",            51.6264, -0.0182, 450),
    "whetstone":        ("Whetstone",            51.6286, -0.1770, 350),

    # ── Inner South ───────────────────────────────────────────────────────────
    "brixton":          ("Brixton",              51.4613, -0.1156, 400),
    "clapham":          ("Clapham",              51.4618, -0.1388, 400),
    "peckham":          ("Peckham",              51.4736, -0.0694, 350),
    "bermondsey":       ("Bermondsey",           51.4995, -0.0745, 300),
    "vauxhall":         ("Vauxhall",             51.4855, -0.1234, 300),
    "elephant_castle":  ("Elephant & Castle",    51.4959, -0.1002, 300),
    "greenwich":        ("Greenwich",            51.4826, -0.0077, 400),
    "lewisham":         ("Lewisham",             51.4614, -0.0118, 350),
    "new_cross":        ("New Cross",            51.4771, -0.0389, 300),
    "deptford":         ("Deptford",             51.4792, -0.0228, 300),
    "balham":           ("Balham",               51.4430, -0.1520, 350),
    "streatham":        ("Streatham",            51.4278, -0.1241, 400),
    "dulwich":          ("Dulwich",              51.4449, -0.0863, 350),

    # ── Outer South ───────────────────────────────────────────────────────────
    "tooting":          ("Tooting",              51.4273, -0.1680, 400),
    "wimbledon":        ("Wimbledon",            51.4222, -0.2044, 450),
    "mitcham":          ("Mitcham",              51.4028, -0.1688, 400),
    "sutton":           ("Sutton",               51.3618, -0.1947, 500),
    "croydon":          ("Croydon",              51.3762, -0.0982, 500),
    "crystal_palace":   ("Crystal Palace",       51.4171, -0.0685, 350),
    "forest_hill":      ("Forest Hill",          51.4374, -0.0539, 300),
    "catford":          ("Catford",              51.4444, -0.0197, 350),
    "bromley":          ("Bromley",              51.4065,  0.0141, 450),
    "eltham":           ("Eltham",               51.4512,  0.0535, 400),
    "woolwich":         ("Woolwich",             51.4909,  0.0661, 400),
    "sidcup":           ("Sidcup",               51.4257,  0.1006, 400),
    "norwood":          ("Norwood",              51.4190, -0.1000, 350),

    # ── Inner West ────────────────────────────────────────────────────────────
    "notting_hill":     ("Notting Hill",         51.5085, -0.1960, 350),
    "chelsea":          ("Chelsea",              51.4875, -0.1687, 350),
    "fulham":           ("Fulham",               51.4739, -0.1994, 350),
    "hammersmith":      ("Hammersmith",          51.4927, -0.2237, 350),
    "shepherds_bush":   ("Shepherd's Bush",      51.5045, -0.2180, 350),
    "ealing":           ("Ealing",               51.5130, -0.3089, 400),
    "wandsworth":       ("Wandsworth",           51.4571, -0.1919, 350),
    "putney":           ("Putney",               51.4618, -0.2140, 350),
    "battersea":        ("Battersea",            51.4782, -0.1651, 350),
    "acton":            ("Acton",                51.5084, -0.2680, 350),
    "chiswick":         ("Chiswick",             51.4940, -0.2683, 350),
    "kilburn":          ("Kilburn",              51.5458, -0.2010, 300),

    # ── Outer West ────────────────────────────────────────────────────────────
    "brentford":        ("Brentford",            51.4878, -0.3082, 350),
    "hounslow":         ("Hounslow",             51.4677, -0.3603, 450),
    "richmond":         ("Richmond",             51.4613, -0.3037, 400),
    "twickenham":       ("Twickenham",           51.4488, -0.3319, 400),
    "kingston":         ("Kingston upon Thames", 51.4123, -0.3007, 450),
    "southall":         ("Southall",             51.5128, -0.3756, 400),
    "hayes":            ("Hayes",                51.5082, -0.4199, 400),
    "uxbridge":         ("Uxbridge",             51.5441, -0.4773, 500),

    # ── Outer North-West ──────────────────────────────────────────────────────
    "wembley":          ("Wembley",              51.5528, -0.2959, 450),
    "harrow":           ("Harrow",               51.5796, -0.3336, 450),
    "ruislip":          ("Ruislip",              51.5714, -0.4289, 400),
    "willesden":        ("Willesden",            51.5467, -0.2290, 350),
    "cricklewood":      ("Cricklewood",          51.5584, -0.2211, 300),

    # ── Additional areas ──────────────────────────────────────────────────────
    "southwark":        ("Southwark",            51.5033, -0.0855, 350),
    "camberwell":       ("Camberwell",           51.4700, -0.0900, 350),
    "canning_town":     ("Canning Town",         51.5137,  0.0083, 350),
    "edgware":          ("Edgware",              51.6142, -0.2756, 400),
    "epsom":            ("Epsom",                51.3340, -0.2685, 500),
    "rainham":          ("Rainham",              51.5192,  0.1934, 450),
}


def resolve_area_key(geo_area: str) -> str | None:
    """Resolve a geo_area value to a valid AREAS key.

    Handles three formats accounts may store:
    - Exact key:          "bermondsey"           → "bermondsey"
    - Capitalised/spaces: "Crystal Palace"       → "crystal_palace"
    - Display name:       "Elephant and Castle"  → "elephant_castle"
    Returns None if no match found.
    """
    if not geo_area:
        return None
    if geo_area in AREAS:
        return geo_area
    normalised = geo_area.lower().replace(" ", "_").replace("&", "and").replace("-", "_")
    if normalised in AREAS:
        return normalised
    # Also try stripping "and" back to "&" variant
    normalised2 = geo_area.lower().replace(" and ", "_").replace("&", "").replace(" ", "_").replace("__", "_").strip("_")
    if normalised2 in AREAS:
        return normalised2
    geo_lower = geo_area.lower()
    # Normalise both sides for display name comparison: treat "&" and "and" as equivalent
    def _norm(s: str) -> str:
        return s.lower().replace(" & ", " and ").replace("&", "and").strip()
    geo_norm = _norm(geo_area)
    for key, (display, *_) in AREAS.items():
        if _norm(display) == geo_norm:
            return key
    return None


def area_coords(area_key: str, jitter: bool = True) -> tuple[float, float]:
    """
    Return (lat, lon) for the given area.
    If jitter=True, adds a small random offset within the area radius.
    """
    if area_key not in AREAS:
        raise ValueError(f"Unknown area: {area_key!r}. Run --list-areas to see options.")
    _, lat, lon, radius_m = AREAS[area_key]
    if jitter:
        # ~1 degree latitude ≈ 111,000 m; ~1 degree longitude ≈ 71,500 m at 51°N
        lat += random.uniform(-radius_m / 111000, radius_m / 111000)
        lon += random.uniform(-radius_m / 71500,  radius_m / 71500)
    return round(lat, 6), round(lon, 6)


def set_phone_gps(phone_id: str, lat: float, lon: float) -> bool:
    """Push GPS coordinates to a running GeelarK phone via the API."""
    from core.geelark_client import _post
    try:
        r = _post("/open/v1/phone/gps/set", {"list": [{"id": phone_id, "lat": lat, "lng": lon}]})
        return r.get("successAmount", 0) > 0
    except Exception as e:
        log.warning("GPS set failed for %s: %s", phone_id, e)
        return False


# ── Account helpers ────────────────────────────────────────────────────────────

def _load() -> tuple[Path, list]:
    data = yaml.safe_load(DATA_FILE.read_text(encoding="utf-8")) or {}
    return DATA_FILE, data.get("accounts", [])


def _save(accounts: list) -> None:
    DATA_FILE.write_text(
        yaml.dump({"accounts": accounts}, default_flow_style=False,
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_list_areas() -> None:
    print(f"\n{'Key':<20}  {'Display name':<22}  {'Lat':>9}  {'Lon':>10}  Radius")
    print("-" * 75)
    for key, (name, lat, lon, radius) in sorted(AREAS.items()):
        print(f"{key:<20}  {name:<22}  {lat:>9.4f}  {lon:>10.4f}  {radius}m")
    print()


def cmd_list() -> None:
    _, accounts = _load()
    print(f"\n{'ID':<10}  {'Email':<36}  {'Area key':<20}  Display name")
    print("-" * 85)
    for a in accounts:
        area_key = a.get("geo_area", "—")
        display  = AREAS[area_key][0] if area_key in AREAS else "not set"
        print(f"{a.get('id',''):<10}  {a.get('email',''):<36}  {area_key:<20}  {display}")
    print()


def cmd_set(account_id: str, area_key: str) -> None:
    area_key = area_key.lower().replace(" ", "_").replace("-", "_")
    if area_key not in AREAS:
        print(f"ERROR: Unknown area {area_key!r}")
        print("Run --list-areas to see all options.")
        sys.exit(1)

    path, accounts = _load()
    matched = False
    for a in accounts:
        if a["id"] == account_id:
            a["geo_area"] = area_key
            matched = True
            break

    if not matched:
        print(f"ERROR: Account {account_id!r} not found.")
        sys.exit(1)

    _save(accounts)
    name = AREAS[area_key][0]
    print(f"Set {account_id} -> {area_key} ({name})")


def cmd_set_all() -> None:
    """Interactive: prompt the user to assign an area to every account."""
    _, accounts = _load()
    cmd_list_areas()

    updated = []
    for a in accounts:
        current = a.get("geo_area", "not set")
        print(f"\n{a['id']} ({a['email']})  — current area: {current}")
        while True:
            choice = input("  Enter area key (or Enter to keep current): ").strip().lower()
            if not choice:
                break
            choice = choice.replace(" ", "_").replace("-", "_")
            if choice in AREAS:
                a["geo_area"] = choice
                print(f"  -> Set to {AREAS[choice][0]}")
                updated.append(a["id"])
                break
            else:
                print(f"  Unknown area. Run --list-areas to see options.")

    if updated:
        path, accounts2 = _load()
        # Merge updates back
        area_map = {a["id"]: a.get("geo_area") for a in accounts}
        for a in accounts2:
            if a["id"] in area_map and area_map[a["id"]]:
                a["geo_area"] = area_map[a["id"]]
        _save(accounts2)
        print(f"\nSaved areas for: {updated}")
    else:
        print("\nNo changes made.")


def cmd_apply(account_id: str) -> None:
    """Push the account's geo_area GPS to the phone right now (phone must be running)."""
    _, accounts = _load()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        print(f"ERROR: Account {account_id!r} not found.")
        sys.exit(1)

    area_key = acc.get("geo_area")
    if not area_key:
        print(f"ERROR: No geo_area set for {account_id}. Run --set first.")
        sys.exit(1)

    phone_id = acc.get("geelark_phone_id")
    if not phone_id:
        print(f"ERROR: No phone_id for {account_id}.")
        sys.exit(1)

    lat, lon = area_coords(area_key, jitter=True)
    name = AREAS[area_key][0]
    print(f"Setting GPS for {account_id} -> {name} ({lat}, {lon}) ...")
    ok = set_phone_gps(phone_id, lat, lon)
    if ok:
        print(f"GPS set successfully.")
    else:
        print(f"GPS set failed — is the phone running?")


def cmd_apply_all() -> None:
    _, accounts = _load()
    for a in accounts:
        area_key = a.get("geo_area")
        phone_id = a.get("geelark_phone_id")
        if not area_key or not phone_id:
            log.info("[%s] Skipping — no geo_area or phone_id", a["id"])
            continue
        lat, lon = area_coords(area_key, jitter=True)
        name = AREAS[area_key][0]
        ok = set_phone_gps(phone_id, lat, lon)
        status = "OK" if ok else "FAILED"
        print(f"  {a['id']} ({name}) -> {lat}, {lon}  [{status}]")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Assign home areas to GeelarK phones")
    parser.add_argument("--list-areas", action="store_true", help="Show all available areas")
    parser.add_argument("--list",       action="store_true", help="Show current area per account")
    parser.add_argument("--set",        nargs=2, metavar=("ACCOUNT_ID", "AREA"),
                        help="Assign area to one account (e.g. --set gl_001 shoreditch)")
    parser.add_argument("--set-all",    action="store_true", help="Interactively assign areas to all accounts")
    parser.add_argument("--apply",      metavar="ACCOUNT_ID", help="Push GPS to a running phone now")
    parser.add_argument("--apply-all",  action="store_true", help="Push GPS to all running phones")
    args = parser.parse_args()

    if args.list_areas:
        cmd_list_areas()
    elif args.list:
        cmd_list()
    elif args.set:
        cmd_set(args.set[0], args.set[1])
    elif args.set_all:
        cmd_set_all()
    elif args.apply:
        cmd_apply(args.apply)
    elif args.apply_all:
        cmd_apply_all()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
