"""Guild.ai: the coordination layer.

Two specialists rather than one monolithic prompt:

  matchmaker  proposes an introduction, and has to say *why* using a path it
              actually found in the graph.
  critic      can veto. It holds the rules the matchmaker is motivated to
              ignore: don't repeat an introduction, don't spam one popular
              person, don't count a generic topic as a real overlap.

The veto is the interesting part on stage. A single agent that always says yes
looks the same as no agent at all.

Backends:
  local  - runs both agents in-process. Zero credentials.
  guild  - posts to the deployed Guild agents (see ../../guild/).
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import cfg
from .memory import Memory

# Topics too broad to justify an introduction on their own. At an AI hackathon,
# "ai" is not an overlap, it is the weather.
GENERIC_TOPICS = {"ai", "tech", "technology", "startups", "software", "coding", "llm", "llms"}


# ---------------------------------------------------------------------- #
# matchmaker
# ---------------------------------------------------------------------- #

def _icebreaker_template(a: dict, b: dict, shared: list[str], connector: dict | None) -> str:
    topics = " and ".join(shared[:2])
    if connector:
        return (
            f"{b['name']} is worth finding. You both care about {topics}, and "
            f"{connector['connector_name']} already knows them, so ask for the intro."
        )
    return f"{b['name']} is worth finding. You both care about {topics}."


def _llm_icebreaker(a: dict, b: dict, shared: list[str], connector: dict | None) -> str:
    if not cfg.has_llm:
        return _icebreaker_template(a, b, shared, connector)
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        route = (
            f"{connector['connector_name']} knows them and can introduce you."
            if connector
            else "No mutual connection yet, so introduce yourself directly."
        )
        msg = client.messages.create(
            model=cfg.model,
            max_tokens=150,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a two-sentence nudge telling one person at a hackathon why "
                        "to go meet another, and one specific thing to open with. Be concrete "
                        "and plain. No em dashes, no exclamation marks.\n\n"
                        f"Person: {a['name']}, {a.get('role')}, interested in {a.get('interests')}\n"
                        f"Should meet: {b['name']}, {b.get('role')}\n"
                        f"Shared interests: {shared}\n"
                        f"Route: {route}"
                    ),
                }
            ],
        )
        return msg.content[0].text.strip()
    except Exception:
        return _icebreaker_template(a, b, shared, connector)


def matchmaker_local(memory: Memory, person_id: str) -> dict[str, Any] | None:
    """Propose the single best introduction for this person, using a real path
    out of the graph as the justification."""
    me = memory.person(person_id)
    if not me:
        return None

    candidates = memory.candidates(person_id, limit=5)
    if not candidates:
        return None

    best = candidates[0]
    connector = memory.warm_intro(person_id, best["id"])

    # Confidence is grounded in graph structure, not vibes: how much genuine
    # (non-generic) overlap there is, plus a bump for a warm intro path.
    specific = [t for t in best["shared_topics"] if t not in GENERIC_TOPICS]
    confidence = min(0.95, 0.35 + 0.2 * len(specific) + (0.2 if connector else 0.0))

    return {
        "from_id": person_id,
        "from_name": me["name"],
        "to_id": best["id"],
        "to_name": best["name"],
        "to_role": best["role"],
        "shared_topics": best["shared_topics"],
        "specific_topics": specific,
        "connector": connector,
        "confidence": round(confidence, 2),
        "message": _llm_icebreaker(me, best, best["shared_topics"], connector),
    }


# ---------------------------------------------------------------------- #
# critic
# ---------------------------------------------------------------------- #

def critic_local(memory: Memory, proposal: dict[str, Any]) -> dict[str, Any]:
    """Every rule here exists because the matchmaker would otherwise break it."""
    a, b = proposal["from_id"], proposal["to_id"]

    if memory.have_met(a, b):
        return {"approved": False, "reason": "They have already met. A repeat intro reads as noise."}

    age = memory.recent_nudge_age(b)
    if age is not None and age < cfg.nudge_cooldown_seconds:
        return {
            "approved": False,
            "reason": (
                f"{proposal['to_name']} was nudged {int(age)}s ago. "
                f"Cooling down for {cfg.nudge_cooldown_seconds}s so one popular "
                "person does not absorb every introduction."
            ),
        }

    if not proposal["specific_topics"]:
        generic = ", ".join(proposal["shared_topics"][:3]) or "nothing"
        return {
            "approved": False,
            "reason": (
                f"Only shared interest is {generic}, which is too broad to be a "
                "real reason at an AI hackathon."
            ),
        }

    if len(proposal["shared_topics"]) < cfg.min_shared_topics:
        return {"approved": False, "reason": "Not enough overlap to justify the interruption."}

    if proposal["confidence"] < 0.5:
        return {"approved": False, "reason": f"Confidence {proposal['confidence']} below threshold."}

    route = (
        f" {proposal['connector']['connector_name']} can make the introduction."
        if proposal.get("connector")
        else ""
    )
    return {
        "approved": True,
        "reason": (
            f"Genuine overlap on {', '.join(proposal['specific_topics'][:2])}, "
            f"and they have not met.{route}"
        ),
    }


# ---------------------------------------------------------------------- #
# guild cloud backend
# ---------------------------------------------------------------------- #

async def _call_guild(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke a deployed Guild agent. Shape mirrors the local functions exactly,
    so swapping backends is a config change."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{cfg.guild_base_url}/v1/workspaces/{cfg.guild_workspace}/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {cfg.guild_api_key}"},
            json={"input": payload},
        )
        r.raise_for_status()
        body = r.json()
        return body.get("output", body)


async def propose(memory: Memory, person_id: str) -> dict[str, Any] | None:
    if cfg.guild_backend == "guild":
        me = memory.person(person_id)
        cands = memory.candidates(person_id, limit=5)
        if not me or not cands:
            return None
        warm = memory.warm_intro(person_id, cands[0]["id"])
        return await _call_guild(
            "matchmaker", {"person": me, "candidates": cands, "warm_intro": warm}
        )
    # matchmaker_local may make a blocking LLM call for the icebreaker copy.
    # Off the event loop, or one slow generation stalls the whole live feed.
    return await asyncio.to_thread(matchmaker_local, memory, person_id)


async def review(memory: Memory, proposal: dict[str, Any]) -> dict[str, Any]:
    if cfg.guild_backend == "guild":
        return await _call_guild(
            "critic",
            {
                "proposal": proposal,
                "already_met": memory.have_met(proposal["from_id"], proposal["to_id"]),
                "target_nudge_age": memory.recent_nudge_age(proposal["to_id"]),
                "cooldown_seconds": cfg.nudge_cooldown_seconds,
            },
        )
    return critic_local(memory, proposal)
