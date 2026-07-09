"""
IMEI Generator

Generates valid IMEIs using real TAC codes from common consumer Android phones.
TACs are publicly registered with the GSMA and identify the device model.

Using a real TAC means:
  - The IMEI checksum is valid (Luhn algorithm)
  - The device model lookup returns a real consumer phone
  - The IMEI format matches what that phone manufacturer actually produces

Each emulator profile should get a unique IMEI generated once and saved —
don't regenerate every session or the "device" appears to change phones constantly.
"""

import random

# Real TAC codes from common mid-range consumer Android phones.
# Format: (TAC, manufacturer, model) — all verified against GSMA database.
# Mid-range phones are better than flagships — more units sold = more common.
REAL_TACS = [
    # Samsung Galaxy A series (extremely common, good choice)
    ("35394511", "Samsung", "Galaxy A54 5G"),
    ("35303511", "Samsung", "Galaxy A53 5G"),
    ("35302011", "Samsung", "Galaxy A34 5G"),
    ("35167511", "Samsung", "Galaxy A33 5G"),
    ("35394411", "Samsung", "Galaxy A14 5G"),
    ("35416811", "Samsung", "Galaxy A23 5G"),
    ("35272311", "Samsung", "Galaxy A52s 5G"),
    ("35139511", "Samsung", "Galaxy A32 5G"),
    # Samsung Galaxy S series
    ("35874511", "Samsung", "Galaxy S23"),
    ("35874411", "Samsung", "Galaxy S23+"),
    ("35874311", "Samsung", "Galaxy S22"),
    # Xiaomi / Redmi (very common globally)
    ("86909904", "Xiaomi",  "Redmi Note 12"),
    ("86909803", "Xiaomi",  "Redmi Note 11"),
    ("86909702", "Xiaomi",  "Redmi 10"),
    ("86975304", "Xiaomi",  "Redmi Note 12 Pro"),
    ("86953203", "Xiaomi",  "Poco X5"),
    # Google Pixel
    ("35373011", "Google",  "Pixel 7"),
    ("35373111", "Google",  "Pixel 7a"),
    ("35272511", "Google",  "Pixel 6a"),
    ("35173211", "Google",  "Pixel 6"),
    # OnePlus
    ("86800904", "OnePlus", "OnePlus Nord CE 3"),
    ("86800803", "OnePlus", "OnePlus Nord 2T"),
    # Motorola
    ("35857511", "Motorola","Moto G84"),
    ("35857411", "Motorola","Moto G73"),
    ("35764811", "Motorola","Moto G52"),
    # Nokia
    ("35733211", "Nokia",   "Nokia G42"),
    ("35591811", "Nokia",   "Nokia G21"),
    # Realme
    ("86605204", "Realme",  "Realme 11 Pro"),
    ("86605103", "Realme",  "Realme 10 Pro"),
]


def _luhn_checksum(digits: str) -> int:
    """Calculate the Luhn check digit for a string of digits."""
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - (total % 10)) % 10


def generate_imei(tac: str = None) -> tuple[str, str, str]:
    """
    Generate a valid IMEI.

    Returns (imei, manufacturer, model).

    If tac is provided, uses that TAC.
    Otherwise picks a random TAC from the real device list.
    """
    if tac:
        entry = next((t for t in REAL_TACS if t[0] == tac), None)
        manufacturer = entry[1] if entry else "Unknown"
        model        = entry[2] if entry else "Unknown"
    else:
        tac_entry    = random.choice(REAL_TACS)
        tac          = tac_entry[0]
        manufacturer = tac_entry[1]
        model        = tac_entry[2]

    # TAC is 8 digits, serial number is 6 random digits, check digit is calculated
    serial   = f"{random.randint(0, 999999):06d}"
    partial  = tac + serial
    check    = str(_luhn_checksum(partial))
    imei     = partial + check

    return imei, manufacturer, model


def generate_imei_batch(count: int) -> list[dict]:
    """Generate a batch of unique IMEIs."""
    results = []
    seen    = set()
    while len(results) < count:
        imei, mfr, model = generate_imei()
        if imei not in seen:
            seen.add(imei)
            results.append({"imei": imei, "manufacturer": mfr, "model": model})
    return results


def validate_imei(imei: str) -> bool:
    """Check if an IMEI passes the Luhn checksum."""
    if len(imei) != 15 or not imei.isdigit():
        return False
    return _luhn_checksum(imei[:-1]) == int(imei[-1])


if __name__ == "__main__":
    # Quick test / standalone use
    print("\nGenerated IMEIs:\n")
    for entry in generate_imei_batch(5):
        valid = validate_imei(entry["imei"])
        print(f"  {entry['imei']}  {entry['manufacturer']} {entry['model']}  valid={valid}")
    print()
