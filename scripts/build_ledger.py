"""Build the synthetic ledger: fictional teams, players, matches, customers and bets.

Every name and number here is invented. Matches are hand-specified so that each
rule in data/rules.md is exercised; bets are generated around them with a fixed
seed. Settlement comes from the reference engine.

    python scripts/build_ledger.py   ->  data/ledger.json
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supportbot.engine import cash_out_quote, leg_key, settle  # noqa: E402

rng = random.Random(7)

TEAMS = ["Ashford Rovers", "Briar Vale", "Cobalt Town", "Dunmore United", "Elmbridge City",
         "Fallowfield", "Glenshaw Athletic", "Harrow Bay", "Ironmoor", "Juniper Wanderers",
         "Kestrel Park", "Larkspur FC", "Marrow Heath", "Northwick", "Oakhaven", "Pellan Albion",
         "Quarry Lane", "Redwater", "Sallow Green", "Thornbury Town", "Umber Rangers",
         "Valecrest", "Willowmere", "Yarrow Bridge"]
FIRST = ["Adam", "Ben", "Cian", "Dara", "Eoin", "Femi", "Gus", "Hugo", "Ivan", "Jonah", "Kofi",
         "Liam", "Milo", "Nico", "Owen", "Pavel", "Rory", "Sami", "Theo", "Uli", "Vik", "Wes"]
LAST = ["Abbot", "Byrne", "Carver", "Doyle", "Ekwe", "Fenwick", "Garde", "Holt", "Ives", "Jarrow",
        "Keane", "Lowry", "Marsh", "Nolan", "Orme", "Pryce", "Quill", "Rennick", "Sorley", "Tobin"]


def squad(team: str) -> tuple[list[str], list[str]]:
    r = random.Random(team)
    names = []
    while len(names) < 14:
        n = f"{r.choice(FIRST)} {r.choice(LAST)}"
        if n not in names:
            names.append(n)
    return names[:11], names[11:]          # starters, substitutes


def event(eid, home, away, kickoff, status, goals=(), abandoned_minute=None, knockout=False,
          qualified=None, current_odds=None, suspended=()):
    hs, hsub = squad(home)
    as_, asub = squad(away)
    return {"event_id": eid, "home": home, "away": away, "kickoff": kickoff, "status": status,
            "abandoned_minute": abandoned_minute, "knockout": knockout, "qualified": qualified,
            "goals": list(goals), "starters": {"home": hs, "away": as_},
            "substitutes": {"home": hsub, "away": asub},
            "current_odds": current_odds or {}, "suspended": list(suspended)}


def goal(minute, team, home, away, idx, own_goal=False, sub=False):
    """Scorer by index into the squad of the scoring side (own goals: the conceding side)."""
    side = (away if team == "home" else home) if own_goal else (home if team == "home" else away)
    starters, subs = squad(side)
    player = subs[idx] if sub else starters[idx]
    return {"minute": minute, "team": team, "player": player, "own_goal": own_goal}


def build_events() -> dict[str, dict]:
    t = TEAMS
    ev = {}

    def add(e):
        ev[e["event_id"]] = e

    add(event("E01", t[0], t[1], "2026-10-03T15:00", "completed",
              [goal(12, "home", t[0], t[1], 9), goal(55, "away", t[0], t[1], 10), goal(81, "home", t[0], t[1], 8)]))
    add(event("E02", t[2], t[3], "2026-10-03T17:30", "completed"))
    # first goal is an own goal (R5.3), then a real goal
    add(event("E03", t[4], t[5], "2026-10-04T14:00", "completed",
              [goal(8, "home", t[4], t[5], 3, own_goal=True), goal(70, "away", t[4], t[5], 9)]))
    # a player scores an own goal (R5.2) in a 3-2
    add(event("E04", t[6], t[7], "2026-10-04T16:30", "completed",
              [goal(5, "home", t[6], t[7], 10), goal(22, "away", t[6], t[7], 9), goal(40, "home", t[6], t[7], 4, own_goal=True),
               goal(63, "away", t[6], t[7], 8), goal(88, "home", t[6], t[7], 9)]))
    # knockout: 1-1 after 90, home qualify on penalties (R1.1 / R1.2)
    add(event("E05", t[8], t[9], "2026-10-07T19:45", "completed",
              [goal(30, "home", t[8], t[9], 9), goal(77, "away", t[8], t[9], 10)], knockout=True, qualified="home"))
    # knockout: 0-0 after 90, away score in extra time
    add(event("E06", t[10], t[11], "2026-10-07T20:00", "completed", knockout=True, qualified="away"))
    # abandoned at 60' with 2-0 (R2.2)
    add(event("E07", t[12], t[13], "2026-10-11T15:00", "abandoned",
              [goal(18, "home", t[12], t[13], 9), goal(44, "home", t[12], t[13], 10), goal(75, "away", t[12], t[13], 9)],
              abandoned_minute=60))
    add(event("E08", t[14], t[15], "2026-10-11T17:30", "abandoned", abandoned_minute=30))
    add(event("E09", t[16], t[17], "2026-10-12T14:00", "postponed"))
    # a substitute scores (R5.1)
    add(event("E10", t[18], t[19], "2026-10-12T16:30", "completed",
              [goal(35, "home", t[18], t[19], 9), goal(68, "away", t[18], t[19], 0, sub=True),
               goal(74, "away", t[18], t[19], 10), goal(90, "home", t[18], t[19], 1, sub=True)]))
    for i, eid in enumerate(["E11", "E12", "E13", "E14", "E15", "E16"]):
        h, a = t[(2 * i + 1) % 24], t[(2 * i + 6) % 24]
        gl = []
        for _ in range(rng.randint(0, 4)):
            side = rng.choice(["home", "away"])
            gl.append(goal(rng.randint(2, 89), side, h, a, rng.choice([8, 9, 10])))
        add(event(eid, h, a, f"2026-10-{18 + i // 3}T{15 + 2 * (i % 3)}:00", "completed", gl))

    def odds_book(eid, h, a):
        hs, _ = squad(h)
        as_, _ = squad(a)
        p = rng.uniform(0.3, 0.55)
        d = 0.27
        book = {"match_result:home": round(1 / p, 2), "match_result:draw": round(1 / d, 2),
                "match_result:away": round(1 / (1 - p - d + 0.02), 2),
                "total_goals:2.5:over": round(rng.uniform(1.7, 2.2), 2),
                "total_goals:2.5:under": round(rng.uniform(1.7, 2.1), 2),
                "btts:yes": round(rng.uniform(1.6, 2.0), 2), "btts:no": round(rng.uniform(1.8, 2.2), 2)}
        for pl in hs[8:] + as_[8:]:
            book[f"anytime_scorer:{pl}"] = round(rng.uniform(2.4, 4.5), 2)
        return book

    for i, eid in enumerate(["E17", "E18", "E19", "E20"]):        # in play
        h, a = t[(3 * i + 2) % 24], t[(3 * i + 13) % 24]
        e = event(eid, h, a, "2026-11-06T19:00", "live")
        e["current_odds"] = odds_book(eid, h, a)
        if eid == "E19":
            e["suspended"] = ["match_result:home", "match_result:draw", "match_result:away"]
        add(e)
    for i, eid in enumerate(["E21", "E22", "E23", "E24", "E25", "E26"]):   # later this week
        h, a = t[(5 * i + 3) % 24], t[(5 * i + 16) % 24]
        e = event(eid, h, a, f"2026-11-{8 + i // 2}T{15 + i % 2 * 2}:00", "scheduled")
        e["current_odds"] = odds_book(eid, h, a)
        add(e)
    add(event("E27", t[22], t[23], "2026-11-01T15:00", "postponed"))
    return ev


def market_price(market: str, pick: str, line=None) -> float:
    base = {"match_result": {"home": 2.1, "draw": 3.4, "away": 3.3}, "btts": {"yes": 1.8, "no": 1.95},
            "total_goals": {"over": 1.9, "under": 1.9}, "to_qualify": {"home": 1.7, "away": 2.1}}
    if market in base:
        return round(base[market][pick] * rng.uniform(0.85, 1.2), 2)
    return round(rng.uniform(2.5, 9.0) if market == "first_scorer" else rng.uniform(2.2, 4.5), 2)


def leg(eid, market, pick, line=None, odds=None):
    lg = {"event_id": eid, "market": market, "pick": pick}
    if line is not None:
        lg["line"] = line
    lg["odds"] = odds if odds is not None else market_price(market, pick, line)
    return lg


def build_bets(ev: dict) -> list[dict]:
    def st(eid, side, i, sub=False):
        s, subs = squad(ev[eid][side])
        return subs[i] if sub else s[i]

    specs = [
        # --- settled singles ---
        ("single", [leg("E01", "match_result", "home")]), ("single", [leg("E01", "match_result", "away")]),
        ("single", [leg("E01", "total_goals", "over", 2.5)]), ("single", [leg("E01", "btts", "yes")]),
        ("single", [leg("E02", "match_result", "draw")]), ("single", [leg("E02", "btts", "no")]),
        ("single", [leg("E02", "total_goals", "over", 1.5)]),
        ("single", [leg("E03", "first_scorer", st("E03", "away", 9))]),          # R5.3: OG ignored, wins
        ("single", [leg("E03", "first_scorer", st("E03", "away", 3))]),          # loses
        ("single", [leg("E04", "anytime_scorer", st("E04", "away", 4))]),        # R5.2: only an OG
        ("single", [leg("E04", "anytime_scorer", st("E04", "home", 9))]),
        ("single", [leg("E04", "total_goals", "over", 3.5)]),
        ("single", [leg("E05", "match_result", "home")]),                        # R1.1: draw after 90
        ("single", [leg("E05", "match_result", "draw")]),
        ("single", [leg("E05", "to_qualify", "home")]),                          # R1.2
        ("single", [leg("E06", "match_result", "away")]),
        ("single", [leg("E06", "to_qualify", "away")]),
        ("single", [leg("E07", "total_goals", "over", 1.5)]),                    # R2.2 decided
        ("single", [leg("E07", "btts", "yes")]),                                 # R2.2 void
        ("single", [leg("E07", "match_result", "home")]),                        # R2.2 void
        ("single", [leg("E07", "anytime_scorer", st("E07", "home", 9))]),        # R2.2 decided
        ("single", [leg("E08", "match_result", "away")]),
        ("single", [leg("E09", "match_result", "home")]), ("single", [leg("E09", "btts", "yes")]),
        ("single", [leg("E10", "anytime_scorer", st("E10", "away", 0, sub=True))]),   # R5.1 sub scored
        ("single", [leg("E10", "anytime_scorer", st("E10", "away", 10))]),
        ("single", [leg("E10", "match_result", "draw")]),
        ("single", [leg("E11", "match_result", "home")]), ("single", [leg("E12", "btts", "yes")]),
        ("single", [leg("E13", "total_goals", "under", 2.5)]), ("single", [leg("E14", "match_result", "away")]),
        ("single", [leg("E15", "total_goals", "over", 2.5)]), ("single", [leg("E16", "match_result", "draw")]),
        # --- settled accumulators ---
        ("accumulator", [leg("E01", "match_result", "home"), leg("E09", "match_result", "away"), leg("E02", "btts", "no")]),
        ("accumulator", [leg("E01", "match_result", "home"), leg("E08", "btts", "yes"), leg("E05", "to_qualify", "home")]),
        ("accumulator", [leg("E02", "match_result", "draw"), leg("E10", "anytime_scorer", st("E10", "away", 0, sub=True)),
                         leg("E06", "to_qualify", "away")]),
        ("accumulator", [leg("E01", "btts", "yes"), leg("E07", "match_result", "home")]),
        ("accumulator", [leg("E04", "total_goals", "over", 3.5), leg("E09", "btts", "no"), leg("E03", "btts", "yes")]),
        ("accumulator", [leg("E01", "match_result", "away"), leg("E02", "btts", "no")]),
        ("accumulator", [leg("E05", "match_result", "home"), leg("E06", "to_qualify", "away")]),
        ("accumulator", [leg("E09", "match_result", "home"), leg("E08", "match_result", "draw")]),
        ("accumulator", [leg("E07", "total_goals", "over", 1.5), leg("E01", "total_goals", "over", 2.5)]),
        ("accumulator", [leg("E11", "match_result", "home"), leg("E12", "btts", "yes"), leg("E13", "total_goals", "under", 2.5)]),
        # --- settled bet builders (one match) ---
        ("bet_builder", [leg("E01", "match_result", "home"), leg("E01", "btts", "yes"),
                         leg("E01", "anytime_scorer", st("E01", "home", 9))]),
        ("bet_builder", [leg("E10", "match_result", "draw"), leg("E10", "anytime_scorer", st("E10", "away", 0, sub=True)),
                         leg("E10", "btts", "yes")]),                                     # R4.2
        ("bet_builder", [leg("E10", "total_goals", "over", 2.5), leg("E10", "anytime_scorer", st("E10", "home", 1, sub=True))]),
        ("bet_builder", [leg("E04", "total_goals", "over", 3.5), leg("E04", "anytime_scorer", st("E04", "away", 4))]),
        ("bet_builder", [leg("E07", "total_goals", "over", 1.5), leg("E07", "anytime_scorer", st("E07", "home", 10))]),  # R4.3 won
        ("bet_builder", [leg("E07", "match_result", "home"), leg("E07", "btts", "no")]),                                 # R4.3 void
        ("bet_builder", [leg("E09", "match_result", "home"), leg("E09", "btts", "yes")]),                                # all void
        ("bet_builder", [leg("E02", "btts", "no"), leg("E02", "match_result", "draw")]),
        # --- capped return (R1.3) ---
        ("accumulator", [leg("E01", "match_result", "home", odds=6.0), leg("E02", "match_result", "draw", odds=7.5),
                         leg("E05", "match_result", "draw", odds=4.0)]),
    ]
    open_specs = []
    for eid in ["E17", "E18", "E19", "E20", "E21", "E22", "E23", "E24", "E25", "E26"]:
        open_specs.append(("single", [leg(eid, "match_result", "home", odds=ev[eid]["current_odds"]["match_result:home"]
                                          * round(rng.uniform(0.8, 1.25), 2))]))
    open_specs += [
        ("accumulator", [leg("E21", "match_result", "home"), leg("E22", "btts", "yes"), leg("E27", "match_result", "away")]),
        ("accumulator", [leg("E23", "total_goals", "over", 2.5), leg("E27", "btts", "yes")]),
        ("accumulator", [leg("E24", "match_result", "home"), leg("E25", "match_result", "home")]),
        ("accumulator", [leg("E17", "btts", "yes"), leg("E26", "match_result", "home")]),
        ("accumulator", [leg("E19", "match_result", "home"), leg("E21", "btts", "yes")]),   # suspended leg
        ("bet_builder", [leg("E22", "match_result", "home"), leg("E22", "btts", "yes")]),
        ("bet_builder", [leg("E18", "total_goals", "over", 2.5), leg("E18", "btts", "yes")]),
    ]
    bets = []
    customers = [f"C{1001 + i}" for i in range(30)]
    for n, (btype, legs) in enumerate(specs + open_specs):
        stake = float(rng.choice([2, 5, 5, 10, 10, 10, 20, 25, 50, 100]))
        if btype == "accumulator" and legs[0].get("odds") == 6.0:
            stake = 1000.0
        if btype == "bet_builder":
            odds = round(max(1.2, (legs[0]["odds"] * legs[1]["odds"] * (legs[2]["odds"] if len(legs) > 2 else 1)) * 0.8), 2)
        else:
            odds = round(float(__import__("math").prod(lg["odds"] for lg in legs)), 2)
        for lg in legs:
            lg["odds"] = round(lg["odds"], 2)
        bets.append({"bet_id": f"B{5001 + n}", "customer_id": customers[n % 30], "type": btype,
                     "stake": stake, "odds": odds, "legs": legs,
                     "placed_at": min(ev[lg["event_id"]]["kickoff"] for lg in legs)[:10] + "T09:00"})
    # two bets already cashed out (R6.4)
    for b in [bets[-12], bets[-11]]:
        q = cash_out_quote(b, ev)
        b["cashed_out"] = {"amount": q["value"], "at": "2026-11-05T21:10"}
    return bets


def main() -> None:
    ev = build_events()
    bets = build_bets(ev)
    for b in bets:
        # bet builders need a current combined price for cash out (R6.2)
        e = ev[b["legs"][0]["event_id"]]
        if b["type"] == "bet_builder" and e["status"] in ("live", "scheduled"):
            e["current_odds"][f"bet_builder:{b['bet_id']}"] = round(b["odds"] * rng.uniform(0.7, 1.3), 2)
        for lg in b["legs"]:
            e = ev[lg["event_id"]]
            if e["status"] in ("live", "scheduled") and leg_key(lg) not in e["current_odds"]:
                e["current_odds"][leg_key(lg)] = round(lg["odds"] * rng.uniform(0.75, 1.3), 2)
        s = settle(b, ev)
        if b.get("cashed_out"):
            b["status"], b["total_return"] = "cashed_out", b["cashed_out"]["amount"]
        else:
            b["status"] = s.outcome
            b["total_return"] = s.total_return
        b["leg_status"] = [lr.outcome for lr in s.leg_results]
    out = {"events": ev, "bets": bets}
    (ROOT / "data" / "ledger.json").write_text(json.dumps(out, indent=1))
    counts = {}
    for b in bets:
        counts[(b["type"], b["status"])] = counts.get((b["type"], b["status"]), 0) + 1
    print(len(ev), "events,", len(bets), "bets")
    for k, v in sorted(counts.items()):
        print(" ", k, v)


if __name__ == "__main__":
    main()
