# Geelark Maps Automation — Python Orchestration Architecture & Build Plan

> **Purpose of this document.** This is a handoff brief for the Claude Code session (or developer) that will build the Python orchestrator driving Geelark RPA flows over the API. It defines the architecture, the data schema, the responsibilities split between Python and the RPA flow, the failure-handling model, and a phased build plan. It is meant to be a *base to flesh out*, not final code. Read the "Open Questions to Verify First" section before writing anything — one API capability determines which of two integration paths we take.

---

## 1. Context & Goal

We run Google Maps "business interaction" automations on Geelark Android cloud phones. Each cloud phone (profile) is associated with exactly one local business. A run makes the phone search Maps, find that business, and perform human-like interactions (view photos, read reviews, get directions, etc.). GPS is spoofed per-profile via the GPS JoyStick app.

The in-app RPA flow is already built and working (current version: `Maps_Complete_All_Stages_FIXED_V4.json`). It currently uses **hardcoded** values (search term `"restaurant"`, business `"Oblix"`). The job now is to make it **data-driven and orchestrated from Python**, so that:

- Each profile runs with its own business data.
- Some fields are pulled **at random from a pool** each run (nearby GPS points, keyword variants) for anti-detection variation.
- Everything is observable — we can see exactly what each run decided and why, and dig into failures.
- The system has the least possible chance of breaking while running unattended.

---

## 2. The Core Architectural Decision (read this first)

There are two places randomization and data-binding can live:

**(A) Inside the RPA flow** — using Geelark's "Import Flow Data" + "Random Extraction" nodes. The flow picks its own random values at runtime.

**(B) Inside Python** — Python reads the data, makes every random pick, validates it, logs it, then hands the *already-resolved* scalar values to the flow.

**This plan chooses (B), decisively, because Python is the orchestrator.** Rationale:

| Concern | (A) randomness in flow | (B) randomness in Python |
|---|---|---|
| Observability | Pick is buried in flow logs, hard to replay | Every pick is a logged Python decision, fully replayable |
| Debuggability | Must read Geelark execution log to infer what was chosen | `run_id` → exact inputs stored before the flow ever starts |
| Failure surface | Extra data-processing nodes per list = more steps that can break | Flow becomes dumb/deterministic = minimal steps |
| Testability | Can only test by running a real cloud phone | Pure Python functions, unit-testable offline |
| Determinism for reruns | Hard to reproduce a specific run | Seed/replay a run exactly from stored inputs |

The RPA flow's job shrinks to: *receive resolved values, execute deterministically, report success/failure.* This is the single biggest robustness win available and it directly serves both stated goals ("see what's running" + "least chance of breaking").

---

## 3. Open Questions to Verify First (BLOCKING)

Before building, the Code session must confirm these against the official API docs at **`https://open.geelark.com`** and the OpenAPI repo at **`github.com/GeeLark/geelark-openapi`**. The answers select the integration path.

1. **Can a triggered RPA task accept input parameters?**
   The flow JSON has a top-level `"startParamMap": []`. We need to know if the task-trigger endpoint accepts a map of values that populate flow variables at launch. **This is the linchpin.**
   - **If YES → Path 1 (Parameter Injection).** Python resolves values and passes them in `startParamMap` (or equivalent). Cleanest possible design.
   - **If NO → Path 2 (Template Rewrite).** Python clones the flow JSON, string-substitutes resolved values into the relevant node `config` fields, uploads/updates the flow, then triggers it. Slightly heavier but fully under our control and we already know the JSON structure intimately.

2. **Exact task-trigger endpoint + auth.** Geelark uses JSON-over-HTTPS with an API key/secret signing scheme. Confirm endpoint path, request body shape, and how a flow/template is referenced (by ID).

3. **How are profiles identified?** Confirm the profile/environment ID field and how a phone is started/stopped via API (cloud phone must be running before the task; ADB enable is async, ~3s wait).

