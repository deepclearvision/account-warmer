# GeelarK GPS Spoofing — Correct Method Guide

**Date**: 2026-05-08 (updated)
**Status**: RESOLVED — GPS spoofing works when done correctly
**Scope**: How to successfully inject mock locations on GeelarK cloud phones

---

## Previous Conclusion (WRONG)

Our earlier testing concluded GPS spoofing does NOT work on GeelarK cloud phones. This was **incorrect**.

See `docs/GPS_SPOOFING_POST_MORTEM.md` for the full analysis of why our tests failed.

---

## Verified Working Method

A user manually tested and confirmed this works:

1. **Install fake GPS app** (Lexa Fake GPS or similar)
2. **Enable Developer Options**: Settings → About phone → tap **Build Number 7 times**
3. **Select mock location app**: Developer Options → "Select mock location app" → choose the fake GPS app
4. **Open fake GPS app** → enter coordinates → start mocking
5. **Open Google Maps** → blue dot appears at the spoofed location

---

## Why Our Automated Tests Failed

| Failure | What we did wrong | Why it failed |
|---|---|---|
| App not running | Sent ADB broadcast without opening app first | `MockLocationReceiver` is dynamically registered — needs app process alive |
| Wrong system tested | Used `cmd location` and checked `dumpsys location` for GPS HAL | Cloud phones lack GPS hardware; `GnssLocationProvider` always crashes |
| Developer Options | Never verified Developer Options was enabled via UI | `settings put` alone may not trigger all internal flags |
| Encoding bug | Used `pm list packages \| grep` for install check | Output had `????` characters causing false negatives |
| Wrong verification | Looked for `last mock location` in `dumpsys` | That metric tracks hardware GPS, not mock framework |

**Key distinction**: Cloud phones lack GPS HAL → `GnssLocationProvider` fails. BUT the **Mock Location Framework** is pure software and works fine.

---

## Correct Automated Flow

```
1. Install APK (via GeelarK upload_and_install_apk)
2. Enable Developer Options via UI automation:
   a. Open Settings → About phone
   b. Find and tap Build Number 7 times
   c. Verify "You are now a developer" toast
3. Set mock location app:
   a. settings put secure mock_location_app <package>
   b. appops set <package> android:mock_location allow
4. LAUNCH the app (critical step we missed):
   a. am start -n <package>/<activity>
   b. Wait for app to fully load
5. Set coordinates:
   a. Method A: am broadcast (only works if app is running)
   b. Method B: UI automation within the app
6. Open Google Maps → tap My Location → verify blue dot
7. Take screenshot for proof
```

---

## Automated Test Script

`test_gps_comprehensive.py` implements the complete phased test:

- **Phase 1**: Install fake GPS app (with fallback)
- **Phase 2**: Enable Developer Options via UI automation
- **Phase 3**: Set mock location app + grant permissions
- **Phase 4**: Launch app and set coordinates (broadcast + UI fallback)
- **Phase 5**: Verify in Google Maps with screenshots
- **Phase 6**: Test driving/animated GPS (optional)

Run with:
```bash
python test_gps_comprehensive.py <phone_id> [account_id]
```

---

## Driving / Animated GPS

For simulating movement (directions, road following):

- **Method**: Send mock location broadcasts repeatedly with interpolated waypoints
- **Timing**: 5–10 seconds between updates simulates realistic urban driving
- **Route generation**: Use OSRM API to get road-following waypoints between two points
- **Maps behavior**: Google Maps will show a moving blue dot along the route
- **Trust signal**: Location History records the movement path, excellent for GMB trust

Example waypoint generation (simple linear interpolation):
```python
for i in range(steps + 1):
    t = i / steps
    lat = start_lat + (end_lat - start_lat) * t
    lon = start_lon + (end_lon - start_lon) * t
    set_gps(phone_id, lat, lon)
    time.sleep(random.uniform(5, 10))
```

For production, replace linear interpolation with OSRM road-following routes.

---

## External Research — Still Relevant

The external research in the original doc remains valid **for the GnssLocationProvider / hardware GPS path**, but does NOT apply to the mock location framework:

- **Appium Issue #21613**: Appium's `driver.setLocation()` uses the hardware GPS path — fails on cloud phones
- **Flutter Mock Location**: Same issue — uses hardware GPS, not mock framework
- **Waydroid #226**: Container Android lacks GPS HAL — hardware path fails

**The mock location framework is a separate, software-only system that works on cloud phones.**

---

## Related Code

- `test_gps_comprehensive.py` — Full phased test script (NEW)
- `docs/GPS_SPOOFING_POST_MORTEM.md` — Detailed failure analysis (NEW)
- `core/gps_spoofing.py` — Production GPS module (NEEDS UPDATE)
- `core/geelark_client.py` — Phone creation API
- `api/routers/mobile.py` — Dashboard provisioning endpoint

---

## Fleet Audit

All 44 phones (Android 10 and 13) should support mock location spoofing when the correct method is used. No need to recreate phones for GPS compatibility.
