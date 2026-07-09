# Bug Report — CoWork Multi-Tenant Coworking Space Booking API

21 bugs found and fixed across easy, medium, and hard tiers. Grouped by
difficulty; each entry lists the file/line(s), what was wrong, why it broke
the spec, and how it was fixed. Fixes were verified with the repo's smoke
test (`tests/test_smoke.py`) and an additional concurrency validation script
(`concurrency_check.py`) that fires concurrent requests at the running app
to confirm the hard-tier fixes hold under load.

---

## Easy

### 1. `app/routers/bookings.py` — 5-minute grace window on booking start time
**Line:** `create_booking`, the future-start check.
**Bug:** `if start <= now - timedelta(seconds=300):` allowed `start_time` up
to 5 minutes in the past to be accepted as valid.
**Spec violated:** Rule 2 — "start_time must be strictly in the future at
request time - no grace window."
**Fix:** Changed the check to `if start <= now:`.

### 2. `app/routers/bookings.py` — wrong pagination offset/limit
**Line:** `list_bookings`.
**Bug:** `.offset(page * limit).limit(10)` — used `page * limit` instead of
`(page - 1) * limit` (page 1 skipped the first `limit` items entirely), and
hardcoded `.limit(10)` instead of honoring the caller's `limit` query param.
**Spec violated:** Rule 11 — "Sequential pages never skip or repeat items."
**Fix:** `.offset((page - 1) * limit).limit(limit)`.

### 3. `app/routers/bookings.py` — wrong sort direction
**Line:** `list_bookings`.
**Bug:** `.order_by(Booking.start_time.desc(), Booking.id.asc())` sorted
descending by start time.
**Spec violated:** Rule 11 — "sorted ascending by start time (ties by
ascending id)."
**Fix:** Changed to `.order_by(Booking.start_time.asc(), Booking.id.asc())`.

### 4. `app/routers/bookings.py` — `get_booking` overwrote `start_time`
**Line:** `get_booking`.
**Bug:** `response["start_time"] = iso_utc(booking.created_at)` clobbered the
correct `start_time` (already set by `serialize_booking`) with the booking's
`created_at` timestamp.
**Spec violated:** API contract — `GET /bookings/{id}` must return the
booking's actual `start_time`.
**Fix:** Removed the overwriting line entirely.

### 5. `app/routers/bookings.py` — wrong refund tier for <24h notice
**Line:** `cancel_booking`.
**Bug:** The `else` branch (notice < 24h) set `refund_percent = 50` instead
of `0`.
**Spec violated:** Rule 6 — "notice < 24 hours → 0% refund."
**Fix:** Changed the `else` branch to `refund_percent = 0`.

---

## Medium

### 6. `app/timeutils.py` — offset datetimes not converted to UTC
**Line:** `parse_input_datetime`.
**Bug:** `dt.replace(tzinfo=None)` stripped a UTC offset without first
converting the clock time to UTC, so e.g. `10:00+05:00` (which is `05:00
UTC`) was stored as `10:00` naive — 5 hours wrong.
**Spec violated:** Rule 1 — "Input datetimes carrying a UTC offset must be
converted to UTC before storage or comparison."
**Fix:** `dt.astimezone(timezone.utc).replace(tzinfo=None)`.

### 7. `app/auth.py` — access token lifetime 60x too long
**Line:** `create_access_token`.
**Bug:** `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)` — with
`ACCESS_TOKEN_EXPIRE_MINUTES = 15`, this produced a 54,000-second token
instead of 900 seconds.
**Spec violated:** Rule 8 — "Access tokens expire in exactly 900 seconds."
**Fix:** `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`.

### 8. `app/auth.py` — logout never actually revoked the token
**Line:** `revoke_access_token` / `get_token_payload`.
**Bug:** `revoke_access_token` stored the token's `jti` in `_revoked_tokens`,
but `get_token_payload` checked `payload.get("sub") in _revoked_tokens` —
comparing the user id against a set of token ids, which never matches.
**Spec violated:** Rule 8 — "Logout immediately invalidates the presented
access token (subsequent use → 401)."
**Fix:** Changed the check to `payload.get("jti") in _revoked_tokens`.

