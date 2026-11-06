"""Tools the support agent can call, bound to the logged-in customer.

A session only ever sees its own customer's bets: asking for anyone else's bet
returns "not found", the same answer as a bet that does not exist (R7.1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from supportbot.engine import cash_out_quote, leg_key

DATA = Path(__file__).resolve().parents[2] / "data"
ESCALATION_TEAMS = ["customer_resolution", "safer_gambling", "specialist"]
OUTCOMES = ["won", "lost", "void", "open", "cashed_out", "not_applicable"]


def load_ledger(path: Path = DATA / "ledger.json") -> dict:
    return json.loads(path.read_text())


def load_rules(path: Path = DATA / "rules.md") -> str:
    return path.read_text(encoding="utf-8")


def rule_ids(rules_text: str) -> set[str]:
    import re
    return set(re.findall(r"\*\*(R\d+\.\d+)\*\*", rules_text))


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS = [
    {
        "name": "list_my_bets",
        "description": "List the logged-in customer's bets with id, type, stake, odds, status and placement date. "
                       "Use this when the customer does not give a bet id.",
        "input_schema": _schema({"status": {"type": "string", "enum": ["any", "open", "settled"],
                                            "description": "Filter; 'settled' means won, lost, void or cashed out."}},
                                ["status"]),
    },
    {
        "name": "get_bet",
        "description": "Full details of one of the logged-in customer's bets: legs with their markets, picks and "
                       "odds at placement, each leg's status, the bet's status and total return. Returns 'not found' "
                       "for bets that do not belong to this customer.",
        "input_schema": _schema({"bet_id": {"type": "string", "description": "e.g. B5012"}}, ["bet_id"]),
    },
    {
        "name": "get_event",
        "description": "A match's status (scheduled, live, completed, abandoned, postponed), kick-off, goals with "
                       "minute, scorer and own-goal flag, starting line-ups and substitutes, the abandonment minute, "
                       "and for knockout ties which team qualified.",
        "input_schema": _schema({"event_id": {"type": "string", "description": "e.g. E07"}}, ["event_id"]),
    },
    {
        "name": "get_cash_out_quote",
        "description": "The current Cash Out offer for one of the customer's unsettled bets, or the reason none is "
                       "available. Read-only: it does not cash anything out.",
        "input_schema": _schema({"bet_id": {"type": "string"}}, ["bet_id"]),
    },
    {
        "name": "escalate",
        "description": "Refer the conversation to a specialist team. Use it when the rules say to refer the "
                       "customer (R7.2, R7.3, R7.4).",
        "input_schema": _schema({"team": {"type": "string", "enum": ESCALATION_TEAMS},
                                 "summary": {"type": "string", "description": "One or two sentences for the team."}},
                                ["team", "summary"]),
    },
    {
        "name": "submit_answer",
        "description": "Send the final reply to the customer. Call this exactly once, as the last step.",
        "input_schema": _schema({
            "reply": {"type": "string", "description": "The message the customer sees."},
            "outcome": {"type": "string", "enum": OUTCOMES,
                        "description": "Status of the bet the customer asked about; not_applicable if no bet."},
            "amount": {"type": ["number", "null"],
                       "description": "The single money figure the reply is about (total return, refund, cash-out "
                                      "value or potential return), in euro; null if none."},
            "rule_ids": {"type": "array", "items": {"type": "string"},
                         "description": "Ids of the rules the reply relies on, e.g. ['R3.2']; empty if none."},
        }, ["reply", "outcome", "amount", "rule_ids"]),
    },
]
TOOLS = [{**t, "strict": True} for t in TOOLS]


@dataclass
class Session:
    customer_id: str
    ledger: dict
    calls: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)
    answer: dict | None = None

    def _bet(self, bet_id: str) -> dict | None:
        for b in self.ledger["bets"]:
            if b["bet_id"] == bet_id.strip().upper() and b["customer_id"] == self.customer_id:
                return b
        return None

    def _public_bet(self, b: dict) -> dict:
        out = {k: b[k] for k in ["bet_id", "type", "stake", "odds", "placed_at", "status", "total_return"]}
        out["legs"] = []
        for leg, st in zip(b["legs"], b["leg_status"]):
            ev = self.ledger["events"][leg["event_id"]]
            item = {"event_id": leg["event_id"], "match": f"{ev['home']} v {ev['away']}",
                    "market": leg["market"], "pick": leg["pick"], "odds_at_placement": leg["odds"],
                    "leg_status": "settled_by_cash_out" if b["status"] == "cashed_out" else st}
            if "line" in leg:
                item["line"] = leg["line"]
            out["legs"].append(item)
        if b.get("cashed_out"):
            out["cashed_out"] = b["cashed_out"]
        return out

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Execute a tool. Returns (content, is_error)."""
        self.calls.append({"name": name, "input": args})
        if name == "list_my_bets":
            mine = [b for b in self.ledger["bets"] if b["customer_id"] == self.customer_id]
            if args["status"] == "open":
                mine = [b for b in mine if b["status"] == "open"]
            elif args["status"] == "settled":
                mine = [b for b in mine if b["status"] != "open"]
            rows = [{k: b[k] for k in ["bet_id", "type", "stake", "odds", "status", "placed_at"]} for b in mine]
            return json.dumps(rows), False
        if name == "get_bet":
            b = self._bet(args["bet_id"])
            if b is None:
                return f"Bet {args['bet_id']} not found on this account.", True
            return json.dumps(self._public_bet(b)), False
        if name == "get_event":
            ev = self.ledger["events"].get(args["event_id"].strip().upper())
            if ev is None:
                return f"Event {args['event_id']} not found.", True
            public = {k: v for k, v in ev.items() if k not in ("current_odds", "suspended")}
            return json.dumps(public), False
        if name == "get_cash_out_quote":
            b = self._bet(args["bet_id"])
            if b is None:
                return f"Bet {args['bet_id']} not found on this account.", True
            q = cash_out_quote(b, self.ledger["events"])
            q.pop("rules", None)  # the agent must find the rule itself
            return json.dumps(q), False
        if name == "escalate":
            self.escalations.append(args)
            return "Referred. Tell the customer the team will contact them.", False
        if name == "submit_answer":
            self.answer = args
            return "Sent.", False
        return f"Unknown tool {name}.", True


__all__ = ["TOOLS", "Session", "load_ledger", "load_rules", "rule_ids", "leg_key", "OUTCOMES"]
