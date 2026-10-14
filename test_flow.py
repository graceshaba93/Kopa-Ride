"""End-to-end test of the booking flow. Run:  python3 test_flow.py

Starts the server on a temporary database, seeds it, and checks the rules that matter:
no overbooking (even with 10 people grabbing the last seat at once), cancellations restore seats,
and corpers cannot use operator features.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"
tmp = tempfile.mkdtemp()
ENV = {**os.environ, "DB_PATH": os.path.join(tmp, "test.db"), "PORT": str(PORT)}


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def raw(path):
    """Fetch a non-API URL. Returns (status, content type, body text)."""
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode()


passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


def login(email):
    s, d = call("POST", "/api/login", {"email": email, "password": "demo1234"})
    assert s == 200, d
    return d["token"]


def seats_left(trip_id, token):
    _, d = call("GET", "/api/trips", token=token)
    return next(t["seats_left"] for t in d["trips"] if t["id"] == trip_id)


def main():
    subprocess.run([sys.executable, os.path.join(HERE, "seed.py")], env=ENV, check=True, stdout=subprocess.DEVNULL)
    server = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py")], env=ENV, stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/meta", timeout=1)
                break
            except Exception:
                time.sleep(0.1)

        print("Pages")
        s, ct, body = raw("/")
        check("/ serves the landing page", s == 200 and "html" in ct and 'href="/app"' in body)
        s, ct, body = raw("/app")
        check("/app serves the booking app", s == 200 and 'id="auth"' in body and "/js/app.js" in body)
        s, ct, body = raw("/css/app.css")
        check("CSS is served as CSS", s == 200 and "css" in ct and ".card" in body)
        s, ct, body = raw("/css/landing.css")
        check("landing CSS is served", s == 200 and "css" in ct and "--brand" in body)
        s, ct, body = raw("/js/app.js")
        check("JavaScript is served as JavaScript", s == 200 and "javascript" in ct and "function openTrack" in body)
        s, ct, body = raw("/../app.py")
        check("server code cannot be downloaded", "OPERATOR_CODE" not in body and "html" in ct)
        s, ct, body = raw("/nope/not-here")
        check("unknown path falls back to the landing page", s == 200 and "html" in ct)

        print("Accounts")
        corper = login("demo@example.com")
        op = login("ops@example.com")
        s, _ = call("POST", "/api/login", {"email": "demo@example.com", "password": "wrong"})
        check("wrong password is rejected", s == 401)
        s, _ = call("POST", "/api/signup", {"name": "Test", "email": "x@example.com", "password": "abcdef",
                                            "role": "operator", "operator_code": "nope"})
        check("operator signup needs the operator code", s == 403)
        s, d = call("POST", "/api/signup", {"name": "New Corper", "email": "new@example.com", "password": "abcdef",
                                            "role": "corper", "batch": "Batch A Stream I"})
        check("corper can sign up", s == 200 and d["user"]["role"] == "corper")

        print("Booking rules")
        _, d = call("GET", "/api/trips?origin=Lagos&destination=FCT", token=corper)
        check("search filters by route", d["trips"] and all(t["origin"] == "Lagos" and t["destination"] == "FCT" for t in d["trips"]))
        _, everything = call("GET", "/api/trips", token=corper)
        open_trip = next(t for t in everything["trips"] if t["seats_left"] > 5 and t["id"] not in (1, 2))
        before = open_trip["seats_left"]
        s, _ = call("POST", "/api/bookings", {"trip_id": open_trip["id"], "seats": 1}, corper)
        check("corper can book a seat", s == 200)
        check("seat count drops by 1", seats_left(open_trip["id"], corper) == before - 1)
        s, _ = call("POST", "/api/bookings", {"trip_id": open_trip["id"], "seats": 1}, corper)
        check("same corper cannot book the same trip twice", s == 409)
        _, mine = call("GET", "/api/bookings", token=corper)
        bid = next(b["id"] for b in mine["bookings"] if b["trip_id"] == open_trip["id"])
        s, _ = call("POST", f"/api/bookings/{bid}/cancel", token=corper)
        check("cancel works", s == 200)
        check("cancel gives the seat back", seats_left(open_trip["id"], corper) == before)
        s, _ = call("POST", f"/api/bookings/{bid}/cancel", token=corper)
        check("cancelling twice is refused", s == 409)
        s, _ = call("POST", "/api/bookings", {"trip_id": open_trip["id"], "seats": 9}, corper)
        check("cannot book more than 4 seats at once", s == 400)
        s, _ = call("POST", "/api/bookings", {"trip_id": 1, "seats": 1}, corper)
        check("full trip refuses booking", s == 409)
        s, _ = call("POST", "/api/bookings", {"trip_id": open_trip["id"], "seats": 1})
        check("booking without login is refused", s == 403)

        print("Tracking")
        seeded = next(b for b in mine["bookings"] if b["status"] == "confirmed" and b["trip_id"] != open_trip["id"])
        s, d = call("GET", f"/api/trips/{seeded['trip_id']}/track", token=corper)
        check("corper can track a trip they booked", s == 200 and d["simulated"] is True and d["distance_km"] > 0
              and d["duration_min"] > 0 and len(d["states"]) == 37, str(d)[:120])
        s, _ = call("GET", f"/api/trips/{open_trip['id']}/track", token=corper)
        check("corper cannot track a trip they have not booked", s == 403)
        s, _ = call("GET", f"/api/trips/{seeded['trip_id']}/track")
        check("tracking needs login", s == 401)
        owners = {}
        for email in ("ops@example.com", "ops2@example.com", "ops3@example.com"):
            tok = login(email)
            _, ot = call("GET", "/api/operator/trips", token=tok)
            owns = any(t["id"] == seeded["trip_id"] for t in ot["trips"])
            s, _ = call("GET", f"/api/trips/{seeded['trip_id']}/track", token=tok)
            owners[email] = (owns, s)
        check("only the trip's own operator can track it",
              all(s == (200 if owns else 403) for owns, s in owners.values()) and any(o for o, _ in owners.values()),
              str(owners))
        s, _ = call("GET", "/api/trips/99999/track", token=corper)
        check("unknown trip gives 404", s == 404)

        print("Last seat, 10 people at once")
        tokens = []
        for i in range(10):
            s, d = call("POST", "/api/signup", {"name": f"Racer {i}", "email": f"racer{i}@example.com",
                                                "password": "abcdef", "role": "corper", "batch": "Batch B Stream I"})
            tokens.append(d["token"])
        results = []
        barrier = threading.Barrier(10)

        def grab(tok):
            barrier.wait()
            results.append(call("POST", "/api/bookings", {"trip_id": 2, "seats": 1}, tok)[0])

        threads = [threading.Thread(target=grab, args=(t,)) for t in tokens]
        [t.start() for t in threads]
        [t.join() for t in threads]
        check("exactly one person gets the last seat", results.count(200) == 1, str(results))
        check("everyone else is told it is full", results.count(409) == 9, str(results))
        check("seats left is 0, never negative", seats_left(2, corper) == 0)

        print("Operator")
        s, _ = call("GET", "/api/operator/demand", token=corper)
        check("corper cannot see operator demand", s == 403)
        s, d = call("POST", "/api/operator/trips", {"origin": "Lagos", "destination": "Kwara", "batch": "Batch C Stream II",
                                                    "depart_date": "2099-01-01", "depart_time": "07:30",
                                                    "seats_total": 18, "price": 15000}, op)
        check("operator can create a trip", s == 200)
        s, _ = call("POST", "/api/operator/trips", {"origin": "Lagos", "destination": "Lagos", "batch": "Batch C Stream II",
                                                    "depart_date": "2099-01-01", "depart_time": "07:30",
                                                    "seats_total": 18, "price": 15000}, op)
        check("same origin and destination is refused", s == 400)
        _, d = call("GET", "/api/operator/demand", token=op)
        check("demand view lists routes with booked seats", d["demand"] and "seats_booked" in d["demand"][0])
        _, d = call("GET", "/api/operator/trips", token=op)
        mine_trip = next(t for t in d["trips"] if t["seats_total"] - t["seats_left"] > 0)
        s, d = call("GET", f"/api/operator/trips/{mine_trip['id']}/passengers", token=op)
        check("operator sees the passenger list", s == 200 and len(d["passengers"]) > 0)

        print("Admin fleet")
        s, _ = call("POST", "/api/signup", {"name": "Bad Admin", "email": "badadmin@example.com", "password": "abcdef",
                                            "role": "admin", "admin_code": "nope"})
        check("admin signup needs the admin code", s == 403)
        s, d = call("POST", "/api/signup", {"name": "New Admin", "email": "newadmin@example.com", "password": "abcdef",
                                            "role": "admin", "admin_code": "demo-admin"})
        check("admin can sign up with the code", s == 200 and d["user"]["role"] == "admin")
        admin = login("admin@example.com")
        s, _ = call("GET", "/api/admin/fleet", token=corper)
        check("corper cannot see the fleet", s == 403)
        s, _ = call("GET", "/api/admin/fleet", token=op)
        check("operator cannot see the fleet", s == 403)
        s, fleet = call("GET", "/api/admin/fleet", token=admin)
        check("admin gets the fleet", s == 200 and fleet["buses"] and fleet["simulated"] is True and len(fleet["states"]) == 37)
        need = {"id", "operator_id", "operator_name", "origin", "destination", "batch", "depart_at", "passengers",
                "seats_total", "distance_km", "duration_min", "origin_pos", "destination_pos"}
        check("each bus has the fields the map needs", all(need <= set(b) for b in fleet["buses"]))
        now = datetime.fromisoformat(fleet["server_now"])
        on_road = [b for b in fleet["buses"]
                   if datetime.fromisoformat(b["depart_at"]) <= now < datetime.fromisoformat(b["depart_at"]) + timedelta(minutes=b["duration_min"])]
        check("fleet includes buses on the road right now", len(on_road) >= 5, f"found {len(on_road)}")
        s, _ = call("POST", "/api/bookings", {"trip_id": on_road[0]["id"], "seats": 1}, corper)
        check("cannot book a bus that has already left", s == 400)
        _, listed = call("GET", "/api/trips", token=corper)
        check("departed trips are hidden from search", not {t["id"] for t in listed["trips"]} & {b["id"] for b in on_road})
        s, _ = call("GET", f"/api/trips/{on_road[0]['id']}/track", token=admin)
        check("admin can open the tracker for any trip", s == 200)

        print("Chat")
        op1, op2 = login("ops@example.com"), login("ops2@example.com")
        op1_id = call("GET", "/api/me", token=op1)[1]["user"]["id"]
        _, u = call("GET", "/api/chat/unread", token=admin)
        check("admin starts with 2 unread messages", u["unread"] == 2, str(u))
        s, th = call("GET", "/api/chat/threads", token=admin)
        check("admin sees one thread per operator", s == 200 and len(th["threads"]) == 3 and th["threads"][0]["unread"] >= 1)
        s, th = call("GET", "/api/chat/threads", token=op1)
        check("operators cannot list all threads", s == 403)
        s, m = call("GET", f"/api/chat/messages?operator_id={op1_id}", token=admin)
        check("admin reads an operator's thread", s == 200 and len(m["messages"]) == 3)
        _, u = call("GET", "/api/chat/unread", token=admin)
        check("opening the thread marks it read", u["unread"] == 1, str(u))
        s, _ = call("POST", "/api/chat/messages", {"operator_id": op1_id, "body": "Received, thank you."}, admin)
        check("admin can message an operator", s == 200)
        _, u = call("GET", "/api/chat/unread", token=op1)
        check("operator sees 1 unread from the admin", u["unread"] == 1, str(u))
        s, m = call("GET", "/api/chat/messages", token=op1)
        check("operator reads the reply in their own thread",
              s == 200 and m["messages"][-1]["body"] == "Received, thank you." and m["messages"][-1]["sender_role"] == "admin")
        last_id = m["messages"][-1]["id"]
        s, _ = call("POST", "/api/chat/messages", {"body": "We have arrived safely."}, op1)
        check("operator can message the admin", s == 200)
        s, m = call("GET", f"/api/chat/messages?after={last_id}", token=op1)
        check("?after= returns only newer messages", [x["body"] for x in m["messages"]] == ["We have arrived safely."])
        s, m = call("GET", f"/api/chat/messages?operator_id={op1_id}", token=op2)
        check("an operator cannot read another operator's thread",
              s == 200 and all(x["body"] != "Received, thank you." for x in m["messages"]))
        s, _ = call("GET", "/api/chat/messages", token=corper)
        check("corpers have no chat", s == 403)
        s, _ = call("GET", "/api/chat/messages")
        check("chat needs login", s == 401)
        s, _ = call("POST", "/api/chat/messages", {"body": "hello"}, admin)
        check("admin must choose an operator", s == 400)
        s, _ = call("POST", "/api/chat/messages", {"body": "   "}, op1)
        check("empty messages are refused", s == 400)
        s, _ = call("POST", "/api/chat/messages", {"body": "x" * 1001}, op1)
        check("messages over 1000 characters are refused", s == 400)
    finally:
        server.terminate()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
