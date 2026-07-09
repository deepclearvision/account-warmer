# GPS Spoofing Post-Mortem — Why Our Previous Tests Failed

**Date**: 2026-05-08
**Status**: RESOLVED — GPS spoofing works on GeelarK when done correctly

---

## What the User Discovered (Manual Test)

When done manually through the phone UI:
1. Download fake GPS app
2. Go to Settings → About phone → tap **Build Number 7 times** → Developer Options enabled
3. Go to Developer Options → **Select mock location app** → choose fake GPS app
4. Open fake GPS app → enter coordinates → start
5. Open Google Maps → **blue dot appears at the spoofed location**

This proves **GPS mock location injection DOES work** on GeelarK cloud phones.

---

## Why Our Automated Tests Failed

### Failure 1: App Process Was Not Running

Our test script (`test_gps_android10.py`) sent ADB broadcasts like:
```
am broadcast -a com.lexa.fakegps.SET_LOCATION --ef lat 51.5007 --ef lng -0.1246
```

**The problem**: `MockLocationReceiver` is a **dynamically registered** broadcast receiver (not declared in `AndroidManifest.xml`). It only exists when the app process is alive.

Our script:
- Installed the app (or checked it was installed)
- Immediately sent the broadcast
- **Never opened the app UI**

**Result**: Broadcast was dispatched (`result=0`) but no receiver was listening. The app process was dead.

**Fix**: Must launch the app activity first, wait for it to initialize, THEN send broadcasts or interact with its UI.

---

### Failure 2: Conflated Two Different GPS Systems

We saw `dumpsys location` showing `GnssLocationProvider: native_start failed` and concluded "GPS is broken on cloud phones."

**The truth**: There are TWO separate location systems on Android:

| System | Hardware Required? | What we saw | Mock locations use it? |
|---|---|---|---|
| **GnssLocationProvider** | Yes — GPS HAL | `native_start failed` on cloud phones | NO |
| **Mock Location Framework** | No — software only | Works when app is properly registered | YES |

Mock locations are injected through `LocationManager.addTestProvider()` → `LocationManager.setTestProviderLocation()`. This is a **pure software path** that does NOT require a GPS HAL.

Our mistake: We tested `cmd location` (which talks to GnssLocationProvider) and when it failed, we assumed ALL location injection was broken.

**Fix**: Use mock location apps (Lexa, Fake GPS) which use the software framework, not `cmd location` which requires hardware.

---

### Failure 3: Developer Options Not Properly Enabled

Our script tried to set:
```
settings put secure mock_location_app com.lexa.fakegps
```

While this command succeeds, on some Android versions the mock location framework also checks:
- `settings get global development_settings_enabled` must be `1`
- The app must have been explicitly approved through the Developer Options UI at least once

Our script never verified Developer Options was actually enabled.

**Fix**: Must enable Developer Options first (tap Build Number 7 times), then verify `development_settings_enabled=1`.

---

### Failure 4: Used `pm list packages | grep` (Encoding Bug)

Our install check:
```
pm list packages | grep com.lexa.fakegps
```

Output contained `????` characters, making us think the app wasn't installed when it actually was. This led us to skip configure steps or re-install unnecessarily.

**Fix**: Use `pm path com.lexa.fakegps` which is ASCII-safe.

---

### Failure 5: Lexa App Install Failed on Android 10

GeelarK's `upload_and_install_apk` API failed with "grpc retry limit exceeded" on some phones. We didn't retry or use alternative install methods.

**Fix**: Use `pm install` via ADB push if GeelarK upload fails.

---

### Failure 6: No Verification of Mock Provider Registration

After setting mock location app, we never verified:
1. The app actually registered as a mock provider
2. `dumpsys location` shows a `MockLocationProvider` or test provider
3. The location actually changed from `0.000000,0.000000`

We only checked `dumpsys location | grep 'last mock location'` which is the wrong metric on Android 10+ (it checks GnssLocationProvider, not mock framework).

**Fix**: Verify with `dumpsys location | grep -i 'fakegps\|mock\|test'` AND open Maps to visually confirm.

---

## Correct Flow (What We Should Have Done)

```
1. Install APK (via GeelarK upload OR adb push + pm install)
2. Enable Developer Options
   a. Open Settings → About phone
   b. Find Build Number
   c. Tap 7 times
   d. Verify "You are now a developer!" toast
3. Open Developer Options
   a. settings put global development_settings_enabled 1
   b. settings put secure mock_location_app <package>
4. Launch the fake GPS app (am start <package>/<activity>)
   a. Wait for app to fully load
   b. Use UI automation OR broadcast to set coordinates
5. Start mock location within the app
6. Open Google Maps
7. Tap My Location button
8. Verify blue dot is at spoofed coordinates (screenshot)
```

---

## Key Lesson

**Cloud phones lack GPS hardware → GnssLocationProvider fails → `cmd location` fails**
**BUT: Mock Location Framework is pure software → works when app is running and registered**

Never test GPS injection with `cmd location` or `dumpsys location` hardware metrics on cloud phones. Always use a mock location app and verify visually in Maps.

---

## Files to Update

- `core/gps_spoofing.py` — Remove comments saying GPS doesn't work; fix the flow
- `docs/GEELARK_GPS_COMPATIBILITY.md` — Mark as OUTDATED; rewrite with correct method
- `memory/geelark_gps_model_selection.md` — Mark as OUTDATED
- `memory/geelark_gps_error_catalogue.md` — Add this post-mortem as Error 11
- `test_gps_android10.py` — Rewrite with correct flow

---

## Moving GPS / Driving Directions

Next research topic: Can we animate the mock location to simulate driving?

- Lexa Fake GPS has a "Route" mode where you can set waypoints and it animates between them
- ADB broadcasts can be sent repeatedly with different coordinates
- Google Maps directions need a moving blue dot to look natural
- OSRM can generate realistic road-following routes between two points

See `test_gps_phased.py` for the implementation plan.
