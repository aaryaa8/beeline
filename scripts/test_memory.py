"""Exercises every Cypher query in memory.py against a real FalkorDB, and proves
the WP1 acceptance criteria (intent-first ranking, the emotional gate, routing
with a connector, and feedback shifting affinity).

Run this again tomorrow the moment you have the FalkorDB Cloud connection
string, to prove the same queries work there."""
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from overlap.memory import Memory

m = Memory()
m.reset()

# --- the room ---------------------------------------------------------------
# p1 is us: wants "frontend engineering", offers "design mentorship".
m.record_checkin("p1", "Aaryaa", "product designer",
                 ["spatial cognition", "graph databases", "learning science"],
                 ask="frontend engineering", offer="design mentorship",
                 zone="Window", state="open")
m.record_checkin("p2", "Dev", "ml engineer",
                 ["graph databases", "vector search"],
                 ask="", offer="", zone="Window", state="open")
m.record_checkin("p3", "Mira", "founder",
                 ["learning science", "graph databases"],
                 ask="", offer="", zone="Coffee", state="open")
m.record_checkin("p4", "Sam", "infra engineer",
                 ["vector search", "streaming"],
                 ask="", offer="", zone="Cafe", state="open")

# p5 complements p1 by INTENT only: offers exactly what p1 wants, shares no topic.
m.record_checkin("p5", "Chen", "frontend engineer",
                 ["rust"],
                 ask="", offer="frontend engineering", zone="Window", state="open")
# p6 matches p1 only on a shared INTEREST, no complementary intent.
m.record_checkin("p6", "Lin", "researcher",
                 ["graph databases"],
                 ask="", offer="", zone="Coffee", state="open")
# p7 would be the strongest match of all (also offers what p1 wants) but is
# recharging, so the gate must exclude them entirely.
m.record_checkin("p7", "Noor", "staff engineer",
                 ["graph databases"],
                 ask="", offer="frontend engineering", zone="Cafe", state="recharging")

# Routing setup: p8 is a target in a different zone, reachable via connector p9.
m.record_checkin("p8", "Priya", "ml engineer",
                 ["retrieval"], ask="", offer="", zone="Stage", state="open")
m.record_checkin("p9", "Kai", "designer",
                 ["retrieval"], ask="", offer="", zone="Coffee", state="open")

# Mira already knows Dev -> warm-intro path from Aaryaa to Dev.
m.record_met("p3", "p2")
# Kai knows Priya, and we know Kai -> Kai is a known connector to Priya.
m.record_met("p9", "p8")
m.record_met("p1", "p9")

print("stats           ", m.stats())
print("person p1       ", m.person("p1"))
assert m.person("p1")["ask"] == "frontend engineering"
assert m.person("p1")["offer"] == "design mentorship"
assert m.person("p1")["zone"] == "Window"
assert m.person("p1")["state"] == "open"

# --- CRITERION 1: complementary intent ranks above a mere shared interest ----
cands = m.candidates("p1")
print("candidates(p1)  ")
for c in cands:
    print("   ", {k: c[k] for k in ("id", "name", "complements", "shared_topics", "overlap")})
assert cands, "expected at least one candidate"
assert all(c["id"] != "p1" for c in cands)
ids = [c["id"] for c in cands]
assert "p5" in ids and "p6" in ids, "both the intent match and the interest match should appear"
assert ids.index("p5") < ids.index("p6"), \
    "complementary-intent match (p5) must rank above shared-interest-only match (p6)"
p5 = next(c for c in cands if c["id"] == "p5")
p6 = next(c for c in cands if c["id"] == "p6")
assert p5["complements"] == ["frontend engineering"], p5["complements"]
assert p6["complements"] == [], p6["complements"]
# the frontend contract: every candidate carries zone/state/affinity
for c in cands:
    assert set(("id", "name", "role", "zone", "state",
                "shared_topics", "complements", "overlap", "affinity")) <= set(c)
print("CRITERION 1 (intent beats interest) PASS")