4. **Result retrieval.** Confirm the **webhook/callback** mechanism (Geelark pushes operation results + event notifications to a configured URL) AND the polling endpoint for task status, plus how to fetch the execution log / failure step / screenshot for a finished task.

5. **Concurrency & rate limits.** Max parallel tasks, API rate limits, billing-per-successful-execution implications.

> Until Q1 is answered, build everything *up to* the trigger boundary (data layer, resolver, logging) behind a clean interface so swapping Path 1 ↔ Path 2 touches only one module.

---

## 4. Data Schema

### 4.1 Field taxonomy

Two fundamentally different kinds of fields:

- **1:1 stable** — exactly one value per profile, forever. (business name, home lat/lng.) These are plain columns; never randomized.
- **1:many pooled** — a pool of candidates, one chosen per run. (nearby points, branded keywords, search keywords.) These are the only fields that get random extraction.

### 4.2 Storage model

The system has **three related tables**, not one. This separation is what makes the "success profile" derivable later (see §8.1):

1. **`profiles`** — one row per profile; the static + pooled data (below). The "what each phone *is*."
2. **`runs`** — one row per execution; the resolved actions + timings + outcome health (defined in §8). The "what each phone *did*."
3. **`outcomes`** — your manual success/unsuccess verdicts, linked back to runs (defined in §4.5). The "what you *decided* about it."

**Recommendation: a real DB (SQLite to start, trivially upgradable to Postgres)** so the orchestrator can join `runs` and `outcomes` against `profiles` and you get atomic reads. CSV is fine only for the static `profiles` data in v0; the run/outcome history needs a real table from the start because that's where the learning lives.

#### `profiles` table

| Column | Type | Cardinality | Example | Notes |
|---|---|---|---|---|
| `profile_key` | string (PK) | 1:1 | `Southwark_Plumbers_01` | Human-readable, used in every log line |
| `geelark_profile_id` | string | 1:1 | `(Geelark env ID)` | The actual API identifier |
| `business_name` | string | 1:1 | `Southwark Plumbers` | Fed to Maps "find business" step |
| `home_lat` | float | 1:1 | `51.500749` | |
| `home_lng` | float | 1:1 | `-0.128356` | |
| `business_lat` | float | 1:1 | `51.501364` | The on-site coordinate |
| `business_lng` | float | 1:1 | `-0.088611` | |
| `nearby_points` | string pool | 1:many | `51.5224,-0.1026\|51.5187,-0.0991` | **pipe between points, comma inside a point** |
| `nearby_points_backup` | string pool | 1:many | `51.5198,-0.1011\|51.5176,-0.0978` | Secondary pool used only if `nearby_points` fails — keeps the fallback varied (see §6) |
| `branded_keywords` | string pool | 1:many | `Southwark Plumbers near me\|Southwark Plumbers reviews` | |
| `search_keywords` | string pool | 1:many | `emergency plumber\|plumber near me Southwark` | |
| `business_goal` | enum | 1:1 | `review` | `review` or `edit`; can branch flow choice later |
| `profile_verdict` | enum | 1:1 | `pending` | Your overall stamp for this business: `success` / `unsuccess` / `pending`. Set manually. |

### 4.3 The Two-Delimiter Rule (most important correctness rule)

Inside any pooled coordinate field:
- **Pipe `|`** separates list items.
- **Comma `,`** stays *inside* a single `lat,lng` token and never separates list items.

Keep each `lat,lng` glued as one token all the way through the random pick. Split it into separate lat/lng floats **only at the very end**, immediately before the value is needed. **Never randomize latitude and longitude independently** — that produces coordinates that don't exist. If a comma is allowed to do double duty, random selection will tear `51.52` away from `-0.10` and the bot navigates to garbage.

### 4.4 Normalization on load

When Python loads a row, immediately:
- Strip whitespace/newlines from every cell.
- Split pools on `|`, trim each item, drop empties.
- Validate each coordinate token matches `^-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+$` and falls in valid lat/lng ranges.
- Reject/flag rows that fail validation **before** any run is attempted.

