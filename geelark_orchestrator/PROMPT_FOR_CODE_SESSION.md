================================================================================
COPY EVERYTHING BELOW THIS LINE INTO YOUR CLAUDE CODE SESSION
================================================================================

I'm building a Python orchestrator that drives Geelark RPA flows (Google Maps
business-interaction automations on Android cloud phones) over the Geelark
OpenAPI. A working in-app RPA flow already exists. The job now is to make it
data-driven and orchestrated from Python, with full transparency and the least
chance of breaking. I'm attaching a starter project that already has a TESTED
core — do not rewrite it; build around it.

## ⚠️ DO THIS BEFORE ANYTHING ELSE — RECONCILE WITH YOUR EXISTING WORK

You (this session) may have already started your own version of some of this in
this folder. This attached project is a TESTED, TRANSPARENT reference
implementation. Before building or changing anything:

1. Compare this reference against whatever you have already done here.
2. Tell me where they DIFFER — especially anywhere your existing version makes
   random picks, selects data, applies fallbacks, or handles coordinates
   differently from the reference.
3. Do NOT overwrite either your existing work OR this reference without first
   explaining the difference and why one approach is better. If your version and
   the reference disagree on a decision rule, surface it — don't silently merge.
4. The reference's job is to bring transparency and tested correctness. If your
   existing code is opaque about how it chooses GPS points or keywords, the
   reference shows the intended behaviour; align to it unless you can justify otherwise.

Treat this as a reconciliation, not a blind rebuild. Report the differences first,
then propose how to merge — and wait for my go-ahead before changing working code.

## ATTACHED FILES (read them first)

- `Geelark_Python_Orchestration_Plan.md` — the full architecture & phased build plan. READ THIS FIRST.
- `Geelark_Master_Reference_Section9_Data_Architecture.md` — data model, the two-delimiter rule, the three-table schema, fallback chains.
- A `geelark_orchestrator/` project folder containing:
  - `src/data_layer.py`   — TESTED. Loads/validates profiles, enforces two-delimiter rule.
  - `src/resolver.py`     — TESTED. Makes every random pick + fallback; reproducible via seed.
  - `src/run_logger.py`   — TESTED. Writes runs.log (human) + runs.jsonl (machine, with seed).
  - `src/runs_store.py`   — TESTED. SQLite persistence (profiles/runs/outcomes) + history & success-profile queries.
  - `src/geelark_client.py` — SCAFFOLD, marked `# UNVERIFIED`. The network methods are NOT confirmed.
  - `src/schema.sql`      — the three-table store.
  - `scripts/dry_run.py`  — resolve a profile and print every decision (no API). Supports `--log`.
  - `scripts/feedback_packet.py` — extract a copy-paste debug packet by run_id.
  - `tests/test_resolver.py` — 15 tests, all passing.
  - `data/sample_profiles.csv` — sample data incl. edge cases.
  - `README.md` — what's tested vs scaffolded, and the logging/feedback workflow.

## GROUND RULES (important)

1. The TESTED core (`data_layer.py`, `resolver.py`, `run_logger.py`, `runs_store.py`)
   is correct and unit-tested. Do NOT rewrite it. Build the API layer AROUND it.
   If you think something in the core is wrong, flag it and explain — don't silently change it.
2. Transparency is the priority. Every decision must be logged and reproducible.
   No randomness or data selection on the device or hidden in the flow — it all
   happens in Python and gets logged (the seed makes any run replayable).
3. Be honest about uncertainty. If you can't verify something against the real
   API, mark it `# UNVERIFIED` exactly like the existing scaffold does. Do not
   present untested API code as working.

## THE ONE BLOCKING QUESTION — DO THIS FIRST

