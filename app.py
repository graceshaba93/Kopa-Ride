"""CorperRide: batch-based interstate trip booking for NYSC corps members.

Zero dependencies. Run:  python3 app.py   then open http://localhost:8000
"""
import hashlib
import json
import math
import mimetypes
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "corpride.db"))
STATIC_DIR = os.path.join(HERE, "static")
OPERATOR_CODE = os.environ.get("OPERATOR_CODE", "demo-operator")
ADMIN_CODE = os.environ.get("ADMIN_CODE", "demo-admin")

STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT", "Gombe", "Imo",
    "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa",
    "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba",
    "Yobe", "Zamfara",
]
# Batch labels follow NYSC naming (Batch A/B/C, Stream I/II). Confirm real camp dates on nysc.gov.ng.
BATCHES = [f"Batch {b} Stream {s}" for b in "ABC" for s in ("I", "II")]

# Approximate (lat, lng) of each state capital. Used only for the SIMULATED tracking screen.
STATE_COORDS = {
    "Abia": (5.53, 7.49), "Adamawa": (9.20, 12.48), "Akwa Ibom": (5.05, 7.93), "Anambra": (6.21, 7.07),
    "Bauchi": (10.31, 9.84), "Bayelsa": (4.93, 6.27), "Benue": (7.73, 8.52), "Borno": (11.83, 13.15),
    "Cross River": (4.95, 8.33), "Delta": (6.20, 6.73), "Ebonyi": (6.32, 8.11), "Edo": (6.34, 5.63),
    "Ekiti": (7.62, 5.22), "Enugu": (6.46, 7.55), "FCT": (9.06, 7.49), "Gombe": (10.29, 11.17),
    "Imo": (5.49, 7.03), "Jigawa": (11.76, 9.34), "Kaduna": (10.52, 7.44), "Kano": (12.00, 8.52),
    "Katsina": (12.99, 7.60), "Kebbi": (12.45, 4.20), "Kogi": (7.80, 6.74), "Kwara": (8.50, 4.55),
    "Lagos": (6.60, 3.35), "Nasarawa": (8.49, 8.52), "Niger": (9.61, 6.55), "Ogun": (7.16, 3.35),
    "Ondo": (7.25, 5.19), "Osun": (7.77, 4.56), "Oyo": (7.38, 3.95), "Plateau": (9.93, 8.89),
    "Rivers": (4.82, 7.03), "Sokoto": (13.06, 5.24), "Taraba": (8.89, 11.36), "Yobe": (11.75, 11.96),
    "Zamfara": (12.17, 6.66),
}
AVG_KMH = 62          # assumed average coach speed, used for the demo ETA
ROAD_FACTOR = 1.25    # roads are longer than the straight line between two capitals

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('corper','operator','admin')),
  batch TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS trips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  operator_id INTEGER NOT NULL REFERENCES users(id),
  origin TEXT NOT NULL,
  destination TEXT NOT NULL,
  batch TEXT NOT NULL,
  depart_date TEXT NOT NULL,
  depart_time TEXT NOT NULL,
  seats_total INTEGER NOT NULL CHECK (seats_total > 0),
  seats_left INTEGER NOT NULL CHECK (seats_left >= 0),
  price INTEGER NOT NULL CHECK (price >= 0),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id INTEGER NOT NULL REFERENCES trips(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  seats INTEGER NOT NULL CHECK (seats BETWEEN 1 AND 4),
  status TEXT NOT NULL CHECK (status IN ('confirmed','cancelled')) DEFAULT 'confirmed',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- One chat thread per operator: the operator on one side, the admins on the other.
-- "read" means the other side has seen it.
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  operator_id INTEGER NOT NULL REFERENCES users(id),
  sender_id INTEGER NOT NULL REFERENCES users(id),
  sender_role TEXT NOT NULL CHECK (sender_role IN ('operator','admin')),
  body TEXT NOT NULL,
  read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(operator_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking
  ON bookings(trip_id, user_id) WHERE status = 'confirmed';
CREATE INDEX IF NOT EXISTS trips_route ON trips(origin, destination, depart_date);
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def connect():
    # isolation_level=None: we control transactions ourselves (BEGIN IMMEDIATE for booking).
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    users_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
    if "'admin'" not in users_sql:
        print("WARNING: this database is from an older version and cannot hold admin accounts.\n"
              "         Stop the app, run  python3 seed.py  to rebuild it, then start the app again.")
    conn.close()


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()


def create_user(conn, name, email, password, role, batch=None):
    salt = secrets.token_hex(16)
    cur = conn.execute(
        "INSERT INTO users (name, email, password_hash, salt, role, batch) VALUES (?,?,?,?,?,?)",
        (name, email.lower(), hash_password(password, salt), salt, role, batch),
    )
    return cur.lastrowid


def new_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user_id))
    return token


