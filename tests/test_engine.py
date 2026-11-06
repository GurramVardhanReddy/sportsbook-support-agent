import json
from pathlib import Path

import pytest

from supportbot.engine import cash_out_quote, potential_return, settle

LEDGER = json.loads((Path(__file__).resolve().parents[1] / "data" / "ledger.json").read_text())
EV = LEDGER["events"]
BETS = {b["bet_id"]: b for b in LEDGER["bets"]}


def find(pred):
    return next(b for b in LEDGER["bets"] if pred(b))


def single(event_id, market, pick=None):
    return find(lambda b: b["type"] == "single" and b["legs"][0]["event_id"] == event_id
                and b["legs"][0]["market"] == market and (pick is None or b["legs"][0]["pick"] == pick))


def test_extra_time_does_not_count_but_to_qualify_does():
    assert settle(single("E05", "match_result", "home"), EV).outcome == "lost"      # 1-1 after 90 (R1.1)
    assert settle(single("E05", "match_result", "draw"), EV).outcome == "won"
    s = settle(single("E05", "to_qualify", "home"), EV)
    assert s.outcome == "won" and "R1.2" in s.rules


def test_own_goal_rules():
    s = settle(single("E04", "anytime_scorer"), EV)     # first E04 anytime bet: player only scored an OG
    assert s.outcome == "lost" and "R5.2" in s.rules
    winners = [b for b in LEDGER["bets"] if b["type"] == "single" and b["legs"][0]["event_id"] == "E03"
               and b["legs"][0]["market"] == "first_scorer"]
    results = {settle(b, EV).outcome for b in winners}
    assert results == {"won", "lost"} and all("R5.3" in settle(b, EV).rules for b in winners)


def test_abandoned_match_decided_and_undecided_markets():
    assert settle(single("E07", "total_goals"), EV).outcome == "won"   # 2 goals before 60' (R2.2)
    s = settle(single("E07", "btts"), EV)
    assert s.outcome == "void" and s.total_return == single("E07", "btts")["stake"]


def test_postponed_and_non_starter_are_void():
    assert settle(single("E09", "match_result"), EV).outcome == "void"
    s = settle(single("E10", "anytime_scorer", next(
        b["legs"][0]["pick"] for b in LEDGER["bets"] if settle(b, EV).rules == ["R2.3", "R5.1"])), EV)
    assert s.outcome == "void" and "R5.1" in s.rules


def test_accumulator_void_leg_counts_at_odds_one():
    b = find(lambda b: b["type"] == "accumulator" and "R3.2" in settle(b, EV).rules)
    s = settle(b, EV)
    kept = [lg["odds"] for lg, st in zip(b["legs"], b["leg_status"]) if st != "void"]
    expected = b["stake"]
    for o in kept:
        expected *= o
    assert s.total_return == pytest.approx(round(expected, 2))


def test_bet_builder_removes_non_starter_leg():
    b = find(lambda b: b["type"] == "bet_builder" and "R4.2" in settle(b, EV).rules)
    s = settle(b, EV)
    void_leg = next(lg for lg, st in zip(b["legs"], b["leg_status"]) if st == "void")
    assert s.effective_odds == max(round(b["odds"] / void_leg["odds"], 2), 1.01)


def test_max_return_cap():
    s = settle(find(lambda b: b["stake"] == 1000.0), EV)
    assert s.total_return == 100_000.0 and "R1.3" in s.rules


def test_cash_out_never_exceeds_potential_return_and_respects_suspension():
    for b in LEDGER["bets"]:
        if b["status"] != "open":
            continue
        q = cash_out_quote(b, EV)
        if q["available"]:
            assert q["value"] <= potential_return(b, EV)[0]
        else:
            assert "suspended" in q["reason"]
    assert not cash_out_quote(find(lambda b: b["status"] == "cashed_out"), EV)["available"]
