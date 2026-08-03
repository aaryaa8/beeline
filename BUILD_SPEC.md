# Beeline — Build Spec

**Memory Meets Motion hackathon, 3 Aug 2026, Frontier Tower SF. Solo build, ~6 hours.**

This spec is written to be dispatched to multiple agents in parallel. Section 4
freezes the shared contracts (data model, events, API, messages). As long as
every agent builds to Section 4, the work packages in Section 5 can be done
independently and will fit together. Each work package ends with a ready-to-paste
agent brief.

---

## 0. The problem statement, and why we repeat it

Every decision below is checked against the official brief. Keep it in view.

> **Theme.** Build AI that **remembers** (Memory: durable, structured, queryable
> context that persists across sessions, users and time) and **acts** (Motion:
> orchestrate tools, take real-time action, coordinate multiple agents, respond
> to live data). "An AI that knows things and does things because of what it knows."

> **Mandated stack — all four must be load-bearing. Judges specifically check
> real usage of each. A one-line SDK import that is never called again will not
> count.**
> - **FalkorDB** = the memory layer. A graph of entities and relationships, used
>   as the retrieval backend, written to continuously so memory compounds.
> - **RocketRide.ai** = the motion / orchestration layer. Reads memory, decides
>   and executes the next action, sequences multi-step tool calls.
> - **Guild.ai** = the multi-agent / coordination layer. Specialist agents that
>   hand off, plus human-in-the-loop moments.
> - **LaserData** = the real-time data layer. Live/streaming input the agent
>   reacts to, paired with FalkorDB which persists it into long-term memory.

> **Reference architecture (their words):** LaserData brings in what is happening
> now → FalkorDB remembers what has ever happened → RocketRide decides and acts on
> both → Guild.ai coordinates the agents doing it → the user sees the result.

**Prizes:** RocketRide $1,000 (best use of RocketRide Cloud, deployed pipeline,
zero infra managed) and a $250 social post. Submit via `/submit` in the
RocketRide Discord by the deadline.

Every work package in Section 5 names which mandated technology it makes
load-bearing. If a change does not serve one of the four, it is out of scope.

---

## 1. What we are building

**Beeline**: live event networking that routes you physically to the person you
should meet, gated by how people feel, learning from what happened.

The name is the pitch. A beeline is the shortest direct path to something. Beeline
draws you a path to the right person and gets you there.

**"Memory Meets Motion" is claimed three ways, all load-bearing:**

1. **Memory** — the room's graph in FalkorDB. Who is here, what they care about,
   what they need and offer, who knows whom, who has met, how meetings felt. It
   compounds through the event and persists after it (the "across time" clause).
2. **Motion (spatial)** — RocketRide computes and delivers a physical route
   through the room: "go to the Window, say hi to Chen on the way, he knows Lin."
3. **E-motion** — self-reported emotional state that **gates** routing, plus
   one-tap post-meeting feedback that **teaches** the match graph. How you feel
   changes where you are sent next.

**Design principle, stated on stage:** emotion is **self-reported only, never
sensed.** No cameras, no microphones, no covert inference. One tap. This is the
ethical spine and it doubles as the Guild human-in-the-loop story.

**The matching insight:** people come to rooms with **asks and offers**, not just
interests. The person worth meeting is the one whose offer matches your ask,
reachable through someone who can vouch for you. Matching is intent-first.

---

## 2. What already exists (build on it, do not restart)

A working spine is in this repo. Do not rebuild from scratch; extend these files.

