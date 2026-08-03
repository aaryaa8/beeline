"""Proves the WP2 acceptance criteria for the four Guild agents against the live
local FalkorDB on :6379, graph "overlap":

  1. matchmaker  - complementary intent beats a mere shared interest in selection.
  2. icebreaker  - the opener names the complementary ask/offer, and the connector
                   when a warm-intro path exists.
  3. empath      - holds when the target is `recharging`, approves when `open`,
                   and vetoes a generic-only overlap.
  4. router      - returns a path containing the expected connector.
  5. empath.learn - a `not-for-me` feedback lowers that pair's next affinity.

The icebreaker is exercised on its deterministic template path (ANTHROPIC_API_KEY
is unset below) so the assertions are offline and reproducible. The LLM path is a
drop-in that degrades to exactly this template.

Run: cd /Users/aaryaakamdar/Desktop/overlap && .venv/bin/python scripts/test_agents.py
"""
import os
# Force the deterministic template path for the icebreaker regardless of .env.
# Setting it empty (not popping) matters: config.py runs load_dotenv(), which
# would re-read the key from .env if the var were merely absent. load_dotenv does
# not override an already-set var, so an empty string wins and cfg.has_llm is False.
os.environ["ANTHROPIC_API_KEY"] = ""

import asyncio
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from overlap import agents
from overlap.memory import Memory


def build_room(m: Memory) -> None:
    """A small room engineered so the intent match is the unambiguous winner.

      p1 (us):  wants "frontend engineering", offers "design mentorship".
      p5 Chen:  offers exactly what p1 wants, shares NO topic  -> intent only.
      p6 Lin:   shares one interest, no complementary intent   -> interest only.
      p9 Kai:   shares p1's interest and has MET Chen           -> warm-intro
                connector for p1 -> Chen; also MET Priya for the routing test.
                p1 has MET Kai, so Kai is excluded as a candidate.
      p8 Priya: routing target in a different zone (Stage).
    """
    m.reset()
    m.record_checkin("p1", "Aaryaa", "product designer",
                     ["spatial cognition", "graph databases"],
                     ask="frontend engineering", offer="design mentorship",
                     zone="Window", state="open")
    # Intent-only match: complements p1, shares no interest.
    m.record_checkin("p5", "Chen", "frontend engineer",
                     ["rust"], ask="", offer="frontend engineering",
                     zone="Coffee", state="open")
    # Interest-only match: one shared topic, no complement.
    m.record_checkin("p6", "Lin", "researcher",
                     ["graph databases"], ask="", offer="",
                     zone="Coffee", state="open")
    # Connector: shares an interest with p1, has met Chen (warm intro) and Priya.
    m.record_checkin("p9", "Kai", "designer",
                     ["graph databases"], ask="", offer="",
                     zone="Coffee", state="open")
    # Routing target in a different zone.
    m.record_checkin("p8", "Priya", "ml engineer",
                     ["retrieval"], ask="", offer="",
                     zone="Stage", state="open")

    m.record_met("p9", "p5")   # Kai knows Chen -> warm intro p1 -> Chen via Kai
    m.record_met("p9", "p8")   # Kai knows Priya -> routing connector
    m.record_met("p1", "p9")   # we know Kai -> a real vouch chain, and excludes Kai


