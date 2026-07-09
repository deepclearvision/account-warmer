# Run History Logging Schema

## Storage

- Primary: `logs/run_history.jsonl` — append-only JSON Lines.
- Per-run directories: `geelark_orchestrator/scripts/live_test_<account>_<timestamp>/` — screenshots + UI dumps.

## JSONL Record Schema

Every execution appends one line with the following fields:

```json
{
  "timestamp": "2026-06-07T17:08:51.329586",
  "script_name": "gps_maps_live_test_v3.py",
  "run_id": "20260607_170606",
  "phone_id": "614216903581237315",
  "account_email": "oakleighoneal9987@gmail.com",
  "business_id": "biz_001",
  "attempt": 1,
  "started_ok": true,
  "overall_status": "GPS_MAPS_LIVE_TEST",
  "keyword_type": "money",
  "keyword_used": "emergency plumber Southwark",
  "provisioning_gates": {
    "gate_1_phone_running": true,
    "gate_2_developer_options": true,
    "gate_3_mock_app_set": true,
    "gate_4_overlay_service": true,
    "gate_5_google_account": true,
    "gate_6_proxy_routing": true,
    "gate_7_ip_freshness": true
  },
  "gps_test": {
    "target_lat": 51.501512,
    "target_lng": -0.071167,
    "mock_success": true,
    "dumpsys_mock_ok": true,
    "dumpsys_mock_info": "51.501509,-0.071168"
  },
  "maps_test": {
    "search_term": "emergency plumber Southwark",
    "maps_search_result": "FOUND",
    "business_found_text": "Southwark Plumbers",
    "screenshot_path": "...",
    "ui_dump_path": "..."
  },
  "interactions": {
    "scrolls": 0,
    "taps": 0,
    "detail_page": false,
    "share_sheet": false
  },
  "errors": [],
  "live_view_url": "https://console.geelark.com/...",
  "duration_seconds": 120
}
```

## Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO-8601 | Run completion time |
| `script_name` | string | Script that produced the record |
| `run_id` | string | Human-readable run identifier (timestamp) |
| `phone_id` | string | GeelarK phone ID |
| `account_email` | string | Google account email |
| `business_id` | string | Business identifier |
| `attempt` | int | Retry attempt number |
| `started_ok` | bool | Phone started successfully |
| `overall_status` | string | High-level outcome label |
| `keyword_type` | string | `branded` or `money` |
| `keyword_used` | string | Exact search term sent to Maps |
| `provisioning_gates` | object | Boolean results for all 7 pre-run checks |
| `gps_test` | object | GPS mock target, actual, and success flags |
| `maps_test` | object | Search result, found text, evidence paths |
| `interactions` | object | Interaction heuristics (scrolls, taps, detail page) |
| `errors` | string[] | Any error messages encountered |
| `live_view_url` | string | GeelarK live-view URL returned by `start_phone` |
| `duration_seconds` | int | Total run wall-clock time |
