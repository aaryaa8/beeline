"""RocketRide: the motion layer.

This is the thing that turns "what the graph knows" into "what the system does".
For every event off the stream it runs the same five steps:

    1. persist the event into FalkorDB          (memory compounds)
    2. traverse the graph for who to introduce  (multi-hop retrieval)
    3. ask the Guild matchmaker for a proposal  (coordination)
    4. ask the Guild critic to approve or veto  (coordination)
    5. execute: deliver the nudge, write it back to memory   (motion)

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

        # 1. persist
        subject = self._persist(event)
        steps.append({"step": "memory.write", "detail": f"{event['type']} -> FalkorDB"})
        if not subject:
            return self._finish(event, steps, None, started)

        # 2 + 3. traverse and propose
        proposal = await agents.propose(self.memory, subject)
        if not proposal:
            steps.append({"step": "matchmaker", "detail": "no candidate with shared interests yet"})
            return self._finish(event, steps, None, started)
        steps.append(
            {
                "step": "matchmaker",
                "detail": (
                    f"proposes {proposal['from_name']} meets {proposal['to_name']} "
                    f"(overlap: {', '.join(proposal['shared_topics'][:3])}, "
                    f"confidence {proposal['confidence']})"
                ),
            }
        )

        # 4. review
        verdict = await agents.review(self.memory, proposal)
        steps.append(
            {
                "step": "critic",
                "detail": ("APPROVED. " if verdict["approved"] else "VETOED. ") + verdict["reason"],
                "approved": verdict["approved"],
            }
        )

        if not verdict["approved"]:
            self.memory.record_nudge(proposal["from_id"], proposal["to_id"], "vetoed")
            return self._finish(event, steps, {**proposal, "verdict": verdict}, started)

        # 5. act
        self._deliver(proposal, verdict)
        steps.append({"step": "action", "detail": f"nudge delivered to {proposal['from_name']}"})
        return self._finish(event, steps, {**proposal, "verdict": verdict}, started)

    def _persist(self, event: dict[str, Any]) -> str | None:
        p = event["payload"]
        kind = event["type"]
        if kind == stream.CHECKIN:
            self.memory.record_checkin(
                p["id"], p["name"], p.get("role", ""), p.get("interests", []), p.get("location", "")
            )
            return p["id"]
        if kind == stream.INTEREST:
            self.memory.record_interest(p["id"], p["topic"])
            return p["id"]
        if kind == stream.MET:
            self.memory.record_met(p["a"], p["b"])
            return None  # meeting someone is memory, not a trigger to nudge
        if kind == stream.LOCATION:
            self.memory.record_location(p["id"], p["location"])
            return p["id"]
        return None

    def _deliver(self, proposal: dict[str, Any], verdict: dict[str, Any]) -> None:
        self.memory.record_nudge(proposal["from_id"], proposal["to_id"], "approved")
        item = {
            "at": time.time(),
            "to": proposal["from_name"],
            "to_id": proposal["from_id"],
            "about": proposal["to_name"],
            "message": proposal["message"],
            "why": verdict["reason"],
            "connector": proposal.get("connector"),
        }
        self.outbox.append(item)
        if self.on_action:
            self.on_action(item)

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
    # cloud execution: the deployed .pipe does all five steps
    # ------------------------------------------------------------------ #

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
        if record.get("proposal", {}).get("verdict", {}).get("approved"):
            self.outbox.append(
                {
                    "at": time.time(),
                    "to": record["proposal"]["from_name"],
                    "to_id": record["proposal"]["from_id"],
                    "about": record["proposal"]["to_name"],
                    "message": record["proposal"]["message"],
                    "why": record["proposal"]["verdict"]["reason"],
                    "connector": record["proposal"].get("connector"),
                }
            )
        return record
