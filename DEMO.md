# Beeline — Demo Runbook

**Memory Meets Motion, Frontier Tower SF. Solo. ~90 seconds on stage.**

Beeline is the *Event Networking Matchmaker* idea, taken deep. Three readings of
motion, all load-bearing: **memory** (the room graph), **motion** (a physical
path to the person you should meet), and **e-motion** (self-reported state that
gates the nudge and teaches the match graph).

---

## 0. Pre-flight (do this before you present, ~5 min)

Four terminals, or run the first three in the background.

```bash
# 1. FalkorDB (memory) — the local graph engine
/opt/homebrew/opt/redis/bin/redis-server --port 6379 \
  --loadmodule /Users/aaryaakamdar/Desktop/overlap/.local/falkordb.so --daemonize yes
```

```bash
# 2. The app (serves the map + the phone page + the live API)
cd /Users/aaryaakamdar/Desktop/overlap && \
  .venv/bin/uvicorn overlap.server:app --app-dir src --port 8113
```

```bash
# 3. Public URL so judges can scan the QR and open /join on their phones
bash /Users/aaryaakamdar/Desktop/overlap/scripts/tunnel.sh
```

Then open the **map through the tunnel URL** on the projector (not localhost),
so the on-screen QR points at the public `/join`:

```
https://<your-tunnel>.trycloudflare.com/?live=1
```

Sanity check before you walk up: press **Fill the room**, confirm dots land in
the four zones and a couple of introductions appear in the panel. Press
**Reset**. You are ready.

---

## 1. The 90-second script

Two modes on purpose. **Play demo** tells the controlled story; **live** proves
it is real with the judges' own phones. Both are the same engine.

### Beat 1 — the empty room (memory about to compound)
Start on the empty floor plan. "This is floor 16 right now. It knows nothing
yet." Press **Play demo**. Dots stream into the four zones.
> **LaserData**: every dot is a live event on the stream.

### Beat 2 — the graph fills (memory)
Point at the panel as people arrive. "It is remembering who is here, what they
care about, and crucially what they *need* and *offer*."
> **FalkorDB**: a graph of people, interests, and ask/offer, written to live.

### Beat 3 — the veto (coordination, and taste)
A check-in produces a match with only a generic overlap. The panel shows the
**empath** killing it, red tag: *"only shared interest is ai, too broad."*
"Most systems would fire that. Ours has an agent whose job is to say no."
> **Guild**: specialist agents, one of them allowed to refuse.

### Beat 4 — the beeline (motion, the money shot)
An approved intro animates a path across zones: you → connector → target. Read
the card. "It is not telling Tomas that Marcus is similar. Marcus is **hiring a
designer**, Tomas **does design**, and Priya already knows Marcus, so it says:
go to the Stage, say hi to Priya on the way."
> **RocketRide**: memory becomes a physical, routed action. Ask meets offer.

### Beat 5 — the hold, and the learning (e-motion)
Someone taps **recharging**. A nudge about to fire **holds**, amber tag: *"Fatima
needs a minute, holding until she is open."* Then a **not-for-me** tap, and the
panel shows the empath **learning**: *affinity 0.0 → -1.0*. The next suggestion
shifts. "How you feel changes where you get sent next. And nothing here is
sensed. You tap it yourself."
> **Guild + the ethic**: self-reported only, human-in-the-loop, and it learns.

### Close — make it real (live)
Switch to the live map (already on the tunnel URL). "This is not a recording."
Scan the QR yourself, or ask a judge to. Their dot appears in a zone, and a
**beeline lands on their phone**: go meet this person, here is the way. Close on
their screen.

---

## 2. Why all four are load-bearing (the judging test)

Judges check that removing any one tool breaks the demo. It does:

| Tool | Its job in Beeline | Remove it and… |
|---|---|---|
| **LaserData** | the live check-in / position / state / feedback stream | nothing is happening now; the room is a static file |
| **FalkorDB** | the memory graph; multi-hop ask↔offer and warm-intro | no "who can introduce me"; back to flat similarity |
| **RocketRide** | orchestrates persist → match → gate → icebreak → route → deliver | nothing sequences the decision into an action |
| **Guild** | matchmaker + icebreaker (baseline) + empath + router | every proposal ships; no veto, no hold, no learning |

Baseline coverage from the problem statement's own idea row is met first
(matchmaker + icebreaker agents, check-in/location/interest stream, connection
graph, real-time nudges), then extended (intent matching, physical routing,
emotional gate, learning loop).

---

## 3. If something breaks (resilience)

Everything runs on `local` backends by default, so no sponsor outage can stop the
demo. Flip to a real service only if it is up:

- **LaserData down or slow?** `STREAM_BACKEND=local` (default). The Laser client
  keeps a local mirror, so even on `laser` a dead network still drives the map.
- **RocketRide Cloud pipeline not ready?** `MOTION_BACKEND=local` (default) runs
  the identical pipeline in-process.
- **FalkorDB Cloud flaky?** `FALKOR_BACKEND=local` (default) is the running local
  graph. Same Cypher.
- **Venue wifi hostile / tunnel dies?** Present the whole thing on `localhost`
  with **Play demo**. The phone-scan close is the only part that needs the
  tunnel; skip it and narrate instead.
- **The map ever looks wrong?** Reload the tunnel URL with `?live=1`. On connect
  it replays the current graph.

The backend chips on screen (memory / stream / motion / guild) show exactly which
mode each is in, so you always know what is live.

---

## 4. Submit

- Confirm the actual submission channel first: the official problem statement
  points to `discord.gg/QXVbqWxHHb`; the RocketRide guide says `/submit` in the
  RocketRide Discord. Ask a mentor which counts, then submit there.
- Have ready: the tunnel URL (live demo), the repo, and a one-liner:
  *"Beeline turns a room's live signals into a memory graph and routes you to the
  person whose offer answers your ask, gated by how people feel."*

### Social track ($250, ~10 min)
Post on LinkedIn tagging RocketRide, follow their Instagram, and be in the
Discord during the event. Draft:

> Built **Beeline** at Memory Meets Motion: a live networking app that remembers
> who is in the room and what they need, then draws you a path to the person
> whose offer answers your ask. Memory in a graph (FalkorDB), a live signal
> stream (LaserData), four coordinating agents (Guild) including one that can say
> no, all orchestrated on RocketRide. Three meanings of motion in one build:
> memory, movement, and emotion. #RocketRideAI #buildinpublic
