# API guide (for the frontend)

The frontend only needs this file. Every endpoint is plain JSON over HTTP, served by the same server as the pages, so call them with relative URLs like `fetch("/api/trips")`.

**Auth:** log in or sign up to get a `token`, then send it on every later request as the header `Authorization: Bearer <token>`.

**Errors:** every failure returns a non-200 status and `{"error": "A message you can show the user"}`.

Statuses you will see: `400` bad input, `401` not logged in, `403` not allowed, `404` not found, `409` conflict (for example the trip is full).

Try any GET in the browser, for example http://localhost:8000/api/meta

---

## Accounts

### `POST /api/signup`
```json
{ "name": "Ada Obi", "email": "ada@example.com", "password": "min6chars",
  "role": "corper", "batch": "Batch A Stream I" }
```
For an operator, send `"role": "operator"` and `"operator_code": "demo-operator"` instead of `batch`. For an admin, send `"role": "admin"` and `"admin_code": "demo-admin"`.
Returns `{ "token": "...", "user": { "id", "name", "email", "role", "batch" } }`.

### `POST /api/login`
`{ "email": "demo@example.com", "password": "demo1234" }` returns the same shape as signup.

### `GET /api/me`
Returns `{ "user": {...} }` for the logged-in user, or `401`.

### `GET /api/meta`
Lists for dropdowns: `{ "states": ["Abia", ...], "batches": ["Batch A Stream I", ...] }`.

---

## Trips and booking (corpers)

### `GET /api/trips`
Trips whose departure time has passed are not listed, and booking or cancelling one returns `400` ("This trip has already left.").
Optional filters in the query string: `origin`, `destination`, `batch`, `date` (YYYY-MM-DD). Only upcoming trips are returned.
```json
{ "trips": [ { "id": 7, "origin": "Lagos", "destination": "FCT", "batch": "Batch C Stream I",
               "depart_date": "2026-10-20", "depart_time": "06:00", "seats_total": 45,
               "seats_left": 12, "price": 30000, "operator_name": "SafeRide Express" } ] }
```
`seats_left` of `0` means full.

### `POST /api/bookings`
`{ "trip_id": 7, "seats": 1 }` (1 to 4 seats). Returns `{ "booking_id": 31, "message": "..." }`.
`409` if the trip is full, not enough seats are left, or you already have a seat on it.

### `GET /api/bookings`
The logged-in corper's bookings, newest trips first. `status` is `confirmed` or `cancelled`.
```json
{ "bookings": [ { "id": 31, "trip_id": 7, "seats": 1, "status": "confirmed", "created_at": "...",
                  "origin": "Lagos", "destination": "FCT", "batch": "Batch C Stream I",
                  "depart_date": "2026-10-20", "depart_time": "06:00", "price": 30000,
                  "operator_name": "SafeRide Express" } ] }
```

### `POST /api/bookings/{id}/cancel`
Returns `{ "message": "Booking cancelled." }` and gives the seat back.

### `GET /api/trips/{id}/track`
Route data for the tracking screen. Allowed for a corper with a confirmed booking on that trip, the operator who owns it, or any admin.
```json
{ "trip": { ...same fields as a trip... },
  "origin": { "name": "Lagos", "lat": 6.6, "lng": 3.35 },
  "destination": { "name": "FCT", "lat": 9.06, "lng": 7.49 },
  "states": [ { "name": "Abia", "lat": 5.53, "lng": 7.49 }, ... ],
  "distance_km": 665, "duration_min": 644, "simulated": true }
```
**The bus position is not in the response.** The browser computes it from a clock (see `tkRender()` in `js/app.js`). To make tracking real later, add a position field here and draw that instead.

---

## Operators

All of these return `403` for a corper.

### `POST /api/operator/trips`
```json
{ "origin": "Lagos", "destination": "Kwara", "batch": "Batch C Stream II",
  "depart_date": "2026-11-01", "depart_time": "07:30", "seats_total": 18, "price": 15000 }
```
Returns `{ "trip_id": 40 }`.

### `GET /api/operator/trips`
The operator's own trips, same shape as `GET /api/trips` (past trips included).

### `GET /api/operator/trips/{id}/passengers`
`{ "passengers": [ { "name", "email", "batch", "seats" } ] }`

### `GET /api/operator/demand`
Seats booked per route per batch, for upcoming trips, biggest first.
`{ "demand": [ { "origin", "destination", "batch", "trips", "seats_total", "seats_booked" } ] }`

---

## Admin (fleet tracking)

Returns `403` for anyone who is not an admin.

### `GET /api/admin/fleet`
Every bus that left in the last day or departs in the next two days (up to 80).
```json
{ "server_now": "2026-10-13T14:05", "simulated": true,
  "operators": [ { "id": 1, "name": "SafeRide Express" } ],
  "states": [ { "name": "Abia", "lat": 5.53, "lng": 7.49 } ],
  "buses": [ { "id": 41, "operator_id": 1, "operator_name": "SafeRide Express",
               "origin": "Lagos", "destination": "FCT", "batch": "Batch A Stream I",
               "depart_at": "2026-10-13T13:20", "seats_total": 45, "passengers": 31,
               "distance_km": 665, "duration_min": 644,
               "origin_pos": { "lat": 6.6, "lng": 3.35 }, "destination_pos": { "lat": 9.06, "lng": 7.49 } } ] }
```
As with single-trip tracking, the position is not sent. The browser works it out from `depart_at`, `duration_min` and the clock (see the fleet section of `js/app.js`).

---

## Chat (operator and admin)

One conversation per operator. Operators always talk to the admin; the admin picks an operator. Corpers get `403`. The browser polls every 4 seconds.

### `GET /api/chat/threads` (admin)
`{ "threads": [ { "operator_id", "operator_name", "last_body", "last_at", "unread" } ] }`, most recent first.

### `GET /api/chat/messages?operator_id=1&after=0`
`operator_id` is required for admins and ignored for operators. `after` is the last message id you already have. Returns `{ "messages": [ { "id", "sender_role", "body", "created_at" } ] }` and marks the other side's messages as read.

### `POST /api/chat/messages`
`{ "operator_id": 1, "body": "Please confirm your passenger count." }` (`operator_id` only for admins; body up to 1000 characters). Returns the saved message.

### `GET /api/chat/unread`
`{ "unread": 2 }` for the red badge on the Messages tab.
