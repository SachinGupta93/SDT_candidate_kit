# STATE.md — Project Memory

## Current State
- **Milestone:** v1.0
- **Active Phase:** 1 (Foundation & Infrastructure)
- **Status:** Planning complete, ready for execution

## Phase Status
| Phase | Name | Status |
|---|---|---|
| 1 | Foundation & Infrastructure | ⬜ Not started |
| 2 | Fix Existing Endpoints | ⬜ Not started |
| 3 | Punch-Out & Regularization | ⬜ Not started |
| 4 | Analytics Endpoints | ⬜ Not started |
| 5 | Admin Explain & Index Verification | ⬜ Not started |
| 6 | Documentation & Polish | ⬜ Not started |

## Decisions Log
- 2026-10-09: Project initialized with 6-phase roadmap
- 2026-10-09: Single-file constraint acknowledged — all code in app/main.py

## Key Insights
- Starter code has ~28 intentional bugs (race conditions, wrong math, naive datetimes, etc.)
- Grader uses hidden ~100k record dataset with edge cases not in samples
- MongoDB $densify required for trend endpoint (no Python loops)
- Half-up rounding (Decimal) required, not Python's round()
