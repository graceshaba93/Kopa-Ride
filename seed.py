"""Fill the database with SYNTHETIC demo data. Run:  python3 seed.py

Real: the list of states and the Batch/Stream naming.
Synthetic: every user, trip, price and booking below. State this in your write-up.
Replace route fares and dates with real ones once you have talked to corpers and operators.
"""
import os
import random
from datetime import date, datetime, timedelta

import app

random.seed(42)
today = date.today()

FIRST = ["Chinedu", "Aisha", "Tunde", "Ngozi", "Emeka", "Fatima", "Seun", "Amaka", "Ibrahim", "Kemi",
         "Obinna", "Zainab", "Femi", "Chioma", "Musa", "Bisi", "Uche", "Hauwa", "Dayo", "Ifeoma",
         "Yusuf", "Tolu", "Nneka", "Sani", "Folake"]
LAST = ["Okafor", "Bello", "Adeyemi", "Eze", "Abubakar", "Balogun", "Nwosu", "Danjuma", "Ogunleye",
        "Ibe", "Lawal", "Okonkwo", "Garba", "Afolabi", "Umeh", "Salisu", "Akinola", "Obi"]

# (origin, destination, base fare in naira, popularity weight) - fares are made up for the demo
ROUTES = [
    ("Lagos", "FCT", 30000, 10), ("FCT", "Lagos", 30000, 8), ("Lagos", "Rivers", 32000, 7),
    ("Rivers", "Lagos", 32000, 5), ("Lagos", "Oyo", 9000, 8), ("Oyo", "Lagos", 9000, 6),
    ("Lagos", "Enugu", 28000, 5), ("Enugu", "Lagos", 28000, 5), ("Kano", "FCT", 22000, 5),
    ("FCT", "Kano", 22000, 4), ("FCT", "Kaduna", 12000, 4), ("Kaduna", "FCT", 12000, 3),
    ("Rivers", "Enugu", 15000, 3), ("Anambra", "FCT", 20000, 3), ("Delta", "Lagos", 18000, 3),
    ("Lagos", "Kwara", 14000, 3), ("Ogun", "Kano", 35000, 1), ("Lagos", "Sokoto", 45000, 1),
]
TIMES = ["05:30", "06:00", "07:00", "08:30", "10:00", "13:00"]
OPERATORS = ["SafeRide Express", "Naija Coaches", "CampLink Transport"]


