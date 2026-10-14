/*
  The booking app (served at /app). Plain JavaScript, no framework, no build step.
  Sections, top to bottom:
    auth                     log in and sign up
    app shell                tabs, depending on whether the user is a corper or an operator
    corper: find + book      search trips, book a seat, my bookings and cancel
    operator                 create trips, demand table, passenger lists
    simulated live tracking  the single-trip tracking screen (the bus position is simulated, see tkRender)
    admin: fleet map         every bus on one map for the admin (also simulated, see busState)
    chat                     operator <-> admin messages, by polling
    boot                     fills the dropdowns and restores the session
  Every call to the server goes through api(method, path, body). Endpoints are listed in API.md.
*/
const $ = id => document.getElementById(id);
const naira = n => "₦" + Number(n).toLocaleString("en-NG");
let state = { token: localStorage.getItem("token"), user: null, meta: null, mode: "login", tab: "find" };

function toast(msg, isErr) {
  const t = $("toast"); t.textContent = msg; t.className = isErr ? "err" : ""; t.style.display = "block";
  clearTimeout(toast.h); toast.h = setTimeout(() => t.style.display = "none", 3500);
}
async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: { "Content-Type": "application/json", ...(state.token ? { Authorization: "Bearer " + state.token } : {}) },
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const opts = (arr, any) => (any ? `<option value="">${any}</option>` : "") + arr.map(x => `<option>${esc(x)}</option>`).join("");

// ---------- auth ----------
function showAuth() {
  $("auth").classList.remove("hide"); $("app").classList.add("hide");
  $("logout").classList.add("hide"); $("who").textContent = "";
  const signup = state.mode === "signup";
  $("auth-title").textContent = signup ? "Create an account" : "Log in";
  $("a-go").textContent = signup ? "Sign up" : "Log in";
  $("a-switch").textContent = signup ? "I have an account" : "Create an account";
  document.querySelectorAll(".signup-only").forEach(e => e.classList.toggle("hide", !signup));
  const staff = $("a-role").value !== "corper";   // operators and admins sign up with an access code, not a batch
  $("a-batch-wrap").classList.toggle("hide", staff); $("a-code-wrap").classList.toggle("hide", !staff);
}
$("a-switch").onclick = () => { state.mode = state.mode === "login" ? "signup" : "login"; showAuth(); };
$("a-role").onchange = showAuth;
$("a-go").onclick = async () => {
  try {
    const body = { email: $("a-email").value, password: $("a-pass").value };
    if (state.mode === "signup") Object.assign(body, {
      name: $("a-name").value, role: $("a-role").value,
      batch: $("a-batch").value, operator_code: $("a-code").value, admin_code: $("a-code").value
    });
    const data = await api("POST", state.mode === "signup" ? "/api/signup" : "/api/login", body);
    state.token = data.token; state.user = data.user; localStorage.setItem("token", data.token);
    enterApp();
  } catch (e) { toast(e.message, true); }
};
$("logout").onclick = () => { stopFleet(); stopChat(); stopBadge(); localStorage.removeItem("token"); state.token = null; state.user = null; state.mode = "login"; showAuth(); };

// ---------- app shell ----------
function enterApp() {
  $("auth").classList.add("hide"); $("app").classList.remove("hide"); $("logout").classList.remove("hide");
  const role = state.user.role;
  $("who").textContent = state.user.name + " · " + (state.user.batch || role);
  const tabs = role === "admin" ? [["fleet", "Fleet map"], ["chat", "Messages"]]
    : role === "operator" ? [["ops", "Operator dashboard"], ["chat", "Messages"]]
      : [["find", "Find a trip"], ["mine", "My bookings"]];
  $("tabs").innerHTML = tabs.map(([k, l]) => `<button data-t="${k}">${l}</button>`).join("");
  $("tabs").querySelectorAll("button").forEach(b => b.onclick = () => showTab(b.dataset.t));
  stopBadge(); if (role !== "corper") startBadge();   // unread-message count on the Messages tab
  showTab(tabs[0][0]);
}
function showTab(t) {
  state.tab = t;
  stopFleet(); stopChat();   // only the visible screen keeps polling the server
  ["find", "mine", "ops", "fleet", "chat"].forEach(k => $("tab-" + k).classList.toggle("hide", k !== t));
  $("tabs").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  if (t === "find") searchTrips(); if (t === "mine") loadMine(); if (t === "ops") loadOps();
  if (t === "fleet") startFleet(); if (t === "chat") openChat();
}

