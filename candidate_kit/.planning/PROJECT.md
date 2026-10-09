# PROJECT.md

## Project Identity
- **Name:** HROne Employee Attendance & Analytics API
- **Type:** Backend REST API (assignment submission)
- **Milestone:** v1.0 — Full contract implementation

## Problem Statement
Build a production-quality FastAPI + MongoDB REST API for employee attendance management. The API handles:
- Employee CRUD operations
- Attendance punch-in/punch-out with IST timezone rules
- Manual regularization (corrections) with audit trail
- Monthly analytics per employee & department
- Late-comers leaderboard with competition ranking
- Daily attendance trend with gap-filling & moving averages
- Admin explain endpoint for query plan inspection

## Technical Stack
| Layer | Technology |
|---|---|
| Framework | FastAPI >= 0.110 |
| Database | MongoDB 7.0 (via PyMongo >= 4.7) |
| Validation | Pydantic >= 2.6 |
| Server | Uvicorn >= 0.29 |
| Language | Python 3.10+ |

## Constraints
1. **Single file:** All code in `app/main.py`
2. **No auth:** No authentication/authorization
3. **IST timezone:** All shift times and dates in IST (UTC+05:30)
4. **Epoch milliseconds:** All instants in API are epoch ms integers
5. **MongoDB indexes:** Must create at startup, no COLLSCAN allowed
6. **Concurrency safety:** Duplicate operations must be handled atomically (unique indexes)
7. **Grader hidden dataset:** ~100k records with edge cases not in sample data
8. **Half-up rounding:** Not Python's default banker's rounding
9. **Stored derived values:** Analytics must read stored values, not recompute

## Key Business Rules
- **R1:** IST timezone, overnight shift date logic, truncate to whole seconds
- **R2:** Late = strictly > 10 min after shift_start; floor(minutes since shift_start)
- **R3:** Overtime = floor(minutes after shift_end) if >= 30, else 0
- **R4:** work_hours = seconds/3600, 2 decimals half-up
- **R5:** half_day = true if work_hours < 4.50
- **R6:** PRESENT/WFH/ON_DUTY = present; ABSENT/LEAVE = not present
- **R7:** Working days = Mon-Fri; mid-month joiners count from joined_on
- **R8:** 2 decimals half-up (rates: 4 decimals half-up)
- **R9:** Headcount = employees with joined_on <= period end (zero-log employees count)
- **R10:** Pagination: page starts at 1, page_size max 100, default 20

## Data Model
- **Collections:** `employees`, `attendance_logs`
- **Employee key:** `emp_code` (NOT MongoDB `_id`)
- **Attendance key:** `(emp_code, date)` compound natural key
- **Instants in DB:** BSON datetime (UTC) — converted to/from epoch ms at API boundary
- **Dates/times in DB:** strings (`YYYY-MM-DD`, `HH:MM`)

## Success Criteria
- All 12 endpoints match the OpenAPI contract exactly
- All 10 business rules (R1-R10) correctly implemented
- No COLLSCAN on the grader's ~100k dataset
- Concurrency-safe: unique indexes enforce no duplicates
- Legacy records (missing `history`/`half_day`) handled gracefully
- REVIEW.md and DECISIONS.md filled in with quality answers

## Deliverables
1. `app/main.py` — complete implementation
2. `requirements.txt` — dependencies
3. `REVIEW.md` — defect table from starter code
4. `DECISIONS.md` — design decisions (indexes, concurrency, ties, headcount, scaling)
5. `README.md` — setup instructions