| File | Today | Beeline change |
|---|---|---|
| `src/overlap/memory.py` | FalkorDB graph: Person, Topic, Location; interest / met / nudge; `candidates`, `warm_intro`, `snapshot`, `stats` | Add Capability (ask/offer), Zone, state, affinity; `route`, `room_energy`; extend snapshot (WP1) |
| `src/overlap/agents.py` | `matchmaker_local`, `critic_local`, `propose`, `review`, local+guild backends | Restructure to matchmaker / empath / router (WP2) |
| `src/overlap/motion.py` | 5-step local pipeline + cloud handoff | Add new event types + 3-agent flow (WP3) |
| `src/overlap/stream.py` | Local + Laser stream, event types checkin/interest/met/location | Add intent/position/state/feedback (WP4) |
| `src/overlap/seed.py` | 12 synthetic attendees + acquaintances | Add ask/offer, zones, states, a demo movement script (WP4) |
| `src/overlap/server.py` | FastAPI + SSE, seed/reset/event endpoints | Add check-in, state, position, feedback, energy, `/join` (WP5) |
| `web/index.html` | Force-directed interest graph | Replace with floor-plan map + path animation (WP6) |
| `guild/*.agent.ts` | matchmaker + critic stubs | matchmaker / empath / router (WP2) |
| `config.py` | backends, tuning knobs | Add ZONES / STATES / FEEDBACK enums (WP4) |

Backends all default to `local` and flip to real with one env var each
(`FALKOR_BACKEND`, `STREAM_BACKEND`, `MOTION_BACKEND`, `GUILD_BACKEND`). Keep that
property. The demo must run fully on `local`; the real services are the upgrade.

---

## 3. System shape

```
  phone /join page ──▶ POST /api/checkin, /state, /position, /feedback
        (real live input, judges use it)         │
                                                  ▼
                    LaserData stream  ◀── the real-time layer (mandate)
                                                  │  events: checkin, interest,
                                                  │  intent, position, state,
                                                  ▼  met, feedback
        RocketRide Motion pipeline  ◀── the orchestration layer (mandate)
          1. persist event ─────────▶ FalkorDB  ◀── the memory layer (mandate)
          2. matchmaker (who)   ┐
          3. empath   (gate)    ├──── Guild agents ◀── coordination layer (mandate)
          4. router   (path)    ┘
          5. deliver nudge / hold
                                                  │
                                                  ▼
                         SSE ──▶ floor-plan map (the user sees the result)
```

---

## 4. FROZEN CONTRACTS — every agent builds to these

Do not change anything in this section without updating the spec and telling the
other agents. These are the seams that let parallel work fit together.

### 4.1 Enums (define in `config.py`)

```python
ZONES  = ["Window", "Coffee", "Stage", "Cafe"]      # floor-16 zones
STATES = ["open", "heads-down", "recharging", "in-flow"]
FEEDBACK = ["clicked", "neutral", "not-for-me"]
# GENERIC_TOPICS already exists in agents.py; reuse it.
```

**State semantics (the gate):** only a person in state `open` participates in
active routing, as either the target of an introduction or the recipient of a
nudge. `heads-down`, `in-flow`, `recharging` are paused, with different copy:
- `heads-down` / `in-flow`: "building, do not disturb" — skip silently.
- `recharging`: "needs a minute" — hold and retry when they return to `open`.

### 4.2 Event schema (LaserData / stream)

`make_event(kind, **payload)` returns `{id, type, ts, payload}`.

| type | payload | meaning |
|---|---|---|
| `checkin` | `{id, name, role, interests[], ask, offer, zone, state}` | person arrives |
| `interest` | `{id, topic}` | add one interest |
| `intent` | `{id, ask?, offer?}` | set/update what they want / offer |
| `position` | `{id, zone}` | person moved to a zone |
| `state` | `{id, state}` | person changed emotional state |
| `met` | `{a, b}` | two people met (prior history or live) |
| `feedback` | `{from_id, to_id, value}` | one-tap after a meeting; value ∈ FEEDBACK |

### 4.3 Graph model (FalkorDB)

Nodes:
- `(:Person {id, name, role, checked_in_at, state})`
- `(:Topic {name})`
- `(:Capability {name})`  — normalized ask/offer tag
- `(:Zone {name})`

