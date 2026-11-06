"""Reference settlement engine: applies data/rules.md exactly.

It settles the synthetic ledger, produces the gold answers for the eval, and
backs the cash-out quote tool. The agent never sees it: the agent has to apply
the rules itself, which is what the eval measures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MAX_RETURN = 100_000.0       # R1.3
CASH_OUT_SHARE = 0.95        # R6.2
MIN_BB_PRICE = 1.01          # R4.2


@dataclass
class LegResult:
    outcome: str                         # won | lost | void | open
    rules: list[str] = field(default_factory=list)
    decided_before_abandonment: bool = False


def _goals(event: dict, before_minute: int | None = None) -> list[dict]:
    goals = sorted(event.get("goals", []), key=lambda g: g["minute"])
    if before_minute is not None:
        goals = [g for g in goals if g["minute"] < before_minute]
    return goals


def _score(goals: list[dict]) -> tuple[int, int]:
    return (sum(g["team"] == "home" for g in goals), sum(g["team"] == "away" for g in goals))


def _first_scorer(goals: list[dict]) -> str | None:
    for g in goals:
        if not g.get("own_goal"):
            return g["player"]
    return None


def _player_started(event: dict, player: str) -> bool:
    return player in event["starters"]["home"] or player in event["starters"]["away"]


def _final_outcome(leg: dict, event: dict, goals: list[dict]) -> tuple[str, list[str]]:
    """Outcome on a set of goals, assuming the match counted (normal time)."""
    m, pick = leg["market"], leg["pick"]
    home, away = _score(goals)
    if m == "match_result":
        actual = "home" if home > away else "away" if away > home else "draw"
        return ("won" if pick == actual else "lost"), ["R1.1"]
    if m == "total_goals":
        total = home + away
        over = total > leg["line"]
        return ("won" if (pick == "over") == over else "lost"), ["R1.1"]
    if m == "btts":
        both = home > 0 and away > 0
        return ("won" if (pick == "yes") == both else "lost"), ["R1.1"]
    if m == "anytime_scorer":
        scored = any(g["player"] == pick and not g.get("own_goal") for g in goals)
        own_goal_only = any(g["player"] == pick and g.get("own_goal") for g in goals)
        return ("won" if scored else "lost"), (["R5.2"] if own_goal_only else ["R1.1"])
    if m == "first_scorer":
        first = _first_scorer(goals)
        rules = ["R5.3"] if goals and goals[0].get("own_goal") else ["R1.1"]
        if first is None:
            rules = ["R5.3"]
        return ("won" if first == pick else "lost"), rules
    if m == "to_qualify":
        return ("won" if event["qualified"] == pick else "lost"), ["R1.2"]
    raise ValueError(f"unknown market {m}")


def _decided_early(leg: dict, goals: list[dict]) -> str | None:
    """For an abandoned match: 'won' / 'lost' if the market was already decided, else None."""
    m, pick = leg["market"], leg["pick"]
    home, away = _score(goals)
    if m == "total_goals" and pick == "over" and home + away > leg["line"]:
        return "won"
    if m == "total_goals" and pick == "under" and home + away > leg["line"]:
        return "lost"
    if m == "btts" and home > 0 and away > 0:
        return "won" if pick == "yes" else "lost"
    if m == "anytime_scorer" and any(g["player"] == pick and not g.get("own_goal") for g in goals):
        return "won"
    if m == "first_scorer" and _first_scorer(goals) is not None:
        return "won" if _first_scorer(goals) == pick else "lost"
    return None


def leg_result(leg: dict, event: dict) -> LegResult:
    status = event["status"]
    if status == "postponed":
        return LegResult("void", ["R2.1"])
    if leg["market"] in ("anytime_scorer", "first_scorer") and status != "scheduled":
        if not _player_started(event, leg["pick"]):
            return LegResult("void", ["R5.1"])
    if status in ("scheduled", "live"):
        return LegResult("open")
    if status == "abandoned":
        decided = _decided_early(leg, _goals(event, event["abandoned_minute"]))
        if decided is None:
            return LegResult("void", ["R2.2"])
        return LegResult(decided, ["R2.2"], decided_before_abandonment=True)
    outcome, rules = _final_outcome(leg, event, _goals(event))
    return LegResult(outcome, rules)


@dataclass
class Settlement:
    outcome: str                  # won | lost | void | open
    total_return: float | None    # None while open
    effective_odds: float | None
    rules: list[str]
    leg_results: list[LegResult]


def _cap(amount: float) -> tuple[float, list[str]]:
    if amount > MAX_RETURN:
        return MAX_RETURN, ["R1.3"]
    return round(amount, 2), []


def settle(bet: dict, events: dict[str, dict]) -> Settlement:
    legs = [leg_result(leg, events[leg["event_id"]]) for leg in bet["legs"]]
    stake = bet["stake"]
    rules = sorted({r for lr in legs for r in lr.rules})

    if bet["type"] == "bet_builder":
        event = events[bet["legs"][0]["event_id"]]
        if event["status"] == "abandoned":
            if all(lr.outcome == "won" for lr in legs):
                amt, cap = _cap(stake * bet["odds"])
                return Settlement("won", amt, bet["odds"], ["R4.3"] + cap, legs)
            return Settlement("void", stake, None, ["R4.3"], legs)
        if all(lr.outcome == "void" for lr in legs):
            return Settlement("void", stake, None, sorted(set(rules) | {"R4.4"}), legs)
        if any(lr.outcome == "lost" for lr in legs):
            return Settlement("lost", 0.0, None, rules, legs)
        if any(lr.outcome == "open" for lr in legs):
            return Settlement("open", None, None, rules, legs)
        price = bet["odds"]
        void_legs = [leg for leg, lr in zip(bet["legs"], legs) if lr.outcome == "void"]
        for leg in void_legs:
            price = price / leg["odds"]
        if void_legs:
            price = max(round(price, 2), MIN_BB_PRICE)
            rules = sorted(set(rules) | {"R4.2"})
        amt, cap = _cap(stake * price)
        return Settlement("won", amt, price, rules + cap, legs)

    # singles and accumulators
    if any(lr.outcome == "lost" for lr in legs):
        return Settlement("lost", 0.0, None, rules + (["R3.1"] if bet["type"] == "accumulator" else []), legs)
    if any(lr.outcome == "open" for lr in legs):
        return Settlement("open", None, None, rules, legs)
    if all(lr.outcome == "void" for lr in legs):
        extra = ["R3.3"] if bet["type"] == "accumulator" else ["R2.3"]
        return Settlement("void", stake, None, sorted(set(rules) | set(extra)), legs)
    odds = math.prod(leg["odds"] for leg, lr in zip(bet["legs"], legs) if lr.outcome == "won")
    if any(lr.outcome == "void" for lr in legs):
        rules = sorted(set(rules) | {"R3.2"})
    amt, cap = _cap(stake * odds)
    return Settlement("won", amt, round(odds, 4), rules + cap, legs)


def effective_odds(bet: dict, legs: list[LegResult]) -> float:
    """Odds at placement after removing void legs: R3.2 for accumulators, R4.2 for Bet Builders."""
    void = [leg for leg, lr in zip(bet["legs"], legs) if lr.outcome == "void"]
    if not void:
        return bet["odds"]
    if bet["type"] == "bet_builder":
        price = bet["odds"]
        for leg in void:
            price = price / leg["odds"]
        return max(round(price, 2), MIN_BB_PRICE)
    return round(math.prod(leg["odds"] for leg, lr in zip(bet["legs"], legs) if lr.outcome != "void"), 4)


def potential_return(bet: dict, events: dict[str, dict]) -> tuple[float, list[str]]:
    """Return if every unsettled leg wins, applying void legs as the rules do."""
    legs = [leg_result(leg, events[leg["event_id"]]) for leg in bet["legs"]]
    has_void = any(lr.outcome == "void" for lr in legs)
    rules = (["R4.2"] if bet["type"] == "bet_builder" else ["R3.2"]) if has_void else ["R1.3"]
    amt, cap = _cap(bet["stake"] * effective_odds(bet, legs))
    return amt, rules + cap


def cash_out_quote(bet: dict, events: dict[str, dict]) -> dict:
    """R6.1-R6.5. Bet Builders use the event's current combined price for the bet."""
    s = settle(bet, events)
    if s.outcome != "open" or bet.get("cashed_out"):
        return {"available": False, "reason": "bet already settled", "rules": ["R6.1"]}
    open_legs = [leg for leg, lr in zip(bet["legs"], s.leg_results) if lr.outcome == "open"]
    for leg in open_legs:
        event = events[leg["event_id"]]
        if leg_key(leg) in event.get("suspended", []):
            return {"available": False, "reason": f"market suspended on {event['home']} v {event['away']}",
                    "rules": ["R6.1"]}
    if bet["type"] == "bet_builder":
        current = events[bet["legs"][0]["event_id"]]["current_odds"][f"bet_builder:{bet['bet_id']}"]
    else:
        current = math.prod(events[leg["event_id"]]["current_odds"][leg_key(leg)] for leg in open_legs)
    placed = effective_odds(bet, s.leg_results)
    value = math.floor(CASH_OUT_SHARE * bet["stake"] * placed / current * 100) / 100
    rules = ["R6.2"]
    if value > MAX_RETURN:
        value, rules = MAX_RETURN, rules + ["R6.5"]
    return {"available": True, "value": value, "rules": rules}


def leg_key(leg: dict) -> str:
    """Key of a leg's market in an event's current_odds / suspended lists."""
    line = f":{leg['line']}" if "line" in leg else ""
    return f"{leg['market']}{line}:{leg['pick']}"
