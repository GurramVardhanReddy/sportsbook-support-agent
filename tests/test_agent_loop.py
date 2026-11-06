"""The agent loop against a scripted fake client: no network, no key."""

from types import SimpleNamespace as NS

from supportbot.agent import PRICES, answer
from supportbot.tools import Session, load_ledger


def usage(i=100, o=50, r=0, w=0):
    return NS(input_tokens=i, output_tokens=o, cache_read_input_tokens=r, cache_creation_input_tokens=w)


def tool_use(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot: the loop keeps appending
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.beta = NS(messages=self.messages)


def test_loop_looks_up_then_submits_and_prices_usage():
    ledger = load_ledger()
    bet = next(b for b in ledger["bets"] if b["status"] == "won")
    script = [
        NS(model="claude-opus-5-5", stop_reason="tool_use", usage=usage(w=2000),
           content=[tool_use("t1", "get_bet", {"bet_id": bet["bet_id"]})]),
        NS(model="claude-opus-5-5", stop_reason="tool_use", usage=usage(r=2000),
           content=[tool_use("t2", "submit_answer", {"reply": f"You got €{bet['total_return']:.2f}.",
                                                     "outcome": "won", "amount": bet["total_return"],
                                                     "rule_ids": ["R1.3"]})]),
    ]
    client = FakeClient(script)
    res = answer(client, Session(bet["customer_id"], ledger), "how did it go?")
    assert res.error is None and res.answer["outcome"] == "won"
    assert [c["name"] for c in res.calls] == ["get_bet", "submit_answer"]
    # the tool result for get_bet went back in the second request, with the cached system prompt
    second = client.messages.requests[1]
    assert second["messages"][-1]["content"][0]["tool_use_id"] == "t1"
    assert second["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert second["fallbacks"] == "default" and second["output_config"] == {"effort": "medium"}
    p = PRICES["claude-opus-5-5"]
    expected = (200 * p["input"] + 100 * p["output"] + 2000 * p["cache_read"] + 2000 * p["input"] * 1.25) / 1e6
    assert abs(res.cost_usd - expected) < 1e-12


def test_loop_reports_missing_answer_and_refusal():
    ledger = load_ledger()
    end = NS(model="claude-haiku-5-5", stop_reason="end_turn", usage=usage(), content=[NS(type="text", text="hi")])
    res = answer(FakeClient([end]), Session("C1001", ledger), "hello", model="claude-haiku-5-5")
    assert res.error == "no_submit_answer"
    refusal = NS(model="claude-opus-5-5", stop_reason="refusal", usage=usage(), content=[])
    assert answer(FakeClient([refusal]), Session("C1001", ledger), "x").error == "refusal"


def test_haiku_does_not_send_fallbacks():
    ledger = load_ledger()
    end = NS(model="claude-haiku-5-5", stop_reason="end_turn", usage=usage(), content=[])
    client = FakeClient([end])
    answer(client, Session("C1001", ledger), "x", model="claude-haiku-5-5")
    assert "fallbacks" not in client.messages.requests[0]
