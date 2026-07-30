#!/usr/bin/env python3
"""Generate branded and money keywords v2 — no compass directions, 'near me' focus."""
import csv
import json
import random
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_data_dir = _repo_root / "data"
_docs_dir = _repo_root / "account-warmer" / "docs"
_docs_dir.mkdir(parents=True, exist_ok=True)

CSV_PATH = _data_dir / "accounts_business_mapping.csv"
JSON_PATH = _data_dir / "accounts_business_mapping.json"


def generate_branded_keywords(row: dict) -> list[str]:
    """20 variations of exact business name."""
    name = row["business_name"].strip()
    addr = row["business_address"].strip()
    parts = addr.split(",")
    street = parts[0].strip() if parts else addr
    postcode = parts[-1].strip() if len(parts) > 1 else ""

    templates = [
        name,
        f"{name} near me",
        f"{name} reviews",
        f"{name} phone number",
        f"{name} contact",
        f"{name} opening hours",
        f"{name} emergency",
        f"{name} 24 hour",
        f"{name} plumber",
        f"{name} plumbing",
        f"{name} services",
        f"{name} {street}",
        f"{name} {postcode}",
        f"{name} Google Maps",
        f"{name} directions",
        f"{name} website",
        f"{name} call",
        f"{name} cheap",
        f"{name} best",
        f"{name} local",
    ]
    seen = set()
    out = []
    for t in templates:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    while len(out) < 20:
        suffix = random.choice(["reviews", "near me", "phone", "emergency", "24hr", "plumber", "plumbing", "local", "cheap", "best"])
        candidate = f"{name} {suffix}"
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out[:20]


def generate_money_keywords(row: dict) -> list[str]:
    """20 generic 'near me' queries — no compass directions, no town names."""
    templates = [
        "emergency plumber near me",
        "best plumber near me",
        "24 hour plumber near me",
        "cheap plumber near me",
        "plumber near me",
        "emergency plumbing near me",
        "local plumber near me",
        "plumbing services near me",
        "gas plumber near me",
        "boiler repair near me",
        "drain unblocking near me",
        "heating engineer near me",
        "reliable plumber near me",
        "same day plumber near me",
        "plumbing company near me",
        "burst pipe repair near me",
        "central heating repair near me",
        "toilet repair near me",
        "affordable plumber near me",
        "top rated plumber near me",
    ]
    seen = set()
    out = []
    for t in templates:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    # Fill if any duplicates were removed
    extras = [
        "plumber call out near me",
        "after hours plumber near me",
        "plumbing maintenance near me",
        "commercial plumber near me",
        "residential plumbing near me",
        "plumber reviews near me",
        "emergency heating repair near me",
        "blocked drain plumber near me",
        "leak repair near me",
        "shower repair near me",
    ]
    for e in extras:
        e = e.strip()
        if e not in seen and len(out) < 20:
            seen.add(e)
            out.append(e)
    return out[:20]


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        row["branded_keywords"] = "|".join(generate_branded_keywords(row))
        row["money_keywords"] = "|".join(generate_money_keywords(row))

    fieldnames = list(rows[0].keys())
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

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
            "business_area": row["business_area"],
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
            "nearby_points": [p.strip() for p in row["nearby_points"].split("|") if p.strip()] if row.get("nearby_points") else [],
            "onsite_points": [p.strip() for p in row["onsite_points"].split("|") if p.strip()] if row.get("onsite_points") else [],
            "branded_keywords": row["branded_keywords"].split("|"),
            "money_keywords": row["money_keywords"].split("|"),
        }

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    print(f"Updated CSV: {CSV_PATH}")
    print(f"Updated JSON: {JSON_PATH}")
    print(f"Businesses processed: {len(rows)}")


if __name__ == "__main__":
    main()
