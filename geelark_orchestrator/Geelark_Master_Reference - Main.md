# Geelark RPA Master Reference Document

> **How to use this document:** At the start of every new Claude chat, paste the relevant sections before asking your question. The more context you provide, the better the answer. Suggested opener:
> *"I am building Geelark RPA automations. Here are my conventions: [paste Section 1]. I need help with [specific task]. Here is my relevant JSON: [paste block if applicable]."*

---

## SECTION 1 — CORE CONVENTIONS & RULES

These apply to every flow. Always follow these unless a specific exception is noted.

### Scroll Direction (CRITICAL)
- `"direction": "top"` = swipes UP = scrolls content **DOWN** (to see more below)
- `"direction": "bottom"` = swipes DOWN = scrolls content **UP** (to go back up)
- `"direction": "left"` = swipes LEFT = moves content right (e.g. photo gallery next)
- `"direction": "right"` = swipes RIGHT = moves content left (e.g. photo gallery back)
- **In Google Maps:** to expand the results panel, use a large `"direction": "top"` swipe (900-1000px)

### Variable Syntax (CRITICAL)
- Always use `${variable_name}` with dollar sign
- Wrong: `{business_name}` — Right: `${business_name}`
- Variables come from the Import Flow Data (Excel/CSV) block at the top of the task

### Standard Scroll Settings for Navigating Down a Page
```json
{
  "_a": 400, "_b": 600,
  "direction": "top",
  "distanceMax": "500", "distanceMin": "300",
  "position": [260, "1200"],
  "randomDistance": 10,
  "randomWheelSleepTime": [400, 600]
}
```

### Exception Handling
- Set to **Skip** on all Click steps where the element might not always be present
- Set to **Skip** on Element Appears steps inside loops
- The flow-level `errorType` should always be `"skip"`

### IF Condition — Exist Check
- Never include `"probability"` field on an exist check
- Correct:
```json
"conditionV3": [[{"relation": "exist", "useVariable": "myVar"}]]
```
- Wrong:
```json
"conditionV3": [[{"probability": 50, "relation": "exist", "useVariable": "myVar"}]]
```

### Probability (Random) Conditions
- Only used for random behaviour (e.g. 60% chance to view photos)
- Correct:
```json
"conditionV3": [[{"probability": "60", "relation": "random"}]]
```

### Loop Types
- **For Loop Times** — use for scroll-and-find patterns, fixed repetitions, random repetitions
- **For Loop Elements** — only use when looping through a known list of elements already on screen. Do NOT use for scroll-and-search patterns
- **For Loop Data** — use for looping through a data variable (Batch Text)

### Standard Scroll-and-Find Pattern
Every time you need to scroll until an element is found, use this structure:
```
For Loop Times (10)
  └── Element Appears → save to variable "myVar" (searchTime: 2000, exception: Skip)
  └── Statement IF variable "myVar" Exists
       ├── TRUE → Click variable "myVar" → Exit Loop
       └── ELSE → Scroll (direction: top, 300-500px) → Wait (1000ms)
```

### Wait Times
- Between app open and first interaction: 3000-5000ms
- Between steps in Settings navigation: 1000ms fixed
- Between interactions in Maps: 1500-3000ms random
- After tapping Build Number x7: 5000-8000ms
- After GPS START before opening Maps: 5000-8000ms
- Website page load: 2000-4000ms

---

## SECTION 2 — REUSABLE JSON BLOCKS

### 2.1 Standard Scroll-and-Find Loop Template
Replace `SEARCH_TEXT` and `VARIABLE_NAME` with your values.
```json
{
  "config": {
    "children": [
      {
        "config": {
          "filterCollection": [[{"content": "SEARCH_TEXT", "filterType": "equal", "type": "text"}]],
          "hiddenChildren": false,
          "searchTime": 2000,
          "serial": 1,
          "serialType": "fixedValue",
          "variable": "VARIABLE_NAME"
        },
        "id": "wait-VARIABLE_NAME-1",
        "name": "Element appears",
        "type": "waitEle"
      },
      {
        "config": {
          "children": [
            {
              "config": {"doubleClick": false, "randomDistance": 0, "saveItemName": "VARIABLE_NAME", "useOffset": false},
              "id": "click-VARIABLE_NAME-1",
              "name": "Click",
              "type": "click"
            },
            {"config": {}, "id": "exit-VARIABLE_NAME-1", "name": "Exit Loop", "type": "breakLoop"}
          ],
          "conditionV3": [[{"relation": "exist", "useVariable": "VARIABLE_NAME"}]],
          "other": [
            {
              "config": {
                "_a": 400, "_b": 600, "direction": "top",
                "distanceMax": "500", "distanceMin": "300",
                "position": [260, "1200"], "randomDistance": 10,
                "randomWheelSleepTime": [400, 600]
              },
              "id": "scroll-VARIABLE_NAME-1",
              "name": "Scroll",
              "type": "scrollPage"
            },
            {"config": {"timeout": 1000, "timeoutType": "fixedValue"}, "id": "wait-VARIABLE_NAME-2", "name": "Time", "type": "waitTime"}
          ]
        },
        "id": "if-VARIABLE_NAME-1",
        "name": "Statement if",
        "type": "ifElse"
      }
    ],
    "serialType": "fixedValue",
    "times": "10",
    "remark": "Find and tap SEARCH_TEXT"
  },
  "id": "loop-VARIABLE_NAME-1",
  "name": "For Loop Times",
  "type": "forTimes"
}
```

### 2.2 Random Interaction Block Template (Probability)
Replace `PROBABILITY`, `REMARK` and add steps inside children.
```json
{
  "config": {
    "children": [],
    "conditionV3": [[{"probability": "PROBABILITY", "relation": "random"}]],
    "remark": "REMARK"
  },
  "id": "interaction-block-id",
  "name": "Statement if",
  "type": "ifElse"
}
```

### 2.3 Random Loop (Min/Max Iterations)
```json
{
  "config": {
    "children": [],
    "maxTimes": 7,
    "minTimes": 3,
    "serialType": "randomInterval"
  },
  "id": "random-loop-id",
  "name": "For Loop Times",
  "type": "forTimes"
}
```

---

## SECTION 3 — KNOWN WORKING ELEMENT SELECTORS

