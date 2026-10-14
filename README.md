# CorperRide

Batch-based interstate trip booking for NYSC corps members. Corpers search trips by route and batch and book a seat. Operators create trips, see passenger lists and demand per route per batch, and chat with the admin. The admin watches every bus on a fleet map and chats with operators.

Built with the Python standard library only, so there is nothing to install.

## Run it (needs Python 3.9+)

```
python3 seed.py     # creates the database with SYNTHETIC demo data (run it again after updating the code: the schema changed)
python3 app.py      # starts the server
```

Open http://localhost:8000 for the landing page. The booking app is at http://localhost:8000/app

| Role | Email | Password |
|---|---|---|
| Corper | demo@example.com | demo1234 |
| Operator | ops@example.com | demo1234 |
| Admin | admin@example.com | demo1234 |

New operator accounts need the code `demo-operator` and new admin accounts need `demo-admin` (change them with the `OPERATOR_CODE` and `ADMIN_CODE` environment variables).

Run the tests: `python3 test_flow.py`

## Using the terminal

You only need it to start the app.

1. Unzip the folder, then open a terminal **inside** it (Windows: open the folder, click the address bar, type `cmd`, press Enter; Mac: right-click the folder, New Terminal at Folder).
2. Run the two commands above. On Windows use `python` instead of `python3` if `python3` is not found.
3. Leave that terminal window open: closing it, or pressing Ctrl+C, stops the app.
4. Port already in use? Run it on another port: `PORT=8001 python3 app.py` (Windows: `set PORT=8001` then `python app.py`).
5. Want a fresh start? Run `python3 seed.py` again. It rebuilds the database from scratch.

The demo corper already has one booked seat, so you can open My bookings and press Track trip straight away.

## What works

- Sign up and log in as a corper (with batch), an operator or an admin
- Search trips by origin, destination, batch and date
- Book 1 seat, with a seat count that can never go below zero, even when many people book at once
- One active booking per corper per trip; cancel to give the seat back
- Operator dashboard: create trips, passenger list per trip, demand by route and batch
- Simulated live tracking (see below)
- Admin fleet map: every bus on one map, filter by operator, click a bus for speed, progress and arrival time, adjustable demo speed
- Operator and admin chat: one conversation per operator, unread badge, refreshes every 4 seconds
- Trips that have already left are hidden and cannot be booked

## Simulated live tracking

Corpers press **Track trip** on a confirmed booking (operators press **Track** on their own trips). The screen shows a map-style view of the route with a bus that moves, plus progress, distance covered, speed, estimated arrival, a "GPS ping" counter and a status timeline (Seat booked, Boarding, On the road, Arrived).

**It is a simulation, and the screen says so.** The server sends the route (approximate capital-to-capital distance) and a duration at an assumed 62 km/h. The browser works out where the bus "is" from a clock. Two modes:

- **Live demo (sped up):** the trip plays out in about a minute so you can show it in a presentation. The speed is adjustable.
- **Follow the real schedule:** the bus sits at the start until the trip's departure time, then moves in real time.

Say clearly in your presentation that positions are simulated. A real version would receive positions from the driver's phone or a vehicle tracker: the screen and the `/api/trips/{id}/track` endpoint stay, and only the source of the position changes.

## What is deliberately not built

- Real payments (bookings say "pay at boarding")
- Real GPS (tracking and the fleet map are simulated), ratings, notifications, mobile app
- Email verification and password reset

## Files

| File | Purpose | Who edits it |
|---|---|---|
| `static/landing.html`, `static/css/landing.css` | Landing page, served at `/` | Frontend person: redesign freely |
| `static/app.html`, `static/css/app.css` | Booking app layout and styles, served at `/app` | Frontend person |
| `static/js/app.js` | Booking app behaviour: login, search, booking, tracking, operator dashboard, fleet map, chat | Frontend person |
| `API.md` | Every endpoint with example requests and responses | Read by the frontend person; update when the backend changes |
| `app.py` | Server, database schema and all API endpoints (one file, sections marked in comments) | Backend people |
| `seed.py` | Synthetic demo data | Backend and data people |
| `test_flow.py` | End-to-end tests of the backend | Whoever changes `app.py` |

## Working together

- **Frontend and backend are separate.** The frontend person only needs `API.md` and the `static/` folder. The backend people only touch `app.py` and `seed.py`.
- **The pages are plain HTML, CSS and JavaScript** with no build step or framework. Save a file in `static/`, refresh the browser, and the change shows. If the frontend person wants React or Tailwind later, the API does not change.
- **Changing an endpoint?** Update `API.md` in the same commit and tell the frontend person.
- **Before sharing changes,** run `python3 test_flow.py`. It takes a few seconds.
- **Use git** if you can. `.gitignore` already keeps the local database out of the repository.

## About the data

Real: the list of states, and the Batch A/B/C and Stream I/II naming.
Synthetic: every user, trip, fare and booking. State this in your write-up. Replace the fares and dates in `seed.py` with real ones after talking to corpers and operators, and confirm real batch and camp dates on nysc.gov.ng.

## Next steps, in order

1. Talk to 3-5 corpers and put their real answers in your write-up.
2. Add Paystack test-mode payments.
3. Add demand forecasting only if you need an ML component, and say it is trained on synthetic data.

## Moving to Go or another stack

The API in `app.py` is the contract: 12 endpoints returning JSON, and the schema at the top is the database design. A Go backend only needs to implement the same endpoints and the `static/index.html` frontend keeps working unchanged. The one rule to keep is the booking transaction (take the write lock, check seats, decrement, insert).