def main():
    if os.path.exists(app.DB_PATH):
        os.remove(app.DB_PATH)
    app.init_db()
    conn = app.connect()
    conn.execute("BEGIN")

    ops = [app.create_user(conn, name, f"ops{i + 1}@example.com", "demo1234", "operator")
           for i, name in enumerate(OPERATORS)]
    conn.execute("UPDATE users SET email='ops@example.com' WHERE id=?", (ops[0],))  # easy demo login
    admin_id = app.create_user(conn, "CorperRide Admin", "admin@example.com", "demo1234", "admin")

    corpers = []
    for i in range(80):
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"
        email = "demo@example.com" if i == 0 else f"corper{i}@example.com"
        corpers.append(app.create_user(conn, name, email, "demo1234", "corper", random.choice(app.BATCHES)))

    # Demand clusters around a few batches, so the demand view has an obvious shape.
    batch_weights = [4, 3, 6, 2, 3, 1]
    trips = []
    for origin, dest, fare, weight in ROUTES:
        for _ in range(max(1, weight // 2)):
            batch = random.choices(app.BATCHES, batch_weights)[0]
            d = today + timedelta(days=random.randint(1, 30))
            seats = random.choice([14, 18, 30, 45])
            price = int(fare * random.uniform(0.9, 1.15) // 500 * 500)
            cur = conn.execute(
                """INSERT INTO trips (operator_id, origin, destination, batch, depart_date, depart_time,
                                      seats_total, seats_left, price) VALUES (?,?,?,?,?,?,?,?,?)""",
                (random.choice(ops), origin, dest, batch, d.isoformat(), random.choice(TIMES), seats, seats, price))
            trips.append((cur.lastrowid, seats, weight))

    def add_booking(trip_id, user_id, seats=1, status="confirmed"):
        try:
            conn.execute("INSERT INTO bookings (trip_id, user_id, seats, status) VALUES (?,?,?,?)",
                         (trip_id, user_id, seats, status))
        except Exception:
            return False
        if status == "confirmed":
            conn.execute("UPDATE trips SET seats_left = seats_left - ? WHERE id=?", (seats, trip_id))
        return True

    for trip_id, seats, weight in trips:
        target = int(seats * min(0.9, 0.15 * weight))
        users = random.sample(corpers[1:], min(target, len(corpers) - 1))
        for u in users:
            add_booking(trip_id, u)
        if random.random() < 0.25 and users:
            conn.execute("UPDATE bookings SET status='cancelled' WHERE trip_id=? AND user_id=?", (trip_id, users[0]))
            conn.execute("UPDATE trips SET seats_left = seats_left + 1 WHERE id=?", (trip_id,))

    # Edge cases for the demo and tests: one full trip, one trip with a single seat left.
    def fill_to(trip_id, target_left):
        """Add real synthetic bookings until the trip has exactly target_left seats left."""
        left = conn.execute("SELECT seats_left FROM trips WHERE id=?", (trip_id,)).fetchone()[0]
        taken = {r[0] for r in conn.execute(
            "SELECT user_id FROM bookings WHERE trip_id=? AND status='confirmed'", (trip_id,))}
        for u in corpers[1:]:  # corpers[0] is the demo login, left free so you can book live
            if left <= target_left:
                break
            if u in taken:
                continue
            take = min(left - target_left, 4)
            add_booking(trip_id, u, take)
            left -= take

    full_id, last_seat = [r[0] for r in conn.execute("SELECT id FROM trips ORDER BY id LIMIT 2")]
    fill_to(full_id, 0)
    fill_to(last_seat, 1)

    # Give the demo corper one confirmed seat so "Track trip" can be shown immediately.
    demo_trip = conn.execute(
        """SELECT id FROM trips WHERE seats_left >= 2 AND id NOT IN (?, ?)
           ORDER BY (origin='Lagos' AND destination='FCT') DESC, depart_date LIMIT 1""", (full_id, last_seat)).fetchone()[0]
    add_booking(demo_trip, corpers[0])

    # Buses that are on the road RIGHT NOW (relative to the moment you run this script), so the admin fleet
    # map has movement to show. Minutes from now: negative = already left, positive = still boarding.
    now = datetime.now().replace(second=0, microsecond=0)
    LIVE = [("Lagos", "FCT", -45, 45), ("Rivers", "Lagos", -200, 30), ("Kano", "FCT", -120, 45),
            ("FCT", "Kaduna", -60, 18), ("Lagos", "Oyo", -30, 14), ("Enugu", "Lagos", -300, 45),
            ("Lagos", "Kwara", 25, 30)]
    live_ids = []
    for i, (o, d, mins, seats) in enumerate(LIVE):
        dep = now + timedelta(minutes=mins)
        fare = next(f for (a, b, f, _) in ROUTES if a == o and b == d)
        cur = conn.execute(
            """INSERT INTO trips (operator_id, origin, destination, batch, depart_date, depart_time,
                                  seats_total, seats_left, price) VALUES (?,?,?,?,?,?,?,?,?)""",
            (ops[i % len(ops)], o, d, random.choice(app.BATCHES), dep.strftime("%Y-%m-%d"),
             dep.strftime("%H:%M"), seats, seats, fare))
        live_ids.append(cur.lastrowid)
        fill_to(cur.lastrowid, int(seats * 0.3))
    add_booking(live_ids[0], corpers[0])   # the demo corper is on the first bus, so their tracker moves in schedule mode

    # A few chat messages so the Messages screens are not empty. Synthetic, like everything else here.
    def say(op, role, text, read):
        sender = admin_id if role == "admin" else op
        conn.execute("INSERT INTO messages (operator_id, sender_id, sender_role, body, read) VALUES (?,?,?,?,?)",
                     (op, sender, role, text, read))
    say(ops[0], "operator", "Good morning. Our Lagos to FCT bus has left with a full load.", 1)
    say(ops[0], "admin", "Thanks. Please message me when you pass the halfway point.", 1)
    say(ops[0], "operator", "Running about 20 minutes late because of traffic. Will update you.", 0)
    say(ops[1], "operator", "Can I add a second bus on the Rivers to Lagos route for Batch C?", 0)

    conn.execute("COMMIT")
    n_trips = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    n_book = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    print(f"Seeded {len(ops)} operators, {len(corpers)} corpers, {n_trips} trips, {n_book} bookings.")
    print("Corper login:   demo@example.com / demo1234")
    print("Operator login: ops@example.com  / demo1234")
    print("Admin login:    admin@example.com / demo1234")
    print(f"Edge cases: trip #{full_id} is full, trip #{last_seat} has 1 seat left.")
    print(f"The demo corper already has a seat on trip #{demo_trip}: open My bookings, then Track trip.")


if __name__ == "__main__":
    main()