Edges:
- `(:Person)-[:INTERESTED_IN {weight}]->(:Topic)`
- `(:Person)-[:WANTS]->(:Capability)`   — their ask
- `(:Person)-[:OFFERS]->(:Capability)`  — their offer
- `(:Person)-[:AT {since}]->(:Zone)`
- `(:Person)-[:MET {at}]->(:Person)`
- `(:Person)-[:NUDGED {at, status}]->(:Person)`  — status: approved|vetoed|held
- `(:Person)-[:FELT {value, at}]->(:Person)`      — affinity from feedback

**Complementary match** = my `WANTS` ∩ their `OFFERS` (or their `WANTS` ∩ my
`OFFERS`). This is the intent-first matching, and it is a relationship-aware graph
query a flat vector store cannot express — that is the FalkorDB "load-bearing"
argument, so keep it a real traversal.

### 4.4 Memory API (method signatures on `Memory`)

```
record_checkin(id, name, role, interests, ask, offer, zone, state)
set_intent(id, ask, offer)
record_interest(id, topic, weight=1.0)
set_state(id, state)
record_position(id, zone)
record_met(a, b)
record_nudge(a, b, status)
record_feedback(from_id, to_id, value)

candidates(id, limit=5) -> [ {id, name, role, zone, state,
                              shared_topics[], complements[],  # capability names
                              overlap, affinity} ]             # both parties open only
warm_intro(a, b) -> {connector_id, connector_name, via_topics[]} | None
route(a, b)      -> {target_zone, hops:[{id, name, zone, reason}]}  # people passed, ordered
affinity(a, b)   -> float                # from FELT edges + similarity to liked/disliked
room_energy()    -> [ {zone, open, "heads-down", recharging, "in-flow"} ]
have_met(a, b) -> bool
person(id) -> {...} | None
snapshot() -> see 4.6
stats() -> {people, topics, capabilities, met, nudges, feedback}
```

### 4.5 HTTP API (`server.py`)

Keep existing routes. Add or change:

| Method + path | body / query | does |
|---|---|---|
| `GET /` | — | serves the map (`web/index.html`) |
| `GET /join` | — | serves the phone check-in page (`web/join.html`) |
| `POST /api/checkin` | `{name, interests[], ask, offer, zone, state}` | publishes `checkin`; returns `{id}` |
| `POST /api/state` | `{id, state}` | publishes `state` |
| `POST /api/position` | `{id, zone}` | publishes `position` |
| `POST /api/feedback` | `{from_id, to_id, value}` | publishes `feedback` |
| `GET /api/route` | `?a=&b=` | returns `route(a,b)` |
| `GET /api/energy` | — | returns `room_energy()` |
| `POST /api/event` | `{type, payload}` | generic publish (exists) |
| `POST /api/seed` | `{delay?}` | runs the demo sequence (exists) |
| `POST /api/reset` | — | clears graph (exists) |
| `GET /api/stream` | — | SSE (exists), now also emits `energy` |

### 4.6 SSE message + snapshot shape (frozen for the frontend)

SSE frames are `{kind, data}`. Kinds: `graph`, `stats`, `event`, `trace`,
`outbox`, `energy`, `reset`, `error`.

`snapshot()` (the `graph` payload) is:

```json
{
  "zones": ["Window","Coffee","Stage","Cafe"],
  "nodes": [
    {"id":"a1","kind":"person","label":"Priya","role":"ml engineer",
     "zone":"Window","state":"open"},
    {"id":"topic:retrieval","kind":"topic","label":"retrieval","weight":3},
    {"id":"cap:hiring-design","kind":"capability","label":"hiring: design"}
  ],
  "edges": [
    {"source":"a1","target":"topic:retrieval","kind":"interest"},
    {"source":"a1","target":"a5","kind":"met"},
    {"source":"a1","target":"a2","kind":"nudge","status":"approved"}
  ]
}
```

A delivered introduction (the `outbox` kind) is:

