# Scaling Kickoff — Flow Accumulation + Batch-1 Plan

## 1. SOLVED: Ephemeral Flow Accumulation

### Finding
GeelarK's `import_rpa_flow(gal_json, flow_id=...)` **updates an existing flow in-place** when `flow_id` is passed. Confirmed by API test on both GPS and Maps templates:

```
Original title: Set Up of GPS - Final
New flow ID: 621113141610152290
Updated flow ID returned: 621113141610152290
Re-exported title: TEST OVERWRITE FLOW - UPDATED
SUCCESS: Flow was updated in place
```

Same result confirmed for Maps flow.

### Solution Implemented
1. **`flow_registry.py`** — New persistent JSON registry mapping `phone_id → {"gps_flow_id": "...", "maps_flow_id": "..."}`
2. **`gps_flow_baker.py` + `maps_flow_baker.py`** — Both now accept `reuse_flow_id`. If provided, the baker calls `client.import_rpa_flow(..., flow_id=reuse_flow_id)`, overwriting the flow content. If `None`, a new flow is created and its ID is stored.
3. **`run_one_phone.py`** — On every run:
   - Queries registry for existing flow IDs for that phone
   - Passes them to bakers (update in-place)
   - Registers new IDs on first run only

### Result
Flow count is now **capped at 2 × phone_count** (one GPS + one Maps per phone) instead of **2 × run_count**. At 10 phones × 1 run/day = 20 flows total, forever. No manual cleanup required.

---

## 2. SCOPE: Batch-1 Scaling Plan

### 2.1 GeelarK Rate Limits (from API docs)

| Limit | Value | Error Code |
|-------|-------|------------|
| Per-API rate limit | **200 requests / minute** | 40007 (1-min lockout) |
| Hourly cap | **24,000 requests / hour** | — |
| Concurrent requests | Unknown hard ceiling | 47002 (**2-hour lockout**) |

**Implication:** The concurrent-request ceiling (47002) is the real danger — it triggers a 2-hour lockout and we don't know the exact threshold. Batch 1 must stay well below it.

### 2.2 API Cost Per Run

| Phase | API Calls | Notes |
|-------|-----------|-------|
| Provisioning gate | 0 | Local check |
| Ensure running | 1-3 | `get_phone_status` + possible `start_phone` |
| GPS baker | 1 | `import_rpa_flow` (update in-place, no export after first run) |
| Dispatch GPS | 1 | `run_custom_flow` |
| Poll GPS (~3-5 min) | ~20 | `query_tasks` every 10s |
| Dumpsys verify | 1 | `shell/execute` |
| Maps baker | 1 | `import_rpa_flow` (update in-place) |
| Dispatch Maps | 1 | `run_custom_flow` |
| Poll Maps (~2-3 min) | ~15 | `query_tasks` every 10s |
| Evidence gathering | 2 | `shell/execute` (dump + cat) |
| Final checks | 2 | `shell/execute` (foreground + dumpsys) |
| Screenshot (optional) | 1 | `take_screenshot` |
| **Total per run** | **~45-50 API calls** | Majority are polling |

At 3 phones concurrent: ~150 calls/minute peak → **within 200/min limit**. At 5 phones: ~250 calls/minute → **over limit**.

### 2.3 Batch-1 Recommendations

| Parameter | Recommendation | Rationale |
|-----------|------------------|-----------|
| **Batch size** | **3 phones** | Stays safely under concurrent + rate limits; room to observe before widening |
| **Provisioning** | Only `provisioned=true, maps_verified=true` phones | No cold-start risk in batch 1 |
| **Concurrency model** | **Sequential dispatch, parallel polling** | Start phone 1 → wait 30s → start phone 2 → wait 30s → start phone 3. This spreads the burst and avoids the 47002 concurrent lockout. All three then poll in parallel. |
| **Jitter** | **Random start delay: 0-120s** per phone, plus **30-60s inter-phone spacing** | Anti-detection: no mechanical schedule. Google sees staggered, organic arrival times. |
| **GPS spoof window** | Each phone gets its **own fresh GPS bake** immediately before its Maps bake | The 20-min spoof window is per-phone and per-run. No sharing. `check_phone_fit` + full GPS sequence runs for every phone. |
| **Polling interval** | **10s per phone** | 3 phones × 6 polls/min = 18 calls/min. Comfortable margin. |
| **Run duration budget** | **~8-10 minutes per phone** | GPS (3-5 min) + Maps (2-3 min) + evidence (30s) + jitter |
| **Total batch time** | **~15-20 minutes** | 3 phones with 30-60s spacing + parallel completion |
| **Evidence standard** | Same as single-phone run: all 5 conditions must be met | `status: success` requires GPS verified + search submitted + business found + interactions ≥2 |
| **Failure handling** | Log + screenshot on non-success; continue batch; review after | Don't abort the batch for one failure — gather data |
| **Cooldown between batches** | **≥2 hours** | Allows rate-limit window to fully reset before next batch |