Confirm against the official docs (https://open.geelark.com and the OpenAPI repo
github.com/GeeLark/geelark-openapi):

  >>> Can a triggered RPA task accept input parameters at launch? <<<
  (The flow JSON has a top-level "startParamMap": [] which hints yes.)

  - If YES -> Path 1: pass resolved values in startParamMap. Cleanest.
  - If NO  -> Path 2: clone the flow JSON, string-substitute resolved values into
              the node configs, upload/update the flow, then trigger.

Whichever it is, isolate it in ONE adapter module (`flow_adapter.py`) so the rest
of the code doesn't care which path we took. `geelark_client.build_task_payload()`
already produces the resolved scalar map (tested) — that's the input to either path.

Also confirm: exact task-trigger endpoint + auth signing; how a profile/phone is
started/stopped (ADB enable is async ~3s); how to fetch a finished task's
execution log + failing-step screenshot; the webhook/callback mechanism; rate limits.

## BUILD ORDER (from the plan)

- Phase 0: answer the blocking question. Decide Path 1 vs 2.
- Phase 1: DONE — the tested core is attached. Verify `python3 tests/test_resolver.py`
           passes on your machine and `dry_run.py` works on the sample data.
- Phase 2: implement `geelark_client.py` against the REAL endpoints. First, trigger
           the EXISTING flow unchanged (hardcoded values) to prove we can drive a
           phone at all. Store the result.
- Phase 3: wire the resolved RunPlan values into the flow via `flow_adapter.py`.
           Run ONE profile with its own business + a random search term + a random
           nearby GPS point.
- Phase 4: persist every run with `runs_store.py` (record_run before, finish_run
           after); fetch + store the Geelark log/screenshot on failure; add the
           manual-verdict entry (record_outcome, both run-level and profile-level).
- Phase 5: orchestrate (concurrency caps, per-run jitter, retries/backoff,
           randomized scheduling). Roll out to 3-5 profiles, then widen.

Do NOT skip Phase 2's hardcoded run — it isolates "can we drive Geelark at all"
from "is our data correct."

## DATA RULES YOU MUST PRESERVE

- Two-delimiter rule: `|` separates pool items, `,` stays inside a `lat,lng`
  coordinate. Never split lat/lng independently. (Tests enforce this — keep them green.)
- GPS fallback chain: nearby_points -> nearby_points_backup -> business coord
  (warn) -> abort. (Phones repeat a few times, so the fallback must stay varied.)
- Search fallback: search_keywords -> first item -> abort.
- Branded: random -> skip (never aborts).
- Store the RNG seed on every run so it can be replayed.

## LOGS TO PRODUCE AND REPORT BACK (so we can debug across sessions)

Whenever something breaks and you want it reviewed, produce a feedback packet with
BOTH layers, linked by run_id:

  LAYER 1 — the decision (our code):
    python3 scripts/feedback_packet.py <run_id> --log logs
    (the runs.jsonl record: resolved plan + seed -> fully reproducible)

  LAYER 2 — the device (Geelark):
    the execution log with timestamps + the failing-step screenshot for the same run_id

  PLUS one line: what you expected vs what actually happened.

Always report: which Phase you're in, the Path 1/2 decision, the confirmed
task-trigger request shape, whether the tests still pass, and a sample dry_run
output for one real profile. If a Geelark call fails, paste the request you sent
(secrets redacted) and the full response.

## FIRST REPLY I WANT FROM YOU

1. The RECONCILIATION report (see the ⚠️ section above): how this reference differs
   from any work you've already done here, especially on data selection / random
   picks / fallbacks / coordinates. List the differences; don't change code yet.
2. Confirm you've read the plan + the attached code and understand the tested-core
   vs scaffold split.
3. Your answer to the blocking question (Path 1 or Path 2), with the doc reference.
4. The confirmed task-trigger endpoint + request body shape.
5. Confirmation that `tests/test_resolver.py` passes and `dry_run.py` runs on the
   sample data on your machine.
Then propose how to merge with your existing work, and your Phase 2 plan — and
WAIT for my go-ahead before changing any working code.

================================================================================
END OF PROMPT
================================================================================
