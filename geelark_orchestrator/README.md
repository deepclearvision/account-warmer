# Geelark Orchestrator — Phase 1 Starter (tested core + honest scaffold)

This is the foundation for the Python orchestrator described in
`Geelark_Python_Orchestration_Plan.md`. It is deliberately split into two parts
with very different trust levels — read this before wiring anything to a live phone.

## What is REAL and TESTED (use it now)

These run offline, need no Geelark, no API key, no network. **15/15 unit tests pass.**

| File | What it does | Status |
|---|---|---|
| `src/data_layer.py` | Load + validate profiles; enforce the two-delimiter rule | ✅ tested |
| `src/resolver.py` | Make every random pick + fallback for a run; reproducible via seed | ✅ tested |
| `scripts/dry_run.py` | **The transparency tool** — resolve a profile and print every decision | ✅ tested |
| `tests/test_resolver.py` | 15 tests: delimiter rule, coord integrity, fallbacks, reproducibility | ✅ all pass |
| `src/geelark_client.py` → `build_task_payload()` | Turn a RunPlan into the named values a flow consumes | ✅ tested (pure part) |
| `src/schema.sql` | The three-table store (profiles / runs / outcomes) | ✅ ready |
| `data/sample_profiles.csv` | Sample data incl. edge cases (single-item pool, empty pool) | ✅ |

## What is SCAFFOLD and UNVERIFIED (confirm before trusting)

These touch the Geelark network and are marked `# UNVERIFIED` in the code. They
encode the *shape* of what's needed but the exact endpoints, auth signing, and
request bodies must be confirmed against **https://open.geelark.com** and
**github.com/GeeLark/geelark-openapi** first.

| File | Why it's unverified |
|---|---|
| `src/geelark_client.py` (network methods) | Endpoint paths, auth scheme, and **whether RPA tasks accept input params at all** (plan §3 Q1) are unconfirmed |

I will not pretend code I can't test against the real API works — that's the
same opacity you're trying to get away from.

---

## Can I "debug a profile" with this? — straight answer

**Yes, for the decision layer, right now, completely transparently:**

```bash
# See exactly what the system would do for one phone, and why:
python3 scripts/dry_run.py data/sample_profiles.csv Southwark_Plumbers_01 --seed 42

# Reproduce that exact run any time (same seed = same plan):
python3 scripts/dry_run.py data/sample_profiles.csv Southwark_Plumbers_01 --seed 42

# Watch the per-run variation a few-run phone will show (no repeats):
python3 scripts/dry_run.py data/sample_profiles.csv Southwark_Plumbers_01 --runs 4

# Validate your WHOLE dataset and see any data warnings:
python3 scripts/dry_run.py data/sample_profiles.csv --all
```

Every pick, every fallback, every abort is printed with a plain-language trail.
Nothing is hidden on the device or buried in a flow. Drop your real data into a
CSV with the same columns and you can audit every profile before a single phone
ever runs.

**Not yet, for driving an actual cloud phone end-to-end** — that needs the API
question (§3 Q1) answered and your credentials wired into the scaffold. Once
that's confirmed, the resolved payload (`build_task_payload`, already tested)
flows straight into the trigger call. The boundary is clean and isolated on
purpose: confirming the API turns the scaffold real without touching the tested core.

---

## Run the tests

```bash
python3 tests/test_resolver.py          # no pytest needed
# or:  python3 -m pytest tests/ -v
```

## Logs & feedback (when something breaks)

There are two layers of logging. Sending both is what lets a problem be debugged
without re-explaining everything.

**Layer 1 — the decision (this code).** Add `--log logs` to any dry-run and it
writes two files:
- `logs/runs.log` — human-readable decision trail.
- `logs/runs.jsonl` — one JSON record per run, **including the seed**, so the
  exact run is reproducible.

```bash
python3 scripts/dry_run.py data/sample_profiles.csv Southwark_Plumbers_01 --log logs

# When a run misbehaves, pull a copy-paste packet for it:
python3 scripts/feedback_packet.py <run_id> --log logs
```

**Layer 2 — the phone (Geelark).** The execution log with timestamps + the
failing-step screenshot, exactly like the debug logs already shared in chat. The
orchestrator (later phase) stores these against the same `run_id` in the `runs`
table (`failed_step`, `screenshot_url`).

**Ideal feedback packet when stuck:**
1. The Layer 1 JSON record (`feedback_packet.py` output) — the inputs + seed.
2. The Layer 2 Geelark log + screenshot — what the device did.
3. One line: expected vs. actual.

With 1 + 2 linked by `run_id`, the first question — *is the bug in the decision
or the execution?* — is answerable immediately.

## The one rule that matters most

**Two-delimiter rule:** in any pool, `|` separates items, `,` stays *inside* a
`lat,lng` coordinate. `51.5224,-0.1026|51.5187,-0.0991` is two points. The tests
lock this down because if a comma ever splits items, a coordinate gets torn in
half and the bot navigates to a location that doesn't exist.

## Next step for the Code session

1. Confirm §3 Q1 (does the RPA task-trigger accept params?) against the official docs.
2. Implement the `# UNVERIFIED` methods in `geelark_client.py` against the real endpoints.
3. Keep `resolver.py` / `data_layer.py` untouched — they're the tested core; the
   API path plugs in around them.