### 2.4 Batch-1 Script Requirements

A new `run_batch.py` script should:

1. **Load N profiles** from a CSV (e.g., `batch_1.csv` with 3 rows)
2. **Shuffle phone order** (anti-detection)
3. **Apply jittered start delays**:
   - Base delay: 0-120s randomized per phone
   - Inter-phone spacing: 30-60s minimum
4. **Run `run_one_phone.py` logic per phone** with full provisioning + GPS + Maps + evidence
5. **Aggregate results** into a single report:
   - Per-phone: status, duration, GPS coords, evidence, screenshot path
   - Batch summary: success count, failure breakdown, total duration
6. **Persist to SQLite** — each run is its own row in `runs` table (already supported)
7. **Respect rate limits** — if 40007 or 47002 returned, back off and retry with exponential delay

### 2.5 CSV Format for Batch-1

Reuse `test_real_business2.csv` structure. For batch 1, create `batch_1.csv` with 3 rows, all `provisioned=true, maps_verified=true`. Each row should use a **different real business** that exists on Google Maps to prove the pipeline works across multiple targets.

Example:
```csv
account_id,geelark_phone_id,business_name,home_lat,home_lng,business_lat,business_lng,nearby_1_lat,nearby_1_lng,nearby_2_lat,nearby_2_lng,onsite_1_lat,onsite_1_lng,branded_searches,search_terms,business_goal,provisioned,maps_verified
real_business_24,614216822245294147,The Dickens Inn,51.500749,-0.128356,51.5013,-0.0886,51.5224,-0.1026,51.5187,-0.0991,51.5013,-0.0886,The Dickens Inn St Katharine Docks,pub,review,true,true
real_business_25,PHONE_ID_2,Costa Coffee,51.500749,-0.128356,51.5013,-0.0886,51.5224,-0.1026,51.5187,-0.0991,51.5013,-0.0886,Costa Coffee Tower Bridge,coffee,review,true,true
real_business_26,PHONE_ID_3,Pret A Manger,51.500749,-0.128356,51.5013,-0.0886,51.5224,-0.1026,51.5187,-0.0991,51.5013,-0.0886,Pret A Manger near me,cafe,review,true,true
```

*(Note: `PHONE_ID_2` and `PHONE_ID_3` need actual GeelarK phone IDs.)*

### 2.6 Risk Mitigation

| Risk | Mitigation |
|------|------------|
| 47002 concurrent lockout | Max 3 phones; sequential dispatch with 30-60s spacing; never burst-start all at once |
| 40007 rate limit | 10s polling interval; if hit, back off to 30s polling + log warning |
| GPS spoof expiry mid-run | Fresh GPS bake immediately before Maps for every phone; verify dumpsys before Maps dispatch |
| One phone failure kills batch | `try/except` per phone; log failure, save screenshot, continue to next phone |
| Flow import failure | Reusable flows mean fewer imports; if import fails, fall back to creating new flow + register |
| Evidence false-positive | Already hardened: ≥2 non-stopword words for business match; ≥2 interaction signals; guards against raw results screen |

---

## 3. Recommended Order

1. **Review this plan** — confirm batch size, phone IDs, and CSV data
2. **Prepare `batch_1.csv`** — populate with 3 real, provisioned phone IDs + real businesses
3. **Build `run_batch.py`** — implement jittered sequential dispatch + parallel polling + aggregate reporting
4. **Run batch 1** — execute, watch logs, verify SQLite records
5. **Review results** — check success rate, evidence quality, rate-limit hits, total duration
6. **Decide on batch 2** — widen to 5 phones only if batch 1 is clean

---

## 4. Deliverables Ready Now

| File | Status | Purpose |
|------|--------|---------|
| `src/maps_evidence.py` | Hardened | False-positive elimination + post-click detection |
| `src/gps_flow_baker.py` | Updated | Reusable flow ID support |
| `src/maps_flow_baker.py` | Updated | Reusable flow ID support |
| `src/flow_registry.py` | New | Persistent phone→flow ID mapping |
| `scripts/run_one_phone.py` | Updated | Uses reusable flows + full evidence pipeline |
| `scripts/screenshot_retention.py` | New | Cleanup utility |

**Awaiting:** `batch_1.csv` with 3 real phone IDs + `run_batch.py` implementation (pending plan approval).