```json
{"at":..., "to":"Priya", "to_id":"a1", "about":"Marcus", "about_id":"a2",
 "message":"...", "why":"...", "connector":{"connector_name":"Chen",...},
 "route":{"target_zone":"Stage","hops":[{"name":"Chen","zone":"Coffee","reason":"knows Marcus"}]},
 "match":{"complements":["hiring: design"], "shared_topics":["agent memory"]}}
```

---

## 5. Work packages

Dependency and parallelism at a glance:

```
Phase A (parallel):   WP1 memory      WP4 stream+seed
Phase B (parallel):   WP2 agents(needs WP1)   WP6 map UI(mock)   WP7 phone UI(mock)
Phase C:              WP3 motion(needs WP1,WP2)   WP5 server(needs WP1,WP3,WP4)
Phase D:              integrate WP6/WP7 to real WP5   WP8 demo+resilience
```

Frontend WP6/WP7 can start immediately against the frozen Section 4 contracts
using mock data, then point at the real API in Phase D.

---

### WP1 — Memory layer (FalkorDB) · makes the MEMORY mandate load-bearing

**Goal.** Extend `memory.py` so the graph carries intent (ask/offer), zones,
state, and affinity, and answers the intent-first and routing queries.

**Files.** `src/overlap/memory.py`, `scripts/test_memory.py`.

**Tasks.**
1. Migrate the `Location` label/relationship to `Zone` (keep `AT`).
2. Add `Capability` nodes with `WANTS` / `OFFERS` edges. Normalize ask/offer text
   to lowercase capability tags (a helper that lowercases and trims; an LLM tidy
   is optional and must degrade to the plain version).
3. Add `state` on Person; `set_state`, `record_position`, `set_intent`,
   `record_feedback` (writes `FELT`).
4. Rewrite `candidates` to score intent-first: complementary capabilities first,
   then specific shared interests, exclude met/nudged, and **only return people
   where both parties are `open`.** Return `complements`, `zone`, `state`,
   `affinity`.
