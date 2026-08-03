"""The space agents: derive a floor plan from a room scan, and optimise the
networking workflow across the current areas.

Both use the LLM when a key is present and degrade to a sensible deterministic
answer otherwise, so the feature never hard-fails in a demo. The scan agent is
multimodal (Claude vision): a photo of the room becomes a suggested set of areas
plus the architectural landmarks worth routing around.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from .config import cfg


def _strip_data_url(image: str) -> tuple[str, str]:
    """Return (media_type, base64_payload) from a data URL or bare base64."""
    m = re.match(r"data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", image, re.DOTALL)
    if m:
        return m.group(1), m.group(2)
    return "image/jpeg", image


def _first_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply (it may wrap it in prose)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------------- #
# scan: a photo of the room -> areas + landmarks (the space planner)
# ---------------------------------------------------------------------- #

def scan_room(image: str) -> dict[str, Any]:
    """Look at a photo of the venue and propose the named areas to put on the
    map, plus the architectural landmarks a route can lean on ("the pillar", "the
    big window"). Returns {areas: [str], landmarks: [{name, note}], summary}.

    Without a key (or on any error) it returns a neutral generic layout so the UI
    still gets something to show."""
    fallback = {
        "areas": ["Entrance", "Main Floor", "Seating", "Quiet Corner"],
        "landmarks": [
            {"name": "Entrance", "note": "where people arrive and first mix"},
            {"name": "Seating", "note": "where longer conversations settle"},
        ],
        "summary": "A generic four-area layout. Add a photo and an API key for a scan-derived plan.",
        "source": "fallback",
    }
    if not cfg.has_llm or not image:
        return fallback
    try:
        import anthropic

        media_type, payload = _strip_data_url(image)
        # sanity: make sure it decodes
        base64.b64decode(payload[:64] + "==")
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key, timeout=25.0, max_retries=0)
        msg = client.messages.create(
            model=cfg.model,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": payload},
                        },
                        {
                            "type": "text",
                            "text": (
                                "You are laying out a live networking app over this room. Look at "
                                "the photo and return STRICT JSON only, no prose:\n"
                                '{"areas": ["4 to 6 short area names people would recognise"], '
                                '"landmarks": [{"name": "...", "note": "why it helps people navigate"}], '
                                '"summary": "one plain sentence"}\n'
                                "Areas are places people gather (e.g. Kitchen, Stage, Lobby). "
                                "Landmarks are fixed architectural features useful for directions "
                                "(a pillar, a big window, a staircase). No em dashes."
                            ),
                        },
                    ],
                }
            ],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        data = _first_json(text)
        if not data or not data.get("areas"):
            return fallback
        # normalise
        areas = [str(a).strip() for a in data.get("areas", []) if str(a).strip()][:8]
        landmarks = [
            {"name": str(l.get("name", "")).strip(), "note": str(l.get("note", "")).strip()}
            for l in data.get("landmarks", [])
            if isinstance(l, dict) and str(l.get("name", "")).strip()
        ][:8]
        return {
            "areas": areas,
            "landmarks": landmarks,
            "summary": str(data.get("summary", "")).strip() or "Derived from your room scan.",
            "source": "vision",
        }
    except Exception as exc:
        return {**fallback, "summary": f"Scan fell back to a generic layout ({type(exc).__name__})."}


# ---------------------------------------------------------------------- #
# optimise: read the live graph and suggest workflow improvements
# ---------------------------------------------------------------------- #

def optimise(snapshot: dict[str, Any], bridges: list[dict[str, Any]]) -> dict[str, Any]:
    """Given the current room (areas, who is where, which topics are crowded but
    unconnected) suggest concrete moves that would create more introductions:
    where to seed a conversation, which area is a dead end, who to reposition.

    `bridges` is memory.bridge_topics(): topics many people share but few of
    those people have met. That is exactly the untapped-connection signal, so we
    hand it to the agent as the spine of the recommendation."""
    zones = snapshot.get("zones", [])
    people = [n for n in snapshot.get("nodes", []) if n.get("kind") == "person"]
    by_zone: dict[str, list[str]] = {}
    for p in people:
        by_zone.setdefault(p.get("zone") or "unknown", []).append(p.get("label"))

    # Deterministic baseline: the highest-unmet bridge topic + the emptiest area.
    baseline: list[dict[str, str]] = []
    if bridges:
        top = bridges[0]
        baseline.append({
            "title": f"Gather the {top['topic']} crowd",
            "detail": (
                f"{top['people']} people care about {top['topic']} but only "
                f"{int(top.get('met', 0))} of those pairs have met. Seed a "
                f"{top['topic']} conversation in one area and route them there."
            ),
        })
    if zones:
        counts = {z: len(by_zone.get(z, [])) for z in zones}
        empty = min(counts, key=counts.get)
        busy = max(counts, key=counts.get)
        if counts[busy] - counts[empty] >= 2:
            baseline.append({
                "title": f"Even out {empty}",
                "detail": f"{busy} is crowded and {empty} is quiet. Move a magnet (food, a talk) to {empty} to spread the room.",
            })

    if not cfg.has_llm:
        return {"suggestions": baseline or [{"title": "Room looks balanced", "detail": "No obvious dead ends right now."}], "source": "heuristic"}

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key, timeout=20.0, max_retries=0)
        summary = {
            "areas": zones,
            "people_per_area": {z: by_zone.get(z, []) for z in zones},
            "unmet_bridge_topics": bridges[:5],
        }
        msg = client.messages.create(
            model=cfg.model,
            max_tokens=450,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You optimise a live networking room. Given this state, return STRICT "
                        "JSON only: {\"suggestions\": [{\"title\": \"short\", \"detail\": \"one "
                        "concrete action\"}]}. Focus on turning unmet shared interests into "
                        "introductions and fixing dead-end areas. Two or three suggestions. No em dashes.\n\n"
                        + json.dumps(summary)
                    ),
                }
            ],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        data = _first_json(text)
        sugg = data.get("suggestions") if data else None
        if not sugg:
            return {"suggestions": baseline, "source": "heuristic"}
        clean = [
            {"title": str(s.get("title", "")).strip(), "detail": str(s.get("detail", "")).strip()}
            for s in sugg
            if isinstance(s, dict) and str(s.get("title", "")).strip()
        ][:4]
        return {"suggestions": clean or baseline, "source": "agent"}
    except Exception:
        return {"suggestions": baseline, "source": "heuristic"}
