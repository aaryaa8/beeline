"""Proves the WP3 acceptance criteria for the RocketRide motion pipeline against
the live local FalkorDB on :6379, graph "overlap".

Two acts, both driven through Motion (the real orchestration seam), never by
poking memory behind its back:

  PART A - replay seed.demo_sequence() through Motion._handle_local and assert:
    * at least one delivered introduction (outbox non-empty) whose message is
      non-empty and whose route carries hops;
    * a feedback event produces a learn trace whose affinity moved
      (affinity_before != affinity_after), the visible learning shift.

  PART B - the recharging gate (Fatima / a5), reproduced through the pipeline.
    memory.candidates() gates a non-`open` person out as both target and
    recipient, so the empath's HOLD is by construction a cross-event guard: the
    matchmaker proposes while the target is `open`, the target then self-reports
    `recharging`, and the empath, reading state fresh at review time, holds. That
    is exactly beat 5 of the stage demo ("you tap recharging, a nudge holds").
    demo_sequence() orders the a5->recharging event after the check-ins that
    would pair her, so a purely synchronous replay cannot stage the gap; we stage
    it here with the demo's own actors (Ivan a8 -> Fatima a5 on retrieval/eval),
    still running the empath gate and the nudge write through Motion.gate().

The icebreaker runs on its deterministic template path (ANTHROPIC_API_KEY is
unset below) so every assertion is offline and reproducible.

Run: cd /Users/aaryaakamdar/Desktop/overlap && .venv/bin/python scripts/test_motion.py
"""
import os
# Force the deterministic template opener so the run is offline and reproducible.
os.environ.pop("ANTHROPIC_API_KEY", None)

import asyncio
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from overlap import agents, seed, stream
from overlap.memory import Memory
from overlap.motion import Motion

# The trace step names WP3 froze. Every record's steps must be drawn from these.
TRACE_STEPS = {"memory.write", "matchmaker", "empath", "icebreaker", "router", "action"}
# The frozen §4.6 outbox shape.
OUTBOX_KEYS = {"at", "to", "to_id", "about", "about_id", "message", "why",
               "connector", "route", "match"}


async def part_a() -> None:
    print("=== PART A: replay demo_sequence() through Motion ===")
    m = Memory()
    m.reset()
    motion = Motion(m)

    counts = {"approved": 0, "held": 0, "vetoed": 0, "learn": 0}
    feedback_learn = None
    for event in seed.demo_sequence():
        record = await motion.handle(event)

        # every step name is one of the frozen six, and latency is recorded
        for s in record["steps"]:
            assert s["step"] in TRACE_STEPS, f"unknown trace step {s['step']}"
        assert isinstance(record["ms"], (int, float)), record

        empath = next((s for s in record["steps"] if s["step"] == "empath"), None)
        if empath:
            counts[empath["status"]] = counts.get(empath["status"], 0) + 1
        if record.get("learn"):
            feedback_learn = record["learn"]

    delivered = len(motion.outbox)
    print(f"records         {len(motion.trace)}")
    print(f"outcomes        approved={counts['approved']} held={counts['held']} "
          f"vetoed={counts['vetoed']} learn={counts['learn']}")
    print(f"delivered       {delivered}")
    for o in motion.outbox:
        print(f"   {o['to']} -> meet {o['about']} | hops={len(o['route']['hops'])} "
              f"| zone={o['route']['target_zone']} | msg='{o['message'][:52]}...'")

    # --- ASSERT 1: at least one real delivery with a message and a routed path --
    assert delivered >= 1, "expected at least one delivered introduction"
    good = [o for o in motion.outbox if o["message"] and o["route"]["hops"]]
    assert good, "a delivered intro must have a non-empty message and a route with hops"
    sample = good[0]
    assert OUTBOX_KEYS <= set(sample), f"outbox item missing keys: {OUTBOX_KEYS - set(sample)}"
    assert isinstance(sample["match"]["shared_topics"], list)
    print(f"ASSERT 1 (delivered intro with message + routed hops) PASS  "
          f"[{delivered} delivered, {counts['vetoed']} vetoed, {counts['held']} held]")

    # --- ASSERT 2: the feedback event moved affinity (the learning shift) -------
    print("feedback learn  ", feedback_learn)
    assert feedback_learn is not None, "demo_sequence must fire one feedback event"
    before = feedback_learn["affinity_before"]
    after = feedback_learn["affinity_after"]
    assert before != after, f"feedback must shift affinity, {before} -> {after}"
    assert after < before, f"a not-for-me feedback must lower affinity, {before} -> {after}"
    print("ASSERT 2 (feedback shifts affinity in a learn trace) PASS")


async def part_b() -> None:
    print("\n=== PART B: the recharging gate holds a nudge (Fatima / a5) ===")
    m = Memory()
    m.reset()
    motion = Motion(m)

    # Setup with the demo's own actors: Ivan and Fatima, both open, sharing
    # retrieval + evaluation. Seeded straight into memory so no auto-nudge fires
    # before we can stage the gap; the gate itself still runs through Motion.
    m.record_checkin("a8", "Ivan", "ml engineer", ["retrieval", "evaluation"],
                     ask="a research collaborator", offer="retrieval evaluation help",
                     zone="Window", state="open")
    m.record_checkin("a5", "Fatima", "research scientist",
                     ["retrieval", "evaluation", "graph databases"],
                     ask="a retrieval eval partner", offer="research collaboration",
                     zone="Window", state="open")

    # The matchmaker decides WHO while Fatima is still open.
    proposal = await agents.propose(m, "a8")
    assert proposal is not None and proposal["to_id"] == "a5", \
        f"expected Ivan -> Fatima, got {proposal and proposal.get('to_id')}"
    print(f"proposal        Ivan -> {proposal['to_name']} "
          f"(shared {proposal['shared_topics']}, confidence {proposal['confidence']})")

    # Fatima taps `recharging` - a real state event, through the pipeline.
    await motion.handle(stream.state("a5", "recharging"))
    assert m.person("a5")["state"] == "recharging"

    # The empath now gates the pending proposal and must HOLD, not deliver.
    record = await motion.gate(proposal, event=stream.state("a5", "recharging"))
    empath = next(s for s in record["steps"] if s["step"] == "empath")
    print("empath verdict  ", {"status": empath["status"], "detail": empath["detail"]})

    assert empath["status"] == "held", f"expected a HELD verdict, got {empath['status']}"
    assert empath["approved"] is False
    assert "recharging" in empath["detail"], empath["detail"]
    assert motion.outbox == [], "a held nudge must not deliver to the outbox"

    # And the hold is recorded on the graph as a NUDGED edge with status held.
    held_edges = [e for e in m.snapshot()["edges"]
                  if e["kind"] == "nudge" and e.get("status") == "held"
                  and e["source"] == "a8" and e["target"] == "a5"]
    assert held_edges, "the held introduction must be recorded as a NUDGED{status:held} edge"
    print("held recorded   ", held_edges[0])
    print("ASSERT 3 (recharging gate records a held outcome) PASS")


async def main() -> None:
    await part_a()
    await part_b()
    print("\nALL WP3 MOTION ACCEPTANCE CRITERIA PASS")


if __name__ == "__main__":
    asyncio.run(main())
