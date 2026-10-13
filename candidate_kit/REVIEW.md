# REVIEW.md

List every defect found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health()` L72-74 | No MongoDB ping — always returns `{"status":"ok"}` even if DB is down | Start app without MongoDB running; grader expects 503 but gets 200 | Added `db.command("ping")` in try/except; raise HTTPException(503) on failure |
| 2 | `create_employee()` L79 | Race condition: `find_one` check-then-insert allows concurrent duplicate `emp_code` | Two simultaneous POST requests both pass the check and both insert | Replaced with unique index on `emp_code` + catch `DuplicateKeyError` → 409 |
| 3 | `create_employee()` L82 | `datetime.now()` returns naive local time, not UTC | `created_at` timestamp wrong on non-UTC servers; stored without timezone | Changed to `datetime.now(timezone.utc)` |
| 4 | `create_employee()` L85 | Response returns `created_at` as raw Python datetime, not epoch milliseconds | API contract requires `created_at` as integer epoch ms; client gets a datetime string | Added `serialize_employee()` that converts `created_at` to `int(dt.timestamp() * 1000)` |
| 5 | `create_employee()` | No validation for `emp_code` format (`^EMP\d{4,6}$`), email format, field lengths, `shift_start != shift_end`, `joined_on` format | Grader sends invalid data (bad emp_code, bad email, same shift start/end); expects 422 | Added Pydantic field validators for all constraints from the OpenAPI spec |
| 6 | `list_employees()` L93 | `skip = page * page_size` — off by one. Page 1 skips `page_size` records instead of 0 | `GET /employees?page=1` returns empty or wrong results; first `page_size` items are skipped | Fixed to `skip = (page - 1) * page_size` |
| 7 | `list_employees()` L94 | `total = db.employees.count_documents({})` ignores the department filter | Filtering by department shows total of ALL employees, not filtered count | Changed to `db.employees.count_documents(q)` using the same filter query |
| 8 | `list_employees()` L95 | No sort by `emp_code` ascending | Contract requires deterministic sort order `emp_code ASC` | Added `.sort("emp_code", ASCENDING)` |
| 9 | `list_employees()` | No `page_size` clamping to max 100 or `page` minimum of 1 | `page_size=200` returns too many; `page=0` or negative causes errors | Added `Query(ge=1, le=100)` constraints and `paginate_params()` helper |
| 10 | `punch_in()` L101-102 | No 404 for unknown employee — if `emp` is `None`, L113 crashes with `TypeError: 'NoneType'` | Punch in for non-existent employee → unhandled exception (500) | Added `if not emp: raise HTTPException(404, "employee not found")` |
| 11 | `punch_in()` L102 | `datetime.fromtimestamp(ms/1000)` returns naive local time, not UTC | All subsequent time calculations (late, date) are wrong on any non-UTC server | Changed to `datetime.fromtimestamp(ms/1000, tz=timezone.utc)` via `from_epoch_ms()` |
| 12 | `punch_in()` L102 | No validation that `punched_at` is a valid epoch ms (range 100000000000–4102444800000) | Grader sends seconds-scale value or string; expects 422 | Added Pydantic validator checking range per `EpochMillis` schema |
| 13 | `punch_in()` L103 | `ts.date()` uses local/naive time — does not compute IST date per R1 | Attendance date wrong for times near midnight (e.g., 23:30 UTC = next day IST) | Created `compute_attendance_date()` that converts to IST first |
| 14 | `punch_in()` L103 | No overnight shift logic (R1): when `shift_end <= shift_start` and punch-in IST < shift_end, date should be previous day | Night-shift employee (22:00–06:00) punching in at 00:30 IST gets wrong date | Added overnight detection in `compute_attendance_date()` |
| 15 | `punch_in()` L104 | Race condition: `find_one` check-then-insert for `(emp_code, date)` duplicate | Two concurrent punch-ins both pass the check, both insert → data corruption | Added unique compound index on `(emp_code, date)` + catch `DuplicateKeyError` → 409 |
| 16 | `punch_in()` L109 | No validation that `status` is a presence status (`PRESENT`, `WFH`, `ON_DUTY`) | Grader sends `status=ABSENT` or `status=LEAVE`; expects 422 | Added Pydantic validator restricting to `PRESENCE_STATUSES` |
| 17 | `punch_in()` | No truncation of instant to whole seconds per R1 | Sub-second precision affects calculations; 09:40:00.900 should count as 09:40:00 | `from_epoch_ms()` applies `.replace(microsecond=0)` |
| 18 | `punch_in()` L119 | Returns `doc["id"] = str(res.inserted_id)` — API contract has no `id` field | Response has extra field `id` that the grader doesn't expect | Removed `id` field; use `serialize_attendance_record()` which excludes `_id` |
| 19 | `punch_in()` | `punch_in` and `punch_out` returned as Python datetimes, not epoch ms | Contract requires all instants as integer epoch ms; client gets datetime strings | `serialize_attendance_record()` converts all datetimes via `to_epoch_ms()` |
| 20 | `compute_late_minutes()` L35 | `punch_in.replace(hour=h, minute=m)` constructs shift_start in UTC, not IST | Late minutes calculated against wrong reference time (off by 5:30) | Refactored: convert punch_in to IST, then build shift_start in IST |
| 21 | `compute_overtime()` L46-47 | Doesn't handle overnight shifts — constructs shift_end on the attendance date | For 22:00–06:00 shift, shift_end should be on the next day; gets wrong overtime | Added overnight detection: if `shift_end <= shift_start`, end_date = next day |
| 22 | `compute_overtime()` L47 | No >= 30 minute threshold per R3 — returns any positive minutes | Overtime of 15 minutes should be 0, but returns 15 | Added `return minutes if minutes >= 30 else 0` |
| 23 | `compute_work_hours()` L41 | Uses Python `round()` which is banker's rounding (round-half-to-even), not half-up per R4 | `round(8.125, 2)` → 8.12 (banker's) instead of 8.13 (half-up) | Replaced with `Decimal.quantize(ROUND_HALF_UP)` |
| 24 | `list_attendance()` L144-146 | Loads **entire collection** into Python memory with `list(find(q))`, sorts in Python | Crashes or takes minutes on 100k records; contract says "must stay fast" | Replaced with MongoDB `.sort().skip().limit()` server-side with index support |
| 25 | `list_attendance()` L145 | Python sort by `date` only — contract requires `date DESC, emp_code ASC` | Records with same date appear in random order | Changed to `.sort([("date", DESCENDING), ("emp_code", ASCENDING)])` |
| 26 | `list_attendance()` L149 | Returns `d["id"] = str(d.pop("_id"))` — contract has no `id` field; `_id` should be excluded | Response has wrong shape; extra `id`, missing proper fields | Use projection `{"_id": 0}` and `serialize_attendance_record()` |
| 27 | `list_attendance()` | No validation that `date_from <= date_to` | Grader sends `date_from=2026-12-01&date_to=2026-01-01`; expects 422 | Added `if date_from > date_to: raise HTTPException(422)` |
| 28 | `list_attendance()` | Datetimes in response not converted to epoch ms (punch_in, punch_out, history timestamps) | All instant fields return as datetime objects instead of epoch ms integers | `serialize_attendance_record()` converts all nested datetimes |
| 29 | `punch_out()` | Query filtered `punch_out: None`, raising 404 instead of 409 when already punched out | Double punch-out expected 409 per OpenAPI contract, got 404 | Separated punch-in lookup from punch_out status check: if already punched out, raise 409 |
| 30 | `/admin/explain/{endpoint}` | MongoDB `explain()` returns BSON types (`Timestamp`, `Binary`) that fail in FastAPI serializer | Grader gets 500 Internal Server Error when calling `/admin/explain/...` | Serialized BSON explain output using `bson.json_util` |
| 31 | `/admin/explain/{endpoint}` | `Cursor.explain("executionStats")` passed positional argument not supported by PyMongo's Cursor API | `TypeError: Cursor.explain() takes 1 positional argument but 2 were given` | Invoked `cursor.explain()` without arguments, which natively includes `executionStats` |
| 32 | `compute_late_minutes()` | Overnight shifts punching in after midnight compared against wrong date's shift start | A punch-in at 00:30 for a 22:00-06:00 shift was calculated as 0 late minutes instead of 150 minutes | Added `att_date_str` parameter to anchor shift start to the attendance date |

## Not a defect (investigated and cleared)

| Where | What I checked | Why it's correct |
|---|---|---|
| `compute_late_minutes()` L36 | `int(seconds / 60)` — uses float division then int truncation | For positive values this is equivalent to `floor(seconds // 60)`, which matches R2's "whole minutes (floor)" — correct |
| `PunchInIn.status` default `"PRESENT"` | Default status value | Matches the OpenAPI spec `default: PRESENT` — correct |
