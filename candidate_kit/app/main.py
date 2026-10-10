"""
Employee Attendance & Analytics API
Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)
"""
import os
import re
import math
import calendar
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional, List, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Startup: create indexes idempotently
# --------------------------------------------------------------------------- #
@app.on_event("startup")
def create_indexes():
    # Employees
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index("department")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)])

    # Attendance logs
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)], unique=True
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)]
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", DESCENDING)]
    )
    db.attendance_logs.create_index("date")


# --------------------------------------------------------------------------- #
# Helpers: rounding, time conversion
# --------------------------------------------------------------------------- #
EPOCH_MS_MIN = 100_000_000_000
EPOCH_MS_MAX = 4_102_444_800_000


def round_half_up(value: float, decimals: int = 2) -> float:
    """Round to `decimals` places using ROUND_HALF_UP (not banker's rounding)."""
    if value is None:
        return None
    d = Decimal(str(value))
    quantize_str = "0." + "0" * decimals
    result = d.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
    return float(result)


def to_epoch_ms(dt) -> Optional[int]:
    """Convert a datetime to epoch milliseconds, or return None."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        # Handle naive datetimes: assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


def from_epoch_ms(ms: int) -> datetime:
    """Convert epoch milliseconds to a UTC-aware datetime truncated to whole seconds."""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    # Truncate to whole seconds per R1
    dt = dt.replace(microsecond=0)
    return dt


def validate_epoch_ms(value, field_name: str = "punched_at") -> int:
    """Validate that value is a valid epoch ms integer."""
    if value is None:
        return None
    if isinstance(value, float):
        raise HTTPException(422, detail=f"{field_name} must be an integer, not a float")
    if not isinstance(value, int):
        raise HTTPException(422, detail=f"{field_name} must be an integer")
    if value < EPOCH_MS_MIN or value > EPOCH_MS_MAX:
        raise HTTPException(
            422,
            detail=f"{field_name} must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}",
        )
    return value


# --------------------------------------------------------------------------- #
# Helpers: business rules R1–R5
# --------------------------------------------------------------------------- #
def compute_attendance_date(
    punch_in_utc: datetime, shift_start: str, shift_end: str
) -> str:
    """
    R1: Compute the IST attendance date.
    - Normal: IST calendar date of punch_in.
    - Overnight shift (shift_end <= shift_start): if punch_in IST time < shift_end,
      the date is the previous calendar day.
    """
    punch_in_ist = punch_in_utc.astimezone(IST)
    ist_date = punch_in_ist.date()

    # Check for overnight shift
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    shift_end_minutes = eh * 60 + em
    shift_start_minutes = sh * 60 + sm

    if shift_end_minutes <= shift_start_minutes:
        # Overnight shift
        punch_time_minutes = punch_in_ist.hour * 60 + punch_in_ist.minute
        # If punch-in IST time is earlier than shift_end, belongs to previous day's shift
        if punch_time_minutes < shift_end_minutes:
            ist_date = ist_date - timedelta(days=1)

    return ist_date.isoformat()


def compute_late_minutes(punch_in_utc: datetime, shift_start: str) -> int:
    """
    R2: Grace period is 10 minutes.
    Late only if punch_in is STRICTLY MORE THAN 10:00 after shift_start.
    When late: floor(total minutes since shift_start).
    """
    punch_in_ist = punch_in_utc.astimezone(IST)
    sh, sm = map(int, shift_start.split(":"))
    # Build shift_start on the same IST date as punch_in
    shift_start_dt = punch_in_ist.replace(hour=sh, minute=sm, second=0, microsecond=0)

    # Handle case where punch_in is before shift_start (e.g. overnight shift wrap)
    # If the diff is hugely negative, the punch_in is before this shift_start
    diff_seconds = (punch_in_ist - shift_start_dt).total_seconds()

    if diff_seconds < 0:
        return 0

    # Grace period: strictly more than 10 minutes = more than 600 seconds
    if diff_seconds <= 600:
        return 0

    # Floor of total minutes since shift_start
    return int(diff_seconds // 60)


def compute_overtime(
    punch_out_utc: datetime,
    shift_end: str,
    attendance_date_str: str,
    shift_start: str,
) -> int:
    """
    R3: overtime_minutes = floor(minutes between shift_end and punch_out).
    Only counted if >= 30 minutes; otherwise 0.
    For overnight shifts, shift_end falls on the next calendar day.
    """
    eh, em = map(int, shift_end.split(":"))
    sh, sm = map(int, shift_start.split(":"))

    att_date = date.fromisoformat(attendance_date_str)

    # Determine which calendar day shift_end falls on
    shift_end_minutes = eh * 60 + em
    shift_start_minutes = sh * 60 + sm

    if shift_end_minutes <= shift_start_minutes:
        # Overnight shift: shift_end is on the next day
        end_date = att_date + timedelta(days=1)
    else:
        end_date = att_date

    shift_end_dt = datetime(
        end_date.year, end_date.month, end_date.day, eh, em, 0, tzinfo=IST
    )

    punch_out_ist = punch_out_utc.astimezone(IST)
    diff_seconds = (punch_out_ist - shift_end_dt).total_seconds()

    if diff_seconds < 0:
        return 0

    minutes = int(diff_seconds // 60)
    return minutes if minutes >= 30 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """
    R4: (punch_out - punch_in) in seconds / 3600,
    rounded to 2 decimals HALF-UP.
    """
    seconds = (punch_out - punch_in).total_seconds()
    raw = seconds / 3600
    return round_half_up(raw, 2)


def compute_half_day(work_hours: float) -> bool:
    """R5: half_day = true when rounded work_hours < 4.50."""
    if work_hours is None:
        return False
    return work_hours < 4.50


# --------------------------------------------------------------------------- #
# Helpers: response serialization
# --------------------------------------------------------------------------- #
def serialize_history_entry(entry: dict) -> dict:
    """Serialize a history entry: datetimes → epoch ms."""
    result = {
        "at": to_epoch_ms(entry.get("at")),
        "by": entry.get("by", ""),
        "reason": entry.get("reason", ""),
        "changes": {},
    }
    for field, change in entry.get("changes", {}).items():
        serialized_change = {}
        for key in ("from", "to"):
            val = change.get(key)
            if isinstance(val, datetime):
                serialized_change[key] = to_epoch_ms(val)
            else:
                serialized_change[key] = val
        result["changes"][field] = serialized_change
    return result


def serialize_attendance_record(doc: dict) -> dict:
    """Convert a MongoDB attendance doc to the API response format."""
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_epoch_ms(doc.get("punch_in")),
        "punch_out": to_epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": [
            serialize_history_entry(h) for h in doc.get("history", [])
        ],
    }


def serialize_employee(doc: dict) -> dict:
    """Convert a MongoDB employee doc to the API response format."""
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc.get("shift_start", "09:30"),
        "shift_end": doc.get("shift_end", "18:30"),
        "joined_on": doc["joined_on"],
        "created_at": to_epoch_ms(doc["created_at"]),
    }


# --------------------------------------------------------------------------- #
# Helpers: date/month utilities
# --------------------------------------------------------------------------- #
VALID_STATUSES = {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}
PRESENCE_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
EMP_CODE_RE = re.compile(r"^EMP\d{4,6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def parse_month(month_str: str):
    """Parse 'YYYY-MM' into (first_date, last_date) of that month."""
    if not MONTH_RE.match(month_str):
        raise HTTPException(422, detail="month must be YYYY-MM format")
    year, mon = map(int, month_str.split("-"))
    first = date(year, mon, 1)
    last_day = calendar.monthrange(year, mon)[1]
    last = date(year, mon, last_day)
    return first, last


def count_working_days(start: date, end: date) -> int:
    """Count Mon-Fri days between start and end inclusive."""
    if start > end:
        return 0
    count = 0
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            count += 1
        d += timedelta(days=1)
    return count


def is_working_day(d: date) -> bool:
    return d.weekday() < 5


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, v):
        if not EMP_CODE_RE.match(v):
            raise ValueError("emp_code must match EMP followed by 4-6 digits")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or len(v) > 100:
            raise ValueError("name must be 1-100 characters")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if len(v) > 120 or not EMAIL_RE.match(v):
            raise ValueError("invalid email format")
        return v

    @field_validator("department")
    @classmethod
    def validate_department(cls, v):
        if not v or len(v) > 50:
            raise ValueError("department must be 1-50 characters")
        return v

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_time_format(cls, v):
        if not TIME_RE.match(v):
            raise ValueError("must be HH:MM (24-hour format)")
        return v

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, v):
        if not DATE_RE.match(v):
            raise ValueError("joined_on must be YYYY-MM-DD")
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("joined_on must be a valid date")
        return v

    @model_validator(mode="after")
    def validate_shift_differs(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in PRESENCE_STATUSES:
            raise ValueError(f"status must be one of {PRESENCE_STATUSES}")
        return v

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, v):
        if v is not None:
            if not isinstance(v, int):
                raise ValueError("punched_at must be an integer")
            if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
                raise ValueError(
                    f"punched_at must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}"
                )
        return v


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, v):
        if v is not None:
            if not isinstance(v, int):
                raise ValueError("punched_at must be an integer")
            if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
                raise ValueError(
                    f"punched_at must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}"
                )
        return v


class RegularizeIn(BaseModel):
    reason: str
    regularized_by: str
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v):
        if len(v) < 5 or len(v) > 200:
            raise ValueError("reason must be 5-200 characters")
        return v

    @field_validator("regularized_by")
    @classmethod
    def validate_regularized_by(cls, v):
        if len(v) < 1 or len(v) > 50:
            raise ValueError("regularized_by must be 1-50 characters")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        return v

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_epoch(cls, v):
        if v is not None:
            if not isinstance(v, int):
                raise ValueError("must be an integer (epoch ms)")
            if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
                raise ValueError(
                    f"must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}"
                )
        return v


# --------------------------------------------------------------------------- #
# Pagination helper
# --------------------------------------------------------------------------- #
def paginate_params(page: int, page_size: int):
    """Clamp and validate pagination per R10."""
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    if page_size > 100:
        page_size = 100
    skip = (page - 1) * page_size
    return page, page_size, skip


# --------------------------------------------------------------------------- #
# Endpoints: System
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, detail="MongoDB is not available")


# --------------------------------------------------------------------------- #
# Endpoints: Employees
# --------------------------------------------------------------------------- #
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, detail="emp_code already exists")
    doc.pop("_id", None)
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    page, page_size, skip = paginate_params(page, page_size)
    q = {}
    if department:
        q["department"] = department
    total = db.employees.count_documents(q)
    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )
    return {
        "items": [serialize_employee(doc) for doc in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------------------------------- #
# Endpoints: Attendance
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, detail="employee not found")

    if body.punched_at is not None:
        ts = from_epoch_ms(body.punched_at)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    # R1: compute attendance date
    att_date = compute_attendance_date(ts, emp["shift_start"], emp["shift_end"])

    doc = {
        "emp_code": body.emp_code,
        "date": att_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, detail="already punched in for this date")
    doc.pop("_id", None)
    return serialize_attendance_record(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, detail="employee not found")

    if body.punched_at is not None:
        ts = from_epoch_ms(body.punched_at)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    # Find the most recent open record where punch_in <= punched_at
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
            "punch_out": None,
        },
        sort=[("punch_in", DESCENDING)],
    )

    if not record:
        raise HTTPException(404, detail="no open punch-in record found")

    punch_in_dt = record["punch_in"]
    if punch_in_dt.tzinfo is None:
        punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)

    # Validate: punched_at must be after punch_in
    if ts <= punch_in_dt:
        raise HTTPException(
            422, detail="punch-out time must be after punch-in time"
        )

    # Validate: within 24 hours
    if (ts - punch_in_dt).total_seconds() > 86400:
        raise HTTPException(
            422, detail="punch-out must be within 24 hours of punch-in"
        )

    # Compute derived fields
    wh = compute_work_hours(punch_in_dt, ts)
    ot = compute_overtime(ts, emp["shift_end"], record["date"], emp["shift_start"])
    hd = compute_half_day(wh)

    # Atomic update: only update if punch_out is still null (concurrency)
    result = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": wh,
                "overtime_minutes": ot,
                "half_day": hd,
            }
        },
        return_document=True,
    )

    if result is None:
        raise HTTPException(409, detail="already punched out (concurrent request)")

    return serialize_attendance_record(result)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    page, page_size, skip = paginate_params(page, page_size)

    # Validate date range
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, detail="date_from must be <= date_to")

    if status and status not in VALID_STATUSES:
        raise HTTPException(422, detail=f"status must be one of {VALID_STATUSES}")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)
    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": [serialize_attendance_record(d) for d in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------------------------------- #
# Endpoints: Regularization
# --------------------------------------------------------------------------- #
@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    # Validate employee exists
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, detail="employee not found")

    # Validate date format
    if not DATE_RE.match(date):
        raise HTTPException(422, detail="date must be YYYY-MM-DD")

    # Get existing record
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, detail="no attendance record for this date")

    # Determine the target status
    new_status = body.status if body.status is not None else record["status"]

    # Validation: ABSENT/LEAVE + punch times → 422
    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                422,
                detail="cannot supply punch_in/punch_out with ABSENT or LEAVE status",
            )

    # Determine new punch_in and punch_out
    old_punch_in = record.get("punch_in")
    old_punch_out = record.get("punch_out")

    if new_status in ("ABSENT", "LEAVE"):
        new_punch_in = None
        new_punch_out = None
    else:
        # Presence status
        if body.punch_in is not None:
            new_punch_in = from_epoch_ms(body.punch_in)
        else:
            new_punch_in = old_punch_in

        if body.punch_out is not None:
            new_punch_out = from_epoch_ms(body.punch_out)
        else:
            new_punch_out = old_punch_out

        # Presence status requires punch_in
        if new_punch_in is None:
            raise HTTPException(
                422, detail="presence status requires a punch_in"
            )

        # Ensure punch_in is timezone-aware
        if new_punch_in.tzinfo is None:
            new_punch_in = new_punch_in.replace(tzinfo=timezone.utc)

        # punch_in must stay on the record's attendance date (R1)
        computed_date = compute_attendance_date(
            new_punch_in, emp["shift_start"], emp["shift_end"]
        )
        if computed_date != date:
            raise HTTPException(
                422,
                detail="punch_in does not fall on this record's attendance date",
            )

        # punch_out validations
        if new_punch_out is not None:
            if new_punch_out.tzinfo is None:
                new_punch_out = new_punch_out.replace(tzinfo=timezone.utc)
            if new_punch_out <= new_punch_in:
                raise HTTPException(
                    422, detail="punch_out must be after punch_in"
                )
            if (new_punch_out - new_punch_in).total_seconds() > 86400:
                raise HTTPException(
                    422, detail="punch_out must be within 24 hours of punch_in"
                )

    # Compute new derived fields
    if new_status in ("ABSENT", "LEAVE"):
        new_late = 0
        new_work_hours = None
        new_overtime = 0
        new_half_day = False
    elif new_punch_out is not None:
        pi = new_punch_in
        if pi.tzinfo is None:
            pi = pi.replace(tzinfo=timezone.utc)
        new_late = compute_late_minutes(pi, emp["shift_start"])
        new_work_hours = compute_work_hours(pi, new_punch_out)
        new_overtime = compute_overtime(
            new_punch_out, emp["shift_end"], date, emp["shift_start"]
        )
        new_half_day = compute_half_day(new_work_hours)
    else:
        pi = new_punch_in
        if pi.tzinfo is None:
            pi = pi.replace(tzinfo=timezone.utc)
        new_late = compute_late_minutes(pi, emp["shift_start"])
        new_work_hours = None
        new_overtime = 0
        new_half_day = False

    # Build the update document
    update_fields = {
        "status": new_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "work_hours": new_work_hours,
        "late_minutes": new_late,
        "overtime_minutes": new_overtime,
        "half_day": new_half_day,
    }

    # Determine what actually changed for history
    changes = {}
    old_values = {
        "status": record.get("status"),
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    for field, new_val in update_fields.items():
        old_val = old_values[field]
        # Compare datetimes properly
        if isinstance(old_val, datetime) and isinstance(new_val, datetime):
            ov = old_val.replace(tzinfo=timezone.utc) if old_val.tzinfo is None else old_val
            nv = new_val.replace(tzinfo=timezone.utc) if new_val.tzinfo is None else new_val
            if ov != nv:
                changes[field] = {"from": old_val, "to": new_val}
        elif old_val != new_val:
            # Handle None vs missing
            if old_val is None and new_val is None:
                continue
            changes[field] = {"from": old_val, "to": new_val}

    if not changes:
        raise HTTPException(422, detail="no changes to apply")

    # Build history entry
    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Atomic update with concurrency protection
    old_history_len = len(record.get("history", []))
    result = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            f"history.{old_history_len}": {"$exists": False},
        },
        {
            "$set": update_fields,
            "$push": {"history": history_entry},
        },
        return_document=True,
    )

    if result is None:
        raise HTTPException(
            409, detail="concurrent modification detected"
        )

    return serialize_attendance_record(result)


# --------------------------------------------------------------------------- #
# Endpoints: Analytics — Employee Monthly
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, detail="employee not found")

    first_day, last_day = parse_month(month)
    joined = date.fromisoformat(emp["joined_on"])

    # R7: Working days from max(month_start, joined_on) to month_end
    effective_start = max(first_day, joined)
    working_days = count_working_days(effective_start, last_day)

    # Query attendance logs for this month
    logs = list(
        db.attendance_logs.find(
            {
                "emp_code": emp_code,
                "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()},
            }
        )
    )

    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for log in logs:
        log_date = date.fromisoformat(log["date"])
        status = log["status"]
        is_wd = is_working_day(log_date)

        if status in PRESENCE_STATUSES:
            # present_days only counts Mon-Fri records
            if is_wd:
                if log.get("half_day", False):
                    present_days += 0.5
                else:
                    present_days += 1.0

        if status == "LEAVE":
            leave_days += 1

        # Late and overtime count on any day (weekend too, per R7)
        lm = log.get("late_minutes", 0)
        if lm > 0:
            late_count += 1
            total_late_minutes += lm

        total_overtime_minutes += log.get("overtime_minutes", 0)

    # R8: attendance_pct
    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = round_half_up(present_days / working_days * 100, 2)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


# --------------------------------------------------------------------------- #
# Endpoints: Analytics — Department Summary
# --------------------------------------------------------------------------- #
@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    first_day, last_day = parse_month(month)

    # Get all employees for headcount (R9)
    emp_query = {"joined_on": {"$lte": last_day.isoformat()}}
    if department:
        emp_query["department"] = department

    employees = list(db.employees.find(emp_query, {"_id": 0}))

    # Group by department
    dept_employees = {}
    for emp in employees:
        dept = emp["department"]
        if dept not in dept_employees:
            dept_employees[dept] = []
        dept_employees[dept].append(emp)

    if not dept_employees:
        return {"month": month, "items": []}

    # Get all attendance logs for the month
    log_query = {
        "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()},
    }
    if department:
        # Get emp_codes in this department
        emp_codes = [e["emp_code"] for e in employees]
        log_query["emp_code"] = {"$in": emp_codes}

    logs = list(db.attendance_logs.find(log_query, {"_id": 0}))

    # Build emp_code → department mapping
    emp_dept_map = {}
    for emp in employees:
        emp_dept_map[emp["emp_code"]] = emp["department"]

    # Aggregate by department
    dept_stats = {}
    for dept in dept_employees:
        dept_stats[dept] = {
            "headcount": len(dept_employees[dept]),
            "present_days": 0.0,
            "work_hours_sum": 0.0,
            "work_hours_count": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "leave_count": 0,
            "on_duty_count": 0,
        }

    for log in logs:
        dept = emp_dept_map.get(log["emp_code"])
        if dept is None or dept not in dept_stats:
            continue

        status = log["status"]
        stats = dept_stats[dept]

        if status in PRESENCE_STATUSES:
            if log.get("half_day", False):
                stats["present_days"] += 0.5
            else:
                stats["present_days"] += 1.0

            if log.get("work_hours") is not None:
                stats["work_hours_sum"] += log["work_hours"]
                stats["work_hours_count"] += 1

        if status == "LEAVE":
            stats["leave_count"] += 1
        if status == "ON_DUTY":
            stats["on_duty_count"] += 1

        lm = log.get("late_minutes", 0)
        if lm > 0:
            stats["late_count"] += 1
            stats["total_late_minutes"] += lm

    items = []
    for dept in sorted(dept_stats.keys()):
        s = dept_stats[dept]
        if s["headcount"] == 0:
            continue
        avg_wh = (
            round_half_up(s["work_hours_sum"] / s["work_hours_count"], 2)
            if s["work_hours_count"] > 0
            else None
        )
        items.append(
            {
                "department": dept,
                "headcount": s["headcount"],
                "present_days": s["present_days"],
                "avg_work_hours": avg_wh,
                "late_count": s["late_count"],
                "total_late_minutes": s["total_late_minutes"],
                "leave_count": s["leave_count"],
                "on_duty_count": s["on_duty_count"],
            }
        )

    return {"month": month, "items": items}


# --------------------------------------------------------------------------- #
# Endpoints: Analytics — Late Leaderboard
# --------------------------------------------------------------------------- #
@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    first_day, last_day = parse_month(month)

    # Build aggregation pipeline
    pipeline = [
        # Match records in the month with late_minutes > 0
        {
            "$match": {
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        # Group by emp_code
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        # Join with employees
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp_info",
            }
        },
        # Unwind (ignore logs with no matching employee)
        {"$unwind": "$emp_info"},
    ]

    # Department filter
    if department:
        pipeline.append({"$match": {"emp_info.department": department}})

    # Sort
    pipeline.append(
        {"$sort": {"total_late_minutes": -1, "_id": 1}}
    )

    results = list(db.attendance_logs.aggregate(pipeline))

    # Competition ranking
    items = []
    prev_minutes = None
    rank = 0
    count = 0

    for r in results:
        count += 1
        if r["total_late_minutes"] != prev_minutes:
            rank = count
            prev_minutes = r["total_late_minutes"]

        if rank > limit:
            break

        items.append(
            {
                "rank": rank,
                "emp_code": r["_id"],
                "name": r["emp_info"]["name"],
                "department": r["emp_info"]["department"],
                "total_late_minutes": r["total_late_minutes"],
                "late_count": r["late_count"],
            }
        )

    return {"month": month, "items": items}


# --------------------------------------------------------------------------- #
# Endpoints: Analytics — Department Trend
# --------------------------------------------------------------------------- #
@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    # Validate department exists
    dept_emp_count = db.employees.count_documents({"department": department})
    if dept_emp_count == 0:
        raise HTTPException(404, detail="department not found")

    # Validate dates
    if not DATE_RE.match(from_date) or not DATE_RE.match(to_date):
        raise HTTPException(422, detail="from and to must be YYYY-MM-DD")

    try:
        d_from = date.fromisoformat(from_date)
        d_to = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(422, detail="invalid date format")

    if d_to < d_from:
        raise HTTPException(422, detail="to must be >= from")

    if (d_to - d_from).days > 92:
        raise HTTPException(422, detail="date range must not exceed 92 days")

    # Get all employees in this department with their joined_on dates
    dept_employees = list(
        db.employees.find(
            {"department": department},
            {"_id": 0, "emp_code": 1, "joined_on": 1},
        )
    )
    emp_codes = [e["emp_code"] for e in dept_employees]

    # Get attendance logs for these employees in the date range
    logs = list(
        db.attendance_logs.find(
            {
                "emp_code": {"$in": emp_codes},
                "date": {
                    "$gte": from_date,
                    "$lte": to_date,
                },
            },
            {"_id": 0},
        )
    )

    # Group logs by date
    logs_by_date = {}
    for log in logs:
        d = log["date"]
        if d not in logs_by_date:
            logs_by_date[d] = []
        logs_by_date[d].append(log)

    # Build day-by-day results
    items = []
    current = d_from
    while current <= d_to:
        date_str = current.isoformat()
        wd = is_working_day(current)

        # R9: headcount = employees with joined_on <= this day
        headcount = sum(
            1 for e in dept_employees if e["joined_on"] <= date_str
        )

        # Get logs for this day
        day_logs = logs_by_date.get(date_str, [])

        present_count = 0.0
        late_count = 0
        for log in day_logs:
            if log["status"] in PRESENCE_STATUSES:
                if log.get("half_day", False):
                    present_count += 0.5
                else:
                    present_count += 1.0
            if log.get("late_minutes", 0) > 0:
                late_count += 1

        # attendance_rate: only on working days with headcount > 0
        if wd and headcount > 0:
            attendance_rate = round_half_up(present_count / headcount, 4)
        else:
            attendance_rate = None

        items.append(
            {
                "date": date_str,
                "is_working_day": wd,
                "headcount": headcount,
                "present_count": present_count,
                "late_count": late_count,
                "attendance_rate": attendance_rate,
                "moving_avg_7d": None,  # will compute below
            }
        )
        current += timedelta(days=1)

    # Compute moving_avg_7d
    for i, item in enumerate(items):
        # Window: current row + up to 6 preceding rows within the range
        start_idx = max(0, i - 6)
        window_rates = [
            items[j]["attendance_rate"]
            for j in range(start_idx, i + 1)
            if items[j]["attendance_rate"] is not None
        ]
        if window_rates:
            item["moving_avg_7d"] = round_half_up(
                sum(window_rates) / len(window_rates), 4
            )
        else:
            item["moving_avg_7d"] = None

    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Endpoints: Admin — Explain
# --------------------------------------------------------------------------- #
VALID_EXPLAIN_ENDPOINTS = {
    "attendance_list",
    "employee_monthly",
    "department_summary",
    "late_leaderboard",
    "department_trend",
}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint not in VALID_EXPLAIN_ENDPOINTS:
        raise HTTPException(422, detail=f"endpoint must be one of {VALID_EXPLAIN_ENDPOINTS}")

    page, page_size, skip = paginate_params(page, page_size)

    if endpoint == "attendance_list":
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status

        explain = db.attendance_logs.find(q).sort(
            [("date", DESCENDING), ("emp_code", ASCENDING)]
        ).skip(skip).limit(page_size).explain()

        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, detail="emp_code and month are required")
        first_day, last_day = parse_month(month)
        q = {
            "emp_code": emp_code,
            "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()},
        }
        explain = db.attendance_logs.find(q).explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, detail="month is required")
        first_day, last_day = parse_month(month)
        q = {
            "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()},
        }
        explain = db.attendance_logs.find(q).explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, detail="month is required")
        first_day, last_day = parse_month(month)
        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": first_day.isoformat(),
                        "$lte": last_day.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {"$group": {"_id": "$emp_code", "total": {"$sum": "$late_minutes"}}},
        ]
        explain = db.command(
            "aggregate",
            "attendance_logs",
            pipeline=pipeline,
            explain=True,
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                422, detail="department, from, and to are required"
            )
        emp_codes = [
            e["emp_code"]
            for e in db.employees.find(
                {"department": department}, {"emp_code": 1, "_id": 0}
            )
        ]
        q = {
            "emp_code": {"$in": emp_codes},
            "date": {"$gte": from_date, "$lte": to_date},
        }
        explain = db.attendance_logs.find(q).explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}