// ---------- corper: find + book ----------
function tripCard(t) {
  const out = t.seats_left === 0, low = t.seats_left > 0 && t.seats_left <= 5;
  const tag = out ? `<span class="tag out">Full</span>` : low ? `<span class="tag low">${t.seats_left} left</span>` : `<span class="tag">${t.seats_left} seats</span>`;
  return `<div class="card trip"><div>
      <div class="route">${esc(t.origin)} → ${esc(t.destination)}</div>
      <div class="meta">${esc(t.depart_date)} at ${esc(t.depart_time)} · ${esc(t.batch)} · ${esc(t.operator_name)}</div>
    </div><div style="text-align:right">
      <div class="price">${naira(t.price)}</div>${tag}
      <div style="margin-top:6px"><button data-book="${t.id}" ${out ? "disabled" : ""}>${out ? "Full" : "Book seat"}</button></div>
    </div></div>`;
}
async function searchTrips() {
  const q = new URLSearchParams({
    origin: $("f-origin").value, destination: $("f-dest").value,
    batch: $("f-batch").value, date: $("f-date").value
  });
  const { trips } = await api("GET", "/api/trips?" + q);
  $("f-results").innerHTML = trips.length ? trips.map(tripCard).join("") : `<div class="empty">No trips match. Try fewer filters.</div>`;
  $("f-results").querySelectorAll("[data-book]").forEach(b => b.onclick = async () => {
    try { const r = await api("POST", "/api/bookings", { trip_id: Number(b.dataset.book), seats: 1 }); toast(r.message); searchTrips(); }
    catch (e) { toast(e.message, true); searchTrips(); }
  });
}
$("f-go").onclick = searchTrips;

async function loadMine() {
  const { bookings } = await api("GET", "/api/bookings");
  $("m-results").innerHTML = bookings.length ? bookings.map(b => `<div class="card trip"><div>
      <div class="route">${esc(b.origin)} → ${esc(b.destination)}</div>
      <div class="meta">${esc(b.depart_date)} at ${esc(b.depart_time)} · ${esc(b.batch)} · ${esc(b.operator_name)} · ${b.seats} seat(s)</div>
    </div><div style="text-align:right"><div class="price">${naira(b.price * b.seats)}</div>
      ${b.status === "confirmed" ? `<span class="tag">Confirmed · pay at boarding</span><div style="margin-top:6px"><button data-track="${b.trip_id}">Track trip</button> <button class="danger" data-cancel="${b.id}">Cancel</button></div>`
      : `<span class="tag out">Cancelled</span>`}
    </div></div>`).join("") : `<div class="empty">No bookings yet.</div>`;
  wireTrack($("m-results"));
  $("m-results").querySelectorAll("[data-cancel]").forEach(b => b.onclick = async () => {
    try { await api("POST", `/api/bookings/${b.dataset.cancel}/cancel`); toast("Booking cancelled."); loadMine(); }
    catch (e) { toast(e.message, true); }
  });
}

