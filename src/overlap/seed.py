"""Synthetic attendees for the room.

Deliberately built so that:
  - overlaps exist but are not uniform, so the graph has real structure
  - some people share only a generic topic, giving the critic something to veto
  - a few pairs already know each other, creating warm-intro paths
"""
from __future__ import annotations

from . import stream

FLOOR = "Frontier Tower 16"
CAFE = "Cafe"

PEOPLE = [
    ("a1", "Priya", "ml engineer", ["graph databases", "retrieval", "ai"], FLOOR),
    ("a2", "Marcus", "founder", ["agent memory", "ai", "fundraising"], FLOOR),
    ("a3", "Lin", "data engineer", ["streaming", "event sourcing", "ai"], FLOOR),
    ("a4", "Tomas", "product designer", ["interface design", "agent memory"], FLOOR),
    ("a5", "Fatima", "research scientist", ["retrieval", "evaluation", "graph databases"], FLOOR),
    ("a6", "Chen", "backend engineer", ["streaming", "distributed systems"], CAFE),
    ("a7", "Rosa", "founder", ["ai", "healthcare"], CAFE),
    ("a8", "Ivan", "ml engineer", ["evaluation", "retrieval"], FLOOR),
    ("a9", "Nadia", "design engineer", ["interface design", "data visualisation"], FLOOR),
    ("a10", "Sol", "infra engineer", ["distributed systems", "event sourcing"], CAFE),
    ("a11", "Kwame", "student", ["ai", "learning science"], FLOOR),
    ("a12", "Yuki", "product manager", ["agent memory", "evaluation"], FLOOR),
]

# Pairs who already know each other. These create the 3-hop warm-intro routes.
ACQUAINTED = [("a1", "a5"), ("a3", "a6"), ("a4", "a9"), ("a8", "a12")]


def checkin_events() -> list[dict]:
    return [
        stream.make_event(
            stream.CHECKIN, id=pid, name=name, role=role, interests=list(interests), location=loc
        )
        for pid, name, role, interests, loc in PEOPLE
    ]


def acquaintance_events() -> list[dict]:
    return [stream.make_event(stream.MET, a=a, b=b) for a, b in ACQUAINTED]


def opening_sequence() -> list[dict]:
    """Prior history first, then the room fills up.

    Order matters more than it looks. If a check-in is processed before the
    system knows who already knows whom, the matchmaker will confidently
    introduce two people who met last year. Loading the acquaintance graph
    first is what stops that, and it is why memory has to lead motion rather
    than race it.
    """
    return acquaintance_events() + checkin_events()
