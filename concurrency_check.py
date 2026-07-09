"""Ad-hoc concurrency validation for the hard bugs. Not part of the graded
test suite -- just used to prove the fixes hold under concurrent load before
handing them off. Run with: python concurrency_check.py
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def register_and_login(org, username):
    client.post("/auth/register", json={"org_name": org, "username": username, "password": "pass123"})
    r = client.post("/auth/login", json={"org_name": org, "username": username, "password": "pass123"})
    return r.json()["access_token"]


def h(token):
    return {"Authorization": f"Bearer {token}"}


# ---- Setup ----
admin_token = register_and_login("concorg", "admin1")
room = client.post(
    "/rooms",
    json={"name": "Room A", "capacity": 4, "hourly_rate_cents": 1000},
    headers=h(admin_token),
).json()
room_id = room["id"]

member_tokens = [register_and_login("concorg", f"member{i}") for i in range(5)]

future_start = (datetime.now(timezone.utc) + timedelta(hours=10)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
future_end = (datetime.now(timezone.utc) + timedelta(hours=11)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"

# ---- Test 1: double-booking race ----
print("=== Test 1: concurrent identical bookings (same room/slot, different users) ===")
results = []
lock = threading.Lock()


def try_book(token):
    r = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": future_start, "end_time": future_end},
        headers=h(token),
    )
    with lock:
        results.append(r.status_code)


threads = [threading.Thread(target=try_book, args=(t,)) for t in member_tokens]
for t in threads:
    t.start()
for t in threads:
    t.join()

success_count = results.count(201)
conflict_count = results.count(409)
print(f"  results: {results}")
print(f"  201 (created): {success_count}, 409 (conflict): {conflict_count}")
assert success_count == 1, f"FAIL: expected exactly 1 successful booking, got {success_count}"
print("  PASS: exactly one booking succeeded, rest correctly rejected as conflicts")

# ---- Test 2: quota race ----
print("\n=== Test 2: concurrent bookings against a 3-booking quota (different slots, same user) ===")
admin2_token = register_and_login("concorg2", "admin2b")
quota_token = register_and_login("concorg2", "quotauser")
room2 = client.post(
    "/rooms",
    json={"name": "Room B", "capacity": 4, "hourly_rate_cents": 500},
    headers=h(admin2_token),
).json()
room2_id = room2["id"]

results2 = []


def try_book_quota(i):
    start = (datetime.now(timezone.utc) + timedelta(hours=2 + i)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    end = (datetime.now(timezone.utc) + timedelta(hours=3 + i)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    r = client.post(
        "/bookings",
        json={"room_id": room2_id, "start_time": start, "end_time": end},
        headers=h(quota_token),
    )
    with lock:
        results2.append(r.status_code)


threads2 = [threading.Thread(target=try_book_quota, args=(i,)) for i in range(6)]
for t in threads2:
    t.start()
for t in threads2:
    t.join()

success2 = results2.count(201)
print(f"  results: {results2}")
print(f"  201 (created): {success2} (quota limit is 3)")
assert success2 == 3, f"FAIL: expected exactly 3 successful bookings (quota=3), got {success2}"
print("  PASS: quota correctly enforced under concurrency")

# ---- Test 3: reference code uniqueness ----
print("\n=== Test 3: reference code uniqueness under concurrency ===")
admin3_token = register_and_login("concorg3", "admin3")
ref_token = register_and_login("concorg3", "refuser")
rooms3 = [
    client.post("/rooms", json={"name": f"R{i}", "capacity": 4, "hourly_rate_cents": 100}, headers=h(admin3_token)).json()
    for i in range(8)
]
codes = []


def try_book_ref(i):
    start = (datetime.now(timezone.utc) + timedelta(hours=20 + i)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    end = (datetime.now(timezone.utc) + timedelta(hours=21 + i)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    r = client.post(
        "/bookings",
        json={"room_id": rooms3[i]["id"], "start_time": start, "end_time": end},
        headers=h(ref_token),
    )
    if r.status_code == 201:
        with lock:
            codes.append(r.json()["reference_code"])


threads3 = [threading.Thread(target=try_book_ref, args=(i,)) for i in range(8)]
for t in threads3:
    t.start()
for t in threads3:
    t.join()

print(f"  codes: {codes}")
assert len(codes) == len(set(codes)), "FAIL: duplicate reference codes generated"
print(f"  PASS: all {len(codes)} reference codes unique")

# ---- Test 4: room stats consistency ----
print("\n=== Test 4: room stats consistency after concurrent creates ===")
stats_resp = client.get(f"/rooms/{rooms3[0]['id']}/stats", headers=h(ref_token)).json()
print(f"  stats for room 0: {stats_resp}")

# ---- Test 5: double-cancel race ----
print("\n=== Test 5: concurrent cancel of the same booking ===")
admin4_token = register_and_login("concorg4", "admin4")
cancel_token = register_and_login("concorg4", "canceluser")
room4 = client.post(
    "/rooms", json={"name": "R4", "capacity": 4, "hourly_rate_cents": 1000}, headers=h(admin4_token)
).json()
start4 = (datetime.now(timezone.utc) + timedelta(hours=60)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
end4 = (datetime.now(timezone.utc) + timedelta(hours=61)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
booking4 = client.post(
    "/bookings",
    json={"room_id": room4["id"], "start_time": start4, "end_time": end4},
    headers=h(cancel_token),
).json()
booking4_id = booking4["id"]

cancel_results = []


def try_cancel():
    r = client.post(f"/bookings/{booking4_id}/cancel", headers=h(cancel_token))
    with lock:
        cancel_results.append(r.status_code)


cthreads = [threading.Thread(target=try_cancel) for _ in range(5)]
for t in cthreads:
    t.start()
for t in cthreads:
    t.join()

cancel_success = cancel_results.count(200)
print(f"  results: {cancel_results}")
assert cancel_success == 1, f"FAIL: expected exactly 1 successful cancel, got {cancel_success}"
print("  PASS: exactly one cancel succeeded")

detail = client.get(f"/bookings/{booking4_id}", headers=h(cancel_token)).json()
print(f"  refunds logged: {len(detail['refunds'])}")
assert len(detail["refunds"]) == 1, f"FAIL: expected exactly 1 refund log entry, got {len(detail['refunds'])}"
print("  PASS: exactly one RefundLog entry")

# ---- Test 6: notification deadlock (liveness) ----
print("\n=== Test 6: concurrent create+cancel notifications don't deadlock ===")
admin5_token = register_and_login("concorg5", "admin5")
live_token = register_and_login("concorg5", "liveuser")
room5 = client.post(
    "/rooms", json={"name": "R5", "capacity": 4, "hourly_rate_cents": 1000}, headers=h(admin5_token)
).json()

pre_start = (datetime.now(timezone.utc) + timedelta(hours=70)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
pre_end = (datetime.now(timezone.utc) + timedelta(hours=71)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
pre_booking = client.post(
    "/bookings",
    json={"room_id": room5["id"], "start_time": pre_start, "end_time": pre_end},
    headers=h(live_token),
).json()


def cancel_it():
    client.post(f"/bookings/{pre_booking['id']}/cancel", headers=h(live_token))


def create_more(i):
    s = (datetime.now(timezone.utc) + timedelta(hours=80 + i)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    e = (datetime.now(timezone.utc) + timedelta(hours=81 + i)).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    client.post(
        "/bookings", json={"room_id": room5["id"], "start_time": s, "end_time": e}, headers=h(live_token)
    )


mix_threads = [threading.Thread(target=cancel_it)] + [
    threading.Thread(target=create_more, args=(i,)) for i in range(5)
]
start_time = time.time()
for t in mix_threads:
    t.start()
for t in mix_threads:
    t.join(timeout=15)
elapsed = time.time() - start_time
alive = [t for t in mix_threads if t.is_alive()]
print(f"  elapsed: {elapsed:.2f}s, threads still alive (hung): {len(alive)}")
assert not alive, "FAIL: service hung -- deadlock detected"
print("  PASS: no deadlock, all requests completed")

print("\nALL CONCURRENCY CHECKS PASSED")