# --- CRITERION 2: a non-open person is excluded from candidates --------------
assert "p7" not in ids, "recharging p7 must be gated out even though it complements"
# and if *we* are not open, nobody is routable to us
m.set_state("p1", "recharging")
assert m.candidates("p1") == [], "no candidates when the requester is not open"
m.set_state("p1", "open")
print("CRITERION 2 (state gate excludes recharging) PASS")

# --- existing warm-intro behaviour still holds -------------------------------
wi = m.warm_intro("p1", "p2")
print("warm_intro p1->p2", wi)
assert wi and wi["connector_id"] == "p3", f"expected Mira as connector, got {wi}"

print("bridge_topics   ")
for b in m.bridge_topics():
    print("   ", b)

print("have_met p3,p2  ", m.have_met("p3", "p2"))
print("have_met p1,p2  ", m.have_met("p1", "p2"))
assert m.have_met("p3", "p2") and not m.have_met("p1", "p2")

# --- CRITERION 3: route returns an ordered path including a known connector ---
r = m.route("p1", "p8")
print("route p1->p8    ", r)
assert r["target_zone"] == "Stage", r["target_zone"]
hop_ids = [h["id"] for h in r["hops"]]
assert "p9" in hop_ids, f"connector Kai (p9) should be on the path, got {hop_ids}"
kai = next(h for h in r["hops"] if h["id"] == "p9")
assert "Priya" in kai["reason"], kai["reason"]
# ordering: someone from our own zone (Window) comes before the connector
window_hops = [i for i, h in enumerate(r["hops"]) if h["reason"] == "in your zone"]
if window_hops:
    assert min(window_hops) < hop_ids.index("p9"), "own-zone hops come before the connector"
print("CRITERION 3 (route path includes connector) PASS")

# --- room energy + snapshot smoke -------------------------------------------
energy = m.room_energy()
print("room_energy     ")
for e in energy:
    print("   ", e)
assert any(e["zone"] == "Cafe" and e["recharging"] == 1 for e in energy), \
    "Noor should show as recharging in Cafe"
assert {e["zone"] for e in energy} >= {"Window", "Coffee", "Stage", "Cafe"}

# --- nudge exclusion still works --------------------------------------------
m.record_nudge("p1", "p2", "approved")
print("nudge age p1    ", round(m.recent_nudge_age("p1") or -1, 3))
assert m.recent_nudge_age("p1") is not None
after = m.candidates("p1")
assert all(c["id"] != "p2" for c in after), "nudged pair should be excluded"
print("candidates after nudge:", [c["id"] for c in after])

# --- CRITERION 4: feedback shifts affinity ----------------------------------
m.record_feedback("p1", "p8", "clicked")      # clicked with Priya
m.record_feedback("p1", "p5", "not-for-me")   # did not click with Chen
aff_clicked = m.affinity("p1", "p8")
aff_notforme = m.affinity("p1", "p5")
print(f"affinity p1->p8 (clicked)    {aff_clicked}")
print(f"affinity p1->p5 (not-for-me) {aff_notforme}")
assert aff_clicked > aff_notforme, "clicked pair must outrank not-for-me pair"
assert aff_notforme < 0 and aff_clicked > 0
print("CRITERION 4 (feedback shifts affinity) PASS")

# --- snapshot exact shape ----------------------------------------------------
snap = m.snapshot()
print("snapshot        ", len(snap["nodes"]), "nodes,", len(snap["edges"]), "edges")
assert snap["zones"] == ["Window", "Coffee", "Stage", "Cafe"], snap["zones"]
assert snap["nodes"] and snap["edges"]
kinds = {n["kind"] for n in snap["nodes"]}
assert {"person", "topic", "capability"} <= kinds, kinds
person_node = next(n for n in snap["nodes"] if n["id"] == "p1")
assert person_node["zone"] == "Window" and person_node["state"] == "open"
assert any(n["kind"] == "capability" and n["label"] == "frontend engineering"
           for n in snap["nodes"])
edge_kinds = {e["kind"] for e in snap["edges"]}
assert {"interest", "met", "nudge"} <= edge_kinds, edge_kinds

st = m.stats()
assert "capabilities" in st and st["capabilities"] >= 2
assert "feedback" in st and st["feedback"] == 2

print("\nALL MEMORY QUERIES + WP1 ACCEPTANCE CRITERIA PASS")
