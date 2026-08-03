"""FalkorDB: the memory layer.

Everything the system has ever seen lives here as a graph. The point of using a
graph rather than a vector store is the multi-hop queries at the bottom of this
file: a flat similarity search can tell you two people are alike, but it cannot
tell you *who in the room can introduce them*. That path is the demo.

Graph shape
-----------
    (:Person {id, name, role, checked_in_at})
    (:Topic  {name})
    (:Location {name})

    (:Person)-[:INTERESTED_IN {weight}]->(:Topic)
    (:Person)-[:AT {since}]->(:Location)
    (:Person)-[:MET {at}]-(:Person)          # stored one way, queried undirected
    (:Person)-[:NUDGED {at, status}]->(:Person)
"""
from __future__ import annotations

import time
from typing import Any

from falkordb import FalkorDB

from .config import cfg


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
            "CREATE INDEX FOR (l:Location) ON (l.name)",
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
        self, person_id: str, name: str, role: str, interests: list[str], location: str
    ) -> None:
        self.g.query(
            """
            MERGE (p:Person {id: $id})
            SET p.name = $name, p.role = $role, p.checked_in_at = $ts
            WITH p
            MERGE (l:Location {name: $loc})
            MERGE (p)-[a:AT]->(l)
            SET a.since = $ts
            """,
            {"id": person_id, "name": name, "role": role, "loc": location, "ts": time.time()},
        )
        for topic in interests:
            self.record_interest(person_id, topic)

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

    def record_location(self, person_id: str, location: str) -> None:
        self.g.query(
            """
            MERGE (p:Person {id: $id})
            MERGE (l:Location {name: $loc})
            MERGE (p)-[a:AT]->(l)
            SET a.since = $ts
            """,
            {"id": person_id, "loc": location, "ts": time.time()},
        )

    def record_nudge(self, a: str, b: str, status: str) -> None:
        self.g.query(
            """
            MATCH (x:Person {id: $a}), (y:Person {id: $b})
            MERGE (x)-[n:NUDGED]->(y)
            SET n.at = $ts, n.status = $status
            """,
            {"a": a, "b": b, "ts": time.time(), "status": status},
        )

    # ------------------------------------------------------------------ #
    # reads: the multi-hop traversals that justify a graph
    # ------------------------------------------------------------------ #

    def candidates(self, person_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Two hops: me -> topic <- them. Excludes people I have already met and
        people I have already been nudged about. This is the query a vector
        store approximates badly, because `shared` is an exact set, not a score."""
        res = self.g.query(
            """
            MATCH (me:Person {id: $id})-[:INTERESTED_IN]->(t:Topic)
                  <-[:INTERESTED_IN]-(them:Person)
            WHERE them.id <> $id
              AND NOT (me)-[:MET]-(them)
              AND NOT (me)-[:NUDGED]-(them)
            WITH them, collect(DISTINCT t.name) AS shared
            OPTIONAL MATCH (them)-[:AT]->(l:Location)
            RETURN them.id, them.name, them.role, shared, size(shared) AS n,
                   l.name AS location
            ORDER BY n DESC
            LIMIT $limit
            """,
            {"id": person_id, "limit": limit},
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "role": r[2],
                "shared_topics": r[3],
                "overlap": r[4],
                "location": r[5],
            }
            for r in res.result_set
        ]

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
            OPTIONAL MATCH (p)-[:AT]->(l:Location)
            RETURN p.id, p.name, p.role, collect(DISTINCT t.name), l.name
            """,
            {"id": person_id},
        )
        if not res.result_set:
            return None
        r = res.result_set[0]
        return {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "interests": [t for t in r[3] if t],
            "location": r[4],
        }

    # ------------------------------------------------------------------ #
    # snapshot for the live visual
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # A person referenced only by a prior-history MET edge exists in the
        # graph but has not checked in yet. Hide them until they arrive, so the
        # canvas never shows an unlabelled ghost node.
        people = self.g.query(
            """
            MATCH (p:Person) WHERE p.name IS NOT NULL
            RETURN p.id, p.name, p.role ORDER BY p.checked_in_at
            """
        )
        for r in people.result_set:
            nodes.append({"id": r[0], "label": r[1], "kind": "person", "role": r[2]})

        topics = self.g.query(
            """
            MATCH (t:Topic)<-[:INTERESTED_IN]-(p:Person)
            RETURN t.name, count(p) AS n ORDER BY n DESC
            """
        )
        for r in topics.result_set:
            nodes.append({"id": f"topic:{r[0]}", "label": r[0], "kind": "topic", "weight": r[1]})

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

        return {"nodes": nodes, "edges": edges}

    def stats(self) -> dict[str, int]:
        def one(q: str) -> int:
            res = self.g.query(q)
            return int(res.result_set[0][0]) if res.result_set else 0

        return {
            "people": one("MATCH (p:Person) RETURN count(p)"),
            "topics": one("MATCH (t:Topic) RETURN count(t)"),
            "interests": one("MATCH ()-[r:INTERESTED_IN]->() RETURN count(r)"),
            "met": one("MATCH ()-[r:MET]->() RETURN count(r)"),
            "nudges": one("MATCH ()-[r:NUDGED]->() RETURN count(r)"),
        }
