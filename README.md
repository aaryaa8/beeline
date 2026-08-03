# Overlap

**Memory Meets Motion hackathon — 3 Aug 2026, Frontier Tower SF.**

Live networking memory for an event. The room checks in, the system builds a
graph of who is here and what they care about, and two agents argue about who
should meet whom before anything reaches a person.

The pitch in one sentence: **most systems can tell you two people are similar;
this one tells you who can introduce them.**

---

## Why this shape

The mandate is that all four sponsor technologies must be load-bearing. Here
each one does a job the others cannot:

| Layer | Tech | Job here | What breaks without it |
|---|---|---|---|
| Real-time | **LaserData** | Every fact enters as an event on a durable stream. Check-ins, interests, introductions. | The graph becomes a database someone typed into, not a consequence of the room. |
| Memory | **FalkorDB** | The graph. Multi-hop traversal finds the warm-intro path. | No warm intros. You are back to similarity scores. |
| Motion | **RocketRide** | Orchestrates the five steps per event and executes the delivery. | Nothing sequences retrieval, coordination and action. |
| Coordination | **Guild.ai** | Matchmaker proposes, critic vetoes. Two specialists, not one prompt. | Every proposal ships. The system spams the room. |

**The single query that justifies a graph** is `warm_intro` in
`src/overlap/memory.py`:

```cypher
MATCH (a:Person {id: $a})-[:INTERESTED_IN]->(t:Topic)
      <-[:INTERESTED_IN]-(c:Person)-[:MET]-(b:Person {id: $b})
RETURN c.id, c.name, collect(DISTINCT t.name) AS via
```

Three hops. The answer to "why should I trust this" is a person, not a score.
Put that on screen and the FalkorDB half of the pitch makes itself.

---

## Run it

FalkorDB is already running locally (redis + the FalkorDB module in `.local/`).
If it is not:

```bash
/opt/homebrew/opt/redis/bin/redis-server --port 6379 --loadmodule /Users/aaryaakamdar/Desktop/overlap/.local/falkordb.so
```

Then:

```bash
cd /Users/aaryaakamdar/Desktop/overlap && .venv/bin/uvicorn overlap.server:app --app-dir src --port 8113 --reload --reload-dir src --reload-dir web
```

Open http://localhost:8113 and press **Fill the room**.

Check every credential as it comes online:

```bash
cd /Users/aaryaakamdar/Desktop/overlap && .venv/bin/python scripts/verify_services.py
```

---

## Current state (accounts set up 2 Aug, night before)

App verified working end to end on local backends:

- 12 attendees stream in, graph builds live, 13 topics, 4 prior acquaintances
- 3 introductions delivered, 5 vetoed, all three veto reasons firing
- 2 of the 3 introductions found a real warm-intro path (via Priya, via Chen)
- End-to-end latency per event: **7–8ms**

All four accounts are created and their state is captured in `.env`. Where each
one stands (run `scripts/verify_services.py` to re-check):

| Service | Account | Verified? | Note |
|---|---|---|---|
| **FalkorDB** | local + cloud instance "overlap" (us-west-2, running) | local: **green** / cloud: pending | cloud password needs a 30s manual re-set (see below) |
| **RocketRide** | token created | **green** | `/status` 200, server v3.3.0.198 |
| **LaserData** | deployment "starter-O0jtl" live | host+creds captured | Warden proxy path is the one open question for the mentor |
| **Guild.ai** | CLI authenticated as aaryaa.kamdar | ready to publish | agents in `guild/` |

The demo runs on `local` backends, which are all working. Flipping each to the
real service is one env var and changes no other code:

| Env var | `local` (demo default) | switch to |
|---|---|---|
| `FALKOR_BACKEND` | localhost | `cloud` |
| `STREAM_BACKEND` | in-process queue | `laser` |
| `MOTION_BACKEND` | in-process pipeline | `cloud` |
| `GUILD_BACKEND` | in-process agents | `guild` |

### The one manual step I could not do

