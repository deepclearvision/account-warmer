# SECTION 9 — PER-PROFILE DATA ARCHITECTURE (Python Orchestration)

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
| `branded_keywords` | pool | 1:many | `Southwark Plumbers near me\|...` |
| `search_keywords` | pool | 1:many | `emergency plumber\|plumber near me Southwark` |
| `business_goal` | enum | 1:1 | `review` or `edit` |
| `profile_verdict` | enum | 1:1 | `success` / `unsuccess` / `pending` |

## 9.6 The fallback chains

GPS (must stay varied because phones repeat a few times):
1. random from `nearby_points`
2. random from `nearby_points_backup`
3. fixed `business_lat/lng` (raise a WARNING — data problem)
4. abort the run (`skipped_data_error`)

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
