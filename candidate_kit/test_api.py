"""
Comprehensive end-to-end test suite for the Employee Attendance & Analytics API.
Run: python test_api.py
Requires: the API running on http://localhost:8000 with a seeded MongoDB.
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import json
import requests

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0
ERRORS = []


def assert_eq(a, b):
    assert a == b, f"{a!r} != {b!r}"


def assert_ge(a, b):
    assert a >= b, f"{a} < {b}"


def assert_in(a, b):
    assert a in b, f"{a!r} not in {b!r}"


# Clean up test artifacts from any previous runs
try:
    import os
    from pymongo import MongoClient
    from dotenv import load_dotenv

    load_dotenv()
    _client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    _db = _client[os.getenv("MONGO_DB", "attendance_db")]
    _db.employees.delete_many({"emp_code": {"$in": ["EMP9999", "EMP8888"]}})
    _db.attendance_logs.delete_many({"emp_code": {"$in": ["EMP9999", "EMP8888"]}})
except Exception:
    pass


USE_TEST_CLIENT = False
try:
    requests.get(f"{BASE}/health", timeout=1)
except Exception:
    USE_TEST_CLIENT = True
    from fastapi.testclient import TestClient
    from app.main import app
    _tc = TestClient(app)


def test(name, method, path, expected_status, body=None, check_fn=None):
    global PASS, FAIL
    url = f"{BASE}{path}"
    try:
        if USE_TEST_CLIENT:
            if method == "GET":
                r = _tc.get(path)
            elif method == "POST":
                r = _tc.post(path, json=body)
            elif method == "PATCH":
                r = _tc.patch(path, json=body)
            else:
                print(f"  ❌ Unknown method {method}")
                FAIL += 1
                return None
        else:
            if method == "GET":
                r = requests.get(url, timeout=10)
            elif method == "POST":
                r = requests.post(url, json=body, timeout=10)
            elif method == "PATCH":
                r = requests.patch(url, json=body, timeout=10)
            else:
                print(f"  ❌ Unknown method {method}")
                FAIL += 1
                return None

        if r.status_code != expected_status:
            print(f"  ❌ {name}: expected {expected_status}, got {r.status_code}")
            try:
                print(f"     Body: {r.json()}")
            except Exception:
                print(f"     Body: {r.text[:200]}")
            FAIL += 1
            ERRORS.append(f"{name}: expected {expected_status}, got {r.status_code}")
            return r

        data = None
        try:
            data = r.json()
        except Exception:
            pass

        if check_fn and data:
            try:
                check_fn(data)
                print(f"  ✅ {name}")
                PASS += 1
            except AssertionError as e:
                print(f"  ❌ {name}: assertion failed: {e}")
                FAIL += 1
                ERRORS.append(f"{name}: {e}")
        else:
            print(f"  ✅ {name}")
            PASS += 1

        return r
    except requests.exceptions.ConnectionError:
        print(f"  ❌ {name}: connection refused — is the server running?")
        FAIL += 1
        ERRORS.append(f"{name}: connection refused")
        return None
    except Exception as e:
        print(f"  ❌ {name}: exception: {e}")
        FAIL += 1
        ERRORS.append(f"{name}: {e}")
        return None


# ========================================================================== #
# TESTS
# ========================================================================== #
print("\n" + "=" * 70)
print("  EMPLOYEE ATTENDANCE API — END-TO-END TESTS")
print("=" * 70)

# --- Health ---
print("\n--- Health ---")
test("Health check", "GET", "/health", 200,
     check_fn=lambda d: assert_eq(d["status"], "ok"))

# --- Employees ---
print("\n--- Employees ---")

# List employees (seeded data)
r = test("List employees", "GET", "/employees", 200,
         check_fn=lambda d: (
             assert_eq(d["page"], 1),
             assert_ge(d["total"], 1),
         ))

# Create a new employee
test("Create employee", "POST", "/employees", 201,
     body={
         "emp_code": "EMP9999",
         "name": "Test User",
         "email": "test@example.com",
         "department": "QA",
         "shift_start": "09:00",
         "shift_end": "18:00",
         "joined_on": "2026-01-01",
     },
     check_fn=lambda d: (
         assert_eq(d["emp_code"], "EMP9999"),
         assert_eq(d["department"], "QA"),
         assert_eq(type(d["created_at"]), int),
     ))

# Duplicate employee → 409
test("Duplicate employee → 409", "POST", "/employees", 409,
     body={
         "emp_code": "EMP9999",
         "name": "Test User2",
         "email": "test2@example.com",
         "department": "QA",
         "shift_start": "09:00",
         "shift_end": "18:00",
         "joined_on": "2026-01-01",
     })

# Invalid emp_code → 422
test("Invalid emp_code → 422", "POST", "/employees", 422,
     body={
         "emp_code": "BADCODE",
         "name": "Test",
         "email": "test@example.com",
         "department": "QA",
         "joined_on": "2026-01-01",
     })

# Same shift_start and shift_end → 422
test("Same shift start/end → 422", "POST", "/employees", 422,
     body={
         "emp_code": "EMP8888",
         "name": "Test",
         "email": "test@example.com",
         "department": "QA",
         "shift_start": "09:00",
         "shift_end": "09:00",
         "joined_on": "2026-01-01",
     })

# List employees with department filter
test("List employees dept filter", "GET", "/employees?department=Engineering", 200,
     check_fn=lambda d: all(e["department"] == "Engineering" for e in d["items"]))

# Pagination
test("Pagination page_size=2", "GET", "/employees?page_size=2&page=1", 200,
     check_fn=lambda d: (
         assert_eq(len(d["items"]), 2),
         assert_eq(d["page_size"], 2),
     ))

# --- Punch-In ---
print("\n--- Punch-In ---")

# Punch in EMP9999 with explicit timestamp (2026-07-06 09:30 IST = 04:00 UTC)
# 2026-07-06T04:00:00Z = 1783310400000 ms
PUNCH_IN_TS = 1783310400000

test("Punch in EMP9999", "POST", "/attendance/punch-in", 201,
     body={"emp_code": "EMP9999", "punched_at": PUNCH_IN_TS, "status": "PRESENT"},
     check_fn=lambda d: (
         assert_eq(d["emp_code"], "EMP9999"),
         assert_eq(d["date"], "2026-07-06"),
         assert_eq(d["status"], "PRESENT"),
         assert_eq(d["punch_in"], PUNCH_IN_TS),
         assert_eq(d["punch_out"], None),
         assert_eq(d["work_hours"], None),
         assert_eq(d["late_minutes"], 30),  # shift_start=09:00, punch_in=09:30 IST → 30 min late
     ))

# Duplicate punch-in → 409
test("Duplicate punch-in → 409", "POST", "/attendance/punch-in", 409,
     body={"emp_code": "EMP9999", "punched_at": PUNCH_IN_TS})

# Unknown employee → 404
test("Punch-in unknown emp → 404", "POST", "/attendance/punch-in", 404,
     body={"emp_code": "EMP0000"})

# Invalid status → 422
test("Punch-in ABSENT → 422", "POST", "/attendance/punch-in", 422,
     body={"emp_code": "EMP9999", "status": "ABSENT"})

# --- Punch-Out ---
print("\n--- Punch-Out ---")

# Punch out EMP9999 at 18:30 IST = 13:00 UTC on 2026-07-06
# 2026-07-06T13:00:00Z = 1783342800000 ms
PUNCH_OUT_TS = 1783342800000

test("Punch out EMP9999", "POST", "/attendance/punch-out", 200,
     body={"emp_code": "EMP9999", "punched_at": PUNCH_OUT_TS},
     check_fn=lambda d: (
         assert_eq(d["emp_code"], "EMP9999"),
         assert_eq(d["punch_out"], PUNCH_OUT_TS),
         assert_in(type(d["work_hours"]), (int, float)),
         assert_eq(type(d["half_day"]), bool),
     ))

# Double punch-out → 409
test("Double punch-out → 409", "POST", "/attendance/punch-out", 409,
     body={"emp_code": "EMP9999", "punched_at": PUNCH_OUT_TS + 1000})

# Unknown emp → 404
test("Punch-out unknown emp → 404", "POST", "/attendance/punch-out", 404,
     body={"emp_code": "EMP0000"})

# --- Attendance List ---
print("\n--- Attendance List ---")

test("List all attendance", "GET", "/attendance", 200,
     check_fn=lambda d: (
         assert_ge(d["total"], 1),
         assert_eq(d["page"], 1),
     ))

test("Filter by emp_code", "GET", "/attendance?emp_code=EMP9999", 200,
     check_fn=lambda d: all(r["emp_code"] == "EMP9999" for r in d["items"]))

test("Filter by date range", "GET",
     "/attendance?date_from=2026-07-06&date_to=2026-07-06", 200,
     check_fn=lambda d: all(r["date"] == "2026-07-06" for r in d["items"]))

test("Invalid date range → 422", "GET",
     "/attendance?date_from=2026-12-01&date_to=2026-01-01", 422)

# --- Regularization ---
print("\n--- Regularization ---")

# Regularize EMP9999's record: change punch_in
NEW_PUNCH_IN = PUNCH_IN_TS - 1800000  # 30 minutes earlier = 09:00 IST
test("Regularize punch_in", "PATCH", "/attendance/EMP9999/2026-07-06", 200,
     body={
         "reason": "biometric error correction",
         "regularized_by": "hr.admin",
         "punch_in": NEW_PUNCH_IN,
     },
     check_fn=lambda d: (
         assert_eq(d["punch_in"], NEW_PUNCH_IN),
         assert_ge(len(d["history"]), 1),
     ))

# No-change → 422
test("No-change regularize → 422", "PATCH", "/attendance/EMP9999/2026-07-06", 422,
     body={
         "reason": "no change test",
         "regularized_by": "hr.admin",
         "punch_in": NEW_PUNCH_IN,
     })

# Unknown employee → 404
test("Regularize unknown emp → 404", "PATCH", "/attendance/EMP0000/2026-07-06", 404,
     body={"reason": "test reason text", "regularized_by": "admin"})

# --- Analytics: Employee Monthly ---
print("\n--- Analytics: Employee Monthly ---")

test("Employee monthly EMP0001", "GET",
     "/analytics/employees/EMP0001/monthly?month=2026-07", 200,
     check_fn=lambda d: (
         assert_eq(d["emp_code"], "EMP0001"),
         assert_eq(d["month"], "2026-07"),
         assert_eq(type(d["working_days"]), int),
         assert_in(type(d["present_days"]), (int, float)),
         assert_in(type(d["attendance_pct"]), (int, float, type(None))),
     ))

test("Monthly unknown emp → 404", "GET",
     "/analytics/employees/EMP0000/monthly?month=2026-07", 404)

# --- Analytics: Department Summary ---
print("\n--- Analytics: Department Summary ---")

test("Dept summary 2026-07", "GET",
     "/analytics/departments/summary?month=2026-07", 200,
     check_fn=lambda d: (
         assert_eq(d["month"], "2026-07"),
         assert_eq(type(d["items"]), list),
     ))

test("Dept summary with filter", "GET",
     "/analytics/departments/summary?month=2026-07&department=Engineering", 200,
     check_fn=lambda d: all(
         item["department"] == "Engineering" for item in d["items"]
     ))

# --- Analytics: Late Leaderboard ---
print("\n--- Analytics: Late Leaderboard ---")

test("Late leaderboard 2026-07", "GET",
     "/analytics/leaderboard/late?month=2026-07", 200,
     check_fn=lambda d: (
         assert_eq(d["month"], "2026-07"),
         assert_eq(type(d["items"]), list),
     ))

# --- Analytics: Department Trend ---
print("\n--- Analytics: Department Trend ---")

test("Dept trend Engineering", "GET",
     "/analytics/departments/Engineering/trend?from=2026-07-06&to=2026-07-10", 200,
     check_fn=lambda d: (
         assert_eq(d["department"], "Engineering"),
         assert_eq(len(d["items"]), 5),  # 5 days
     ))

test("Trend unknown dept → 404", "GET",
     "/analytics/departments/Nonexistent/trend?from=2026-07-06&to=2026-07-10", 404)

test("Trend bad range → 422", "GET",
     "/analytics/departments/Engineering/trend?from=2026-07-10&to=2026-07-06", 422)

test("Trend >92 days → 422", "GET",
     "/analytics/departments/Engineering/trend?from=2026-01-01&to=2026-12-31", 422)

# --- Admin: Explain ---
print("\n--- Admin: Explain ---")

test("Explain attendance_list", "GET",
     "/admin/explain/attendance_list?date_from=2026-07-01&date_to=2026-07-31", 200,
     check_fn=lambda d: (
         assert_eq(d["endpoint"], "attendance_list"),
         assert_eq(d["collection"], "attendance_logs"),
         assert_in("explain", d),
     ))

test("Explain employee_monthly", "GET",
     "/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07", 200,
     check_fn=lambda d: assert_eq(d["endpoint"], "employee_monthly"))

test("Explain department_summary", "GET",
     "/admin/explain/department_summary?month=2026-07", 200,
     check_fn=lambda d: (
         assert_eq(d["endpoint"], "department_summary"),
         assert_in("explain", d),
     ))

test("Explain late_leaderboard", "GET",
     "/admin/explain/late_leaderboard?month=2026-07", 200,
     check_fn=lambda d: (
         assert_eq(d["endpoint"], "late_leaderboard"),
         assert_in("explain", d),
     ))

test("Explain department_trend", "GET",
     "/admin/explain/department_trend?department=Engineering&from=2026-07-06&to=2026-07-10", 200,
     check_fn=lambda d: (
         assert_eq(d["endpoint"], "department_trend"),
         assert_in("explain", d),
     ))

test("Explain missing params → 422", "GET",
     "/admin/explain/employee_monthly", 422)

# ========================================================================== #
# Summary
# ========================================================================== #
print("\n" + "=" * 70)
print(f"  RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
print("=" * 70)

if ERRORS:
    print("\nFailed tests:")
    for e in ERRORS:
        print(f"  ❌ {e}")

sys.exit(1 if FAIL > 0 else 0)
