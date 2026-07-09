# Account-Business Mapping Schema

## Source Files

- `data/accounts_business_mapping.csv` — canonical flat-file master mapping.
- `data/accounts_business_mapping.json` — derived JSON for programmatic access.

## Columns

| Column | Type | Description |
|--------|------|-------------|
| `account_id` | string | Internal account identifier (e.g. `acc_048`) |
| `account_email` | string | Google account email |
| `geelark_phone_id` | string | GeelarK cloud phone ID |
| `multilogin_profile_id` | string | Multilogin desktop browser profile UUID |
| `business_id` | string | Internal business identifier |
| `business_name` | string | Exact registered business name on Google Maps |
| `business_address` | string | Full street address |
| `business_location` | string | Human-readable area (e.g. `Southwark, London`) |
| `business_area` | string | Normalised area tag (e.g. `south_east_london`) |
| `business_lat` | float | Business latitude |
| `business_lng` | float | Business longitude |
| `business_goal` | string | Campaign goal: `review` or `edit` |
| `business_share_link` | string | Short Google Maps share URL |
| `home_lat` | float | Home location latitude |
| `home_lng` | float | Home location longitude |
| `home_address` | string | Home address description |
| `work_lat` | float | Work location latitude |
| `work_lng` | float | Work location longitude |
| `work_address` | string | Work address description |
| `nearby_points` | pipe-separated list | ~20 nearby lat,lng points for warm-up GPS |
| `onsite_points` | pipe-separated list | ~10 very close lat,lng points for on-site simulation |
| **branded_keywords** | pipe-separated list | 20 variations of the exact business name + address modifiers |
| **money_keywords** | pipe-separated list | 20 generic trade + area search queries |

### Keyword columns (new)

- `branded_keywords`: generated from `business_name`, `business_address`, `business_location`, `business_area`. Includes natural variations like "reviews", "phone number", "near me", "London", postcode.
- `money_keywords`: generated from trade (`plumber`/`plumbing`) + location modifiers. Includes emergency, 24-hour, repair, and area-specific queries.

Both are stored pipe-separated in CSV and as arrays in JSON.