def user_json(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"], "batch": u["batch"]}


def require(cond, status, message):
    if not cond:
        raise ApiError(status, message)


def depart_dt(trip):
    """A trip's departure as a datetime (server local time)."""
    return datetime.strptime(f"{trip['depart_date']} {trip['depart_time']}", "%Y-%m-%d %H:%M")


# ---------- handlers: each takes (conn, user, body, query, *path_args) and returns JSON-able ----------

def signup(conn, user, body, query):
    name = str(body.get("name", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    role = body.get("role", "corper")
    batch = body.get("batch")
    require(name and "@" in email, 400, "Enter your name and a valid email.")
    require(len(password) >= 6, 400, "Password must be at least 6 characters.")
    require(role in ("corper", "operator", "admin"), 400, "Invalid role.")
    if role == "operator":
        require(body.get("operator_code") == OPERATOR_CODE, 403, "Wrong operator code.")
    elif role == "admin":
        require(body.get("admin_code") == ADMIN_CODE, 403, "Wrong admin code.")
    else:
        require(batch in BATCHES, 400, "Pick your batch.")
    try:
        uid = create_user(conn, name, email, password, role, batch if role == "corper" else None)
    except sqlite3.IntegrityError:
        raise ApiError(409, "That email is already registered.")
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return {"token": new_session(conn, uid), "user": user_json(u)}


def login(conn, user, body, query):
    email = str(body.get("email", "")).strip().lower()
    u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    ok = u and secrets.compare_digest(
        u["password_hash"], hash_password(str(body.get("password", "")), u["salt"])
    )
    require(ok, 401, "Wrong email or password.")
    return {"token": new_session(conn, u["id"]), "user": user_json(u)}


def me(conn, user, body, query):
    require(user, 401, "Please log in.")
    return {"user": user_json(user)}


def meta(conn, user, body, query):
    return {"states": STATES, "batches": BATCHES}


def trip_json(r):
    return {k: r[k] for k in (
        "id", "origin", "destination", "batch", "depart_date", "depart_time",
        "seats_total", "seats_left", "price", "operator_name")}


TRIP_SELECT = """SELECT t.*, u.name AS operator_name FROM trips t
                 JOIN users u ON u.id = t.operator_id"""


def list_trips(conn, user, body, query):
    # only trips that have not left yet
    where, args = ["(t.depart_date || ' ' || t.depart_time) > ?"], [datetime.now().strftime("%Y-%m-%d %H:%M")]
    for key, col in (("origin", "t.origin"), ("destination", "t.destination"),
                     ("batch", "t.batch"), ("date", "t.depart_date")):
        val = query.get(key, [""])[0]
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    rows = conn.execute(
        f"{TRIP_SELECT} WHERE {' AND '.join(where)} ORDER BY t.depart_date, t.depart_time LIMIT 200", args
    ).fetchall()
    return {"trips": [trip_json(r) for r in rows]}


def book(conn, user, body, query):
    require(user and user["role"] == "corper", 403, "Log in as a corper to book.")
    try:
        trip_id, seats = int(body.get("trip_id")), int(body.get("seats", 1))
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid booking request.")
    require(1 <= seats <= 4, 400, "You can book 1 to 4 seats at a time.")
    conn.execute("BEGIN IMMEDIATE")  # take the write lock first so two people cannot grab the last seat
    try:
        trip = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        require(trip, 404, "Trip not found.")
        require(depart_dt(trip) > datetime.now(), 400, "This trip has already left.")
        dup = conn.execute(
            "SELECT 1 FROM bookings WHERE trip_id=? AND user_id=? AND status='confirmed'",
            (trip_id, user["id"])).fetchone()
        require(not dup, 409, "You already have a seat on this trip.")
        cur = conn.execute(
            "UPDATE trips SET seats_left = seats_left - ? WHERE id=? AND seats_left >= ?",
            (seats, trip_id, seats))
        require(cur.rowcount == 1, 409, "Not enough seats left on this trip.")
        bid = conn.execute(
            "INSERT INTO bookings (trip_id, user_id, seats) VALUES (?,?,?)",
            (trip_id, user["id"], seats)).lastrowid
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"booking_id": bid, "message": "Seat confirmed. Pay at boarding."}


def my_bookings(conn, user, body, query):
    require(user, 401, "Please log in.")
    rows = conn.execute(
        """SELECT b.id, b.trip_id, b.seats, b.status, b.created_at, t.origin, t.destination, t.batch,
                  t.depart_date, t.depart_time, t.price, u.name AS operator_name
           FROM bookings b JOIN trips t ON t.id=b.trip_id JOIN users u ON u.id=t.operator_id
           WHERE b.user_id=? ORDER BY t.depart_date DESC, b.id DESC""", (user["id"],)).fetchall()
    return {"bookings": [dict(r) for r in rows]}


def cancel(conn, user, body, query, booking_id):
    require(user, 401, "Please log in.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        b = conn.execute("SELECT * FROM bookings WHERE id=? AND user_id=?", (booking_id, user["id"])).fetchone()
        require(b, 404, "Booking not found.")
        require(b["status"] == "confirmed", 409, "Already cancelled.")
        trip = conn.execute("SELECT * FROM trips WHERE id=?", (b["trip_id"],)).fetchone()
        require(depart_dt(trip) > datetime.now(), 400, "This trip has already left.")
        conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
        conn.execute("UPDATE trips SET seats_left = seats_left + ? WHERE id=?", (b["seats"], b["trip_id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"message": "Booking cancelled."}


def require_operator(user):
    require(user and user["role"] == "operator", 403, "Operator access only.")


def require_admin(user):
    require(user and user["role"] == "admin", 403, "Admin access only.")


def create_trip(conn, user, body, query):
    require_operator(user)
    origin, dest, batch = body.get("origin"), body.get("destination"), body.get("batch")
    require(origin in STATES and dest in STATES, 400, "Pick valid origin and destination states.")
    require(origin != dest, 400, "Origin and destination must differ.")
    require(batch in BATCHES, 400, "Pick a valid batch.")
    try:
        d = datetime.strptime(str(body.get("depart_date")), "%Y-%m-%d").date()
        t = datetime.strptime(str(body.get("depart_time")), "%H:%M").strftime("%H:%M")
        seats, price = int(body.get("seats_total")), int(body.get("price"))
    except (TypeError, ValueError):
        raise ApiError(400, "Check the date (YYYY-MM-DD), time (HH:MM), seats and price.")
    require(d >= date.today(), 400, "Departure date is in the past.")
    require(1 <= seats <= 70 and price >= 0, 400, "Seats must be 1-70 and price 0 or more.")
    tid = conn.execute(
        """INSERT INTO trips (operator_id, origin, destination, batch, depart_date, depart_time,
                              seats_total, seats_left, price) VALUES (?,?,?,?,?,?,?,?,?)""",
        (user["id"], origin, dest, batch, d.isoformat(), t, seats, seats, price)).lastrowid
    return {"trip_id": tid}


def operator_trips(conn, user, body, query):
    require_operator(user)
    rows = conn.execute(
        f"{TRIP_SELECT} WHERE t.operator_id=? ORDER BY t.depart_date DESC, t.depart_time", (user["id"],)).fetchall()
    return {"trips": [trip_json(r) for r in rows]}


def operator_passengers(conn, user, body, query, trip_id):
    require_operator(user)
    trip = conn.execute("SELECT * FROM trips WHERE id=? AND operator_id=?", (trip_id, user["id"])).fetchone()
    require(trip, 404, "Trip not found.")
    rows = conn.execute(
        """SELECT u.name, u.email, u.batch, b.seats FROM bookings b JOIN users u ON u.id=b.user_id
           WHERE b.trip_id=? AND b.status='confirmed' ORDER BY b.id""", (trip_id,)).fetchall()
    return {"passengers": [dict(r) for r in rows]}


def operator_demand(conn, user, body, query):
    """Seats booked per route per batch: the number operators use to decide how many buses to run."""
    require_operator(user)
    rows = conn.execute(
        """SELECT t.origin, t.destination, t.batch,
                  COUNT(DISTINCT t.id) AS trips,
                  SUM(t.seats_total) AS seats_total,
                  COALESCE(SUM(CASE WHEN b.status='confirmed' THEN b.seats END), 0) AS seats_booked
           FROM trips t LEFT JOIN bookings b ON b.trip_id = t.id
           WHERE t.depart_date >= ?
           GROUP BY t.origin, t.destination, t.batch
           ORDER BY seats_booked DESC LIMIT 100""", (date.today().isoformat(),)).fetchall()
    return {"demand": [dict(r) for r in rows]}


def straight_km(a, b):
    """Great-circle distance in km between two (lat, lng) points."""
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def track_trip(conn, user, body, query, trip_id):
    """Route data for the tracking screen. The bus position itself is SIMULATED in the browser."""
    require(user, 401, "Please log in.")
    trip = conn.execute(f"{TRIP_SELECT} WHERE t.id=?", (trip_id,)).fetchone()
    require(trip, 404, "Trip not found.")
    if user["role"] == "operator":
        require(trip["operator_id"] == user["id"], 403, "This is not your trip.")
    elif user["role"] == "corper":   # admins can track any trip
        has_seat = conn.execute(
            "SELECT 1 FROM bookings WHERE trip_id=? AND user_id=? AND status='confirmed'",
            (trip_id, user["id"])).fetchone()
        require(has_seat, 403, "Book a seat on this trip to track it.")
    o, d = STATE_COORDS[trip["origin"]], STATE_COORDS[trip["destination"]]
    km = round(straight_km(o, d) * ROAD_FACTOR)
    return {
        "trip": trip_json(trip),
        "origin": {"name": trip["origin"], "lat": o[0], "lng": o[1]},
        "destination": {"name": trip["destination"], "lat": d[0], "lng": d[1]},
        "states": [{"name": n, "lat": c[0], "lng": c[1]} for n, c in STATE_COORDS.items()],
        "distance_km": km,
        "duration_min": round(km / AVG_KMH * 60),
        "simulated": True,
    }


def admin_fleet(conn, user, body, query):
    """Every bus that is boarding, on the road, or recently arrived, plus ones leaving soon.
    Like the single-trip tracker, the bus POSITION is simulated in the browser from these times."""
    require_admin(user)
    now = datetime.now()
    lo, hi = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), (now + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    rows = conn.execute(
        f"""SELECT t.*, u.name AS operator_name,
                   COALESCE((SELECT SUM(seats) FROM bookings b WHERE b.trip_id=t.id AND b.status='confirmed'), 0) AS passengers
            FROM trips t JOIN users u ON u.id = t.operator_id
            WHERE (t.depart_date || ' ' || t.depart_time) BETWEEN ? AND ?
            ORDER BY t.depart_date, t.depart_time LIMIT 80""", (lo, hi)).fetchall()
    buses = []
    for r in rows:
        o, d = STATE_COORDS[r["origin"]], STATE_COORDS[r["destination"]]
        km = round(straight_km(o, d) * ROAD_FACTOR)
        buses.append({
            "id": r["id"], "operator_id": r["operator_id"], "operator_name": r["operator_name"],
            "origin": r["origin"], "destination": r["destination"], "batch": r["batch"],
            "depart_at": f"{r['depart_date']}T{r['depart_time']}", "seats_total": r["seats_total"],
            "passengers": r["passengers"], "distance_km": km, "duration_min": round(km / AVG_KMH * 60),
            "origin_pos": list(o), "destination_pos": list(d),
        })
    operators = conn.execute("SELECT id, name FROM users WHERE role='operator' ORDER BY name").fetchall()
    return {
        "server_now": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "buses": buses,
        "operators": [dict(o) for o in operators],
        "states": [{"name": n, "lat": c[0], "lng": c[1]} for n, c in STATE_COORDS.items()],
        "simulated": True,
    }


# ---------- chat between operators and admins ----------

def chat_operator_id(conn, user, source):
    """Which operator's thread is this about? Operators: always their own. Admins: they must name one."""
    require(user, 401, "Please log in.")
    if user["role"] == "operator":
        return user["id"]
    require(user["role"] == "admin", 403, "Chat is for operators and admins.")
    try:
        op_id = int(source.get("operator_id"))
    except (TypeError, ValueError):
        raise ApiError(400, "Choose an operator.")
    require(conn.execute("SELECT 1 FROM users WHERE id=? AND role='operator'", (op_id,)).fetchone(),
            404, "Operator not found.")
    return op_id


def chat_threads(conn, user, body, query):
    """Admin: every operator with the last message and how many of their messages are unread."""
    require_admin(user)
    rows = conn.execute(
        """SELECT u.id, u.name,
                  (SELECT body FROM messages m WHERE m.operator_id=u.id ORDER BY m.id DESC LIMIT 1) AS last_body,
                  (SELECT created_at FROM messages m WHERE m.operator_id=u.id ORDER BY m.id DESC LIMIT 1) AS last_at,
                  (SELECT COUNT(*) FROM messages m
                    WHERE m.operator_id=u.id AND m.sender_role='operator' AND m.read=0) AS unread
           FROM users u WHERE u.role='operator'
           ORDER BY last_at IS NULL, last_at DESC, u.name""").fetchall()
    return {"threads": [dict(r) for r in rows]}


def chat_messages(conn, user, body, query):
    """Messages in a thread (use ?after=<id> to get only new ones). Opening a thread marks the other side's messages read."""
    op_id = chat_operator_id(conn, user, {"operator_id": query.get("operator_id", [None])[0]})
    try:
        after = int(query.get("after", ["0"])[0])
    except ValueError:
        after = 0
    rows = conn.execute(
        """SELECT m.id, m.sender_role, u.name AS sender_name, m.body, m.created_at
           FROM messages m JOIN users u ON u.id = m.sender_id
           WHERE m.operator_id=? AND m.id>? ORDER BY m.id""", (op_id, after)).fetchall()
    other = "admin" if user["role"] == "operator" else "operator"
    conn.execute("UPDATE messages SET read=1 WHERE operator_id=? AND sender_role=? AND read=0", (op_id, other))
    return {"messages": [dict(r) for r in rows]}


def chat_send(conn, user, body, query):
    op_id = chat_operator_id(conn, user, body)
    text = str(body.get("body", "")).strip()
    require(text, 400, "Type a message first.")
    require(len(text) <= 1000, 400, "Messages can be up to 1000 characters.")
    mid = conn.execute(
        "INSERT INTO messages (operator_id, sender_id, sender_role, body) VALUES (?,?,?,?)",
        (op_id, user["id"], user["role"], text)).lastrowid
    return {"id": mid}


def chat_unread(conn, user, body, query):
    """How many unread messages the logged-in user has (for the badge on the Messages tab)."""
    require(user, 401, "Please log in.")
    if user["role"] == "admin":
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE sender_role='operator' AND read=0").fetchone()[0]
    elif user["role"] == "operator":
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE operator_id=? AND sender_role='admin' AND read=0",
                         (user["id"],)).fetchone()[0]
    else:
        n = 0
    return {"unread": n}


ROUTES = [
    ("GET", "/api/admin/fleet", admin_fleet),
    ("GET", "/api/chat/threads", chat_threads),
    ("GET", "/api/chat/messages", chat_messages),
    ("POST", "/api/chat/messages", chat_send),
    ("GET", "/api/chat/unread", chat_unread),
    ("GET", "/api/trips/{id}/track", track_trip),
    ("POST", "/api/signup", signup),
    ("POST", "/api/login", login),
    ("GET", "/api/me", me),
    ("GET", "/api/meta", meta),
    ("GET", "/api/trips", list_trips),
    ("POST", "/api/bookings", book),
    ("GET", "/api/bookings", my_bookings),
    ("POST", "/api/bookings/{id}/cancel", cancel),
    ("POST", "/api/operator/trips", create_trip),
    ("GET", "/api/operator/trips", operator_trips),
    ("GET", "/api/operator/trips/{id}/passengers", operator_passengers),
    ("GET", "/api/operator/demand", operator_demand),
]


def match(pattern, path):
    p, q = pattern.strip("/").split("/"), path.strip("/").split("/")
    if len(p) != len(q):
        return None
    args = []
    for a, b in zip(p, q):
        if a == "{id}":
            if not b.isdigit():
                return None
            args.append(int(b))
        elif a != b:
            return None
    return args


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self, method, parsed):
        conn = connect()
        try:
            user = None
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                user = conn.execute(
                    "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                    (auth[7:],)).fetchone()
            body = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    try:
                        body = json.loads(self.rfile.read(length))
                    except ValueError:
                        raise ApiError(400, "Invalid JSON.")
            for m, pattern, fn in ROUTES:
                args = match(pattern, parsed.path) if m == method else None
                if args is not None:
                    return self.send_json(200, fn(conn, user, body, parse_qs(parsed.query), *args))
            raise ApiError(404, "Not found.")
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # keep the demo alive; log for the developer
            print("ERROR:", repr(e))
            self.send_json(500, {"error": "Something went wrong on the server."})
        finally:
            conn.close()

    def serve_static(self, path):
        """Pages: / is the landing page, /app is the booking app. Everything else is a file in static/."""
        pages = {"": "landing.html", "/": "landing.html", "/app": "app.html", "/app/": "app.html"}
        rel = pages.get(path, path.lstrip("/"))
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR + os.sep) or not os.path.isfile(full):
            full = os.path.join(STATIC_DIR, "landing.html")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(full)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api("GET", parsed)
        else:
            self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api("POST", parsed)
        else:
            self.send_json(404, {"error": "Not found."})


def main():
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"CorperRide running on http://localhost:{port}  (database: {DB_PATH})")
    server.serve_forever()


if __name__ == "__main__":
    main()