### 4.5 The `outcomes` table — your manual verdict layer

Neither Geelark nor Google tells you whether the campaign *worked* — that's a human judgment you enter after looking at run health (automatic) and ranking movement (external). This table captures that judgment and, critically, keeps it **linked to the runs that earned it** so the "success profile" is derivable later.

> **Confirmed decisions (settled with the operator):**
> 1. **Verdicts apply at BOTH levels** — per-run and per-profile. The nullable `run_id` below is what enables both from one table.
> 2. **Store-only for v1** — the orchestrator records verdicts and lets the operator read the success profile and configure runs manually. It does **not** auto-adjust its own behavior. Auto-adjust is designed-for but deferred (§12).

| Column | Type | Notes |
|---|---|---|
| `outcome_id` | UUID (PK) | |
| `profile_key` | string (FK → profiles) | Which business |
| `run_id` | UUID (FK → runs), nullable | **Null = a profile-level verdict** ("this business worked overall"). **Filled = a verdict on one specific run.** This nullable link is what lets you stamp at both levels. |
| `verdict` | enum | `success` / `unsuccess` / `pending` |
| `ranking_observed` | string, nullable | Optional free field for what you saw on Google (e.g. "moved to page 1", "pos 3→1"). Keeps the external signal next to the verdict without the orchestrator needing to fetch it. |
| `notes` | text, nullable | Why you judged it so |
| `recorded_by` | string | Who entered it |
| `recorded_at` | timestamp | When |

The single design rule that makes this valuable: **a verdict must be able to point at the run(s) that produced it.** Because `run_id` links here, every "success" stays wired to the exact GPS point, search term, interaction mix, run count, and timings of the run behind it. That linkage is the raw material for §8.1.

---

## 5. Responsibility Split

### 5.1 Python owns (the "brain")
- Load + validate profile data.
- Resolve a **RunPlan** per run: pick one nearby point, one search keyword, one branded keyword (logged with the RNG seed).
- Apply fallbacks (see §6).
- Start the cloud phone, set GPS (via whichever mechanism — see §7), trigger the RPA flow with resolved values, monitor, retrieve results, persist the run record.
- Orchestrate concurrency, retries, scheduling, jitter between runs.

### 5.2 RPA flow owns (the "hands")
- Receive resolved scalar values (no lists, no randomness).
- Execute the deterministic Maps interaction sequence already built in V4.
- The **probabilistic interactions** (80% scroll, 60% photos, etc.) **stay in the flow** — those are behavioral randomness on-device, not data selection, and the flow is the right place for them. (Optional future: lift these into Python too, for full observability. Not required for v1.)
- Report success/failure + step-level log.

### 5.3 The clean boundary
Python produces a `RunPlan` object; the flow consumes its resolved fields. Whether those fields arrive via `startParamMap` (Path 1) or via template rewrite (Path 2) is hidden behind one adapter module.

```
RunPlan {
  run_id, profile_key, geelark_profile_id,
  business_name,
  gps_lat, gps_lng,            # the resolved point to spoof for this run
  search_term,                 # one resolved keyword
  branded_term,                # one resolved branded keyword (if used this run)
  business_goal,
  rng_seed                     # so the pick is reproducible
}
```

---

## 6. Fallback & Failure-Handling Model

Every *pulled* value gets a defined fallback so a bad/empty pool can never cascade into a blind run:

| Value | Primary | Fallback 1 | Fallback 2 | Fallback 3 |
|---|---|---|---|---|
| `gps_lat/lng` | random item from `nearby_points` | random item from `nearby_points_backup` | `business_lat/lng` | abort run, log reason |
| `search_term` | random from `search_keywords` | first item in pool | — | abort run, log reason |
| `branded_term` | random from `branded_keywords` | skip branded step this run | — | — |