The FalkorDB Cloud instance is running and its host/port/user are correct in
`.env`, but the generated password did not capture cleanly and I do not type
passwords into fields. To finish it: FalkorDB console -> select the `overlap`
instance -> **Modify** -> set a password you type -> paste it into
`FALKORCLOUD_PASSWORD` in `.env` -> set `FALKOR_BACKEND=cloud` -> re-run
`scripts/test_memory.py`. Two minutes. The local FalkorDB works regardless, so
this is not blocking the demo.

---

## Tomorrow, in order

The build window is roughly 3.5 hours (11:00, lunch 12:00–13:00, submit 15:30).
Confirm whether it is really 8 hours in the Discord.

1. **Fill in `.env`** from the four accounts. Run `verify_services.py` until
   four greens. Do not start anything else until this passes.
2. **Flip `FALKOR_HOST`** to FalkorDB Cloud and re-run
   `scripts/test_memory.py`. Every Cypher query is exercised there. This is the
   highest-risk swap because the cloud instance may differ on pattern
   predicates, and one query already needed rewriting for exactly that reason.
3. **Build the RocketRide pipeline** on the visual canvas so it mirrors
   `Motion._handle_local`: persist → traverse → matchmaker → critic → deliver.
   Deploy to Cloud, set `MOTION_BACKEND=cloud`. This is the $1,000 prize, so it
   deserves the most time.
4. **Publish the two Guild agents** from `guild/`, set `GUILD_BACKEND=guild`.
5. **Point the stream at LaserData**, set `STREAM_BACKEND=laser`.
6. **Submit by 15:30** via `/submit` in the RocketRide Discord.

If you run out of time, ship with whichever backends are still `local` and say
so. A working demo with two real integrations beats a broken one with four.

---

## The demo, 90 seconds

Four beats, one per technology. Rehearse it once before you present.

1. **Empty canvas.** "This is the room, and it knows nothing yet."
   Press **Fill the room**. Nodes pulse in as people check in.
   → *LaserData: every one of these is an event off a durable stream.*

2. **The graph thickens.** Point at a topic node several people connect to.
   → *FalkorDB: this is memory, and it compounds. Nothing here was typed in.*

3. **An introduction fires.** Read one out of the panel, the one with a
   connector. "It is not telling Sol that Lin is similar. It is telling Sol
   that Chen already knows Lin, so ask for the intro."
   → *That is a three-hop graph traversal. A vector store cannot answer it.*

4. **A veto.** Point at a red one. "The matchmaker wanted to introduce two
   people whose only shared interest was 'ai'. At an AI hackathon that is not
   an overlap, it is the weather. The critic killed it."
   → *Guild.ai: two agents, and the second one is allowed to say no.*

Close on **Check in live**: put yourself in the room, and let it nudge you in
front of the judges.

---

## Known gaps, stated plainly

- **LaserData**: package (`apache-iggy`), client API, host, creds, and the
  connection-string scheme (`iggy+http` on 443) are all confirmed. The managed
  deployment fronts Iggy through a Warden HTTPS proxy that returns
  `InvalidHttpRequest` on the base path. Ask the LaserData mentor for the exact
  iggy connection string, then set `LASER_URI=<their string>` in `.env`. This is
  the last mile, and `LaserStream` falls back to the local mirror so it can
  never brick the demo.
- **RocketRide**: token and connectivity proven. Pipelines run via the
  `rocketride` SDK (WebSocket), not a REST run-path, so `motion._handle_cloud`
  is a placeholder until the `.pipe` is built in Pipeline Builder at the event.
  That build is the $1,000 task; the local motion layer is its exact spec.
- **Guild**: CLI authed. `guild/matchmaker.agent.ts` and `critic.agent.ts` use
  the real `@guildai/agents-sdk` import. Publish with `guild agent save
  --publish`, then wire invocation.
- Nudges deliver to an outbox in the UI. There is no email or Slack. If a judge
  asks, that is the honest answer: the motion is real, the last mile is a demo
  surface.
- FalkorDB calls are synchronous inside an async server. Fine at 12 people and
  7ms. It would need `asyncio.to_thread` at real scale.
- The critic's rules are deterministic rather than model-driven. That is a
  deliberate call: a veto you can explain in one sentence demos better and is
  reproducible.
