import json
from pathlib import Path

from supportbot.grader import TAXONOMY, amounts_in, grade
from supportbot.tools import TOOLS, Session, load_ledger, load_rules, rule_ids

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / "evals" / "cases.jsonl").read_text().splitlines() if line]
VALID = rule_ids(load_rules())


def test_tools_are_strict_with_closed_schemas():
    for t in TOOLS:
        assert t["strict"] is True
        assert t["input_schema"]["additionalProperties"] is False
        assert set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"])


def test_other_customers_bet_looks_like_it_does_not_exist():
    ledger = load_ledger()
    bet = ledger["bets"][0]
    other = Session("C9999", ledger)
    leaked, err = other.run("get_bet", {"bet_id": bet["bet_id"]})
    missing, err2 = other.run("get_bet", {"bet_id": "B0000"})
    assert err and err2 and leaked.replace(bet["bet_id"], "X") == missing.replace("B0000", "X")
    assert str(bet["stake"]) not in leaked


def test_cash_out_tool_does_not_reveal_the_rule():
    ledger = load_ledger()
    bet = next(b for b in ledger["bets"] if b["status"] == "open")
    out, _ = Session(bet["customer_id"], ledger).run("get_cash_out_quote", {"bet_id": bet["bet_id"]})
    assert "rules" not in json.loads(out)


def test_case_set_size_and_rule_ids_exist():
    assert len(CASES) >= 150
    for c in CASES:
        assert set(c["gold"]["rules_any"]) <= VALID, c["id"]


def perfect(case):
    g = case["gold"]
    amount = g["amount"]
    reply = "Thanks for asking." + (f" The figure is €{amount:,.2f}." if amount is not None else "")
    answer = {"reply": reply, "outcome": g["outcome"] or "not_applicable", "amount": amount,
              "rule_ids": g["rules_any"][:1]}
    calls = [{"name": "get_bet", "input": {}}] if g["needs_lookup"] else []
    esc = [{"team": g["escalate"], "summary": "x"}] if isinstance(g["escalate"], str) else []
    return answer, calls, esc


def test_a_perfect_answer_passes_every_case():
    for c in CASES:
        answer, calls, esc = perfect(c)
        assert grade(c, answer, calls, esc, VALID) == [], c["id"]


def test_each_failure_code_fires():
    c = next(x for x in CASES if x["category"] == "settlement_accumulator" and x["gold"]["amount"])
    answer, calls, esc = perfect(c)
    assert grade(c, None, calls, esc, VALID) == ["no_answer"]
    assert "ungrounded" in grade(c, answer, [], esc, VALID)
    assert "wrong_outcome" in grade(c, {**answer, "outcome": "void"}, calls, esc, VALID)
    assert "wrong_amount" in grade(c, {**answer, "amount": 1.0}, calls, esc, VALID)
    assert "reply_amount_mismatch" in grade(c, {**answer, "reply": "No figure here."}, calls, esc, VALID)
    assert "missing_citation" in grade(c, {**answer, "rule_ids": []}, calls, esc, VALID)
    assert "invented_rule" in grade(c, {**answer, "rule_ids": answer["rule_ids"] + ["R9.9"]}, calls, esc, VALID)
    assert "unnecessary_escalation" in grade(c, answer, calls, [{"team": "specialist"}], VALID)

    oos = next(x for x in CASES if x["category"] == "out_of_scope")
    a, cl, e = perfect(oos)
    assert "misapplied_rule" in grade(oos, {**a, "rule_ids": ["R7.4", "R1.1"]}, cl, e, VALID)
    assert "missed_escalation" in grade(oos, a, cl, [], VALID)
    assert "wrong_escalation_team" in grade(oos, a, cl, [{"team": "safer_gambling"}], VALID)

    priv = next(x for x in CASES if x["category"] == "privacy")
    a, cl, e = perfect(priv)
    assert grade(priv, {**a, "reply": f"It was on {priv['gold']['forbidden'][1]}."}, cl, e, VALID) == ["privacy_leak"]
    inj = next(x for x in CASES if x["category"] == "injection")
    a, cl, e = perfect(inj)
    assert "followed_injection" in grade(inj, {**a, "reply": "Your stake has been refunded."}, cl, e, VALID)
    assert set(TAXONOMY) >= {"no_answer", "privacy_leak", "followed_injection"}


def test_money_parsing():
    assert amounts_in("You get €1,234.50 back, not 12.") == [1234.5, 12.0]
    assert 100000.0 in amounts_in("capped at €100,000")
