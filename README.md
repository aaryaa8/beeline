# Beeline

**Live event networking that remembers who is in the room and draws you a path to the person you should meet.**

Built for the **Memory Meets Motion** hackathon (Frontier Tower, San Francisco).

> Most networking apps tell you two people are *similar*. Beeline tells you who
> can *introduce* you, and walks you there.

- **Live demo:** open the link in the submission (a public HTTPS URL). Press the
  main button to watch a self-narrating 90-second demo, or scan the on-screen QR
  to add yourself from your phone.
- **Code:** https://github.com/aaryaa8/beeline

---

## The idea: three meanings of "motion"

The event is called *Memory Meets Motion*. Beeline takes all three readings of
motion, and each is load-bearing:

1. **Memory** — a live graph of who is here, what they care about, and crucially
   what they **need** vs **offer**.
2. **Motion (physical)** — it draws you a route across the room: "go to the
   Kitchen, say hi to Priya on the way, she knows Marcus."
3. **E-motion** — a self-reported state (open / heads-down / recharging / in-flow)
   that **gates** the nudge, plus a one-tap after-meeting feeling that **teaches**
   the match graph. How you feel changes where you are sent next.

The matching insight: people come to rooms with **asks and offers**, not just
interests. The best person to meet is the one whose offer answers your ask,
reachable through someone who can vouch for you.

Emotion is **self-reported only, never sensed** (no camera, no microphone). That
is the ethical spine and the human-in-the-loop moment.

---

## The four sponsor technologies, each load-bearing

Remove any one and the demo breaks:

| Layer | Tech | Its job in Beeline |
|---|---|---|
| **Real-time** | **LaserData** | Every check-in, move, state change and feeling enters as a live event on a durable stream. |
| **Memory** | **FalkorDB** | The graph. Multi-hop traversal finds the warm-intro path (you → shared topic → connector → them) and the ask/offer complement — things a flat vector search cannot express. |
| **Motion** | **RocketRide** | Orchestrates persist → match → gate → write-opener → route → deliver, and a deployed RocketRide Cloud pipeline (Claude Sonnet) generates the openers. |
| **Coordination** | **Guild.ai** | Four specialist agents — **matchmaker** (who), **icebreaker** (the opener), **empath** (approve / hold / veto + learning), **router** (the path) — published on Guild. |

The single query that justifies a graph, in `src/overlap/memory.py`:

```cypher
MATCH (a:Person)-[:INTERESTED_IN]->(t:Topic)<-[:INTERESTED_IN]-(c:Person)-[:MET]-(b:Person)
RETURN c AS connector, collect(DISTINCT t.name) AS via
```

Three hops. The answer to "why should I trust this" is a person, not a score.

---

## What you can do in the app

- **Watch the guided demo** — a self-narrating 90-second story: people check in,
  the graph fills, an intro fires with a routed path, an agent vetoes a weak
  match, someone recharging holds an intro, and a thumbs-down shifts the next
  suggestion.
- **Add yourself** — scan the QR (or open `/join`) to check in from your phone
  with your name, interests, one ask, one offer, your area, and your state. A
  beeline can then land on your phone.
- **Configure the space** — rename, add, or remove areas for any venue, or **scan
  the room** with a photo and an agent suggests areas and architectural
  landmarks.
- **Optimize the room** — an agent reads the live room and suggests moves that
  turn unmet shared interests into introductions.
- Light theme by default, with a dark toggle.

---

## Run it locally

Needs Python 3.13 and a local FalkorDB (redis + the FalkorDB module, included
under `.local/` when set up; see below).

```bash
# 1. memory: local FalkorDB (redis + the falkordb module)
/opt/homebrew/opt/redis/bin/redis-server --port 6379 --loadmodule "$PWD/.local/falkordb.so" --daemonize yes

# 2. the app (serves the map, the phone page, and the live API)
uv venv && uv pip install -e .
.venv/bin/uvicorn overlap.server:app --app-dir src --port 8113
```

Open http://localhost:8113 and press the main button.

Every external service defaults to a `local` in-process backend, so the app runs
with zero credentials. Flip each to the real cloud service with one env var
(`FALKOR_BACKEND`, `STREAM_BACKEND`, `MOTION_BACKEND`, `GUILD_BACKEND`); see
`.env.example`. Check credentials with `scripts/verify_services.py`.

Give it a public HTTPS URL (so phone cameras and the QR work):

```bash
bash scripts/tunnel.sh
```

---

## Architecture

```
 phone /join ─▶ POST /api/checkin,/state,/position,/feedback
                       │
                       ▼
     LaserData stream  ◀── real-time layer
                       │  events
                       ▼
   RocketRide motion   ◀── orchestration
     persist ─────────▶ FalkorDB  ◀── memory (the graph)
     matchmaker ┐
     empath     ├──── Guild agents ◀── coordination
     icebreaker │
     router     ┘
     deliver
                       │
                       ▼
            SSE ─▶ the floor-plan map (you see the result)
```

Code map: `src/overlap/memory.py` (FalkorDB graph + queries), `agents.py` (the
four agents, local mirror of the published Guild agents), `motion.py` (the
RocketRide pipeline), `stream.py` (LaserData events), `space.py` (the room-scan
and optimizer agents), `server.py` (FastAPI + SSE), `web/` (the map and the phone
page), `guild/` (the publishable Guild agents), `scripts/` (tests + tooling).
