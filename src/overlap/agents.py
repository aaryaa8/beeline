"""Guild.ai: the coordination layer.

Four specialists rather than one monolithic prompt. The split is real: each agent
owns one decision and hands the next agent a strictly smaller job. The problem
statement names the first two by name (matchmaker + icebreaker); the last two are
our depth.

  matchmaker  decides WHO only. It picks the single best person to introduce out
              of memory.candidates(), scoring intent (ask/offer complements)
              above shared interest, with a bump for a warm-intro path. It does
              NOT write the message, so the labour is genuinely divided.
  icebreaker  writes the OPENER. Given the matchmaker's proposal it returns a
              two-sentence nudge: why to meet them, and one concrete thing to
              open with, naming the complementary ask/offer and the warm-intro
              connector when there is one.
  empath      is the gate and the learning loop, the human-in-the-loop spine.
              On a proposal it can hold or veto: a target or recipient who is not
              `open` pauses the intro (recharging holds, heads-down/in-flow skip),
              a re-nudged person is cooled down, a generic-only overlap is
              rejected. On a feedback event it updates affinity so the room learns
              from how a meeting actually felt.
  router      turns the approved match into a physical path. It calls
              memory.route() and folds the ordered hops and the on-the-way
              connector into the delivery alongside the icebreaker's opener.

The veto and the hold are the interesting parts on stage. A coordinator that
always says yes looks the same as no coordinator at all.

Coordination order in the pipeline (WP3):
    matchmaker -> empath gate -> (if approved) icebreaker + router -> deliver.

Backends:
  local  - runs all four agents in-process. Zero credentials.
  guild  - posts to the deployed Guild agents (see ../../guild/). Nothing crosses
           the language boundary except JSON, so a backend swap is a config flip.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import cfg
from .memory import Memory

# Topics too broad to justify an introduction on their own. At an AI hackathon,
# "ai" is not an overlap, it is the weather. Mirrors GENERIC_TOPICS in memory.py.
GENERIC_TOPICS = {"ai", "tech", "technology", "startups", "software", "coding", "llm", "llms"}


def _specific(shared_topics: list[str]) -> list[str]:
    """Shared interests that are specific enough to be a real reason, i.e. past
    the generic 'ai/tech' floor. The matchmaker scores on these, not raw overlap,
    and the empath vetoes a match that has nothing else."""
    return [t for t in shared_topics if t not in GENERIC_TOPICS]


# ---------------------------------------------------------------------- #
# 1. matchmaker: decides WHO
# ---------------------------------------------------------------------- #

def _score(candidate: dict[str, Any], has_warm: bool) -> float:
    """Intent-first score (BUILD_SPEC WP2):

        2.0 * complements + 1.0 * specific_shared + affinity + (0.5 if warm)

    Complements (my WANTS met by their OFFERS, or the reverse) dominate because
    people come to rooms with asks and offers, not just interests. Affinity is
    signed and small, so it only nudges ties. A warm-intro path is worth half a
    shared interest: a suggestion you can act on beats one you cannot."""
    complements = len(candidate["complements"])
    specific = len(_specific(candidate["shared_topics"]))
    affinity_adjust = candidate.get("affinity", 0.0)
    return 2.0 * complements + 1.0 * specific + affinity_adjust + (0.5 if has_warm else 0.0)


def _confidence(complements: list[str], specific: list[str], connector: dict | None) -> float:
    """A number grounded in graph structure, not vibes: how much genuine intent
    and interest overlap there is, plus a bump for an actionable warm-intro path."""
    raw = 0.4 + 0.2 * len(complements) + 0.1 * len(specific) + (0.15 if connector else 0.0)
    return round(min(0.95, raw), 2)


def matchmaker_local(memory: Memory, person_id: str) -> dict[str, Any] | None:
    """Pick the single best introduction for this person and produce the proposal.
    Decides WHO only; the opener is the icebreaker's job, the path is the
    router's. candidates() has already applied the emotional gate and the
    met/nudged exclusion, so everything here is a live, routable option."""
    me = memory.person(person_id)
    if not me:
        return None

    candidates = memory.candidates(person_id, limit=5)
    if not candidates:
        return None

    # Re-score each candidate with its warm-intro path, which candidates() does
    # not know about. Small rooms make the per-candidate lookup cheap.
    scored: list[tuple[float, dict[str, Any], dict | None]] = []
    for c in candidates:
        connector = memory.warm_intro(person_id, c["id"])
        scored.append((_score(c, connector is not None), c, connector))
    scored.sort(key=lambda x: x[0], reverse=True)

    _, best, connector = scored[0]
    specific = _specific(best["shared_topics"])

    return {
        "from_id": person_id,
        "from_name": me["name"],
        "to_id": best["id"],
        "to_name": best["name"],
        "to_role": best["role"],
        "shared_topics": best["shared_topics"],
        "complements": best["complements"],
        "specific_topics": specific,
        "connector": connector,
        "confidence": _confidence(best["complements"], specific, connector),
    }


# ---------------------------------------------------------------------- #
# 2. icebreaker: writes the OPENER
# ---------------------------------------------------------------------- #

def _complement_phrase(me: dict, them: dict, complement: str) -> str:
    """Say the ask/offer match in the right direction. The complement is a single
    capability tag; which side of the trade it sits on decides the wording."""
    my_ask = (me.get("ask") or "")
    my_offer = (me.get("offer") or "")
    if complement and complement == my_offer:
        return f"they are looking for {complement}, which you offer"
    if complement and complement == my_ask:
        return f"they offer {complement}, which you are looking for"
    # Fell through: the pair complements but we could not read the direction
    # cleanly (e.g. a re-normalised tag). State the capability plainly.
    if complement:
        return f"your ask and their offer line up on {complement}"
    return ""


def _opener_template(me: dict, them: dict, proposal: dict[str, Any]) -> str:
    """Deterministic two-sentence opener. Sentence one is why to meet them,
    sentence two is one concrete thing to open with. Names the complementary
    ask/offer and the warm-intro connector when present. No em dashes, no
    exclamation marks (house style, and it keeps the copy calm on stage)."""
    to_name = proposal["to_name"]
    complements = proposal.get("complements") or []
    specific = proposal.get("specific_topics") or []
    connector = proposal.get("connector")

    # Sentence 1: the reason. Prefer the intent match, fall back to a real shared
    # interest, and only then to a plain nudge.
    if complements:
        reason = _complement_phrase(me, them, complements[0])
        why = f"Go find {to_name}, {reason}."
    elif specific:
        topics = " and ".join(specific[:2])
        why = f"Go find {to_name}, you both work on {topics}."
    else:
        why = f"Go find {to_name}, there is a real reason to talk."

    # Sentence 2: the opener. Use the connector as the way in when there is one,
    # otherwise hand them a concrete topic or the capability itself.
    hook = specific[0] if specific else (complements[0] if complements else None)
    if connector:
        name = connector["connector_name"]
        if hook:
            how = f"Open by mentioning {name}, who already knows them, then bring up {hook}."
        else:
            how = f"Open by mentioning {name}, who already knows them and can vouch for you."
    else:
        if hook:
            how = f"Open by asking what they are doing with {hook}."
        else:
            how = "Open by asking what brought them to the room today."

    return f"{why} {how}"


def icebreaker_local(memory: Memory, proposal: dict[str, Any]) -> str:
    """Write the opener. Uses the LLM when a key is present, otherwise the clean
    template above. Either path names the complementary ask/offer and the
    connector, so the split with the matchmaker holds regardless of backend."""
    me = memory.person(proposal["from_id"]) or {"name": proposal.get("from_name")}
    them = memory.person(proposal["to_id"]) or {"name": proposal.get("to_name")}

    if not cfg.has_llm:
        return _opener_template(me, them, proposal)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        complements = proposal.get("complements") or []
        specific = proposal.get("specific_topics") or []
        connector = proposal.get("connector")
        intent = _complement_phrase(me, them, complements[0]) if complements else "no direct ask/offer match"
        route = (
            f"{connector['connector_name']} already knows them and can introduce you."
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
                        "to go meet another, and one specific thing to open with. Name the "
                        "complementary ask/offer, and the connector if there is one. Be "
                        "concrete and plain. No em dashes, no exclamation marks.\n\n"
                        f"Person: {me.get('name')}, {me.get('role')}\n"
                        f"Should meet: {them.get('name')}, {them.get('role')}\n"
                        f"Ask/offer match: {intent}\n"
                        f"Shared interests: {specific}\n"
                        f"Route: {route}"
                    ),
                }
            ],
        )
        text = msg.content[0].text.strip()
        # Never let a stray em dash through, even from the model.
        return text.replace("—", ", ").replace(" - ", ", ")
    except Exception:
        return _opener_template(me, them, proposal)


# ---------------------------------------------------------------------- #
# 3. empath: the gate + the learning loop
# ---------------------------------------------------------------------- #

def empath_local(memory: Memory, proposal: dict[str, Any]) -> dict[str, Any]:
    """The human-in-the-loop gate. Reads state live (it may have changed since the
    matchmaker ran) and can hold or veto. Returns a verdict with a status that the
    motion layer records on the NUDGED edge:

        approved -> deliver, and this is the only status that starts a cooldown.
        held     -> target needs a minute (recharging); retry when they reopen.
        vetoed   -> skip (heads-down/in-flow, already met, generic-only, cooldown).
    """
    a, b = proposal["from_id"], proposal["to_id"]
    me = memory.person(a) or {}
    them = memory.person(b) or {}

    # 1. The emotional gate. Only `open` people take part in active routing, as
    # either the target of an intro or the recipient of a nudge.
    for who, role in ((them, "target"), (me, "recipient")):
        state = who.get("state")
        name = who.get("name", role)
        if state == "recharging":
            return {
                "approved": False,
                "status": "held",
                "reason": f"{name} needs a minute (recharging). Holding until they are open again.",
            }
        if state in ("heads-down", "in-flow"):
            return {
                "approved": False,
                "status": "vetoed",
                "reason": f"{name} is {state} (building, do not disturb). Skipping for now.",
            }

    # 2. Never repeat an introduction.
    if memory.have_met(a, b):
        return {
            "approved": False,
            "status": "vetoed",
            "reason": "They have already met. A repeat intro reads as noise.",
        }

    # 3. Reject a match whose only overlap is generic. A real complement or a
    # specific shared interest is required; "ai" at an AI hackathon is not a reason.
    complements = proposal.get("complements") or []
    specific = proposal.get("specific_topics") or _specific(proposal.get("shared_topics", []))
    if not complements and not specific:
        generic = ", ".join(proposal.get("shared_topics", [])[:3]) or "nothing"
        return {
            "approved": False,
            "status": "vetoed",
            "reason": f"Only shared interest is {generic}, too broad to be a real reason.",
        }

    # 4. Cooldown. Only delivered (approved) nudges start one, so a hold or veto
    # never cascades. recent_nudge_age already filters to approved edges.
    age = memory.recent_nudge_age(b)
    if age is not None and age < cfg.nudge_cooldown_seconds:
        return {
            "approved": False,
            "status": "vetoed",
            "reason": (
                f"{proposal['to_name']} was introduced {int(age)}s ago. Cooling down for "
                f"{cfg.nudge_cooldown_seconds}s so one popular person does not absorb every intro."
            ),
        }

    # Approved. Say why in one line, grounded in the actual overlap.
    if complements:
        basis = f"your ask/offer match on {complements[0]}"
    else:
        basis = f"a real shared interest in {specific[0]}"
    tail = (
        f" {proposal['connector']['connector_name']} can vouch for you."
        if proposal.get("connector")
        else ""
    )
    return {
        "approved": True,
        "status": "approved",
        "reason": f"Both open, not met, {basis}.{tail}",
    }


def learn_local(memory: Memory, feedback: dict[str, Any]) -> dict[str, Any]:
    """The learning loop. A one-tap post-meeting feedback event teaches the match
    graph: memory.record_feedback writes the FELT edge that affinity() reads, so
    a `not-for-me` lowers that pair's next score and a `clicked` lifts it. Accepts
    either a raw {from_id, to_id, value} payload or a full event wrapping one."""
    p = feedback.get("payload", feedback)
    from_id, to_id, value = p["from_id"], p["to_id"], p["value"]
    before = memory.affinity(from_id, to_id)
    memory.record_feedback(from_id, to_id, value)
    after = memory.affinity(from_id, to_id)
    return {
        "from_id": from_id,
        "to_id": to_id,
        "value": value,
        "affinity_before": before,
        "affinity_after": after,
    }


# ---------------------------------------------------------------------- #
# 4. router: the physical path
# ---------------------------------------------------------------------- #

def router_local(memory: Memory, proposal: dict[str, Any]) -> dict[str, Any]:
    """Turn the approved match into a route: the target's zone, the ordered people
    the recipient would pass, and the on-the-way connector who can vouch. The
    graph traversal lives in memory.route(); this agent folds in the warm-intro
    connector the matchmaker already found so the delivery can name it."""
    r = memory.route(proposal["from_id"], proposal["to_id"])
    connector = proposal.get("connector")
    if not connector:
        # Fall back to the first "knows <target>" hop as the vouch connector.
        for hop in r["hops"]:
            if hop["reason"].startswith("knows "):
                connector = {"connector_id": hop["id"], "connector_name": hop["name"]}
                break
    return {"target_zone": r["target_zone"], "hops": r["hops"], "connector": connector}


# ---------------------------------------------------------------------- #
# guild cloud backend
# ---------------------------------------------------------------------- #

async def _call_guild(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke a deployed Guild agent. The request/response shape mirrors the local
    functions exactly, so swapping backends is a config change, not a rewrite."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{cfg.guild_base_url}/v1/workspaces/{cfg.guild_workspace}/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {cfg.guild_api_key}"},
            json={"input": payload},
        )
        r.raise_for_status()
        body = r.json()
        return body.get("output", body)


# ---------------------------------------------------------------------- #
# async entry points (what the motion layer, WP3, calls)
# ---------------------------------------------------------------------- #

async def propose(memory: Memory, person_id: str) -> dict[str, Any] | None:
    """matchmaker: pick who. Blocking graph reads run off the event loop so one
    slow lookup never stalls the live feed."""
    if cfg.guild_backend == "guild":
        me = memory.person(person_id)
        cands = memory.candidates(person_id, limit=5)
        if not me or not cands:
            return None
        # Attach each candidate's warm-intro path so the remote scorer matches ours.
        for c in cands:
            c["warm_intro"] = memory.warm_intro(person_id, c["id"])
        return await _call_guild("matchmaker", {"person": me, "candidates": cands})
    return await asyncio.to_thread(matchmaker_local, memory, person_id)


async def write_opener(memory: Memory, proposal: dict[str, Any]) -> str:
    """icebreaker: write the opener for an already-chosen proposal. The LLM call is
    blocking, so it goes off the event loop."""
    if cfg.guild_backend == "guild":
        me = memory.person(proposal["from_id"]) or {}
        them = memory.person(proposal["to_id"]) or {}
        out = await _call_guild("icebreaker", {"proposal": proposal, "me": me, "them": them})
        return out["opener"] if isinstance(out, dict) else out
    return await asyncio.to_thread(icebreaker_local, memory, proposal)


async def review(memory: Memory, proposal: dict[str, Any]) -> dict[str, Any]:
    """empath: the gate. Returns {approved, status, reason}."""
    if cfg.guild_backend == "guild":
        me = memory.person(proposal["from_id"]) or {}
        them = memory.person(proposal["to_id"]) or {}
        return await _call_guild(
            "empath",
            {
                "proposal": proposal,
                "recipient_state": me.get("state"),
                "recipient_name": me.get("name"),
                "target_state": them.get("state"),
                "target_name": them.get("name"),
                "already_met": memory.have_met(proposal["from_id"], proposal["to_id"]),
                "target_nudge_age": memory.recent_nudge_age(proposal["to_id"]),
                "cooldown_seconds": cfg.nudge_cooldown_seconds,
            },
        )
    return await asyncio.to_thread(empath_local, memory, proposal)


async def route(memory: Memory, proposal: dict[str, Any]) -> dict[str, Any]:
    """router: the physical path for an approved proposal."""
    if cfg.guild_backend == "guild":
        # The traversal stays in Python (Guild has no graph); the remote agent
        # only folds the result and the connector into the delivered shape.
        r = memory.route(proposal["from_id"], proposal["to_id"])
        return await _call_guild("router", {"route": r, "connector": proposal.get("connector")})
    return await asyncio.to_thread(router_local, memory, proposal)


async def learn(memory: Memory, feedback: dict[str, Any]) -> dict[str, Any]:
    """empath's learning update for a feedback event. Always local: it is a memory
    write, not a model call, and the affinity math must be identical everywhere."""
    return await asyncio.to_thread(learn_local, memory, feedback)
