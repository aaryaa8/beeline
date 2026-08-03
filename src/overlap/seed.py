"""Synthetic attendees for the room, plus the demo choreography.

Deliberately built so that:
  - overlaps exist but are not uniform, so the graph has real structure
  - matching is intent-first: several ask/offer pairs *complement* each other
    (one person asks for exactly what another offers), because the whole pitch
    is that the person worth meeting is the one whose offer answers your ask
  - some people share only a generic topic ("ai"), giving the empath something
    to veto
  - a few pairs already know each other, creating 3-hop warm-intro paths
  - people start in different zones and states, so routing is physical and the
    emotional gate is exercisable

Everything here is deterministic. `demo_sequence()` is the script WP8 replays on
stage, and it must tell the same story every time.
"""
from __future__ import annotations

from . import stream
from .config import ZONES, STATES

FLOOR = "Frontier Tower 16"

# Attendee roster. Each row is:
#   (id, name, role, interests[], ask, offer, zone, state)
#
# ask / offer normalize to lowercase Capability tags, and a complement is an
# EXACT tag match (my WANTS == their OFFERS). So the pairs below deliberately
# share a controlled vocabulary word-for-word, otherwise complements come back
# empty and the intent-first pitch silently degrades to shared-interest matching.
# The complementary pairs (ask on one side == offer on the other):
#   "design"                : Marcus wants  <-> Tomas offers      (a2 <-> a4)
#   "founder introductions" : Priya wants   <-> Marcus offers     (a1 <-> a2)
#   "retrieval evaluation"  : Fatima wants  <-> Ivan offers       (a5 <-> a8)
#   "research collaboration": Ivan wants    <-> Fatima offers     (a8 <-> a5, the double bond)
#   "agent memory advice"   : Lin wants     <-> Yuki offers       (a3 <-> a12)
#   "backend engineering"   : Nadia wants   <-> Chen offers       (a9 <-> a6)
# Rosa / Sol / Kwame carry no complement on purpose (and Rosa/Sol are not `open`),
# so the room also produces honest "only generic overlap" vetoes.
PEOPLE = [
    # id    name      role                interests                                              ask                        offer                       zone      state
    ("a1",  "Priya",  "ml engineer",      ["graph databases", "retrieval", "ai"],                "founder introductions",   "graph databases",          "Kitchen", "open"),
    ("a2",  "Marcus", "founder",          ["agent memory", "ai", "fundraising"],                 "design",                  "founder introductions",    "Presentation Stage",  "open"),
    ("a3",  "Lin",    "data engineer",    ["streaming", "event sourcing", "ai"],                 "agent memory advice",     "streaming design",         "Elevator Lobby", "open"),
    ("a4",  "Tomas",  "product designer", ["interface design", "agent memory"],                  "a technical cofounder",   "design",                   "Presentation Stage",  "open"),
    ("a5",  "Fatima", "research scientist",["retrieval", "evaluation", "graph databases"],       "retrieval evaluation",    "research collaboration",   "Kitchen", "open"),
    ("a6",  "Chen",   "backend engineer", ["streaming", "distributed systems"],                  "a frontend collaborator", "backend engineering",      "Back Work Rooms",   "open"),
    ("a7",  "Rosa",   "founder",          ["ai", "healthcare"],                                  "seed fundraising",        "healthcare introductions", "Back Work Rooms",   "heads-down"),
    ("a8",  "Ivan",   "ml engineer",      ["evaluation", "retrieval"],                           "research collaboration",  "retrieval evaluation",     "Kitchen", "open"),
    ("a9",  "Nadia",  "design engineer",  ["interface design", "data visualisation"],            "backend engineering",     "data visualisation",       "Elevator Lobby", "open"),
    ("a10", "Sol",    "infra engineer",   ["distributed systems", "event sourcing"],             "distributed systems help","devops help",              "Back Work Rooms",   "in-flow"),
    ("a11", "Kwame",  "student",          ["ai", "learning science"],                            "a mentor",                "user research",            "Presentation Stage",  "open"),
    ("a12", "Yuki",   "product manager",  ["agent memory", "evaluation"],                        "engineers to build with", "agent memory advice",      "Elevator Lobby", "open"),
]