### 9. `app/auth.py` + `app/routers/auth.py` — refresh tokens not single-use
**Line:** `auth.py` (new `redeem_refresh_token`), `routers/auth.py` `refresh`.
**Bug:** No mechanism existed to invalidate a refresh token after use, so
the same refresh token could be replayed indefinitely.
**Spec violated:** Rule 8 — "Refresh tokens are single-use ... reuse → 401."
**Fix:** Added a `_used_refresh_tokens` set and a `redeem_refresh_token`
helper that raises 401 on replay; wired it into `POST /auth/refresh` before
issuing new tokens.

### 10. `app/routers/auth.py` — duplicate username silently succeeded
**Line:** `register`.
**Bug:** If a username already existed in the org, the endpoint returned
`200` with the existing user's info (without even checking the password)
instead of rejecting the request.
**Spec violated:** Rule 15 — "A duplicate username within the org → 409
USERNAME_TAKEN."
**Fix:** Raise `AppError(409, "USERNAME_TAKEN", ...)` instead of returning.

### 11. `app/routers/bookings.py` — missing min-duration / end>start validation
**Line:** `create_booking`.
**Bug:** `MIN_DURATION_HOURS` was defined but never checked, and there was
no explicit `end_time > start_time` check, so a 0-hour or negative-duration
booking could slip through.
**Spec violated:** Rule 2 — "Duration must be a whole number of hours,
minimum 1 ... end_time must be strictly after start_time."
**Fix:** Added `if end <= start: raise ...` and
`if duration_hours < MIN_DURATION_HOURS: raise ...`.

### 12. `app/routers/bookings.py` — back-to-back bookings rejected
**Line:** `_has_conflict`.
**Bug:** `b.start_time <= end and start <= b.end_time` used non-strict
inequalities, flagging adjacent (back-to-back) bookings as conflicts.
**Spec violated:** Rule 3 — "Back-to-back bookings are allowed."
**Fix:** Changed to strict `<`: `b.start_time < end and start < b.end_time`.

### 13. `app/routers/bookings.py` + `app/cache.py` — half-wired cache invalidation
**Line:** `create_booking` / `cancel_booking`.
**Bug:** `create_booking` invalidated only the availability cache (not the
report cache); `cancel_booking` invalidated only the report cache (not the
availability cache). Each write path left one cache stale.
**Spec violated:** Rules 12–13 — usage report and availability "must reflect
the current state immediately."
**Fix:** Both endpoints now invalidate both caches.