### Android Settings Navigation
| Element | filterType | type | content |
|---------|-----------|------|---------|
| Apps menu | equal | text | Apps |
| Apps menu (alt 1) | equal | text | Applications |
| Apps menu (alt 2) | equal | text | App management |
| Apps menu (alt 3) | equal | text | Apps & notifications |
| See All Apps | contain | text | See all |
| See All Apps (desc) | contain | desc | See all |
| All Apps (alt) | contain | text | All apps |
| About Phone | equal | text | About phone |
| About Device (alt) | equal | text | About device |
| Build Number | equal | text | Build number |
| System | equal | text | System |
| Advanced (inside System) | equal | text | Advanced |
| Developer Options | equal | text | Developer options |
| Select Mock Location App | equal | text | Select mock location app |
| GPS JoyStick app | contain | text | JoyStick |
| GPS JoyStick (alt) | contain | desc | JoyStick |
| Storage | equal | text | Storage |
| Storage & cache (alt) | equal | text | Storage & cache |
| Clear Cache | equal | text | CLEAR CACHE |
| Clear Cache (alt) | equal | desc | CLEAR CACHE |

### Google Maps
| Element | filterType | type | content |
|---------|-----------|------|---------|
| Search box | equal | text | Search here |
| Search box class | equal | class | android.widget.TextView |
| Photos button | equal | text | Photos |
| Reviews button | equal | text | Reviews |
| Call button | equal | text | Call |
| Call button class | equal | class | android.widget.Button |
| Directions button | equal | text | Directions |
| Directions class | equal | class | android.view.View |
| Save button | equal | desc | Save |
| Save button class | equal | class | android.widget.CompoundButton |
| Website (.co.uk) | contain | text | .co.uk |
| Website (.com) | contain | text | .com |
| Done button | equal | text | Done |

### GPS JoyStick App
| Element | filterType | type | content |
|---------|-----------|------|---------|
| Set Location button | equal | id | com.theappninjas.fakegpsjoystick:id/set_location_button |
| Start button | contain | id | com.theappninjas.fakegpsjoystick:id/start_button |
| Start button text | contain | text | START |
| Lat/Lng input | equal | id | com.theappninjas.fakegpsjoystick:id/control |
| Lat/Lng input text | equal | text | Latitude, Longitude |
| Altitude input text | equal | text | Altitude |
| Continue with ads | contain | text | CONTINUE WITH ADS |
| Continue to app | equal | text | Continue to app |
| Consent button | equal | text | Consent |

---

## SECTION 4 — KNOWN ISSUES AND FIXES

### AutoX.js Limitations
- **AutoX.js does NOT work on Android Settings screens** — Android blocks overlays in Settings for security
- **Solution:** Use the For Loop Times scroll-and-find pattern instead of AutoX.js layout analysis
- AutoX.js only works in normal apps (Maps, TikTok, Instagram etc.)
- For Settings element selectors, use ADB dump: `adb shell uiautomator dump /sdcard/ui.xml`

### For Loop Elements vs For Loop Times
- **For Loop Elements times out without scrolling** — it only loops through elements already visible on screen and then exits after the timeout
- **Never use For Loop Elements for scroll-and-find** — always use For Loop Times with Element Appears inside

### Google Maps Results Panel
- After searching, the results show as a small panel at the bottom with the MAP behind it
- Scrolling at this point scrolls the MAP not the results
- **Fix:** Swipe up 900-1000px BEFORE starting the business-finding loop to expand the panel into full list mode
- Wait 3000ms after the swipe before the loop starts

### Scroll Direction in Geelark
- Geelark uses the direction the content moves, not the direction of finger movement
- `"top"` = content moves up = you are scrolling DOWN through the page
- `"bottom"` = content moves down = you are scrolling UP through the page
- This is the opposite of what feels intuitive

### Variable Syntax
- Missing `$` is a silent failure — the literal text `{variable_name}` gets typed instead of the value
- Always check: `${variable_name}` not `{variable_name}`
- **⚠️ CONTESTED (2026-05 testing):** Live device tests showed the builder's login
  flows substitute correctly with SINGLE braces `{email}`/`{password}` in `inputText`.
  The real substitution bug encountered was NOT the `$` — it was that `inputContent`
  does not substitute paramMap at all (see "Maps native input" below). Status:
  single braces confirmed working in `inputText`; the `${}` vs `{}` rule may be
  step-type- or version-specific. Do NOT assume one is universally correct — verify
  per step type. When values are baked in as literals at build time, substitution is
  sidestepped and the question is moot.

### Maps Native Search Box — Input Facts (HARD-WON, 2026-05)
The native Google Maps search field rejects the "standard" input/key actions. Use ONLY:
- **Type into it:** `inputContent` (array content, `simulate`, `taskOrder`, `clear`).
  `inputText` does NOT type into the native Maps search box (confirmed repeatedly).
- **Submit the search:** `keyOption` with `keyType: "enter"`. `pressKey` with
  `key: "enter"` does NOT submit — the keyboard/suggestions stay active and the search
  never runs.
- `inputContent` does NOT substitute paramMap values — it types the literal placeholder.
  So values for Maps search must be BAKED IN as literals at build time, not passed via paramMap.
- An autocomplete SUGGESTION is a different surface from the submitted RESULTS LIST.
  The primary path submits (keyOption enter) and finds the business in the results list;
  clicking a suggestion is a branded-only fallback whose ranking value is unconfirmed.
- Location is set by the GPS spoof and Maps auto-detects it — never type coordinates
  into the search bar for location.

### IF Condition Probability Field on Exist Checks
- Adding `"probability"` to an exist check causes undefined behaviour
- Remove it entirely — exist checks do not need a probability value

### Developer Options — Advanced Button
- Some Android versions hide Developer Options behind an **Advanced** collapsible menu in System settings
- The ELSE branch of the Developer Options loop must also check for and tap Advanced if Developer Options is not found
- After tapping Advanced, the next loop iteration will find Developer Options

### GPS Activation Timing
- After clicking START in GPS JoyStick, wait **5000-8000ms** before opening Maps
- Without this wait, Maps may load with real GPS before the spoof activates

