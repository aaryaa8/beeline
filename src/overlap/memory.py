"""FalkorDB: the memory layer.

Everything the system has ever seen lives here as a graph. The point of using a
graph rather than a vector store is the multi-hop queries at the bottom of this
file: a flat similarity search can tell you two people are alike, but it cannot
tell you *who in the room can introduce them*, nor that your ask matches their
offer through someone who can vouch. Those paths are the demo.

Graph shape (BUILD_SPEC §4.3)
-----------------------------
    (:Person {id, name, role, checked_in_at, state})
    (:Topic  {name})
    (:Capability {name})       # normalized ask/offer tag
    (:Zone   {name})

    (:Person)-[:INTERESTED_IN {weight}]->(:Topic)
    (:Person)-[:WANTS]->(:Capability)          # their ask
    (:Person)-[:OFFERS]->(:Capability)         # their offer
    (:Person)-[:AT {since}]->(:Zone)
    (:Person)-[:MET {at}]-(:Person)            # stored one way, queried undirected
    (:Person)-[:NUDGED {at, status}]->(:Person)
    (:Person)-[:FELT {value, at}]->(:Person)   # affinity from one-tap feedback

Intent-first matching (the FalkorDB "load-bearing" argument): a *complementary*
match is my WANTS ∩ their OFFERS (or the reverse). That is a relationship-aware
traversal a flat vector store cannot express, so it stays a real graph query.
"""
from __future__ import annotations

import re
import time
from typing import Any

from falkordb import FalkorDB

from .config import cfg, ZONES

# Topics too broad to justify an introduction on their own. At an AI hackathon,
# "ai" is not an overlap, it is the weather. Mirrors GENERIC_TOPICS in agents.py
# and is duplicated here (rather than imported) because agents.py imports Memory,
# and a back-import would be circular.
GENERIC_TOPICS = {"ai", "tech", "technology", "startups", "software", "coding", "llm", "llms"}


def normalize_cap(text: str | None) -> str:
    """Turn free-text ask/offer into a stable capability tag. Lowercase, trim,
    collapse internal whitespace. An LLM tidy could go here later; the contract
    is that it must always degrade to exactly this plain version, so matching
    ("my WANTS ∩ their OFFERS") stays an exact-string set intersection."""
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