async def main() -> None:
    m = Memory()
    build_room(m)

    print("stats           ", m.stats())

    # --- CRITERION 1: matchmaker picks intent over interest ------------------
    proposal = await agents.propose(m, "p1")
    print("proposal        ", {
        "to_id": proposal["to_id"], "to_name": proposal["to_name"],
        "complements": proposal["complements"],
        "shared_topics": proposal["shared_topics"],
        "connector": (proposal["connector"] or {}).get("connector_name"),
        "confidence": proposal["confidence"],
    })
    assert proposal is not None, "expected a proposal"
    assert proposal["to_id"] == "p5", \
        f"complementary-intent match Chen (p5) must be chosen, got {proposal['to_id']}"
    assert proposal["complements"] == ["frontend engineering"], proposal["complements"]
    assert "message" not in proposal, "matchmaker decides who, it must NOT write the message"
    assert proposal["connector"] and proposal["connector"]["connector_name"] == "Kai", \
        f"warm-intro connector Kai expected, got {proposal.get('connector')}"
    print("CRITERION 1 (matchmaker: intent beats interest) PASS")

    # --- CRITERION 2: icebreaker names the complement and the connector ------
    opener = await agents.write_opener(m, proposal)
    print("opener          ", opener)
    assert "frontend engineering" in opener, \
        "opener must name the complementary ask/offer"
    assert "Kai" in opener, "opener must name the warm-intro connector when present"
    assert "—" not in opener and "!" not in opener, "no em dashes, no exclamation marks"
    # two sentences
    assert opener.count(".") >= 2, "opener should be two sentences"
    # And with no connector, it still names the complement but not a connector.
    solo = dict(proposal, connector=None)
    solo_opener = await agents.write_opener(m, solo)
    print("opener (no conn)", solo_opener)
    assert "frontend engineering" in solo_opener and "Kai" not in solo_opener
    print("CRITERION 2 (icebreaker names complement + connector) PASS")

    # --- CRITERION 3: empath holds recharging, approves open, vetoes generic --
    verdict_open = await agents.review(m, proposal)
    print("verdict (open)  ", verdict_open)
    assert verdict_open["approved"] and verdict_open["status"] == "approved", verdict_open

    m.set_state("p5", "recharging")
    verdict_hold = await agents.review(m, proposal)
    print("verdict (recharge)", verdict_hold)
    assert not verdict_hold["approved"] and verdict_hold["status"] == "held", verdict_hold
    assert "recharging" in verdict_hold["reason"], verdict_hold["reason"]

    m.set_state("p5", "heads-down")
    verdict_skip = await agents.review(m, proposal)
    print("verdict (heads-down)", verdict_skip)
    assert not verdict_skip["approved"] and verdict_skip["status"] == "vetoed", verdict_skip
    assert "heads-down" in verdict_skip["reason"], verdict_skip["reason"]
    m.set_state("p5", "open")

    # generic-only overlap is rejected even when both are open
    generic_proposal = {
        "from_id": "p1", "to_id": "p6", "to_name": "Lin",
        "shared_topics": ["ai"], "specific_topics": [], "complements": [],
        "connector": None,
    }
    verdict_generic = await agents.review(m, generic_proposal)
    print("verdict (generic)", verdict_generic)
    assert not verdict_generic["approved"] and verdict_generic["status"] == "vetoed"
    assert "too broad" in verdict_generic["reason"], verdict_generic["reason"]
    print("CRITERION 3 (empath gate: hold / approve / veto) PASS")

    # --- CRITERION 4: router returns a path including the connector ----------
    route_proposal = {
        "from_id": "p1", "to_id": "p8", "to_name": "Priya",
        "connector": {"connector_id": "p9", "connector_name": "Kai"},
    }
    routed = await agents.route(m, route_proposal)
    print("route p1->p8    ", {
        "target_zone": routed["target_zone"],
        "hops": [(h["name"], h["reason"]) for h in routed["hops"]],
        "connector": (routed["connector"] or {}).get("connector_name"),
    })
    assert routed["target_zone"] == "Stage", routed["target_zone"]
    hop_ids = [h["id"] for h in routed["hops"]]
    assert "p9" in hop_ids, f"connector Kai (p9) must be on the path, got {hop_ids}"
    kai_hop = next(h for h in routed["hops"] if h["id"] == "p9")
    assert "Priya" in kai_hop["reason"], kai_hop["reason"]
    assert routed["connector"]["connector_name"] == "Kai"
    print("CRITERION 4 (router path includes connector) PASS")

    # --- CRITERION 5: not-for-me feedback lowers the pair's next affinity -----
    before = m.affinity("p1", "p5")
    learned = await agents.learn(m, {"from_id": "p1", "to_id": "p5", "value": "not-for-me"})
    after = m.affinity("p1", "p5")
    print("learn not-for-me", learned)
    assert learned["affinity_before"] == before
    assert learned["affinity_after"] == after
    assert after < before, f"not-for-me must lower affinity, {before} -> {after}"
    assert after < 0, f"not-for-me affinity should be negative, got {after}"
    # And it shows up in the next matchmaker score: re-scoring Chen now carries
    # the penalty, proving the loop feeds back into selection.
    cands = m.candidates("p1")
    chen = next((c for c in cands if c["id"] == "p5"), None)
    assert chen is not None and chen["affinity"] < 0, \
        f"Chen's affinity should now be negative in candidates, got {chen}"
    print(f"   next-round affinity for Chen: {chen['affinity']}")
    print("CRITERION 5 (feedback lowers next affinity) PASS")

    print("\nALL WP2 AGENT ACCEPTANCE CRITERIA PASS")


if __name__ == "__main__":
    asyncio.run(main())