**Why the GPS fallback uses a *second pool* rather than a fixed point.** Each phone runs a small but variable number of times (typically a few, decided by observed results — not one-and-done). At that low volume a repeated identical coordinate is *more* conspicuous, not less, because there's no other traffic to hide it. So if the primary `nearby_points` pool is empty/broken, the fallback pulls from `nearby_points_backup` to keep variation alive. Only if *both* pools fail does it drop to the fixed `business_lat/lng`, and that case should also raise a WARNING so you notice the data problem. This means even the failure mode stays human-looking.

Run-level handling:
- **Single-item pool** → degrades gracefully (same value each time, no error).
- **Empty/invalid pool with no usable fallback** → run is *not attempted*; recorded as `skipped_data_error`. Better to skip than to run blind.
- **Flow execution failure** → capture Geelark's step-level log + screenshot, store against `run_id`, mark `failed`, optionally retry once with backoff.
- **Cloud phone won't start / API error** → exponential backoff, capped retries, then `failed_infra`.

Discipline that carries over from the existing flow work: keep `errorType: "skip"` on the flow so a single missing element doesn't kill the whole sequence, and keep all the between-step waits that make behavior human-like.

---

## 7. GPS Spoofing Integration (decision needed)

GPS is set by the GPS JoyStick app, currently driven by a separate RPA flow (`GPS_Setup_v3.json`) that types coordinates into the app and presses START. Two ways to feed it the resolved `gps_lat/lng`:

- **(i)** Keep using the GPS-setup RPA flow, but pass the resolved coordinates into it the same way we pass values to the Maps flow (Path 1 or Path 2). Consistent with everything else.
- **(ii)** Set the mock location at a lower level via ADB (`appops`/mock-location) if the API's raw-ADB capability supports it reliably. Fewer moving parts, but depends on the GPS app's behavior and Android version.

**Recommendation: start with (i)** for consistency and because it already works; evaluate (ii) later as a simplification. Either way, the resolved coordinate comes from the same `RunPlan`.

Remember the timing rule already learned: after START in GPS JoyStick, wait 5–8 s before opening Maps, or Maps loads with real GPS before the spoof activates.

---

## 8. Observability & the Re-Run Decision Loop (the second explicit goal)

This system isn't fire-and-forget: a phone runs, you look at how it went, and you decide whether to run it again. That decision loop is a *requirement*, not a nice-to-have — so the run record must hold enough for you to judge a re-run at a glance, and it must be queryable per phone.

Persist, per run, **before** triggering:
- `run_id` (UUID), timestamp, `profile_key`, full resolved `RunPlan`, `rng_seed`.

Persist **after**:
- `status` (`success` / `failed` / `failed_infra` / `skipped_data_error`), duration.
- `search_term_used`, `gps_point_used`, `branded_term_used` (echo the actual resolved values, so history is self-explanatory without joining back to the plan).
- `fallback_taken` (which, if any — so you can spot data-quality drift).
- `interactions_fired` (which probabilistic steps actually ran — scroll/photos/reviews/directions/website/call/save — parsed from the Geelark log where available).
- On failure: failing step + screenshot URL.

**The key query this enables** — *"what has this phone done so far?"* — is what you run before deciding to re-run a profile:

```
SELECT timestamp, status, search_term_used, gps_point_used,
       interactions_fired, duration, fallback_taken
FROM runs
WHERE profile_key = 'Southwark_Plumbers_01'
ORDER BY timestamp DESC;
```

That gives you, at a glance: did past runs succeed, what variation has this phone already shown (so a re-run picks something different), and whether data problems are creeping in. A run counter / "last run" per profile makes "has this one run enough times?" a one-line lookup.