# Pairs who already know each other. These create the 3-hop warm-intro routes:
# to reach someone new, the system can point out a mutual you can say hi to on
# the way. History MUST be loaded before check-ins (see demo_sequence) so the
# matchmaker never "introduces" two people who already met.
#   a1-a5: Priya knows Fatima      (both Window, graph/retrieval crowd)
#   a3-a6: Lin knows Chen          (bridges Coffee and Cafe, streaming crowd)
#   a4-a9: Tomas knows Nadia       (the design pair)
#   a8-a12: Ivan knows Yuki        (evaluation pair)
#   a2-a1: Marcus knows Priya      (gives Marcus a warm path toward the Window)
ACQUAINTED = [("a1", "a5"), ("a3", "a6"), ("a4", "a9"), ("a8", "a12"), ("a2", "a1")]


def checkin_events() -> list[dict]:
    """One checkin event per attendee, in roster order."""
    return [
        stream.checkin(
            id=pid, name=name, role=role, interests=list(interests),
            ask=ask, offer=offer, zone=zone, state=state,
        )
        for pid, name, role, interests, ask, offer, zone, state in PEOPLE
    ]


def acquaintance_events() -> list[dict]:
    """Prior meeting history, as `met` events. These write memory only; a met
    never fires a nudge."""
    return [stream.met(a=a, b=b) for a, b in ACQUAINTED]


def opening_sequence() -> list[dict]:
    """Prior history first, then the room fills up.

    The simpler variant: history + check-ins, nothing more. Kept so tests and
    callers that only want a populated room do not have to replay the whole
    stage script. `demo_sequence()` is the full choreography.

    Order matters more than it looks. If a check-in is processed before the
    system knows who already knows whom, the matchmaker will confidently
    introduce two people who met last year. Loading the acquaintance graph
    first is what stops that, and it is why memory has to lead motion rather
    than race it.
    """
    return acquaintance_events() + checkin_events()


def demo_sequence() -> list[dict]:
    """The deterministic stage script (BUILD_SPEC.md §4.2 / WP4 / §6).

    Replayed through the motion pipeline it must tell one legible story and hit
    every mandated-tech beat, the same way every time. The phases, in order:

      (a) HISTORY FIRST. Load prior acquaintances (`met`) before anyone checks
          in, so nobody is ever "introduced" to someone they already know. This
          is the memory-leads-motion rule from opening_sequence, and it is load
          bearing for the warm-intro paths.

      (b) CHECK PEOPLE IN. The room fills. Every checkin is a LaserData event,
          and the graph compounds underneath as they land.

      (c) MOVEMENT. A few people walk between zones (`position`). This makes the
          map move and makes any route physically true: the target's zone is
          where they actually are now, not where they started.

      (d) THE EMOTIONAL GATE. Flip ONE person to `recharging` right before they
          would be a nudge target, so the empath visibly holds an introduction
          instead of firing it. Fatima (a5) is the natural target for Ivan (a8)
          and Priya (a1) via retrieval/eval + a warm intro, so we set her
          `recharging` just before that match would land. "It saw I needed a
          minute."

      (e) THE LEARNING SHIFT. Fire ONE `feedback` of `not-for-me`, from Marcus
          (a2) about Tomas (a4). Marcus asks for a designer and Tomas offers
          design work, so that is the top complementary match; the negative tap
          suppresses Tomas for Marcus and pushes the next suggestion (Nadia, a9,
          who also does design/dataviz) forward. The visible shift is the point.

    The pacing is encoded only in ordering, not sleeps; the server/WP8 spaces
    replay in time. Comments mark each phase so the story is readable in code.
    """
    events: list[dict] = []

    # (a) Prior history, before any arrival.
    events += acquaintance_events()

    # (b) The room checks in.
    events += checkin_events()

    # (c) A few people move. Priya drifts from the Window toward the Stage
    #     (closer to Marcus); Ivan crosses to the Window toward Fatima; Chen
    #     comes in from the Cafe to Coffee (nearer Nadia, the frontend he could
    #     pair with). Movement is what makes the delivered route honest.
    events += [
        stream.position("a1", "Presentation Stage"),
        stream.position("a8", "Kitchen"),
        stream.position("a6", "Elevator Lobby"),
    ]

    # (d) The emotional gate. Fatima needs a minute, right before the
    #     retrieval/eval match to her would fire. The empath must HOLD, not
    #     deliver. This is self-reported, one tap, never sensed.
    events += [
        stream.state("a5", "recharging"),
    ]

    # (e) The learning shift. Marcus met Tomas and it was not for him. That one
    #     tap should suppress the top complementary match (designer) and move the
    #     next-best design match (Nadia) up for Marcus. The system learns from how
    #     the meeting felt, not from what it guessed.
    events += [
        stream.met("a2", "a4"),  # they actually met, so feedback can reference it
        stream.feedback(from_id="a2", to_id="a4", value="not-for-me"),
    ]

    return events