def _cap_node_id(name: str) -> str:
    """Snapshot node id for a capability, e.g. "hiring: design" -> cap:hiring-design."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"cap:{slug}"


class Memory:
    def __init__(self) -> None:
        kwargs: dict[str, Any] = {"host": cfg.falkor_host, "port": cfg.falkor_port}
        if cfg.falkor_username:
            kwargs["username"] = cfg.falkor_username
        if cfg.falkor_password:
            kwargs["password"] = cfg.falkor_password
        self.db = FalkorDB(**kwargs)
        self.g = self.db.select_graph(cfg.graph_name)

    # ------------------------------------------------------------------ #
    # writes: memory compounds as the event stream arrives
    # ------------------------------------------------------------------ #

    def ensure_indices(self) -> None:
        for stmt in (
            "CREATE INDEX FOR (p:Person) ON (p.id)",
            "CREATE INDEX FOR (t:Topic) ON (t.name)",
            "CREATE INDEX FOR (c:Capability) ON (c.name)",
            "CREATE INDEX FOR (z:Zone) ON (z.name)",
        ):
            try:
                self.g.query(stmt)
            except Exception:
                pass  # already exists

    def reset(self) -> None:
        try:
            self.g.delete()
        except Exception:
            pass
        self.g = self.db.select_graph(cfg.graph_name)
        self.ensure_indices()

    def record_checkin(
        self,
        person_id: str,
        name: str,
        role: str,
        interests: list[str],
        ask: str = "",
        offer: str = "",
        zone: str = "",
        state: str = "open",
    ) -> None:
        """A person arrives. Sets identity + state, places them in a zone, and
        seeds interests and intent. A re-checkin moves them (old AT is cleared)."""
        self.g.query(
            """
            MERGE (p:Person {id: $id})
            SET p.name = $name, p.role = $role, p.state = $state,
                p.checked_in_at = $ts
            WITH p
            OPTIONAL MATCH (p)-[old:AT]->(:Zone)
            DELETE old
            WITH p
            MERGE (z:Zone {name: $zone})
            MERGE (p)-[a:AT]->(z)
            SET a.since = $ts
            """,
            {
                "id": person_id,
                "name": name,
                "role": role,
                "state": state,
                "zone": zone,
                "ts": time.time(),
            },
        )
        for topic in interests:
            self.record_interest(person_id, topic)
        # ask/offer are optional at check-in; set_intent no-ops on empties.
        self.set_intent(person_id, ask=ask, offer=offer)

    def record_interest(self, person_id: str, topic: str, weight: float = 1.0) -> None:
        self.g.query(
            """
            MERGE (p:Person {id: $id})
            MERGE (t:Topic {name: $topic})
            MERGE (p)-[r:INTERESTED_IN]->(t)
            SET r.weight = coalesce(r.weight, 0) + $w
            """,
            {"id": person_id, "topic": topic.strip().lower(), "w": weight},
        )

    def set_intent(self, person_id: str, ask: str = "", offer: str = "") -> None:
        """Set/update what a person wants (ask -> WANTS) and offers (OFFERS).
        Each is a single current intent, so a new one replaces the old edges of
        that kind. Empty strings are left untouched, so this is safe to call from
        check-in with only one side filled."""
        ask_tag = normalize_cap(ask)
        if ask_tag:
            self.g.query(
                """
                MERGE (p:Person {id: $id})
                WITH p
                OPTIONAL MATCH (p)-[w:WANTS]->(:Capability)
                DELETE w
                WITH p
                MERGE (c:Capability {name: $cap})
                MERGE (p)-[:WANTS]->(c)
                """,
                {"id": person_id, "cap": ask_tag},
            )
        offer_tag = normalize_cap(offer)
        if offer_tag:
            self.g.query(
                """
                MERGE (p:Person {id: $id})
                WITH p
                OPTIONAL MATCH (p)-[o:OFFERS]->(:Capability)
                DELETE o
                WITH p
                MERGE (c:Capability {name: $cap})
                MERGE (p)-[:OFFERS]->(c)
                """,
                {"id": person_id, "cap": offer_tag},
            )

    def set_state(self, person_id: str, state: str) -> None:
        """Self-reported emotional state. Only `open` participates in routing."""
        self.g.query(
            "MERGE (p:Person {id: $id}) SET p.state = $state",
            {"id": person_id, "state": state},
        )

    def record_met(self, a: str, b: str) -> None:
        self.g.query(
            """
            MERGE (x:Person {id: $a})
            MERGE (y:Person {id: $b})
            MERGE (x)-[m:MET]->(y)
            SET m.at = $ts
            """,
            {"a": a, "b": b, "ts": time.time()},
        )

    def record_position(self, person_id: str, zone: str) -> None:
        """Move a person to a zone. A person is AT exactly one zone, so any prior
        AT edge is cleared first (otherwise room_energy would double-count them)."""
        self.g.query(
            """
            MERGE (p:Person {id: $id})
            WITH p
            OPTIONAL MATCH (p)-[old:AT]->(:Zone)
            DELETE old
            WITH p
            MERGE (z:Zone {name: $zone})
            MERGE (p)-[a:AT]->(z)
            SET a.since = $ts
            """,
            {"id": person_id, "zone": zone, "ts": time.time()},
        )

    # Back-compat alias: the old event type was `location`. Kept so anything not
    # yet migrated to `record_position` keeps working.
    def record_location(self, person_id: str, location: str) -> None:
        self.record_position(person_id, location)

    def record_nudge(self, a: str, b: str, status: str) -> None:
        self.g.query(
            """
            MATCH (x:Person {id: $a}), (y:Person {id: $b})
            MERGE (x)-[n:NUDGED]->(y)
            SET n.at = $ts, n.status = $status
            """,
            {"a": a, "b": b, "ts": time.time(), "status": status},
        )

    def record_feedback(self, from_id: str, to_id: str, value: str) -> None:
        """One tap after a meeting. Writes a FELT edge that affinity() reads. The
        latest tap for a pair wins (single edge, updated), so the learning signal
        is deterministic rather than a growing pile."""
        self.g.query(
            """
            MERGE (x:Person {id: $a})
            MERGE (y:Person {id: $b})
            MERGE (x)-[f:FELT]->(y)
            SET f.value = $value, f.at = $ts
            """,
            {"a": from_id, "b": to_id, "value": value, "ts": time.time()},
        )

    # ------------------------------------------------------------------ #
    # reads: the multi-hop traversals that justify a graph
    # ------------------------------------------------------------------ #

    def candidates(self, person_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Intent-first matching. For each reachable person, we gather:
          - complements: my WANTS ∩ their OFFERS, plus their WANTS ∩ my OFFERS.
            This is the ask/offer match, and it is the top-ranked signal.
          - shared_topics: interests in common (specific ones, past the generic
            "ai/tech" floor, break ties beneath complements).
        Excludes people I have already met or been nudged about, and returns only
        people where *both* parties are `open` (the emotional gate). If I am not
        `open`, nobody is routable to me, so the list is empty."""
        res = self.g.query(
            """
            MATCH (me:Person {id: $id})
            OPTIONAL MATCH (me)-[:WANTS]->(mw:Capability)
            WITH me, collect(DISTINCT mw.name) AS my_wants
            OPTIONAL MATCH (me)-[:OFFERS]->(mo:Capability)
            WITH me, my_wants, collect(DISTINCT mo.name) AS my_offers
            OPTIONAL MATCH (me)-[:INTERESTED_IN]->(mt:Topic)
            WITH me, my_wants, my_offers, collect(DISTINCT mt.name) AS my_topics
            MATCH (them:Person)
            WHERE them.id <> $id
              AND me.state = 'open' AND them.state = 'open'
              AND NOT (me)-[:MET]-(them)
              AND NOT (me)-[:NUDGED]-(them)
            OPTIONAL MATCH (them)-[:WANTS]->(tw:Capability)
            WITH me, my_wants, my_offers, my_topics, them,
                 collect(DISTINCT tw.name) AS their_wants
            OPTIONAL MATCH (them)-[:OFFERS]->(to:Capability)
            WITH me, my_wants, my_offers, my_topics, them, their_wants,
                 collect(DISTINCT to.name) AS their_offers
            OPTIONAL MATCH (them)-[:INTERESTED_IN]->(tt:Topic)
            WITH me, my_wants, my_offers, my_topics, them, their_wants, their_offers,
                 collect(DISTINCT tt.name) AS their_topics
            OPTIONAL MATCH (them)-[:AT]->(z:Zone)
            RETURN them.id, them.name, them.role, z.name AS zone, them.state,
                   my_wants, my_offers, my_topics,
                   their_wants, their_offers, their_topics
            """,
            {"id": person_id},
        )

        out: list[dict[str, Any]] = []
        for r in res.result_set:
            my_wants = set(x for x in r[5] if x)
            my_offers = set(x for x in r[6] if x)
            my_topics = set(x for x in r[7] if x)
            their_wants = set(x for x in r[8] if x)
            their_offers = set(x for x in r[9] if x)
            their_topics = set(x for x in r[10] if x)

            # Complement both ways: they can serve my ask, or I can serve theirs.
            complements = sorted((my_wants & their_offers) | (their_wants & my_offers))
            shared = sorted(my_topics & their_topics)
            specific = [t for t in shared if t not in GENERIC_TOPICS]

            # No reason to introduce two people who neither complement nor share.
            if not complements and not shared:
                continue

            out.append(
                {
                    "id": r[0],
                    "name": r[1],
                    "role": r[2],
                    "zone": r[3],
                    "state": r[4],
                    "shared_topics": shared,
                    "complements": complements,
                    "overlap": len(shared),
                    "affinity": self.affinity(person_id, r[0]),
                    "_specific": len(specific),
                }
            )

        # Intent-first ordering: complements dominate, then specific shared
        # interests, then raw overlap, then how the pair has felt so far.
        out.sort(
            key=lambda c: (len(c["complements"]), c["_specific"], c["overlap"], c["affinity"]),
            reverse=True,
        )
        for c in out:
            c.pop("_specific", None)
        return out[:limit]

    def warm_intro(self, a: str, b: str) -> dict[str, Any] | None:
        """Three hops: a -> topic <- connector -> MET -> b.

        The answer to "why should I trust this suggestion" is a person, not a
        score. This is the single query that makes the graph load-bearing, and
        the one worth putting on screen during the demo."""
        res = self.g.query(
            """
            MATCH (a:Person {id: $a})-[:INTERESTED_IN]->(t:Topic)
                  <-[:INTERESTED_IN]-(c:Person)-[:MET]-(b:Person {id: $b})
            WHERE c.id <> $a AND c.id <> $b
            RETURN c.id, c.name, collect(DISTINCT t.name) AS via
            ORDER BY size(via) DESC
            LIMIT 1
            """,
            {"a": a, "b": b},
        )
        if not res.result_set:
            return None
        row = res.result_set[0]
        return {"connector_id": row[0], "connector_name": row[1], "via_topics": row[2]}

    def route(self, a: str, b: str) -> dict[str, Any]:
        """The physical path from A to their match B: the target's zone, plus the
        people A would pass, ordered:
          1. people already in A's zone (you brush past them leaving),
          2. a known connector who has MET B (say hi, they can vouch),
          3. people in B's zone (the neighbours you arrive among).
        Kept a real traversal so "go this way, say hi to Chen on the way" comes
        straight out of the graph, not a guess."""
        # Target's zone.
        zres = self.g.query(
            "MATCH (b:Person {id: $b})-[:AT]->(z:Zone) RETURN z.name",
            {"b": b},
        )
        target_zone = zres.result_set[0][0] if zres.result_set else None

        # Prefer a connector A also knows (a real a-MET-c-MET-b vouch chain);
        # fall back to anyone who has met B. The OPTIONAL MATCH + null-check on
        # `known` avoids the FalkorDB CASE/pattern-predicate bug.
        cres = self.g.query(
            """
            MATCH (c:Person)-[:MET]-(b:Person {id: $b})
            WHERE c.id <> $a AND c.id <> $b AND c.name IS NOT NULL
            OPTIONAL MATCH (a:Person {id: $a})-[known:MET]-(c)
            OPTIONAL MATCH (c)-[:AT]->(z:Zone)
            WITH c, z, (CASE WHEN known IS NULL THEN 0 ELSE 1 END) AS a_knows
            RETURN c.id, c.name, z.name, a_knows
            ORDER BY a_knows DESC
            LIMIT 1
            """,
            {"a": a, "b": b},
        )
        connector = cres.result_set[0] if cres.result_set else None
        connector_id = connector[0] if connector else None

        b_name_res = self.g.query(
            "MATCH (b:Person {id: $b}) RETURN b.name", {"b": b}
        )
        b_name = b_name_res.result_set[0][0] if b_name_res.result_set else b

        hops: list[dict[str, Any]] = []
        seen: set[str] = {a, b}
        if connector_id:
            seen.add(connector_id)

        # 1. People in A's own zone.
        mine = self.g.query(
            """
            MATCH (a:Person {id: $a})-[:AT]->(z:Zone)<-[:AT]-(x:Person)
            WHERE x.id <> $a AND x.name IS NOT NULL
            RETURN x.id, x.name, z.name
            ORDER BY x.checked_in_at
            """,
            {"a": a},
        )
        for r in mine.result_set:
            if r[0] in seen:
                continue
            seen.add(r[0])
            hops.append({"id": r[0], "name": r[1], "zone": r[2], "reason": "in your zone"})

        # 2. The known connector who can vouch for you.
        if connector:
            hops.append(
                {
                    "id": connector[0],
                    "name": connector[1],
                    "zone": connector[2],
                    "reason": f"knows {b_name}",
                }
            )

        # 3. People already in the target's zone.
        if target_zone:
            near = self.g.query(
                """
                MATCH (b:Person {id: $b})-[:AT]->(z:Zone)<-[:AT]-(y:Person)
                WHERE y.id <> $b AND y.name IS NOT NULL
                RETURN y.id, y.name, z.name
                ORDER BY y.checked_in_at
                """,
                {"b": b},
            )
            for r in near.result_set:
                if r[0] in seen:
                    continue
                seen.add(r[0])
                hops.append(
                    {"id": r[0], "name": r[1], "zone": r[2], "reason": f"near {b_name}"}
                )

        return {"target_zone": target_zone, "hops": hops}

    def affinity(self, a: str, b: str) -> float:
        """How this pair is likely to land, from feedback so far.
          - direct FELT taps between a and b dominate (clicked +1, not-for-me -1),
          - a small similarity term: a's taps on *other* people carry over to b in
            proportion to shared interests (you liked someone like them -> nudge up).
        Positive means "lean in", negative means "hold off"."""
        value_map = {"clicked": 1.0, "neutral": 0.0, "not-for-me": -1.0}

        # Direct feedback, either direction.
        direct_res = self.g.query(
            """
            MATCH (x:Person {id: $a})-[f:FELT]-(y:Person {id: $b})
            RETURN collect(f.value)
            """,
            {"a": a, "b": b},
        )
        direct = 0.0
        if direct_res.result_set and direct_res.result_set[0][0]:
            for v in direct_res.result_set[0][0]:
                direct += value_map.get(v, 0.0)

        # Similarity carry-over: a's feelings about people who share b's topics.
        sim_res = self.g.query(
            """
            MATCH (a:Person {id: $a})-[f:FELT]->(o:Person)
            WHERE o.id <> $b
            OPTIONAL MATCH (o)-[:INTERESTED_IN]->(t:Topic)
                          <-[:INTERESTED_IN]-(b:Person {id: $b})
            RETURN f.value, count(DISTINCT t) AS sim
            """,
            {"a": a, "b": b},
        )
        sim_term = 0.0
        for r in sim_res.result_set:
            sim_term += value_map.get(r[0], 0.0) * float(r[1] or 0)

        return round(direct + 0.1 * sim_term, 4)

    def room_energy(self) -> list[dict[str, Any]]:
        """State counts per zone, for the energy heat layer. Every zone in the
        enum appears, even empty ones, so the map layout is stable."""
        res = self.g.query(
            """
            MATCH (p:Person)-[:AT]->(z:Zone)
            WHERE p.state IS NOT NULL
            RETURN z.name, p.state, count(p)
            """
        )
        by_zone: dict[str, dict[str, int]] = {
            z: {"zone": z, "open": 0, "heads-down": 0, "recharging": 0, "in-flow": 0}
            for z in ZONES
        }
        for r in res.result_set:
            zone, state, n = r[0], r[1], int(r[2])
            row = by_zone.setdefault(
                zone,
                {"zone": zone, "open": 0, "heads-down": 0, "recharging": 0, "in-flow": 0},
            )
            if state in row:
                row[state] = n
        return list(by_zone.values())

    def bridge_topics(self, limit: int = 5) -> list[dict[str, Any]]:
        """Topics that lots of people care about but few of those people have
        met each other. High bridge value means the room is failing to connect
        around that subject, which is exactly where a nudge earns its keep."""
        res = self.g.query(
            """
            MATCH (p:Person)-[:INTERESTED_IN]->(t:Topic)
            WITH t, collect(p) AS people, count(p) AS n
            WHERE n > 1
            UNWIND people AS x
            UNWIND people AS y
            WITH t, n, x, y
            WHERE x.id < y.id
            OPTIONAL MATCH (x)-[m:MET]-(y)
            WITH t, n, CASE WHEN m IS NULL THEN 0 ELSE 1 END AS didmeet
            WITH t, n, count(*) AS pairs, sum(didmeet) AS met_pairs
            RETURN t.name, n, pairs, met_pairs, (pairs - met_pairs) AS unmet
            ORDER BY unmet DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        return [
            {"topic": r[0], "people": r[1], "pairs": r[2], "met": r[3], "unmet": r[4]}
            for r in res.result_set
        ]

    def recent_nudge_age(self, person_id: str) -> float | None:
        """Seconds since an introduction was actually *delivered* involving this
        person. Only approved nudges count: a veto means nothing reached anyone,
        so letting it start a cooldown would make one veto cascade into
        vetoing the entire room."""
        res = self.g.query(
            """
            MATCH (p:Person)-[n:NUDGED]-(:Person)
            WHERE p.id = $id AND n.status = 'approved'
            RETURN max(n.at) AS last
            """,
            {"id": person_id},
        )
        if not res.result_set or res.result_set[0][0] is None:
            return None
        return time.time() - float(res.result_set[0][0])

    def have_met(self, a: str, b: str) -> bool:
        res = self.g.query(
            "MATCH (x:Person {id:$a})-[:MET]-(y:Person {id:$b}) RETURN count(*) AS n",
            {"a": a, "b": b},
        )
        return bool(res.result_set and res.result_set[0][0])

    def person(self, person_id: str) -> dict[str, Any] | None:
        res = self.g.query(
            """
            MATCH (p:Person {id: $id})
            OPTIONAL MATCH (p)-[:INTERESTED_IN]->(t:Topic)
            OPTIONAL MATCH (p)-[:WANTS]->(wc:Capability)
            OPTIONAL MATCH (p)-[:OFFERS]->(oc:Capability)
            OPTIONAL MATCH (p)-[:AT]->(z:Zone)
            RETURN p.id, p.name, p.role, p.state,
                   collect(DISTINCT t.name),
                   collect(DISTINCT wc.name),
                   collect(DISTINCT oc.name),
                   z.name
            """,
            {"id": person_id},
        )
        if not res.result_set:
            return None
        r = res.result_set[0]
        wants = [c for c in r[5] if c]
        offers = [c for c in r[6] if c]
        return {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "state": r[3],
            "interests": [t for t in r[4] if t],
            "ask": wants[0] if wants else None,
            "offer": offers[0] if offers else None,
            "zone": r[7],
        }

    # ------------------------------------------------------------------ #
    # snapshot for the live visual (BUILD_SPEC §4.6, frozen for the frontend)
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # A person referenced only by a prior-history MET edge exists in the
        # graph but has not checked in yet. Hide them until they arrive, so the
        # canvas never shows an unlabelled ghost node. Each person carries their
        # current zone and state so the map can place and colour them.
        people = self.g.query(
            """
            MATCH (p:Person) WHERE p.name IS NOT NULL
            OPTIONAL MATCH (p)-[:AT]->(z:Zone)
            RETURN p.id, p.name, p.role, z.name, p.state
            ORDER BY p.checked_in_at
            """
        )
        for r in people.result_set:
            nodes.append(
                {
                    "id": r[0],
                    "kind": "person",
                    "label": r[1],
                    "role": r[2],
                    "zone": r[3],
                    "state": r[4],
                }
            )

        topics = self.g.query(
            """
            MATCH (t:Topic)<-[:INTERESTED_IN]-(p:Person)
            RETURN t.name, count(p) AS n ORDER BY n DESC
            """
        )
        for r in topics.result_set:
            nodes.append({"id": f"topic:{r[0]}", "kind": "topic", "label": r[0], "weight": r[1]})

        caps = self.g.query("MATCH (c:Capability) RETURN DISTINCT c.name")
        for r in caps.result_set:
            nodes.append({"id": _cap_node_id(r[0]), "kind": "capability", "label": r[0]})

        interests = self.g.query(
            "MATCH (p:Person)-[:INTERESTED_IN]->(t:Topic) RETURN p.id, t.name"
        )
        for r in interests.result_set:
            edges.append({"source": r[0], "target": f"topic:{r[1]}", "kind": "interest"})

        met = self.g.query("MATCH (a:Person)-[:MET]->(b:Person) RETURN a.id, b.id")
        for r in met.result_set:
            edges.append({"source": r[0], "target": r[1], "kind": "met"})

        nudged = self.g.query(
            "MATCH (a:Person)-[n:NUDGED]->(b:Person) RETURN a.id, b.id, n.status"
        )
        for r in nudged.result_set:
            edges.append({"source": r[0], "target": r[1], "kind": "nudge", "status": r[2]})

        return {"zones": list(ZONES), "nodes": nodes, "edges": edges}

    def stats(self) -> dict[str, int]:
        def one(q: str) -> int:
            res = self.g.query(q)
            return int(res.result_set[0][0]) if res.result_set else 0

        return {
            "people": one("MATCH (p:Person) WHERE p.name IS NOT NULL RETURN count(p)"),
            "topics": one("MATCH (t:Topic) RETURN count(t)"),
            "capabilities": one("MATCH (c:Capability) RETURN count(c)"),
            "interests": one("MATCH ()-[r:INTERESTED_IN]->() RETURN count(r)"),
            "met": one("MATCH ()-[r:MET]->() RETURN count(r)"),
            "nudges": one("MATCH ()-[r:NUDGED]->() RETURN count(r)"),
            "feedback": one("MATCH ()-[r:FELT]->() RETURN count(r)"),
        }
