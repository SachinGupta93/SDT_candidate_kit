# ROADMAP.md — v1.0

## Milestone: v1.0 — Full API Implementation

### Phase 1: Foundation & Infrastructure ⬜
**Goal:** Build all shared helpers, indexes, models, and serializers that every endpoint depends on.
**Scope:**
- MongoDB startup indexes (employees unique emp_code, attendance_logs unique emp_code+date, plus analytics indexes)
- IST timezone helpers, epoch ms conversion, truncate-to-seconds
- Decimal half-up rounding helper
- R1 attendance date computation (including overnight shifts)
- R2 late_minutes, R3 overtime, R4 work_hours, R5 half_day computation helpers
- Pydantic request models with full validation (emp_code regex, email regex, epoch ms range, status enum, field lengths)
- Response serialization helpers (datetime→epoch ms, _id exclusion, legacy field defaults)
**UAT:**
- [ ] All helper functions produce correct results for sample data edge cases
- [ ] Pydantic models reject invalid inputs with 422
- [ ] Serializers handle legacy documents (missing history/half_day)

### Phase 2: Fix Existing Endpoints ⬜
**Goal:** Fix all ~28 bugs in the 5 starter endpoints to match the OpenAPI contract.
**Scope:**
- Fix `GET /health` — add MongoDB ping, return 503 on failure
- Fix `POST /employees` — unique index, DuplicateKeyError→409, UTC created_at, validations, epoch ms response
- Fix `GET /employees` — pagination (skip formula), total with filter, sort by emp_code, page_size clamp
- Fix `POST /attendance/punch-in` — 404 check, UTC datetimes, epoch ms validation, IST date calc, overnight R1, late_minutes IST fix, concurrency via unique index, response serialization
- Fix `GET /attendance` — MongoDB sort/skip/limit (no in-memory), correct sort order, serialization, date range validation
**UAT:**
- [ ] /health returns 503 when DB is down, 200 when up
- [ ] POST /employees rejects duplicates with 409 (concurrent safe)
- [ ] GET /employees pagination page=1 returns first records
- [ ] Punch-in computes correct IST date for overnight shifts
- [ ] GET /attendance handles 100k records without loading all in memory

### Phase 3: Punch-Out & Regularization ⬜
**Goal:** Build the two write endpoints that modify attendance records.
**Scope:**
- `POST /attendance/punch-out` — find open record, validate, compute R3/R4/R5, atomic update, concurrency
- `PATCH /attendance/{emp_code}/{date}` — validation rules, status transitions, derived field recompute, history append, concurrency safety
**UAT:**
- [ ] Punch-out computes correct work_hours, overtime, half_day
- [ ] Punch-out handles overnight shifts correctly
- [ ] Regularization appends history with only changed fields
- [ ] Setting ABSENT/LEAVE clears punch times
- [ ] No-change regularization returns 422
- [ ] Concurrent regularizations don't lose history

### Phase 4: Analytics Endpoints ⬜
**Goal:** Build all 4 analytics endpoints using MongoDB aggregation pipelines.
**Scope:**
- `GET /analytics/employees/{emp_code}/monthly` — working_days, present_days (half_day=0.5), leave_days, late stats, attendance_pct
- `GET /analytics/departments/summary` — cross-collection, headcount R9, avg_work_hours, late/leave/on_duty counts
- `GET /analytics/leaderboard/late` — competition ranking, limit after ranking, ties handling
- `GET /analytics/departments/{department}/trend` — $densify gap-fill, is_working_day, headcount per day, attendance_rate, moving_avg_7d
**UAT:**
- [ ] Employee monthly correctly handles mid-month joiner (R7)
- [ ] Department summary includes zero-log employees in headcount (R9)
- [ ] Leaderboard uses competition ranking (1,2,2,4)
- [ ] Leaderboard returns >limit rows when ties exist at cutoff
- [ ] Trend includes weekends and empty days
- [ ] Trend moving_avg_7d uses only rows within requested range
- [ ] All analytics use half-up rounding (R8)

### Phase 5: Admin Explain & Index Verification ⬜
**Goal:** Build the explain endpoint and verify no COLLSCAN on any query path.
**Scope:**
- `GET /admin/explain/{endpoint}` — build same query as each endpoint, run with explain("executionStats"), return raw output
- Verify all 5 explained endpoints show IXSCAN, no COLLSCAN
- Tune indexes if any COLLSCAN found
**UAT:**
- [ ] Explain endpoint accepts all required params for each sub-endpoint
- [ ] All explained queries show IXSCAN
- [ ] No COLLSCAN on ~100k record dataset

### Phase 6: Documentation & Polish ⬜
**Goal:** Fill in REVIEW.md and DECISIONS.md, final testing, cleanup.
**Scope:**
- REVIEW.md — document all bugs found in starter code with table
- DECISIONS.md — answer all 5 design questions
- Final end-to-end testing with sample data
- Edge case verification (overnight shifts, ties, zero-log employees, legacy records)
- README.md update if needed
**UAT:**
- [ ] REVIEW.md has complete defect table
- [ ] DECISIONS.md answers all 5 questions with clear rationale
- [ ] All 12 endpoints pass with sample data
- [ ] Response shapes match OpenAPI contract exactly
