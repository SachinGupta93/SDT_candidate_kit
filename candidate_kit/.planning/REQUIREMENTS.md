# REQUIREMENTS.md

## Source Documents
- `PROBLEM_STATEMENT.docx` — assignment brief
- `openapi.yaml` — API contract & business rules R1-R10
- `DATA_MODEL.md` — MongoDB schema & sample documents

## Functional Requirements

### FR-1: System Health
- `GET /health` returns `{"status": "ok"}` only when MongoDB responds to ping
- Returns 503 when MongoDB is unreachable

### FR-2: Employee Management
- `POST /employees` — create with `emp_code` (unique, format `EMP\d{4,6}`)
- `GET /employees` — paginated list, optional department filter, sorted by `emp_code` asc
- Concurrency: duplicate `emp_code` creates → exactly one 201, rest 409
- `created_at` set by server, returned as epoch ms

### FR-3: Punch-In
- `POST /attendance/punch-in` — create day's record
- Computes `date` (R1), `late_minutes` (R2)
- Defaults: `punched_at` = now, `status` = PRESENT
- Only `PRESENT`, `WFH`, `ON_DUTY` accepted
- Concurrency: duplicate punch-in for same `(emp_code, date)` → one 201, rest 409
- Errors: 404 unknown employee, 409 already punched in, 422 invalid

### FR-4: Punch-Out
- `POST /attendance/punch-out` — close most recent open record
- Finds record where `punch_in <= punched_at` and `punch_out is null`
- Computes `work_hours` (R4), `overtime_minutes` (R3), `half_day` (R5)
- Validation: `punched_at > punch_in`, within 24 hours
- Concurrency: duplicate punch-out → one 200, rest 409
- Errors: 404 no employee or no open record, 409 already punched out, 422 invalid

### FR-5: Attendance List
- `GET /attendance` — paginated, sorted by `date desc, emp_code asc`
- Filters: `emp_code`, `date_from`, `date_to` (inclusive), `status`
- Must use MongoDB sort/skip/limit (no in-memory loading)
- `date_from > date_to` → 422

### FR-6: Regularization (Manual Correction)
- `PATCH /attendance/{emp_code}/{date}` — correct a day's record
- Can change: `status`, `punch_in`, `punch_out`
- Recomputes all derived fields (R2-R5) after change
- Appends one `history` entry with `{at, by, reason, changes}` — only fields that actually changed
- `ABSENT`/`LEAVE` + punch times → 422; presence needs punch_in; punch_in must stay on same date (R1)
- punch_out > punch_in & within 24h; no-change request → 422
- Concurrency: don't silently lose history entries

### FR-7: Employee Monthly Analytics
- `GET /analytics/employees/{emp_code}/monthly`
- `working_days` = Mon-Fri from max(month_start, joined_on) to month_end (R7)
- `present_days` = presence-status records on Mon-Fri; half_day = 0.5 (R5-R7)
- `leave_days`, `late_count`, `total_late_minutes`, `total_overtime_minutes`
- `attendance_pct` = present_days/working_days × 100, 2 decimals half-up (R8), null if working_days=0
- 404 for unknown employee

### FR-8: Department Summary
- `GET /analytics/departments/summary`
- One row per department, sorted by `department` asc
- `headcount` per R9 (zero-log employees count, later joiners don't)
- `present_days`, `avg_work_hours` (mean of work_hours where present & not null), `late_count`, `total_late_minutes`, `leave_count`, `on_duty_count`
- Omit departments with headcount 0; optional department filter

### FR-9: Late Leaderboard
- `GET /analytics/leaderboard/late`
- Ranks by total `late_minutes` desc (only employees with total > 0)
- **Competition ranking**: ties share rank, next skipped (1, 2, 2, 4)
- `limit` applies after ranking: return all rows where `rank <= limit`
- Optional department filter; sort: late_minutes desc, emp_code asc

### FR-10: Department Trend
- `GET /analytics/departments/{department}/trend`
- One row per calendar day from `from` to `to` inclusive
- Weekends and empty days included (use `$densify`)
- `is_working_day`, `headcount` (R9 per day), `present_count` (half_day=0.5), `late_count`
- `attendance_rate` = present_count/headcount on working days with headcount > 0; else null. 4 decimals half-up.
- `moving_avg_7d` = mean of non-null rates in 7-row window within requested range. 4 decimals half-up.
- Errors: 404 unknown dept, 422 if to < from or range > 92 days

### FR-11: Admin Explain
- `GET /admin/explain/{endpoint}` — MongoDB explain with executionStats
- Endpoints: attendance_list, employee_monthly, department_summary, late_leaderboard, department_trend
- Grader checks for IXSCAN, no COLLSCAN

## Non-Functional Requirements

### NFR-1: Performance
- Must handle ~100k attendance records efficiently
- All queries must use indexes (IXSCAN)
- No in-memory collection loading

### NFR-2: Concurrency
- Unique indexes for atomic duplicate prevention
- No check-then-insert patterns

### NFR-3: Data Integrity
- Epoch ms validation (range 100000000000 - 4102444800000)
- All derived fields computed by API, never client-supplied
- History entries append-only, never edited/removed

### NFR-4: Compatibility
- Handle legacy records missing `history` and `half_day` fields
- MongoDB 6.0+ compatibility (grader uses 7.0)