### 14. `app/services/refunds.py` + `app/routers/bookings.py` — refund rounding
**Line:** `log_refund`; `cancel_booking`.
**Bug:** `log_refund` computed the refund via a cents→dollars→cents float
round-trip and truncated with `int()` instead of rounding. Separately,
`cancel_booking` recomputed its own `refund_amount_cents` using `round()`
(banker's rounding) on the same inputs — the two values could disagree.
**Spec violated:** Rule 6 — "Refund amount rounds to the nearest cent,
half-cents rounding up ... the amount returned by the cancel response must
equal the amount stored in the RefundLog."
**Fix:** `log_refund` now computes `(price_cents * percent + 50) // 100` in
integer cents (correct half-up rounding, no float error) and returns the
`RefundLog` entry; `cancel_booking` uses that entry's `amount_cents`
directly instead of recomputing, guaranteeing the two values match.

### 15. `app/services/export.py` — cross-tenant data leak
**Line:** `generate_export` / `fetch_bookings_raw`.
**Bug:** When `include_all=true` and a `room_id` was given,
`fetch_bookings_raw` filtered only by `room_id` with no `org_id` check — an
admin could export another org's bookings by passing a foreign `room_id`.
**Spec violated:** Rule 9 — "A user ... may only ever read or act on data
belonging to their own organization, on every code path."
**Fix:** That branch now calls `_fetch_scoped(db, org_id, None, room_id)`,
which scopes by `org_id` via a join on `Room`.

---

## Hard (concurrency / liveness)

### 16. `app/services/ratelimit.py` — rate limit bypassable under concurrency
**Line:** `record_and_check`.
**Bug:** Read-modify-write on `_buckets[user_id]` (read → trim → sleep →
append → write) with no lock. Concurrent requests from the same user could
all read the same stale bucket and clobber each other's writes, letting the
limit be bypassed.
**Spec violated:** Rule 5 — "Must hold under concurrent requests."
**Fix:** Wrapped the whole critical section in a `threading.Lock`.

### 17. `app/services/reference.py` — duplicate reference codes possible
**Line:** `next_reference_code`.
**Bug:** Counter was read into a local variable, then slept, then
incremented and the *pre-increment* local value returned — concurrent calls
could capture the same counter value and return identical codes.
**Spec violated:** Rule 7 — "Every booking's reference code is unique,
including under concurrent creation."
**Fix:** Wrapped the read-sleep-increment-return sequence in a
`threading.Lock`.

### 18. `app/services/stats.py` — lost updates to room stats
**Line:** `record_create` / `record_cancel`.
**Bug:** Same unlocked read-modify-write pattern as #16/#17, applied to
per-room booking counts/revenue.
**Spec violated:** Rule 14 — "Room stats ... always consistent with the
bookings themselves, including after bursts of concurrent activity."
**Fix:** Added a `threading.Lock` around each function's critical section.

### 19. `app/services/notifications.py` — deadlock from reversed lock order
**Line:** `notify_created` / `notify_cancelled`.
**Bug:** `notify_created` acquired `_email_lock` then `_audit_lock`;
`notify_cancelled` acquired `_audit_lock` then `_email_lock` — a classic
lock-order inversion. A concurrent create and cancel could each hold one
lock while waiting on the other, hanging the request thread indefinitely.
**Spec violated:** Rule 16 — "no combination of concurrent valid requests
may hang the service."
**Fix:** `notify_cancelled` now acquires the locks in the same order as
`notify_created` (`_email_lock` then `_audit_lock`).

### 20. `app/routers/bookings.py` — double-booking / quota bypass under concurrency
**Line:** `create_booking`, `_has_conflict`, `_check_quota`.
**Bug:** The conflict check, quota check, and insert were three separate
unsynchronized steps; concurrent requests for the same slot (or against the
same user's quota) could each pass their checks before either had
committed, allowing double-booking or quota overrun.
**Spec violated:** Rules 3–4 — "Must hold under concurrent requests."
**Fix:** Added a `threading.Lock` (`_booking_creation_lock`) around the
conflict-check + quota-check + insert critical section. The session's
read-only transaction from the room lookup is committed just before
acquiring the lock, so the checks inside the lock start a fresh transaction
and reliably see other requests' just-committed data rather than a stale
snapshot.

### 21. `app/routers/bookings.py` — concurrent cancel could double-refund
**Line:** `cancel_booking`.
**Bug:** The "already cancelled" check and the status update were separated
by a sleep with no lock, so two concurrent cancel requests for the same
booking could both pass the check and both log a refund.
**Spec violated:** Rule 6 — "A cancelled booking has exactly one RefundLog
entry ... must hold under concurrent cancel requests for the same booking."
**Fix:** Added a `threading.Lock` (`_cancel_lock`) around the check +
refund-log + status-update critical section, with the same
commit-before-lock pattern as #20 to guarantee a fresh read inside the lock.

---

## Verification

- `tests/test_smoke.py` passes after every batch of fixes.
- `concurrency_check.py` (included in this repo, not part of the graded
  suite) fires real concurrent threads at the running app and asserts:
  - exactly one winner in a double-booking race for the same room/slot
  - quota capped at exactly 3 confirmed bookings under concurrent load
  - all reference codes issued under concurrency are unique
  - exactly one cancellation (and exactly one RefundLog entry) wins a race
    to cancel the same booking
  - a mixed load of concurrent creates and cancels completes without
    hanging (no deadlock)

  All six checks pass.
