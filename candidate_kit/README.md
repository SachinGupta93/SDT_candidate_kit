# Employee Attendance & Analytics API

A FastAPI + MongoDB REST API for employee attendance management, analytics, and reporting.

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Set environment variables (or create a `.env` file):

```
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

MongoDB **6.0 or newer** is required (tested on 7.0). MongoDB Atlas free tier (M0) also works.

## Seed Sample Data

```bash
python sample_seed.py
```

## Run

```bash
uvicorn app.main:app --port 8000 --reload
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Readiness probe (pings MongoDB) |
| POST | `/employees` | Create an employee |
| GET | `/employees` | List employees (paginated, filterable) |
| POST | `/attendance/punch-in` | Record a punch-in |
| POST | `/attendance/punch-out` | Record a punch-out |
| GET | `/attendance` | List attendance records (paginated, filterable) |
| PATCH | `/attendance/{emp_code}/{date}` | Regularize (correct) a record |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly summary for one employee |
| GET | `/analytics/departments/summary` | Department summary for a month |
| GET | `/analytics/leaderboard/late` | Late-comers leaderboard |
| GET | `/analytics/departments/{department}/trend` | Daily attendance trend |
| GET | `/admin/explain/{endpoint}` | MongoDB explain for query plans |

## Design Decisions

See `DECISIONS.md` for index choices, concurrency handling, ranking logic, and scaling considerations.

## Bug Fixes

See `REVIEW.md` for the full list of 32 defects found and fixed in the starter code.
