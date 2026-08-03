"""WP4 standalone test — PURE PYTHON, no FalkorDB / redis.

Another agent runs graph tests on the same box, so this file must never touch a
broker or the graph. It validates the event *shapes* and the *story order* of
`seed.demo_sequence()` against BUILD_SPEC.md §4.2, by constructing events and
asserting on them. Run:

    cd /Users/aaryaakamdar/Desktop/overlap && .venv/bin/python scripts/test_stream.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from overlap import seed, stream  # noqa: E402
from overlap.config import STATES, FEEDBACK  # noqa: E402

# Required payload keys per §4.2. Optional keys (intent's ask/offer) are checked
# separately; here we list what MUST be present for each type.
REQUIRED_KEYS = {
    stream.CHECKIN: {"id", "name", "role", "interests", "ask", "offer", "zone", "state"},
    stream.INTEREST: {"id", "topic"},
    stream.INTENT: {"id"},  # ask/offer optional, but at least one should appear
    stream.POSITION: {"id", "zone"},
    stream.STATE: {"id", "state"},
    stream.MET: {"a", "b"},
    stream.FEEDBACK: {"from_id", "to_id", "value"},
}

_STOPWORDS = {
    "a", "an", "the", "to", "on", "of", "for", "with", "and", "in", "help",
    "advice", "partner", "warm", "raising", "round",
}


def _stem(word: str) -> str:
    """Crudest possible stemmer: lowercase and drop a few common suffixes so
    'designer'~'design' and 'intros'~'intro' collide. Enough to detect a
    complementary ask/offer pair without importing the real memory normalizer."""
    w = word.lower().strip(".,")
    for suf in ("ing", "ers", "er", "s"):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _tokens(phrase: str) -> set[str]:
    return {_stem(t) for t in phrase.split() if t.lower() not in _STOPWORDS} - {""}


def _complements(ask: str, offer: str) -> bool:
    """A complementary pair shares at least one meaningful stem between one
    person's ask and another's offer."""
    return bool(_tokens(ask) & _tokens(offer))


def main() -> int:
    events = seed.demo_sequence()
    checks: list[tuple[bool, str]] = []

    def check(cond: bool, label: str) -> None:
        checks.append((bool(cond), label))

    # 1. Envelope + payload shape for every event.
    all_shapes_ok = True
    for i, ev in enumerate(events):
        env_ok = set(ev.keys()) == {"id", "type", "ts", "payload"}
        typ = ev.get("type")
        known = typ in REQUIRED_KEYS
        payload = ev.get("payload", {})
        keys_ok = known and REQUIRED_KEYS[typ].issubset(payload.keys())
        if not (env_ok and known and keys_ok):
            all_shapes_ok = False
            print(f"  ! event {i} ({typ}) failed shape: env={env_ok} known={known} keys_ok={keys_ok}")
    check(all_shapes_ok, "every event validates against §4.2 (envelope + required payload keys)")

    # 2. Enum values are legal where they appear.
    states_ok = all(
        ev["payload"]["state"] in STATES for ev in events if ev["type"] == stream.STATE
    )
    checkin_states_ok = all(
        ev["payload"]["state"] in STATES for ev in events if ev["type"] == stream.CHECKIN
    )
    feedback_vals_ok = all(
        ev["payload"]["value"] in FEEDBACK for ev in events if ev["type"] == stream.FEEDBACK
    )
    check(states_ok and checkin_states_ok, "all state values are in STATES")
    check(feedback_vals_ok, "all feedback values are in FEEDBACK")

    # 3. History (met) precedes check-ins: nobody is introduced to someone they
    #    already know because the acquaintance graph is loaded first.
    first_checkin = next((i for i, e in enumerate(events) if e["type"] == stream.CHECKIN), None)
    prior_met = [i for i, e in enumerate(events) if e["type"] == stream.MET and i < (first_checkin or 0)]
    check(first_checkin is not None, "at least one checkin exists")
    check(len(prior_met) > 0, "MET/history events precede the first checkin event")

    # 4. At least one checkin's ask complements another checkin's offer, so a
    #    real intent-first match exists in the seed data.
    checkins = [e["payload"] for e in events if e["type"] == stream.CHECKIN]
    match_found = None
    for asker in checkins:
        for offerer in checkins:
            if asker["id"] == offerer["id"]:
                continue
            if _complements(asker["ask"], offerer["offer"]):
                match_found = (asker["name"], asker["ask"], offerer["name"], offerer["offer"])
                break
        if match_found:
            break
    check(match_found is not None, "a checkin ask complements another checkin offer")
    if match_found:
        print(f"    complement: {match_found[0]} asks '{match_found[1]}' <-> "
              f"{match_found[2]} offers '{match_found[3]}'")

    # 5. Exactly one state->recharging event (the emotional gate demo).
    recharging = [e for e in events if e["type"] == stream.STATE and e["payload"]["state"] == "recharging"]
    check(len(recharging) == 1, "exactly one state->recharging event exists")

    # 6. Exactly one feedback event (the learning-shift demo).
    feedbacks = [e for e in events if e["type"] == stream.FEEDBACK]
    check(len(feedbacks) == 1, "exactly one feedback event exists")

    # 7. Story ordering of the two showcase beats: the recharging flip comes
    #    before the feedback shift, matching the demo narrative.
    if recharging and feedbacks:
        r_idx = events.index(recharging[0])
        f_idx = events.index(feedbacks[0])
        check(r_idx < f_idx, "recharging gate precedes the feedback shift")

    # Report.
    print()
    passed = 0
    for ok, label in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        passed += ok
    print(f"\n{passed}/{len(checks)} checks passed  ({len(events)} events in demo_sequence)")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
