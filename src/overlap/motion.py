"""RocketRide: the motion layer.

This is the thing that turns "what the graph knows" into "what the system does".
For every event off the stream it runs one orchestrated pass, persist first so
memory always leads motion, then decide, then act:

    1. persist the event into FalkorDB              (memory compounds)
    2. matchmaker: pick WHO to introduce            (coordination)
    3. empath:     gate it, approve / hold / veto   (coordination + the ethics)
    4. icebreaker: write the opener                 (coordination)
    5. router:     attach the physical path         (coordination)
    6. deliver the nudge to the outbox              (motion)

The four Guild agents each own one decision (see agents.py). The split is real:
the matchmaker decides who and never writes a word, the icebreaker writes the
opener and never picks anyone, the empath is allowed to say no, and the router
turns an approved match into a route through the room.

Two events never trigger a nudge. A `met` is memory, not a prompt to introduce
(you cannot introduce two people who already know each other). A `feedback` is
the learning loop: it routes to the empath's affinity update, which teaches the
match graph how a meeting actually felt, and never to matchmaking.

MOTION_BACKEND=local runs those steps in-process. MOTION_BACKEND=cloud hands the
whole sequence to the deployed RocketRide pipeline. The response shape is
identical either way, so the server and the UI do not know or care which ran.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from . import agents, stream
from .config import cfg
from .memory import Memory


class Motion:
    def __init__(self, memory: Memory, on_action: Callable[[dict], Any] | None = None) -> None:
        self.memory = memory
        self.on_action = on_action
        self.outbox: list[dict[str, Any]] = []
        self.trace: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #

    async def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        if cfg.motion_backend == "cloud":
            return await self._handle_cloud(event)
        return await self._handle_local(event)

    # ------------------------------------------------------------------ #
    # local execution: the reference implementation of the pipeline
    # ------------------------------------------------------------------ #

    async def _handle_local(self, event: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        steps: list[dict[str, Any]] = []
        kind = event["type"]

        # A feedback event is the learning loop, not a matchmaking trigger. It
        # routes to the empath's affinity update (which writes the FELT edge) and
        # never produces a nudge, so it short-circuits the pipeline here.
        if kind == stream.FEEDBACK:
            return await self._handle_feedback(event, steps, started)

        # 1. persist. Memory leads motion: the graph is updated before anything
        #    decides on it. A `met` (and the already-handled feedback) write
        #    memory but return no subject, so they stop after this step.
        subject = self._persist(event)
        steps.append({"step": "memory.write", "detail": f"{kind} -> FalkorDB"})
        if not subject:
            return self._finish(event, steps, None, started)

        # 2. matchmaker: who should this subject meet. candidates() has already
        #    applied the emotional gate and the met/nudged exclusion, so a
        #    proposal here is a live, routable option.
        proposal = await agents.propose(self.memory, subject)
        if not proposal:
            steps.append(
                {"step": "matchmaker", "detail": "no open candidate with a real reason yet"}
            )
            return self._finish(event, steps, None, started)
        steps.append(self._matchmaker_step(proposal))

        # 3 -> 6. gate, then (if approved) icebreak, route and deliver.
        return await self._decide(proposal, event, steps, started)

    async def _decide(
        self,
        proposal: dict[str, Any],
        event: dict[str, Any],
        steps: list[dict[str, Any]],
        started: float,
    ) -> dict[str, Any]:
        """The empath gate and, if it approves, the icebreaker + router + deliver
        tail. Split out from _handle_local because the matchmaker's choice and the
        empath's gate can straddle a live state change: the target self-reports
        `recharging` in the gap, and the empath, reading state fresh at review
        time, holds the introduction. That cross-event hold is beat 5 of the demo,
        so the gate lives in its own reusable step."""
        # 3. empath: the gate. Record the NUDGED edge with the empath's status so
        #    the graph remembers that a hold or a veto happened, not only an
        #    approval. Only an approved nudge starts a cooldown (see memory.py).
        verdict = await agents.review(self.memory, proposal)
        status = verdict["status"]
        self.memory.record_nudge(proposal["from_id"], proposal["to_id"], status)
        prefix = {"approved": "APPROVED. ", "held": "HELD. ", "vetoed": "VETOED. "}.get(status, "")
        steps.append(
            {
                "step": "empath",
                "detail": prefix + verdict["reason"],
                "status": status,          # approve / hold / veto flag for the UI
                "approved": verdict["approved"],
            }
        )
        if not verdict["approved"]:
            # held  -> target is recharging, will deliver when they reopen.
            # vetoed -> silent skip (heads-down/in-flow, met, generic, cooldown).
            return self._finish(event, steps, {**proposal, "verdict": verdict}, started)

        # 4. icebreaker: the opener. The matchmaker chose who; this writes what to
        #    say, naming the complementary ask/offer and the connector if any.
        opener = await agents.write_opener(self.memory, proposal)
        steps.append({"step": "icebreaker", "detail": opener})

        # 5. router: the physical path. Where they are now and who to pass.
        path = await agents.route(self.memory, proposal)
        steps.append({"step": "router", "detail": self._route_detail(path)})

        # 6. action: deliver the nudge to the outbox.
        self._deliver(proposal, verdict, opener, path)
        steps.append(
            {
                "step": "action",
                "detail": f"nudge delivered to {proposal['from_name']}: head to {path['target_zone']}",
            }
        )
        return self._finish(
            event, steps, {**proposal, "verdict": verdict, "opener": opener, "route": path}, started
        )

    async def gate(self, proposal: dict[str, Any], event: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the gate-and-act tail for a proposal the matchmaker has already
        chosen. Exposed for the case the pipeline itself cannot stage in one
        synchronous event: a proposal made while the target was `open`, reviewed
        after the target self-reported `recharging`, so the empath holds. Builds
        the matchmaker trace step, then defers to _decide, so a caller gets the
        same record shape the live pipeline produces."""
        started = time.perf_counter()
        steps: list[dict[str, Any]] = [self._matchmaker_step(proposal)]
        return await self._decide(proposal, event or {"type": "gate", "payload": {}}, steps, started)

    async def _handle_feedback(
        self, event: dict[str, Any], steps: list[dict[str, Any]], started: float
    ) -> dict[str, Any]:
        """The learning loop. A one-tap post-meeting feedback teaches the match
        graph: agents.learn writes the FELT edge and reports the affinity before
        and after, which the trace surfaces so the shift is visible on stage. No
        matchmaking runs off a feedback event."""
        steps.append({"step": "memory.write", "detail": "feedback -> FELT edge"})
        learned = await agents.learn(self.memory, event)
        steps.append(
            {
                "step": "empath",
                "detail": (
                    f"learned from '{learned['value']}' feedback: affinity "
                    f"{learned['affinity_before']} -> {learned['affinity_after']}"
                ),
                "status": "learn",
            }
        )
        record = {
            "event": event,
            "steps": steps,
            "proposal": None,
            "learn": learned,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self.trace.append(record)
        return record

    # ------------------------------------------------------------------ #

    def _persist(self, event: dict[str, Any]) -> str | None:
        """Write the event into FalkorDB and return the person to evaluate for a
        fresh introduction, or None for events that only remember. Handles every
        §4.2 event type. `met` and `feedback` write memory but never trigger a
        nudge, so they return None (feedback's FELT write is done by agents.learn
        in _handle_feedback; here it is a no-op guard so the type is handled, not
        dropped)."""
        p = event["payload"]
        kind = event["type"]
        if kind == stream.CHECKIN:
            self.memory.record_checkin(
                p["id"],
                p["name"],
                p.get("role", ""),
                p.get("interests", []),
                p.get("ask", ""),
                p.get("offer", ""),
                p.get("zone", ""),
                p.get("state", "open"),
            )
            return p["id"]
        if kind == stream.INTEREST:
            self.memory.record_interest(p["id"], p["topic"])
            return p["id"]
        if kind == stream.INTENT:
            # Either side is optional; set_intent no-ops on an empty string.
            self.memory.set_intent(p["id"], p.get("ask", ""), p.get("offer", ""))
            return p["id"]
        if kind == stream.POSITION:
            self.memory.record_position(p["id"], p["zone"])
            return p["id"]
        if kind == stream.STATE:
            self.memory.set_state(p["id"], p["state"])
            return p["id"]
        if kind == stream.MET:
            self.memory.record_met(p["a"], p["b"])
            return None  # meeting someone is memory, not a trigger to nudge
        if kind == stream.FEEDBACK:
            return None  # learning loop; the FELT edge is written in _handle_feedback
        return None

    def _matchmaker_step(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Describe the matchmaker's choice without a message (the proposal has no
        message key now, that is the icebreaker's job). Ground the line in the
        actual overlap: complements first, then specific shared interests, then a
        plain routable reason, plus the confidence the matchmaker attached."""
        complements = proposal.get("complements") or []
        shared = proposal.get("shared_topics") or []
        if complements:
            basis = "ask/offer match on " + ", ".join(complements[:2])
        elif shared:
            basis = "shared interest in " + ", ".join(shared[:3])
        else:
            basis = "a routable reason"
        return {
            "step": "matchmaker",
            "detail": (
                f"proposes {proposal['from_name']} meets {proposal['to_name']} "
                f"({basis}, confidence {proposal['confidence']})"
            ),
        }

    def _route_detail(self, path: dict[str, Any]) -> str:
        hops = path.get("hops") or []
        zone = path.get("target_zone") or "an unknown zone"
        connector = (path.get("connector") or {}).get("connector_name")
        if connector:
            return f"route to {zone}, {len(hops)} on the way, say hi to {connector}"
        return f"route to {zone}, {len(hops)} on the way"

    def _deliver(
        self,
        proposal: dict[str, Any],
        verdict: dict[str, Any],
        opener: str,
        path: dict[str, Any],
    ) -> dict[str, Any]:
        """Emit the delivered introduction to the outbox in the frozen §4.6 shape.
        Only ever called on an approved verdict, so an outbox item always means a
        real delivery happened. The nudge edge is already recorded in _decide."""
        item = {
            "at": time.time(),
            "to": proposal["from_name"],
            "to_id": proposal["from_id"],
            "about": proposal["to_name"],
            "about_id": proposal["to_id"],
            "message": opener,                       # the icebreaker's opener
            "why": verdict["reason"],                # the empath's reason
            "connector": proposal.get("connector"),  # the matchmaker's warm intro
            "route": {                               # the router's result
                "target_zone": path.get("target_zone"),
                "hops": path.get("hops") or [],
                "connector": path.get("connector"),
            },
            "match": {
                "complements": proposal.get("complements") or [],
                "shared_topics": proposal.get("shared_topics") or [],
            },
        }
        self.outbox.append(item)
        if self.on_action:
            self.on_action(item)
        return item

    def _finish(
        self,
        event: dict[str, Any],
        steps: list[dict[str, Any]],
        proposal: dict[str, Any] | None,
        started: float,
    ) -> dict[str, Any]:
        record = {
            "event": event,
            "steps": steps,
            "proposal": proposal,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self.trace.append(record)
        return record

    # ------------------------------------------------------------------ #
    # cloud execution: the deployed .pipe does all of it
    # ------------------------------------------------------------------ #
    # Placeholder until the .pipe is built at the event. The contract is the same
    # record shape as _handle_local, so the server and UI never learn which ran.
    # We only fold an outbox item out of the response when the deployed pipeline
    # reports an approved verdict, mirroring the local "outbox means a delivery"
    # rule.

    async def _handle_cloud(self, event: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{cfg.rocketride_uri}/v1/pipelines/{cfg.rocketride_pipeline}/run",
                headers={"Authorization": f"Bearer {cfg.rocketride_auth}"},
                json={"input": event},
            )
            r.raise_for_status()
            record = r.json().get("output", r.json())
        self.trace.append(record)
        proposal = record.get("proposal") or {}
        verdict = proposal.get("verdict") or {}
        if verdict.get("approved"):
            path = proposal.get("route") or {}
            self.outbox.append(
                {
                    "at": time.time(),
                    "to": proposal.get("from_name"),
                    "to_id": proposal.get("from_id"),
                    "about": proposal.get("to_name"),
                    "about_id": proposal.get("to_id"),
                    "message": proposal.get("opener") or proposal.get("message"),
                    "why": verdict.get("reason"),
                    "connector": proposal.get("connector"),
                    "route": {
                        "target_zone": path.get("target_zone"),
                        "hops": path.get("hops") or [],
                        "connector": path.get("connector"),
                    },
                    "match": {
                        "complements": proposal.get("complements") or [],
                        "shared_topics": proposal.get("shared_topics") or [],
                    },
                }
            )
        return record