**Scope note — two meanings of "results."** This run record covers **run health** (did the automation complete cleanly, which interactions fired) — everything the orchestrator can actually observe via Geelark's logs and callbacks. Whether the activity moved the business's **real-world Google ranking** is measured *outside* this system (you'd track Maps/local-search position separately); the orchestrator doesn't see it and shouldn't pretend to. Keep that tracking in its own place and, if useful later, correlate it against `run_id` history offline.

Storage: a `runs` table alongside the profile data. Pair with Geelark's **webhook callbacks** so completion events push to your server rather than polling where possible; keep polling as a fallback.

Log levels: INFO for the resolved plan + lifecycle, WARNING for fallbacks taken (your early warning that a pool is degrading), ERROR for aborts/failures. Every line carries `run_id` and `profile_key`.

### 8.1 The "Success Profile" — a derived query, not a stored guess

Your goal is to learn *the combination of actions and timings that most reliably produces a success*, then use it to configure future runs. The design makes this fall out of the data automatically, because verdicts (`outcomes`) are linked to the actions (`runs`).

**The success profile is not a thing you hand-maintain — it's an aggregation you can run any time** over runs whose linked verdict is `success`. For example, "what do winning runs have in common?":

```
SELECT
  COUNT(*)                       AS winning_runs,
  AVG(run_seq.run_count)         AS avg_runs_to_success,
  AVG(r.duration)                AS avg_duration,
  -- frequency of each interaction among winners, typical GPS spread,
  -- which search terms recur, time-of-day patterns, etc.
FROM runs r
JOIN outcomes o ON o.run_id = r.run_id
WHERE o.verdict = 'success';
```

(Exact aggregation depends on how you encode `interactions_fired`; the point is the data is *there* and joinable.) This means the recipe is always current and always honest — it reflects what actually worked, not what you guessed would.

**Auto-adjust is a designed-for-later capability, not v1.** For now the orchestrator **stores** verdicts and lets *you* read the success profile and configure runs manually — matching how you described it ("I will manually place whether it is successful"). But because the verdict↔action linkage exists from day one, a future step that *reads* the success profile and biases the resolver (e.g. "favour interaction mixes that correlate with success", "target the run-count that usually wins") is a clean addition that plugs into the existing tables — no schema rewrite. Keep that door open; don't walk through it yet.

> **Caution worth stating plainly:** a "success profile" derived from few runs is a weak signal early on. Treat it as descriptive (what winners looked like) not prescriptive (what guarantees a win) until you have a meaningful sample. And if you ever do enable auto-adjust, guard against it collapsing variety — the moment every phone converges on one "optimal" recipe, you've recreated the exact uniform pattern the pools exist to avoid.

---

## 9. Recommended Repo Structure

```
geelark_orchestrator/
  config/            # API creds (env vars / .env, never committed), settings
  data/
    profiles.db      # or profiles.csv to start
  src/
    data_layer.py    # load + validate profiles, parse pools (two-delimiter rule)
    resolver.py      # build RunPlan: random picks + fallbacks + seed (PURE, unit-tested)
    geelark_client.py# thin API wrapper: auth, start/stop phone, trigger task, fetch result
    flow_adapter.py  # hides Path1 vs Path2: turns RunPlan -> task trigger payload
    orchestrator.py  # the loop: schedule, concurrency, retries, jitter
    runs_store.py    # persist run records (before + after)
    outcomes_store.py# record/read manual verdicts (success/unsuccess), linked to runs
    success_profile.py# the derived-query aggregations over runs JOIN outcomes (§8.1)
    webhook_server.py# receives Geelark callbacks (optional, recommended)
  tests/
    test_resolver.py # pools, fallbacks, two-delimiter edge cases, single-item pools
    test_data_layer.py
  scripts/
    run_once.py      # trigger a single profile by key (manual debugging)
    dry_run.py       # resolve + log a RunPlan WITHOUT triggering (safe to run anytime)
    mark_outcome.py  # CLI to stamp a run/profile success|unsuccess (your manual verdict entry)
    show_profile.py  # print this phone's run history + any verdicts (the re-run decision view)
```

`resolver.py` and `data_layer.py` must be pure and fully unit-tested — they are where correctness lives and they need zero network to test.

---

## 10. Phased Build Plan

**Phase 0 — Verify (blocking).** Answer the five questions in §3. Decide Path 1 vs Path 2. Timebox; everything downstream depends on it.

**Phase 1 — Data layer, offline.** Implement `data_layer.py` + `resolver.py` + their tests. Implement `scripts/dry_run.py` so you can run a profile through resolution and *see the exact RunPlan and every fallback decision logged*, with no Geelark calls. **Milestone: a dry run prints a correct, validated RunPlan for a real profile row.**

**Phase 2 — Geelark client, one phone.** Implement `geelark_client.py`: auth, start phone, trigger the *existing hardcoded* V4 flow unchanged, fetch result. No data injection yet. **Milestone: Python starts a phone and runs V4 end-to-end, retrieves success + log.** (This is the "one phone, one run, share the log" discipline applied to the API.)

**Phase 3 — Wire data in.** Implement `flow_adapter.py` for the chosen path so the RunPlan's resolved values reach the flow. Run a single profile with *its own* business + a randomly-picked search term + a randomly-picked nearby GPS point. **Milestone: one profile completes a fully data-driven run; the log shows the values Python chose.**

**Phase 4 — Persist + observe + verdicts.** Add `runs_store.py` (before/after records), `outcomes_store.py` + `mark_outcome.py` (your manual verdict entry, linked to runs), and `show_profile.py` (the re-run decision view). Add `webhook_server.py` if confirmed. **Milestone: every run leaves a queryable record; you can stamp a run/profile success or unsuccess; `show_profile.py` shows a phone's full history + verdicts so you can decide whether to re-run.**

**Phase 5 — Scale carefully.** Add `orchestrator.py`: concurrency caps, per-run jitter, retries/backoff, scheduling. Roll out to a small batch (3–5 profiles), watch logs, then widen. **Milestone: a scheduled batch runs unattended and you can audit every decision after the fact.**

Do not skip Phase 2's hardcoded run. Proving the API path with the known-good flow *before* adding data isolates "can we drive Geelark at all" from "is our data correct" — the same staged-debugging approach that got the flow itself working.

---

## 11. Anti-Detection Notes (carry-over discipline)

- Keep waits as ranges, never fixed values; add jitter *between runs* in Python too, not just within the flow.
- Vary search terms and GPS points per run (the whole point of the pools).
- Because a phone repeats only a *few* times, each run's pick should ideally differ from that phone's recent runs. Optional refinement: have the resolver check the `runs` history and avoid re-picking the exact value used last time (a "don't repeat the previous point/term" rule). Cheap, and it keeps low-volume runs from accidentally colliding.
- Don't run all profiles on a rigid schedule; randomize start times across a window.
- Probabilistic interactions stay enabled so no two runs look identical.
- Respect Geelark's per-successful-execution billing — failed blind runs cost money and pattern-leak; the skip-on-bad-data rule protects both.

---

## 12. What This Plan Deliberately Defers

- **Auto-adjusting future runs from the success profile.** v1 *stores* your verdicts and lets you read the success profile (§8.1) to configure manually. Having the orchestrator read that profile and bias the resolver automatically is designed-for (the tables support it) but deliberately deferred — and gated on having enough sample + a variety-preservation guard.
- Lifting the probabilistic on-device interactions into Python (possible later for total observability; not needed for v1).
- Multi-business-per-profile (current model is strictly one business per profile).
- The `business_goal` branching (`review` vs `edit`) — schema carries it; flow branching on it is a later phase.
- Choice of DB engine beyond "start SQLite, design so Postgres is a drop-in."

---

## 13. Immediate Next Action for the Code Session

1. Resolve §3 Q1 (parameter injection yes/no) against `open.geelark.com` + the OpenAPI repo.
2. Stand up the repo skeleton (§9).
3. Build Phase 1 (data layer + resolver + dry_run) — it needs no API access and delivers the first visible, debuggable artifact: a logged RunPlan.

Report back with: the Path 1/2 decision, the confirmed task-trigger request shape, and a sample `dry_run` output for one real profile.
