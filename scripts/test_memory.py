"""Exercises every Cypher query in memory.py against a real FalkorDB.
Run this again tomorrow the moment you have the FalkorDB Cloud connection
string, to prove the same queries work there."""
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from overlap.memory import Memory

m = Memory()
m.reset()

m.record_checkin("p1", "Aaryaa", "product designer",
                 ["spatial cognition", "graph databases", "learning science"], "Floor 16")
m.record_checkin("p2", "Dev", "ml engineer",
                 ["graph databases", "vector search"], "Floor 16")
m.record_checkin("p3", "Mira", "founder",
                 ["learning science", "graph databases"], "Floor 16")
m.record_checkin("p4", "Sam", "infra engineer",
                 ["vector search", "streaming"], "Cafe")

# Mira already knows Dev. So Mira is a warm-intro path from Aaryaa to Dev.
m.record_met("p3", "p2")

print("stats           ", m.stats())
print("person p1       ", m.person("p1"))

cands = m.candidates("p1")
print("candidates(p1)  ")
for c in cands:
    print("   ", c)
assert cands, "expected at least one candidate"
assert all(c["id"] != "p1" for c in cands)

wi = m.warm_intro("p1", "p2")
print("warm_intro p1→p2", wi)
assert wi and wi["connector_id"] == "p3", f"expected Mira as connector, got {wi}"

print("bridge_topics   ")
for b in m.bridge_topics():
    print("   ", b)

print("have_met p3,p2  ", m.have_met("p3", "p2"))
print("have_met p1,p2  ", m.have_met("p1", "p2"))
assert m.have_met("p3", "p2") and not m.have_met("p1", "p2")

m.record_nudge("p1", "p2", "approved")
print("nudge age p1    ", round(m.recent_nudge_age("p1") or -1, 3))
assert m.recent_nudge_age("p1") is not None

# after a nudge, p2 should drop out of p1's candidate list
after = m.candidates("p1")
assert all(c["id"] != "p2" for c in after), "nudged pair should be excluded"
print("candidates after nudge:", [c["id"] for c in after])

snap = m.snapshot()
print("snapshot        ", len(snap["nodes"]), "nodes,", len(snap["edges"]), "edges")
assert snap["nodes"] and snap["edges"]

print("\nALL MEMORY QUERIES PASS")