### GPS Spoof is PERISHABLE (critical for orchestration / scaling)
The Fake GPS spoof does NOT persist. Operating facts:
- It must be (re)STARTED each time the phone is opened.
- It CLOSES after ~20 minutes of inactivity.
- It CLOSES if the phone is switched off between runs.
- Selecting Fake GPS as mock app in settings is NOT enough — the app must be OPENED and
  the location actively STARTED before dumpsys shows the mock provider tag.
Consequences for the orchestrator:
- Start the spoof FRESH every run, immediately before the Maps work — never assume a
  prior run's/setup's spoof is still alive.
- Keep the spoof->search window TIGHT (well inside 20 min). If a run could approach 20
  min, re-check / keep-alive the spoof mid-run, or it will silently drop and Maps will
  switch to the REAL location mid-run (a masked failure — flow still "completes").
- check_phone_fit must confirm via dumpsys that the mock provider is live AND at the
  RESOLVER'S coords immediately before the Maps work — not just that "a mock exists".

PROVEN per-run mechanism (gps_flow_baker, 2026-05):
- Export the working GPS setup flow template ONCE, cache locally.
- Per run: string-replace the hardcoded coord literal in the GAL JSON with the resolver's
  resolved coords; import as a NEW ephemeral flow; dispatch with empty param_map (coords
  are literals inside the flow).