// ---------- operator ----------
async function loadOps() {
  const [{ demand }, { trips }] = await Promise.all([api("GET", "/api/operator/demand"), api("GET", "/api/operator/trips")]);
  const max = Math.max(1, ...demand.map(d => d.seats_booked));
  $("o-demand").innerHTML = demand.length ? `<table><tr><th>Route</th><th>Batch</th><th>Trips</th><th>Booked / seats</th><th></th></tr>` +
    demand.map(d => `<tr><td>${esc(d.origin)} → ${esc(d.destination)}</td><td>${esc(d.batch)}</td><td>${d.trips}</td>
      <td>${d.seats_booked} / ${d.seats_total}</td><td><div class="bar"><i style="width:${Math.round(100 * d.seats_booked / max)}%"></i></div></td></tr>`).join("") + `</table>`
    : `<div class="empty">No trips yet.</div>`;
  $("o-trips").innerHTML = trips.length ? trips.map(t => `<div class="card"><div class="trip"><div>
      <div class="route">${esc(t.origin)} → ${esc(t.destination)}</div>
      <div class="meta">${esc(t.depart_date)} at ${esc(t.depart_time)} · ${esc(t.batch)} · ${naira(t.price)} · ${t.seats_total - t.seats_left}/${t.seats_total} booked</div>
    </div><div><button class="ghost" data-track="${t.id}">Track</button> <button class="ghost" data-pass="${t.id}">Passengers</button></div></div><div id="p-${t.id}"></div></div>`).join("") : `<div class="empty">You have no trips.</div>`;
  wireTrack($("o-trips"));
  $("o-trips").querySelectorAll("[data-pass]").forEach(b => b.onclick = async () => {
    const box = $("p-" + b.dataset.pass);
    if (box.innerHTML) { box.innerHTML = ""; return; }
    const { passengers } = await api("GET", `/api/operator/trips/${b.dataset.pass}/passengers`);
    box.innerHTML = passengers.length ? `<table style="margin-top:8px"><tr><th>Name</th><th>Batch</th><th>Seats</th></tr>` +
      passengers.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.batch || "")}</td><td>${p.seats}</td></tr>`).join("") + `</table>`
      : `<div class="meta" style="margin-top:8px">No passengers yet.</div>`;
  });
}
$("o-go").onclick = async () => {
  try {
    await api("POST", "/api/operator/trips", {
      origin: $("o-origin").value, destination: $("o-dest").value, batch: $("o-batch").value,
      depart_date: $("o-date").value, depart_time: $("o-time").value, seats_total: $("o-seats").value, price: $("o-price").value
    });
    toast("Trip added."); loadOps();
  } catch (e) { toast(e.message, true); }
};

// ---------- simulated live tracking ----------
// The server sends the route and a duration. The bus position is computed here from a clock, so it is a demo,
// not real GPS. In a real product the position would come from a driver's phone or a vehicle tracker.
const tk = { data: null, mode: "demo", rate: 10, t0: 0, timer: null, pingN: -1, lastPing: 0 };
const BOARD_MIN = 20;
const proj = (lat, lng) => [20 + (lng - 2.5) / 12.5 * 560, 20 + (14 - lat) / 10 * 440];
const pad2 = n => String(n).padStart(2, "0");
const hhmm = d => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
const span = m => { m = Math.round(m); const d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60); return (d ? d + "d " : "") + (h || d ? h + "h " : "") + (m % 60) + "m"; };

function wireTrack(root) { root.querySelectorAll("[data-track]").forEach(b => b.onclick = () => openTrack(Number(b.dataset.track))); }

async function openTrack(id) {
  try { tk.data = await api("GET", `/api/trips/${id}/track`); } catch (e) { return toast(e.message, true); }
  const d = tk.data, t = d.trip;
  $("tk-route").textContent = `${t.origin} → ${t.destination}`;
  $("tk-meta").textContent = `${t.depart_date} at ${t.depart_time} · ${t.batch} · ${t.operator_name} · about ${d.distance_km} km`;
  const [ox, oy] = proj(d.origin.lat, d.origin.lng), [dx, dy] = proj(d.destination.lat, d.destination.lng);
  const cx = (ox + dx) / 2 - (dy - oy) * 0.12, cy = (oy + dy) / 2 + (dx - ox) * 0.12;  // bend the route slightly
  const curve = `M${ox} ${oy} Q${cx} ${cy} ${dx} ${dy}`;
  const dots = d.states.map(s => { const [x, y] = proj(s.lat, s.lng); return `<circle cx="${x}" cy="${y}" r="3" class="sd"/>`; }).join("");
  $("tk-map").innerHTML = `${dots}<path d="${curve}" class="rb"/><path id="tk-done" d="${curve}" class="rd"/>
    <circle cx="${ox}" cy="${oy}" r="7" class="pin o"/><circle cx="${dx}" cy="${dy}" r="7" class="pin d"/>
    <text x="${ox}" y="${oy + 24}" text-anchor="middle" class="lbl">${esc(t.origin)}</text>
    <text x="${dx}" y="${dy - 14}" text-anchor="middle" class="lbl">${esc(t.destination)}</text>
    <g id="tk-bus"><circle r="9" class="ring"><animate attributeName="r" from="9" to="24" dur="1.6s" repeatCount="indefinite"/>
      <animate attributeName="opacity" from=".5" to="0" dur="1.6s" repeatCount="indefinite"/></circle>
      <circle r="12" class="busdot"/><text y="5" text-anchor="middle" font-size="14">🚌</text></g>`;
  tk.t0 = Date.now(); tk.pingN = -1;
  $("track").classList.remove("hide");
  clearInterval(tk.timer); tk.timer = setInterval(tkRender, 250); tkRender();
}

function tkRender() {
  const d = tk.data; if (!d) return;
  const t = d.trip, dep = new Date(`${t.depart_date}T${t.depart_time}`), dur = d.duration_min;
  const simMins = tk.mode === "demo" ? (Date.now() - tk.t0) / 1000 * tk.rate - BOARD_MIN : (Date.now() - dep.getTime()) / 60000;
  const p = Math.min(1, Math.max(0, simMins / dur));
  const phase = simMins < 0 ? "boarding" : p >= 1 ? "arrived" : "enroute";

  const path = $("tk-done"), len = path.getTotalLength(), pt = path.getPointAtLength(p * len);
  path.style.strokeDasharray = `${p * len} ${len + 1}`;
  $("tk-bus").setAttribute("transform", `translate(${pt.x} ${pt.y})`);

  $("tk-status").textContent = phase === "boarding"
    ? (tk.mode === "demo" ? "Boarding" : "Departs in " + span(-simMins))
    : phase === "arrived" ? "Arrived" : "On the road";
  $("tk-pct").textContent = Math.round(p * 100) + "%";
  $("tk-km").textContent = `${Math.round(p * d.distance_km)} of ${d.distance_km} km`;
  $("tk-speed-now").textContent = phase === "enroute" ? Math.round(62 + 9 * Math.sin(simMins / 13)) + " km/h" : "0 km/h";
  const eta = new Date(dep.getTime() + dur * 60000);
  $("tk-eta").textContent = hhmm(eta) + (eta.getDate() !== dep.getDate() ? " (next day)" : "");

  const n = Math.floor((Date.now() - tk.t0) / 5000);   // a fake "GPS ping" every 5 seconds
  if (n !== tk.pingN) { tk.pingN = n; tk.lastPing = Date.now(); }
  $("tk-ping").textContent = phase === "arrived" ? "Trip ended" : Math.floor((Date.now() - tk.lastPing) / 1000) + "s ago";

  const order = ["boarding", "enroute", "arrived"], at = order.indexOf(phase);
  const steps = [["Seat booked", "done"], ["Boarding", at > 0 ? "done" : "cur"], ["On the road", at > 1 ? "done" : at === 1 ? "cur" : ""], ["Arrived", at === 2 ? "cur" : ""]];
  $("tk-steps").innerHTML = steps.map(([l, c]) => `<span class="step ${c}">${c === "done" ? "✓ " : ""}${l}</span>`).join("");
}
$("tk-close").onclick = () => { clearInterval(tk.timer); $("track").classList.add("hide"); };
$("tk-restart").onclick = () => { tk.t0 = Date.now(); tk.pingN = -1; };
$("tk-mode").onchange = e => { tk.mode = e.target.value; tk.t0 = Date.now(); };
$("tk-rate").onchange = e => { tk.rate = Number(e.target.value); tk.t0 = Date.now(); };

// ---------- admin: fleet map ----------
// Shows every bus at once. As with the single-trip tracker, positions are SIMULATED from the trip times
// the server sends (GET /api/admin/fleet); real GPS would replace busState().
const fl = { data: null, mode: "demo", rate: 10, t0: Date.now(), timer: null, poll: null, offset: 0, sel: null, view: [], len: {} };

function routeCurve(o, d) {
  const [ox, oy] = proj(o[0], o[1]), [dx, dy] = proj(d[0], d[1]);
  const cx = (ox + dx) / 2 - (dy - oy) * 0.12, cy = (oy + dy) / 2 + (dx - ox) * 0.12;
  return `M${ox} ${oy} Q${cx} ${cy} ${dx} ${dy}`;
}

// Where is this bus right now? Returns minutes since departure, progress 0..1, and a phase.
function busState(b, i) {
  const dur = b.duration_min, dep = new Date(b.depart_at).getTime();
  let simMins;
  if (fl.mode === "demo") {   // every bus loops through boarding, driving and arriving, staggered so the map is busy
    const cycle = dur + BOARD_MIN + 30;
    simMins = ((Date.now() - fl.t0) / 1000 * fl.rate + i * cycle / 7) % cycle - BOARD_MIN;
  } else {
    simMins = (Date.now() + fl.offset - dep) / 60000;
  }
  const p = Math.min(1, Math.max(0, simMins / dur));
  return { simMins, p, phase: simMins < 0 ? "boarding" : p >= 1 ? "arrived" : "enroute" };
}

async function loadFleet() {
  try { fl.data = await api("GET", "/api/admin/fleet"); } catch (e) { return toast(e.message, true); }
  fl.offset = new Date(fl.data.server_now).getTime() - Date.now();   // server clock minus this browser's clock
  const cur = $("fl-op").value;
  $("fl-op").innerHTML = `<option value="">All operators</option>` + fl.data.operators.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join("");
  $("fl-op").value = cur;
  buildFleet();
}

function buildFleet() {
  const d = fl.data; if (!d) return;
  const op = $("fl-op").value, now = Date.now() + fl.offset;
  let buses = d.buses.filter(b => !op || String(b.operator_id) === op);
  if (fl.mode === "demo") {   // the 12 trips closest to now
    buses = buses.slice().sort((a, b) => Math.abs(new Date(a.depart_at) - now) - Math.abs(new Date(b.depart_at) - now)).slice(0, 12).sort((a, b) => a.id - b.id);
  } else {                    // only buses that are boarding, driving, or just arrived
    buses = buses.filter(b => { const m = (now - new Date(b.depart_at)) / 60000; return m > -120 && m < b.duration_min + 60; });
  }
  fl.view = buses; fl.len = {};
  const dots = d.states.map(s => { const [x, y] = proj(s.lat, s.lng); return `<circle cx="${x}" cy="${y}" r="3" class="sd" style="stroke:none"/>`; }).join("");
  const routes = buses.map(b => `<path id="fr-${b.id}" class="rt" d="${routeCurve(b.origin_pos, b.destination_pos)}"/>`).join("");
  const markers = buses.map(b => `<g class="fb" id="fb-${b.id}" data-bus="${b.id}"><circle id="fm-${b.id}" r="8" class="ph-boarding"/></g>`).join("");
  $("fl-map").innerHTML = dots + routes + markers + `<text id="fl-label" class="lbl" font-size="12" text-anchor="start"></text>`;
  $("fl-list").innerHTML = buses.length ? buses.map(b => `<div class="bus" id="fl-row-${b.id}" data-bus="${b.id}">
      <div class="bt"><span>${esc(b.origin)} → ${esc(b.destination)}</span><span class="tag" id="fs-${b.id}"></span></div>
      <div class="sub">${esc(b.operator_name)} · ${esc(b.batch)} · ${b.passengers}/${b.seats_total} passengers</div>
      <div class="bar" style="margin-top:6px"><i id="fp-${b.id}" style="width:0"></i></div></div>`).join("")
    : `<div class="empty">No buses on the road right now. Switch to the live demo to see the map in action.</div>`;
  document.querySelectorAll("#fl-map [data-bus], #fl-list [data-bus]").forEach(e => e.onclick = () => selectBus(Number(e.dataset.bus)));
  if (fl.sel && buses.some(b => b.id === fl.sel)) selectBus(fl.sel); else { fl.sel = null; $("fl-detail").classList.add("hide"); }
  tickFleet();
}

function selectBus(id) {
  fl.sel = id;
  const b = fl.view.find(x => x.id === id); if (!b) return;
  document.querySelectorAll("#fl-list .bus").forEach(r => r.classList.toggle("sel", r.id === "fl-row-" + id));
  document.querySelectorAll("#fl-map .rt").forEach(r => r.classList.toggle("sel", r.id === "fr-" + id));
  const g = $("fb-" + id); g.parentNode.appendChild(g);   // bring the selected bus to the front
  $("fl-detail").classList.remove("hide");
  $("fl-detail").innerHTML = `<div class="trip"><div><div class="route">${esc(b.origin)} → ${esc(b.destination)}</div>
      <div class="meta">${esc(b.operator_name)} · ${esc(b.batch)} · departs ${esc(b.depart_at.replace("T", " "))} · ${b.passengers}/${b.seats_total} passengers · about ${b.distance_km} km</div></div>
      <div><button class="ghost" data-act="track">Open full tracker</button> <button data-act="msg">Message ${esc(b.operator_name)}</button></div></div>
    <div class="detail-grid">
      <div class="stat"><span>Status</span><b id="fd-status"></b></div><div class="stat"><span>Progress</span><b id="fd-pct"></b></div>
      <div class="stat"><span>Distance</span><b id="fd-km"></b></div><div class="stat"><span>Speed</span><b id="fd-speed"></b></div>
      <div class="stat"><span>Timing</span><b id="fd-eta"></b></div><div class="stat"><span>GPS ping</span><b id="fd-ping"></b></div></div>`;
  $("fl-detail").querySelector("[data-act=track]").onclick = () => openTrack(b.id);
  $("fl-detail").querySelector("[data-act=msg]").onclick = () => { ch.op = b.operator_id; showTab("chat"); };
  tickFleet();
}

function tickFleet() {
  if (!fl.view.length) { $("fl-summary").textContent = "0 buses shown"; return; }
  const counts = { enroute: 0, boarding: 0, arrived: 0 };
  fl.view.forEach((b, i) => {
    const s = busState(b, i); counts[s.phase]++;
    const path = $("fr-" + b.id); if (!fl.len[b.id]) fl.len[b.id] = path.getTotalLength();
    const pt = path.getPointAtLength(s.p * fl.len[b.id]);
    $("fb-" + b.id).setAttribute("transform", `translate(${pt.x} ${pt.y})`);
    const dot = $("fm-" + b.id); dot.setAttribute("class", "ph-" + s.phase); dot.setAttribute("r", fl.sel === b.id ? 12 : 8);
    $("fs-" + b.id).textContent = s.phase === "enroute" ? Math.round(s.p * 100) + "%" : s.phase === "boarding" ? "Boarding" : "Arrived";
    $("fp-" + b.id).style.width = Math.round(s.p * 100) + "%";
    if (fl.sel === b.id) {
      const label = $("fl-label"); label.setAttribute("x", pt.x + 16); label.setAttribute("y", pt.y - 12); label.textContent = `${b.origin} → ${b.destination}`;
      $("fd-status").textContent = s.phase === "enroute" ? "On the road" : s.phase === "boarding" ? "Boarding" : "Arrived";
      $("fd-pct").textContent = Math.round(s.p * 100) + "%";
      $("fd-km").textContent = `${Math.round(s.p * b.distance_km)} of ${b.distance_km} km`;
      $("fd-speed").textContent = s.phase === "enroute" ? Math.round(62 + 9 * Math.sin(s.simMins / 13 + b.id)) + " km/h" : "0 km/h";
      $("fd-eta").textContent = s.phase === "enroute" ? "Arrives in " + span(b.duration_min - s.simMins) : s.phase === "boarding" ? "Departs in " + span(-s.simMins) : "Trip ended";
      $("fd-ping").textContent = s.phase === "arrived" ? "Trip ended" : Math.floor(((Date.now() - fl.t0) / 1000 + b.id * 1.7) % 5) + "s ago";
    }
  });
  $("fl-summary").textContent = `${counts.enroute} on the road · ${counts.boarding} boarding · ${counts.arrived} arrived`;
}

function startFleet() {
  stopFleet(); fl.t0 = Date.now();
  loadFleet();
  fl.timer = setInterval(tickFleet, 250);
  fl.poll = setInterval(loadFleet, 30000);   // pick up new trips and bookings
}
function stopFleet() { clearInterval(fl.timer); clearInterval(fl.poll); }
$("fl-op").onchange = buildFleet;
$("fl-mode").onchange = e => { fl.mode = e.target.value; fl.t0 = Date.now(); buildFleet(); };
$("fl-rate").onchange = e => { fl.rate = Number(e.target.value); fl.t0 = Date.now(); };
$("fl-restart").onclick = () => { fl.t0 = Date.now(); };

// ---------- chat: operators <-> admin ----------
// Polling, not websockets: every few seconds the page asks the server for messages newer than the last one it has.
const ch = { op: null, last: 0, threads: [], timer: null, badge: null };
const msgTime = s => hhmm(new Date(s.replace(" ", "T") + "Z"));   // the server stores UTC

function startBadge() { stopBadge(); updateBadge(); ch.badge = setInterval(updateBadge, 8000); }
function stopBadge() { clearInterval(ch.badge); }
async function updateBadge() {
  try {
    const { unread } = await api("GET", "/api/chat/unread");
    const tab = [...$("tabs").querySelectorAll("button")].find(b => b.dataset.t === "chat");
    if (tab) tab.innerHTML = "Messages" + (unread ? `<span class="badge">${unread}</span>` : "");
  } catch (e) { /* keep the old badge */ }
}

async function openChat() {
  const admin = state.user.role === "admin";
  $("ch-threads").classList.toggle("hide", !admin);
  document.querySelector(".chat-grid").classList.toggle("single", !admin);
  if (admin) { await loadThreads(); if (!ch.op && ch.threads.length) ch.op = ch.threads[0].id; }
  else ch.op = state.user.id;
  await loadThread(true);
  stopChat(); ch.timer = setInterval(pollChat, 4000);
}
function stopChat() { clearInterval(ch.timer); }

async function loadThreads() {
  const { threads } = await api("GET", "/api/chat/threads"); ch.threads = threads;
  $("ch-threads").innerHTML = threads.map(t => `<div class="thread ${t.id === ch.op ? "sel" : ""}" data-op="${t.id}">
      <b>${esc(t.name)}${t.unread ? `<span class="badge">${t.unread}</span>` : ""}</b><span>${esc(t.last_body || "No messages yet")}</span></div>`).join("");
  $("ch-threads").querySelectorAll("[data-op]").forEach(e => e.onclick = async () => { ch.op = Number(e.dataset.op); await loadThread(true); loadThreads(); });
}

async function loadThread(reset) {
  if (!ch.op) { $("ch-title").textContent = "Messages"; $("ch-msgs").innerHTML = `<div class="empty">No operators yet.</div>`; return; }
  if (reset) { ch.last = 0; $("ch-msgs").innerHTML = ""; }
  const admin = state.user.role === "admin";
  $("ch-title").textContent = admin ? (ch.threads.find(t => t.id === ch.op) || {}).name || "Messages" : "Chat with the admin";
  const { messages } = await api("GET", `/api/chat/messages?operator_id=${ch.op}&after=${ch.last}`);
  if (!messages.length) { if (reset) $("ch-msgs").innerHTML = `<div class="empty">No messages yet. Say hello.</div>`; return; }
  const box = $("ch-msgs"), atBottom = reset || box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  if (box.querySelector(".empty")) box.innerHTML = "";
  box.insertAdjacentHTML("beforeend", messages.map(m => `<div class="msg ${m.sender_role === state.user.role ? "me" : ""}">${esc(m.body)}<small>${esc(m.sender_name)} · ${msgTime(m.created_at)}</small></div>`).join(""));
  ch.last = messages[messages.length - 1].id;
  if (atBottom) box.scrollTop = box.scrollHeight;
  updateBadge();
}

async function pollChat() {
  try { if (state.user.role === "admin") await loadThreads(); await loadThread(false); } catch (e) { /* try again in 4 seconds */ }
}

async function sendChat() {
  const input = $("ch-input"), text = input.value.trim();
  if (!text) return;
  if (!ch.op) return toast("There is no operator to message yet.", true);
  try {
    await api("POST", "/api/chat/messages", { operator_id: ch.op, body: text });
    input.value = ""; await loadThread(false);
    if (state.user.role === "admin") loadThreads();
  } catch (e) { toast(e.message, true); }
}
$("ch-send").onclick = sendChat;
$("ch-input").onkeydown = e => { if (e.key === "Enter") sendChat(); };

// ---------- boot ----------
(async function boot() {
  state.meta = await api("GET", "/api/meta");
  const { states, batches } = state.meta;
  $("a-batch").innerHTML = opts(batches);
  $("f-origin").innerHTML = opts(states, "Any state"); $("f-dest").innerHTML = opts(states, "Any state");
  $("f-batch").innerHTML = opts(batches, "Any batch");
  $("o-origin").innerHTML = opts(states); $("o-origin").value = "Lagos"; $("o-dest").innerHTML = opts(states, null); $("o-dest").selectedIndex = 14; $("o-batch").innerHTML = opts(batches);
  const tomorrow = new Date(Date.now() + 864e5).toISOString().slice(0, 10); $("o-date").value = tomorrow;
  if (state.token) {
    try { state.user = (await api("GET", "/api/me")).user; return enterApp(); } catch (e) { localStorage.removeItem("token"); state.token = null; }
  }
  showAuth();
})();