5. Implement `warm_intro` (already exists, keep the 3-hop), `route(a,b)`
   (target's zone + people you would pass, ordered: same zone as you first, then
   a known connector, then target's zone), `affinity(a,b)`, `room_energy()`.
6. Extend `snapshot()` to the exact 4.6 shape (zones, person zone+state,
   capability nodes) and `stats()` to include capabilities and feedback.
7. Extend `scripts/test_memory.py` to assert: a complementary-intent match is
   chosen over a mere shared-interest one; a `recharging` person is excluded from
   candidates; `route` returns an ordered path including a connector; feedback
   shifts `affinity`.

**Cypher caution (already hit once):** pattern predicates inside `CASE` evaluate
wrong in FalkorDB. Use `OPTIONAL MATCH` + null check instead.

**Acceptance.** `.venv/bin/python scripts/test_memory.py` prints all asserts pass,
against local FalkorDB on `:6379`.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP1. Working dir
> `/Users/aaryaakamdar/Desktop/overlap`. Extend `src/overlap/memory.py` to the
> frozen graph model (4.3) and Memory API (4.4), and grow
> `scripts/test_memory.py` to prove the WP1 acceptance criteria against the local
> FalkorDB already running on port 6379. Do not touch other files. Preserve the
> local/cloud backend switch. Watch the CASE/pattern-predicate gotcha noted in WP1.

---

### WP2 — Agents (Guild) · makes the COORDINATION mandate load-bearing

**Goal.** Three specialist agents with distinct jobs and a human-in-the-loop gate.

**Files.** `src/overlap/agents.py`, `guild/matchmaker.agent.ts`,
`guild/empath.agent.ts`, `guild/router.agent.ts` (rename `critic.agent.ts`).

**The three agents.**
- **matchmaker** — picks the single best person to introduce, using
  `candidates` + `warm_intro`. Score = `2.0*len(complements) +
  1.0*len(specific_shared) + affinity_adjust + (0.5 if warm_intro)`. Produces the
  proposal (from/to, complements, shared_topics, connector, confidence, message).
  Writes the nudge message (LLM if `ANTHROPIC_API_KEY`, else template; no em
  dashes, no exclamation marks).
- **empath** — the gate and the learning. On a proposal: veto/hold if the target
  or recipient is not `open` (reason references the state), enforce the nudge
  cooldown (only `approved` nudges start it — a hold or veto must not), and reject
  a match whose only overlap is generic. On a `feedback` event: update affinity
  (this is the learning loop; `not-for-me` suppresses that person and downweights
  similar, `clicked` boosts).
- **router** — given an approved proposal, call `route(a,b)`, attach the ordered
  path and the on-the-way connector, and fold it into the delivered message.

Keep the `local` and `guild` backends behind one interface, as today. The TS
agents mirror the Python logic exactly; nothing crosses the boundary but JSON.

**Acceptance.** A fixture test (`scripts/test_agents.py`) on a small in-memory
graph shows: complementary intent beats shared interest in selection; empath holds
when the target is `recharging` and approves when `open`; router returns a path
containing the expected connector; a `not-for-me` feedback lowers that pair's next
score.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP2. Restructure `src/overlap/agents.py`
> into matchmaker / empath / router with the scoring and gating in WP2, keeping
> the local+guild backends behind one interface. Update the TS agents in `guild/`
> to match (rename `critic.agent.ts` to `router.agent.ts`, add `empath.agent.ts`).
> Add `scripts/test_agents.py` proving the acceptance criteria. Depends on the
> Memory API in section 4.4; assume those methods exist.

---

### WP3 — Motion pipeline (RocketRide) · makes the MOTION mandate load-bearing

**Goal.** One orchestrated pass per event: persist → matchmaker → empath gate →
router → deliver, with a readable trace and per-event latency.

**Files.** `src/overlap/motion.py`.

**Tasks.**
1. `_persist` handles all 4.2 event types (checkin, interest, intent, position,
   state, met, feedback). A `met` writes memory but does not trigger a nudge. A
   `feedback` routes to empath's learning update, not to matchmaking.
2. The pipeline: matchmaker proposes → empath approves / holds / vetoes → if
   approved, router attaches the path → deliver to the outbox. Record the nudge
   with the right status (approved | held | vetoed).
3. Trace steps named `memory.write`, `matchmaker`, `empath`, `router`, `action`,
   each with a one-line human-readable detail and the approve/hold/veto flag.
4. Keep `_handle_cloud` as the RocketRide-Cloud path (same return shape); it stays
   a placeholder until the `.pipe` is built at the event.

**Acceptance.** Feeding a scripted event list yields the expected mix of delivered
introductions, holds (state), and vetoes (generic/cooldown), and a `feedback`
event visibly shifts the next matchmaker choice. Latency per event recorded.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP3. Extend `src/overlap/motion.py` to the
> 3-agent flow and the new event types, with a named trace and latency. Depends on
> WP1 (Memory API 4.4) and WP2 (agents). Preserve the local/cloud switch and the
> return shapes in 4.6. Do not touch the frontend or server.

---

### WP4 — Stream + seed + enums (LaserData) · makes the REAL-TIME mandate load-bearing

**Goal.** New live event types, and a seeded room plus a scripted demo sequence.

**Files.** `src/overlap/stream.py`, `src/overlap/seed.py`, `src/overlap/config.py`.

**Tasks.**
1. `config.py`: add `ZONES`, `STATES`, `FEEDBACK` (4.1).
2. `stream.py`: add event kinds/constructors for `intent`, `position`, `state`,
   `feedback` (rename `location`→`position`). Keep both `LocalStream` and
   `LaserStream` behind one interface; `LaserStream` keeps the local mirror so a
   dead network can never brick the demo.
3. `seed.py`: give the 12 attendees `interests`, `ask`, `offer`, a starting
   `zone` and `state`, plus the existing acquaintances (for warm-intro paths).
4. `seed.py`: add `demo_sequence()` — a deterministic, well-paced script that
   (a) loads history, (b) checks people in, (c) moves a few between zones, (d)
   flips one person to `recharging` right before a nudge would fire (to show the
   gate), (e) fires a `feedback` that visibly shifts the next match. This is the
   choreography WP8 presents.

**Acceptance.** `demo_sequence()` run through the pipeline populates the graph with
zones and states, produces at least one delivered intro, one state-hold, and one
feedback-driven shift, deterministically.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP4. Add the new event types to
> `src/overlap/stream.py`, the enums to `config.py`, and rebuild `src/overlap/seed.py`
> with ask/offer/zone/state plus a deterministic `demo_sequence()` that showcases a
> delivered intro, a state-gated hold, and a feedback-driven learning shift. Keep the
> LocalStream/LaserStream interface and the local mirror fallback.

---

### WP5 — Server + API (glue) · wires real-time in and results out

**Goal.** Expose the new endpoints, serve both pages, broadcast the new SSE kinds.

**Files.** `src/overlap/server.py`.

**Tasks.** Implement the 4.5 endpoints; serve `web/join.html` at `/join`; in the
consume loop, broadcast `energy` alongside `graph`/`stats`/`trace`/`outbox`; make
`POST /api/checkin` mint an id and publish. Keep the SSE keepalive and the
per-event broadcast that only emits `outbox` when a delivery actually happened.

**Acceptance.** Each endpoint returns correctly via `curl`; the SSE stream emits
`energy` after events; a `POST /api/checkin` results in a new person on the next
`graph` frame.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP5. Extend `src/overlap/server.py` with the
> endpoints in 4.5 and the `energy` broadcast, serving `web/join.html` at `/join`.
> Depends on WP1, WP3, WP4. Do not change the frozen SSE/snapshot shapes in 4.6.

---

### WP6 — The map UI (the headline) · where "the user sees the result"

**Goal.** A floor-plan of Frontier 16 with four zones, people as dots in their
zone colored by state, live over SSE, with a path that animates to a match.

**Files.** `web/index.html` (may split out `web/app.js`, `web/style.css`).

**Tasks.**
1. Draw the four zones as labeled regions (clean, projector-legible, dark theme).
2. Place person dots inside their current zone; move them smoothly on `position`
   events; color by state (open = warm/bright, heads-down = blue, recharging =
   dim amber, in-flow = violet) with a small legend.
3. On a delivered `outbox` intro, animate a path: you → connector (if any) →
   target, across the zones, and surface the message + why + the intent match
   ("hiring: design ↔ looking for design work").
4. Left/side panel: agent activity showing matchmaker / empath / router steps
   (empath holds and vetoes clearly marked), and the introductions list.
5. Stretch: a soft energy heat layer per zone from `energy`. First thing cut if
   time is short.
6. No external CDNs; inline everything; no build step. Must survive venue wifi.

**Acceptance.** Loads with no console errors; `Fill the room` (calls `/api/seed`)
populates zones; a path animates on a delivered intro; states are visibly colored;
`position` events move dots. Verify in the in-app browser.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP6. Rebuild `web/index.html` as a
> floor-plan map (4 zones) driven by the SSE contract in 4.6: dots in zones, state
> colors, an animated path on delivered intros, and an agent-activity panel. No
> CDNs, no build step, dark theme, projector-legible. You can develop against mock
> SSE data matching 4.6 before the backend is live.

---

### WP7 — Phone check-in + feedback (LaserData input + human-in-the-loop)

**Goal.** The real live data source and the consent/feedback surface.

**Files.** `web/join.html`.

**Tasks.**
1. A phone-friendly form: name, a few interest tags, one **ask** and one
   **offer**, current **zone** (4 buttons), starting **state** (4 buttons).
   Submits to `POST /api/checkin`.
2. After check-in, a lightweight "I'm here now" state re-tap (posts `/api/state`)
   and a zone re-tap (posts `/api/position`).
3. A one-tap **post-meeting feedback** control (clicked / neutral / not-for-me)
   that posts `/api/feedback`. This is the learning loop's input and the visible
   human-in-the-loop moment.
4. A `/join` QR helper on the main map so judges can scan and join live.

**Acceptance.** Submitting on a phone (or a second browser tab) makes a dot appear
on the map within a second, and a feedback tap shows up in the trace.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP7. Build `web/join.html`: a phone-first
> check-in (name, interests, ask, offer, zone, state) posting to `/api/checkin`,
> plus state/zone re-taps and a one-tap post-meeting feedback control posting to the
> 4.5 endpoints. Mobile-legible, no build step. Assume the endpoints from WP5.

---

### WP8 — Demo, resilience, submission

**Goal.** A rehearsed 90-second demo, graceful fallback, and a clean submission.

**Files.** `README.md`, `DEMO.md`, `scripts/verify_services.py` (exists).

**Tasks.**
1. `DEMO.md`: the 90-second script, four beats mapped one-per-mandated-tech plus
   the emotion beat, with the exact clicks and the one live judge check-in.
2. Resilience pass: confirm `STREAM_BACKEND=laser` falls back to the local mirror,
   `MOTION_BACKEND=cloud` falls back cleanly, and the map degrades if `energy` is
   absent. The demo must be green on all-`local`.
3. Submission checklist: `/submit` fields, the RocketRide $1,000 framing (deployed
   pipeline, zero infra), and a drafted $250 social post.

**Acceptance.** A dry run of `DEMO.md` on all-local backends works end to end with
no dead ends.

**Agent brief.**
> Read `BUILD_SPEC.md` sections 0–4 and WP8. Write `DEMO.md` (90-second script,
> four mandated-tech beats + the emotion beat + one live check-in), do a resilience
> pass so the whole thing is green on all-`local` backends, and draft the `/submit`
> and social-post checklist. Do not change app logic.

---

## 6. The 90-second demo (target, refined in WP8)

Each beat names the mandated technology it proves.

1. **Empty floor plan.** "This is floor 16 right now, and it knows nothing." →
   sets up **the memory that is about to compound**.
2. **Press Fill the room.** Dots stream into zones and start moving. → **LaserData**:
   live, and every dot is an event.
3. **The graph fills underneath.** Point at an ask/offer match. → **FalkorDB**: it
   remembers what people need and offer, not just what they like.
4. **A path animates to your match.** "It is not saying who. It is saying go this
   way, and say hi to Chen on the way, he knows her." → **RocketRide**: memory
   becomes a physical route it hands you.
5. **You tap recharging, a nudge holds.** "It saw I needed a minute." Then a
   `not-for-me` tap and the next suggestion shifts. → **Guild**: three agents, one
   of them allowed to say no, and the system learning from how a meeting felt.
6. **Scan the QR, check in a judge live, route them to another judge.** Close.

---

## 7. Definition of done (checked against the mandate)

- [ ] **FalkorDB** holds people, interests, ask/offer, zones, states, met, feedback,
      and answers the intent-first, warm-intro, and route queries. (Memory)
- [ ] **RocketRide** orchestrates persist → decide → act per event, and the
      `.pipe` is deployed to Cloud at the event. (Motion, and the $1,000 prize)
- [ ] **Guild** runs matchmaker + empath + router with a real veto/hold and the
      feedback learning loop. (Coordination)
- [ ] **LaserData** carries the live check-in / position / state / feedback stream,
      fed by real phones. (Real-time)
- [ ] The demo runs green on all-`local` backends, and each real backend flips on
      with one env var.
- [ ] Every one of the four is load-bearing: removing it breaks the demo. (This is
      the exact test the judges said they apply.)