- The flow CLOSES + clears Fake GPS storage/cache, re-opens, grants perms, inputs the
  baked coords, clicks START — establishing fresh state every run, clearing any stale/
  inherited spoof. (Don't just START on top of existing state — close/clear first.)
- Verify dumpsys shows the RESOLVER'S exact coords live before opening Maps.
- Verified working: resolver 51.5013,-0.0886 -> dumpsys 51.501300,-0.088599, survived a
  full run. This closes both "resolver coords ignored" and "spoof perishable" at once.

### Build Number Timing
- After tapping Build Number 7 times, wait **5000-8000ms** before going back
- This gives time for the "You are now a developer" message to appear and process

### Website Block
- Always add **Go Back** after the website browsing loop
- Without it the flow stays on the website and never returns to Maps

---

## SECTION 5 — DATA VARIABLES REFERENCE

These variables come from the Import Flow Data block. Use `${variable_name}` syntax.

| Variable | Content | Example |
|----------|---------|---------|
| `${business_name}` | Target business name | Southwark Plumbers |
| `${business_address}` | Business address | 12 Crosby Row, London SE1 3PT |
| `${business_location}` | Business area | Southwark, London |
| `${business_share_link}` | Google Maps share link | https://maps.app.goo.gl/... |
| `${business_coords}` | Combined lat,lng | 51.501364,-0.088611 |
| `${business_lat}` | Latitude only | 51.501364 |
| `${business_lng}` | Longitude only | -0.088611 |
| `${home_lat}` | Home latitude | 51.500749 |
| `${home_lng}` | Home longitude | -0.128356 |
| `${home_address}` | Home area description | Residential address near south_east_london |
| `${work_lat}` | Work latitude | 51.497655 |
| `${work_lng}` | Work longitude | -0.083149 |
| `${random_nearby_point}` | Combined lat,lng near business | 51.522451,-0.102649 |
| `${random_onsite_point}` | Combined lat,lng at business | 51.501307,-0.088712 |
| `${random_search_term}` | Single pre-selected search term | emergency plumber |
| `${random_branded_search}` | Pre-selected branded search | Southwark Plumbers near me |
| `${random_local_search}` | Pre-selected local search | plumber near me Southwark |
| `${search_terms}` | Pipe-separated search terms | plumber\|emergency plumber\|... |
| `${business_goal}` | review or edit | review |
| `${strategy}` | standard or maps_heavy | standard |
| `${account_email}` | Account email address | user@gmail.com |
| `${geo_city}` | City for geo targeting | london |

---

## SECTION 6 — FLOW ARCHITECTURE

### Recommended Flow Order
1. **Clear Cache flow** — clears GPS JoyStick cache before each run
2. **Developer Options / Mock Location setup flow** — sets GPS JoyStick as mock location app
3. **GPS JoyStick setup flow** — opens app, sets home coordinates, starts spoofing
4. **Main Maps flow** — searches, finds business, interacts with listing

### Maps Interaction Probabilities (Recommended)
| Interaction | Probability | Notes |
|-------------|-------------|-------|
| Scroll full panel | 80% | Most natural passive behaviour |
| View photos | 60% | Common behaviour |
| Read reviews | 50% | Common behaviour |
| Tap directions | 40% | Moderate frequency |
| Tap call | 30% | Less frequent |
| Visit website | 35% | Moderate frequency |
| Tap save | 25% | Least frequent |

### Maps Interaction Scroll Settings Summary
| Purpose | Direction | Distance | Duration | Position Y |
|---------|-----------|----------|----------|------------|
| Expand results panel | top | 900-1000px | 600-800ms | 1500 |
| Scroll results list | top | 400-600px | 400-600ms | 1200 |
| Scroll knowledge panel | top | 300-500px | 400-700ms | 1200 |
| Scroll reviews | top | 200-400px | 500-800ms | 1000 |
| Scroll website | top | 300-500px | 500-800ms | 1000 |
| Scroll to panel bottom | top | 400-600px | 500-800ms | 1200 |
| Swipe photos left | left | 400-600px | 400-600ms | X:500 Y:900 |

---

## SECTION 7 — ASKING CLAUDE EFFECTIVELY

### Template for New Chat
```
I am building Geelark RPA automations for Android cloud phones.

My core conventions:
- Scroll direction: "top" scrolls DOWN, "bottom" scrolls UP
- Variable syntax: ${variable_name} with dollar sign
- Pattern: For Loop Times + Element Appears + Statement IF Exists for scroll-and-find
- Exception handling: Skip on all Click steps
- Never use probability field on exist checks

[Paste any relevant existing JSON block if applicable]

My question: [specific question here]
```

### Tips for Best Results
- Always paste the relevant JSON block when asking about fixing or improving something
- Paste debug logs when something is not working — they show exactly where it fails
- Mention which Android version or phone type if the issue seems device-specific
- Ask for full JSON when you want something you can copy directly
- Say "include total code" when you want the complete updated file not just the changed block

---

## SECTION 8 — QUICK TROUBLESHOOTING GUIDE

| Symptom | Most Likely Cause | Fix |
|---------|------------------|-----|
| Flow keeps scrolling, never finds element | Wrong scroll direction | Change `"bottom"` to `"top"` |
| Variable value not being typed, literal text appears | Missing `$` in variable syntax | Change `{var}` to `${var}` |
| For Loop Elements times out doing nothing | Used For Loop Elements for scroll-and-find | Replace with For Loop Times pattern |
| IF condition never fires TRUE | `probability` field on exist check | Remove probability field |
| Flow stops immediately after Build Number taps | Wait too short | Increase wait to 5000-8000ms |
| Maps scrolls the map not the results | Results panel not expanded | Add 900-1000px swipe up before loop |
| GPS not active when Maps opens | No wait after START | Add 5000-8000ms wait before Maps open |
| Website click does nothing | No website attached to listing | Exception handling already set to Skip |
| Flow stays on website, doesn't return to Maps | Missing Go Back after website loop | Add Go Back + Wait after browsing loop |
| Developer Options not found | Hidden behind Advanced button | Add Advanced button check in ELSE branch |
| Settings navigation fails | AutoX.js cannot inspect Settings | Use For Loop Times scroll-and-find only |
| Element found but wrong one clicked | Same class/text on multiple elements | Use indexInParent or parent container to differentiate |
| Text typed in Maps search box but search never runs | Used `pressKey` enter (doesn't submit Maps) | Use `keyOption` with `keyType:"enter"` |
| Nothing types into Maps search box | Used `inputText` (fails in native Maps field) | Use `inputContent` (array content, simulate, taskOrder, clear) |
| Literal `{var}` typed into Maps search instead of value | `inputContent` doesn't substitute paramMap | Bake the value in as a literal at build time |
| Maps shows wrong-area results, business missing | Spoofed to home (far) or no wait after GPS START | Spoof a near-business point; wait 5-8s after START before opening Maps |
| Run spoofed at first, real location later (silent) | Spoof timed out mid-run (~20 min inactivity) | Start spoof fresh per run, keep spoof→search window tight, re-check dumpsys mid-run if long |
| dumpsys mock coords differ from resolver's chosen point | GPS flow uses its own baked-in coords, ignores resolver | Inject resolver coords (paramMap or per-run bake) into the GPS flow |
| Business never appears in results | May not rank for that keyword yet (not a bug) | Record as "not found at keyword" outcome — that's baseline data |

---

§ SECTION 9 FOLLOWS — Python orchestration data architecture (added 2026-05) §


## SECTION 9 — PER-PROFILE DATA ARCHITECTURE (Python Orchestration)

> Add this section to the Geelark Master Reference. It captures the data model,
> conventions, and rules for running flows data-driven from a Python orchestrator
> (rather than hardcoded values inside the flow). Paste it alongside Sections 1–8.

## 9.1 Where randomness lives: in Python, not the flow

When flows are driven by a Python script over the Geelark API, **all data
selection and randomness happens in Python**, and the flow receives already-
resolved scalar values. The flow stays dumb and deterministic — lowest failure
surface, and every decision is logged and reproducible.

- Python = the brain: load data, pick random values, validate, log, trigger.
- Flow = the hands: receive resolved values, execute, report success/failure.
- The on-device probabilistic interactions (80% scroll, 60% photos, etc.) STAY
  in the flow — those are behavioural randomness, not data selection.

## 9.2 Field taxonomy

- **1:1 stable** — one value per profile, forever (business name, home lat/lng,
  business lat/lng). Plain columns, never randomized.
- **1:many pooled** — a pool of candidates, one chosen per run (nearby points,
  branded keywords, search keywords). Only these get random selection.

## 9.3 The Two-Delimiter Rule (CRITICAL)

In any pooled field:
- **Pipe `|`** separates items in the pool.
- **Comma `,`** stays INSIDE a single `lat,lng` coordinate and never separates items.

`51.5224,-0.1026|51.5187,-0.0991` = two points. Keep each `lat,lng` glued as one
token through the random pick; split into separate lat/lng only at the very end.
**Never randomize latitude and longitude independently** — it produces
coordinates that don't exist. A comma doing double duty tears a coordinate in half.

## 9.4 The three-table model

| Table | Holds | "The…" |
|---|---|---|
| `profiles` | static + pooled data per phone, + overall verdict | what each phone IS |
| `runs` | resolved actions + timings + run health per execution | what each phone DID |
| `outcomes` | manual success/unsuccess verdicts, linked to runs | what you DECIDED |

`outcomes.run_id` is nullable: **NULL = a profile-level verdict**, **filled = a
verdict on one specific run**. This single link lets you stamp at both levels and
keeps every "success" wired to the exact actions that earned it.

## 9.5 profiles columns

| Column | Type | Card. | Example |
|---|---|---|---|
| `profile_key` | string (PK) | 1:1 | `Southwark_Plumbers_01` |
| `geelark_profile_id` | string | 1:1 | (Geelark env ID) |
| `business_name` | string | 1:1 | `Southwark Plumbers` |
| `home_lat` / `home_lng` | float | 1:1 | `51.500749` / `-0.128356` |
| `business_lat` / `business_lng` | float | 1:1 | `51.501364` / `-0.088611` |
| `nearby_points` | pool | 1:many | `51.5224,-0.1026\|51.5187,-0.0991` |
| `nearby_points_backup` | pool | 1:many | `51.5198,-0.1011\|51.5176,-0.0978` |
| `onsite_points` | pool | 1:many | `51.5013,-0.0886\|51.5014,-0.0887` |
| `branded_keywords` | pool | 1:many | `Southwark Plumbers near me\|...` |
| `search_keywords` | pool | 1:many | `emergency plumber\|plumber near me Southwark` |
| `business_goal` | enum | 1:1 | `review` or `edit` |
| `profile_verdict` | enum | 1:1 | `success` / `unsuccess` / `pending` |

## 9.6 The fallback chains

GPS — the run first rolls the **origin split (default 70/30, configurable)**:
- **70% NEARBY** (a local in the business's catchment searching from around the area)
- **30% ONSITE** (someone at the business itself)

The point must always be close enough that the business is in range of a local
search. Both branches stay varied (separate pools), and each has its own fallback:

NEARBY branch (70%):
1. random from `nearby_points`
2. random from `nearby_points_backup`
3. fixed `business_lat/lng` (raise a WARNING — data problem)
4. abort the run (`skipped_data_error`)

ONSITE branch (30%):
1. random from `onsite_points`
2. fixed `business_lat/lng` (raise a WARNING — onsite pool missing)
3. abort the run (`skipped_data_error`)

Notes:
- The 70/30 is a DEFAULT, tunable like the interaction percentages — no rebuild to change.
- Never spoof to `home_lat/lng` as an origin: home may be miles from the business,
  so the business wouldn't appear in local results and a real "searched from the
  wrong area" miss would masquerade as "not ranking". `home` is data, not a spoof point.
- Two separate pools (`nearby_points`, `onsite_points`) exist so even the 30% onsite
  runs don't hammer one identical coordinate — that cluster would itself be a robotic tell.

Search term:
1. random from `search_keywords`
2. first item in pool
3. abort the run

Branded term (never aborts):
1. random from `branded_keywords`
2. skip the branded step this run

## 9.7 Conventions carried over

- Keep `errorType: "skip"` on the flow so one missing element doesn't kill the run.
- Keep all between-step waits (human-like pacing).
- Variable syntax in the flow remains `${variable_name}`.
- Each run is reproducible: the RNG seed is stored, so any run replays exactly.
- "Don't repeat last pick" best-effort: a few-run phone should avoid re-using the
  GPS point / search term from its previous run.

### 9.7.1 Native Google Maps search box — proven input facts (HARD-WON)

The native Maps search field rejects the "standard" input/key actions. Use ONLY:
- **Type into it:** `inputContent` (array content, `simulate`, `taskOrder`, `clear`).
  `inputText` does NOT type into the native Maps search box (confirmed multiple times).
- **Submit the search:** `keyOption` with `keyType: "enter"`. `pressKey` with
  `key: "enter"` does NOT submit (keyboard/suggestions stay active, search never runs).
- With Path 1 param injection, `inputContent` does NOT substitute `paramMap` values —
  it types the literal placeholder. So values for Maps search are BAKED IN as literals
  at build time (Option 3), not passed via paramMap.
- Surfaces differ: an autocomplete SUGGESTION is not the submitted RESULTS LIST. The
  primary path submits the search (keyOption enter) and finds the business in the
  RESULTS LIST. Clicking a suggestion is a branded-only fallback whose association
  value is unconfirmed.
- Location is set by the GPS spoof and Maps auto-detects it. Never type coordinates
  into the search bar for location.

## 9.8 Observability & feedback

Two logging layers:
- **Layer 1 (decision):** Python writes `runs.log` (readable) + `runs.jsonl`
  (machine, includes seed). Reproducible.
- **Layer 2 (device):** Geelark execution log + failing-step screenshot, stored
  against the same `run_id` (`runs.failed_step`, `runs.screenshot_url`).

When debugging, send both, linked by `run_id`. First question answered instantly:
is the bug in the decision (Python) or the execution (flow/device)?

## 9.9 The "success profile" is a query, not a stored recipe

Because verdicts link to actions, "what do winning runs have in common?" is an
aggregation over `runs JOIN outcomes WHERE verdict='success'`. Always current,
reflects what actually worked. v1 STORES verdicts; the operator reads the profile
and configures manually. Auto-adjust is designed-for but deferred — and if ever
enabled, must guard against collapsing all phones onto one recipe (which would
recreate the uniform pattern the pools exist to avoid).

## 9.10 Per-phone PROVISIONING state (prerequisite — enforce before any run)

Phones are NOT interchangeable. A phone is only eligible for a normal Maps run once it
has been PROVISIONED. Discovered the hard way: a phone whose one-time setup never ran
had Fake GPS unregistered as the mock provider, so the spoof silently did nothing and
the run landed on the Play Store — while Geelark still reported "completed".

Provisioning = all of:
1. Developer Options enabled (Build number ×7).
2. Fake GPS Joystick set as the **mock location app** (Select mock location app).
3. Google Maps installed AND current (not a stale version that opens the Play Store).

Track per profile:
- `provisioned`   (bool) — setup ran AND was VERIFIED via `dumpsys location` showing
  Fake GPS as the active mock provider (not just "flow completed").
- `maps_verified` (bool) — Maps opens CLEANLY to a usable search screen with NO Play
  Store / update interstitial intercepting. This is BEHAVIOUR-based, not package-based:
  "installed" is insufficient. Discovered the hard way — a phone with a pending Google
  Play Services update prompt sent every search-box click to the Play Store, even with
  GPS removed entirely. Verify by openApp(maps) -> search screen reachable, confirmed by
  screenshot/element check. A phone failing this is NOT run-eligible.

The orchestrator MUST refuse to dispatch a normal run to an unprovisioned phone — either
provision it first, or skip and log why. Without this, every unprovisioned phone fails
identically when scaling.

Setup-flow caveat: the one-time dev-options/mock-location flow must follow the reliable
pattern (For Loop Times + Element appears + exist check, NO probability field, NO
For Loop Elements for scroll-and-find — see Section 4). A setup flow built on
For Loop Elements + probability:50 exist checks can silently set NOTHING and still report
success under errorType:"skip". Always confirm provisioning with dumpsys, never with
task status.

## 9.11 Built-flow lifecycle — keep the Geelark backend clean

Because per-run values (keyword, coordinates) are baked in as literals at build time
(Option 3), the orchestrator creates a NEW Geelark flow for every run. Left unmanaged,
these pile up — hard to find real/hand-built flows, and eventual storage pressure.

Rule: built flows are EPHEMERAL. Lifecycle = create -> dispatch -> record result in
SQLite -> DELETE the flow. The flow's job ends the moment the run finishes; the DATA
about what happened lives in the `runs` table, not in the Geelark flow, so deleting
loses nothing.

- Confirm Geelark exposes a delete-flow endpoint first. If yes -> implement
  create/dispatch/record/delete cleanup before scaling.
- If no -> name every built flow `auto_{profile_key}_{run_id}` so machine-made flows
  sort together and are distinguishable from hand-built reference flows, plus periodic
  export-and-purge.

Do this before scaling: at a handful of phones it's clutter; at 50+ runs/day it's a real
storage and findability problem.

## 9.12 StreamVia mobile proxy — behaviour facts (confirmed by StreamVia, 2026-05)

Per-interaction IP rotation runs through a StreamVia mobile proxy. Confirmed behaviour:
- NO automatic rotation by default — IP changes ONLY when we trigger it (changeipunique),
  unless StreamVia is asked to enable auto-rotate. So no hidden timer changes IP mid-run.
- The assigned IP CAN occasionally self-change (carrier reassignment / modem reconnect),
  independent of any command. Rare — treat as a known low-probability mid-run failure mode,
  not a primary concern.
- Rotated IPs scatter ALL OVER THE UK — IP geolocation does NOT match the spoofed GPS on
  any given run. CONFIRMED NON-FATAL: runs succeed regardless, so Maps does not hard-block
  on IP-vs-GPS mismatch for these interactions.
- StreamVia recommends rotating NO MORE than once per ~15 minutes for reliability (stricter
  than the 180s hard floor). Respect the 15-min guidance — it feeds batch pacing.
- Settle window: after a rotation, the PHONE's observed IP lags the proxy's new IP by
  ~15-20s. The per-interaction gate must poll until the PHONE IP matches the new proxy IP
  (not just proxy "Ready"), confirm it differs from the previous interaction's IP, then
  proceed. Record interaction_ip per run for audit.
- Credentials: env var / git-ignored secrets file ONLY. Never hardcoded, never in chat.
  TLS verify=False is scoped to the StreamVia control host only, not global.

---

## SECTION 10 — VERIFICATION & EVIDENCE STRATEGY (text-first; screenshots to disk)

### 10.1 The core principle: text-first verification, vision as unreliable fallback

The orchestrating model (currently Kimi via Claude Code) has UNRELIABLE and version-
specific image handling. Observed: one deployment hit a "max 30 images per conversation"
limit; Kimi docs variously state no fixed image count but a ~100MB request-body cap,
base64-only (no URLs), and at least one source says image input is NOT exposed via the
K2.6 API at all (vision used internally only). Conclusion: do NOT depend on feeding
screenshots to the model in the normal run path. Treat model-vision as a fallback that
may or may not work, not a verification mechanism.

This is not just an image-limit workaround — it may be a CORRECTNESS issue: if the
variant can't reliably ingest images, screenshots passed "for the model to check" may
not be doing anything. So verification must be TEXT-based wherever possible.

### 10.2 Two purposes of a screenshot — keep them separate

- Purpose A — model needs to SEE it to reason (e.g. "which screen is showing?"). Rare;
  vision-dependent; unreliable on this stack.
- Purpose B — a durable RECORD for the operator/audit, stored against a run_id.
Most screenshots are Purpose B. Only pull into the model when actively debugging.

### 10.3 Text-based checks per stage (preferred — don't count against any image limit)

| Question | Text check (reliable) | Not this |
|---|---|---|
| Spoof active? | `dumpsys location` → mock provider + mock fix | screenshot of blue dot |
| Maps open vs Play Store? | foreground package via ADB | screenshot interpretation |
| Search submitted / results shown? | element/text presence check | screenshot |
| Business found? | element exists in results | screenshot |

Text checks are both lighter AND less ambiguous than a picture the model must interpret.

### 10.4 Screenshots: capture to DISK, keyed by run_id; into the model only on trouble

- Store at `screenshots/{run_id}/{stage}.png` on disk. Disk has no per-conversation
  image cap and is cheap. This is the durable evidence record.
- Pass a screenshot into the model ONLY when a text check is insufficient/ambiguous —
  i.e. when something went wrong and the visual is genuinely needed to diagnose. This is
  the "fall back on screenshots when in trouble" rule, and it's correct.
- Normal run: text verification → disk screenshots for the record → nothing into model.
  Trouble: pull that run's disk screenshots into the model to diagnose.

### 10.5 Storage retention (screenshots accumulate at scale)

- FAILED / flagged runs: keep screenshots indefinitely (debugging gold).
- SUCCESSFUL runs: auto-purge screenshots after N days (rarely needed once verified).
Same "ephemeral unless it matters" principle as built-flow cleanup (9.11). Keeps disk
bounded without losing the shots that matter. At 50+ runs/day × several shots each this
matters; build the retention policy into the orchestrator, don't rely on manual cleanup.

### 10.6 Net rule

Verify with TEXT (dumpsys, foreground package, element checks). Store screenshots to
DISK as the record. Show the model a screenshot only when debugging a specific failure.
Never make the normal run path depend on the model seeing an image.

---

## SECTION 11 — SHARED CORE vs PLATFORM EDGE (multi-session / multi-platform rulebook)

The project has TWO execution sides over ONE shared core:
- MOBILE side: Geelark cloud phones, Fake GPS spoofing, StreamVia SOCKS proxy, RPA flows.
- DESKTOP side: Multilogin browser profiles, fixed residential proxies, browser automation.
Same accounts, same businesses, same verification discipline — different execution surface.

They are NOT two projects. They are two EDGES over one CORE. This section is the rulebook
that lets parallel Code sessions (one per side) work without colliding.

### 11.1 What is SHARED CORE (coordinate changes — do NOT edit independently in parallel)
- The data model / Profile structure and the SQLite schema (profiles, runs, outcomes).
- Business <-> account <-> data BINDING logic (match by stable key, verify exactly one,
  abort on zero/many — never "first available"). Both sides use the same accounts attached
  to the same businesses, so binding integrity is a CORE concern serving both.
- The success DEFINITION and verification discipline: success requires EVIDENCE, never
  "completed"; behavioural verification over status codes; the multi-condition gate.
- Resolver / randomisation conventions, seed/reproducibility, logging (runs.log + jsonl).
- Outcome classification and the success-profile query.

CHANGES TO SHARED CORE GO THROUGH THE HUMAN COORDINATOR. A core change made by one side
(e.g. adding a schema column, changing how a profile binds to a business) affects the other
side automatically. Two sessions editing core in parallel collide — literally (same file) or
logically (incompatible assumptions). So: core changes are coordinated, not independent.

### 11.2 What is PLATFORM EDGE (each side owns its own — safe to change independently)
- MOBILE-only: Geelark client/API, GPS flow baker, Fake GPS handling, UI Clear Storage,
  Maps RPA flow + maps_evidence, StreamVia SOCKS proxy control, per-interaction IP gate,
  dumpsys/foreground verification, provisioning (dev options + mock app + proxy routing).
- DESKTOP-only: Multilogin profile control, browser automation, fixed residential proxy
  handling, browser-based evidence capture.
Each side may freely change its own edge without affecting the other. No coordination needed.

### 11.3 The discipline is SHARED, the implementation is per-edge
The hard-won verification discipline is a CORE property, not a mobile one: "prove the action
happened with evidence, or stop and say so" applies identically to desktop. The desktop side
does NOT start from scratch — it applies the SAME discipline to a different surface. Its job:
"here's how mobile proves things (dumpsys, foreground, element checks, >=2 real interactions);
build the browser equivalent (DOM/screenshot evidence) to the same standard."

### 11.4 What is independent / no conflict
- Proxy stacks differ entirely (mobile: StreamVia SOCKS; desktop: fixed residential via
  Multilogin) — no shared proxy logic, no conflict.
- Execution mechanics (RPA flows vs browser scripts) are fully separate.

### 11.5 Folder structure to make parallel work safe
Aim for: a core/ directory both sides import (data model, schema, resolver, success logic,
logging, binding) + separate mobile/ and desktop/ directories for platform execution. The
project already half-has this (account-warmer/core/, geelark_orchestrator/). Making the
boundary clean is what lets two sessions work without stepping on each other.

### 11.6 Coordination model
- Mobile Code session owns the mobile edge; desktop Code session owns the desktop edge.
- The HUMAN owns the shared core and is the continuity across both (the same role that has
  caught regressions throughout — operator memory is the project's continuity).
- Each side gets its own handoff prompt including: "core/ is SHARED — do not change it
  without flagging; only change <your-side>-specific code."
- Strategy is also split: one strategy thread per side, both referencing THIS document as
  the shared-core knowledge base.

### 11.7 Shared task worth doing once, in core, for BOTH sides
The business<->account binding integrity cleanup (the "relationships aren't fully enforced"
issue) is a CORE task that benefits both sides. Do it ONCE in core, not separately per side.

---

*Last updated: 2026-05.*

---

## SECTION 12 — FINDINGS FROM THE LATEST PUSH (proxy gate, A13 cohort, fleet picture)

This section captures hard-won learnings from the long thread that closed with Goal A1 met on FIND X (Android 10) and on Pixel 7 (Android 13), plus a fleet survey.

### 12.1 The no-force-stop fix (Android 10 cohort generalisation)
The pipeline previously contained a `force-stop` step inside `_activate_gps_via_deeplink` in `run_one_phone.py`. This was a leftover from the pre-UI-Clear-Storage ad fix and was no longer earning its keep. On serial_24 the mock happened to persist through the force-stop; on other Android 10 phones (OPPO FIND X, A72, X27) the mock died ~5s after force-stop, blocking those phones from running.

Resolution: REMOVE the force-stop step. Mock persists on every Android 10 phone without it. serial_24 still works (its persistence wasn't dependent on the force-stop, it just happened to survive it). The fix removed a serial_24-specific assumption and made the pipeline more robust across the whole Android 10 cohort.

Rule: when changing pipeline behaviour, re-confirm the known-good phone (serial_24, plus any other confirmed-fit baseline) still goes fully green — not just mock-active, the FULL chain end-to-end. The fix that helps new phones must not silently break the working ones.

### 12.2 The app-path proxy gate (verification URL matters)
The pipeline gained a mandatory pre-run gate, `_verify_app_path_proxy`, that opens Chrome to a "what's my IP" URL on the phone and confirms the IP matches the rotated proxy IP. This is the third hard fitness gate alongside `provisioned` and `maps_verified`.

CRITICAL: the VERIFICATION URL matters. The original choice was `checkip.amazonaws.com`. That specific URL bypasses the proxy on Android 13 phones (AWS-range carve-out somewhere in Geelark's proxy config). This produced a false-positive "Android 13 leaks" survey result across 9 of 9 tested phones. The real picture, proven by a manual test against multiple checker sites (ifconfig.me, ip.me, icanhazip.com), is that Android 13 phones tunnel all OTHER traffic — Google Maps, Play Services, general HTTPS — through the proxy correctly.

Resolution: the gate now uses `ifconfig.me` primary, `icanhazip.com` fallback. Both proven reliable; `checkip.amazonaws.com` removed. The gate's behavioural model (one Chrome page load, extract IP, compare to expected proxy IP) is unchanged; only the URL list changes.

Rule: any future "is this proxied?" check must be verified against MULTIPLE checker sites before being trusted as the canonical answer. Don't assume one site's result generalises.

### 12.3 The Google leak test (cross-side verification)
The AWS-URL bypass raised the question: was the same exclusion swallowing Google traffic too? The decisive test combined two sides of evidence on the Pixel 7:
- PHONE-SIDE (netstat against the Maps PID during a search): 12 active Maps connections, all from the wlan0 local IP to Fastly/CloudFront CDN endpoints, zero connections to the Zenlayer (real) IP. GMS routed the same way. No QUIC/UDP detected. DNS via internal resolver.
- GOOGLE-SIDE (the test account's Gmail "Last account activity > Details" panel checked from a different machine): the two Authorized Application entries from the test window were logged by Google at `31.94.24.42`, a StreamVia proxy IP. Not the real IP.

Result: tunneling holds for ACTUAL Google work on Android 13. The AWS-URL bypass is cosmetic, not a real leak. The "Android 13 leaks" survey was a verification-URL artefact.

Rule: when verifying anti-detection / proxy posture, check BOTH SIDES — phone-side network evidence (netstat / dumpsys / connection origin) AND target-side activity records (account activity page, login history, location history). One side alone can be misleading.

Operational hygiene note: the Gmail-dashboard check inevitably logs the operator's home IP against the test account. For accounts where keeping only proxy IPs in account history matters, do future dashboard checks from a separate machine/connection.

### 12.4 The Android 13 GPS provisioning fork (cohort splitting)
The Fake GPS JoyStick v5.3.0 build on Android 13 changed how the mock is maintained: the mock is tied to the map activity's foreground lifecycle. Backgrounding the app kills the mock; the OverlayService no longer holds it alive independently the way it does on the Android 10 build.

Resolution for GOOGLE-stock Android 13 (Pixel 7): after activating the mock on the map view, open the notification shade and tap "Hide" on the GPS JoyStick notification. This keeps the mock alive while the phone switches to other apps (verified: mock persistent 30s+ during Maps foreground). This is the `gps_provision_android13` flow, added as a small bounded extension to the existing provisioning sequence. The orchestrator selects A10 vs A13 path based on the phone's Android version.

CAVEAT — the A13 flow does NOT generalise across all OEM Android 13 phones. X70 (Oppo ColorOS) and Y31 (Vivo FunTouch OS) both FAIL the same way: the mock never activates after the map tap, even before any backgrounding question. Pixel 7 (Google stock-ish A13) is the EXCEPTION, not the rule, in a fleet dominated by OEM Android 13 devices.

Rule: an "Android 13 flow" is not one thing. There's a Google-stock path (working) and an OEM-stack path (unsolved as of this thread's close). The OEM path is the highest-value remaining technical question because the OEM cohort is the largest single group in the 43-phone fleet.

### 12.5 The interface-type rule was a measurement artefact
Earlier in the thread, the survey produced an apparently clean rule: "single-interface Android 10 = clean; multi-interface Android 13 = leak." Held perfectly across 9 phones, zero exceptions. That rule turned out to be FALSE — it was an artefact of using `checkip.amazonaws.com` as the only verification URL. The real rule is "Android 13 phones leak only `checkip.amazonaws.com`, not other traffic."

Rule: a 100%-consistent pattern from a single verification method is suspicious, not reassuring. If a pattern feels too clean, sanity-check the verification method against an independent measurement before treating the pattern as a law.

### 12.6 Sequential operation with one shared proxy (confirmed)
Single StreamVia proxy serves all 43 phones. Distinct-IP-per-interaction therefore forces SEQUENTIAL operation: rotate -> run one phone -> rotate -> next. Concurrent phones would share the same proxy IP and violate the rule. A second proxy is a future throughput lever; not in scope for now.

### 12.7 Current fleet picture (as of the close of the long thread)
- 4 confirmed-fit Android 10 phones: serial_24, FIND X, A72, X27. Run-eligible.
- 1 confirmed-fit Android 13 phone: Pixel 7 (tannerchambers9987). Google-stock A13, runs via the new A13 flow.
- = 5 phones currently capable of full interaction runs end-to-end.

Other proxy-clean but NOT run-eligible (proxy gate passed, GPS provisioning unsolved):
- X70 (gracelynvillanueva9987), Y31 (luisamathis93437) — confirmed OEM-A13 mock-activation failure.
- Reno7 (eddievilla9987 / batch_01_phone2), iQOO 8 (sweeneyjanus), "8" (makaylacrane9987) — proxy-clean from the wider survey, OEM Android 13, GPS provisioning likely fails the same way as X70/Y31 (untested).

Unknown — proxy-routing status not yet established:
- ~33 stopped phones in the fleet. The survey tested them but the 15s post-start window was too short for shell-responsiveness; the script labelled them "leak" but the raw evidence shows API error 42002 ("phone is not running"). Their true proxy status is UNKNOWN. A longer boot wait or a retry loop is needed to determine them.

Fleet composition observation: of all 43 phones, ~9 are Android 10 and ~34 are Android 13, with the Android 13 group dominated by OEM stacks (Samsung Galaxy series, Oppo, Vivo). Pixel devices are the small minority. So the OEM-A13 problem is not a niche — solving it is the unlock to most of the remaining fleet.

### 12.8 Three hypotheses for the OEM Android 13 mock-activation failure (untested as of thread close)
For the next push to investigate, in likely-most-productive order:
1. DIFFERENT FAKE GPS APK VERSION on the OEM phones vs Pixel 7. Geelark may install different builds based on device model or Android image, or Play Store may serve different versions to different OEM stores. Two OEMs failing identically is consistent with one shared APK that behaves differently from Pixel 7's. Cheap to test: dumpsys the package version on serial_24/FIND X (working A10), Pixel 7 (working A13), X70/Y31 (failing OEM A13), and compare.
2. OEM-specific battery/background policies silently blocking mock-provider activation. Oppo and Vivo are known for aggressive background restrictions on top of stock Android. Symptom would match: "the app does the tap, Android-side dumpsys never sees a mock registered." Test by checking the app's permission/background-allow state in OEM settings.
3. The map tap is hitting wrong UI elements due to OEM screen layout / DPI differences. Less likely given two different OEMs failing identically (would be a coincidence), but cheap to rule out: capture the foreground UI element at tap time on X70 and confirm it is the map element.

### 12.9 Survey-script improvement noted for the next push
The wider proxy survey (Task B) used a 15s post-start window before testing shell responsiveness. That was too short for Geelark cloud phones, which often take 30-60s to become fully shell-responsive after a cold start. Result: 33 phones got `null` IPs and were mislabelled "leak" when the real status is UNKNOWN. Fix: increase the post-start wait (30-60s), or retry the shell command a few times before declaring a phone unverifiable. Until then, treat any "leak" verdict from a stopped phone as "could not verify," not as evidence of leak.

### 12.10 Discipline observations from this push (worth preserving)
- Sessions can converge on conclusions that don't quite fit the evidence. The fix is "show me the evidence behind that checkmark" — applied repeatedly, it caught the no-force-stop leftover, the AWS-URL artefact, and the fictional-business test trap.
- Brute-force loops on a stuck problem are easy to fall into; the fix is "after three failed variations on the same problem, stop and report rather than try more variations." Applied successfully on the OEM Android 13 mock-activation failure — stopped after one observation, no hour-long loop.
- Cross-side verification (phone-side + Google-side, here) is strictly stronger than single-side. Worth the small extra cost for any anti-detection question.
- Operator memory of earlier work is a project asset distinct from the AI session's memory. Repeatedly caught regressions and false-greens that the session would have papered over.

---

*Last updated: 2026-05. Sections 1-8 (mobile RPA mechanics); Section 9 (Python orchestration data architecture); Section 10 (verification & evidence strategy); Section 11 (shared-core vs platform-edge rulebook); Section 12 (proxy gate, A13 cohort findings, fleet picture).*
