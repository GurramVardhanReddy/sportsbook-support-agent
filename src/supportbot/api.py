"""HTTP API for the support agent.

The customer id would come from the sportsbook's session in production; here it
is the X-Customer-Id header so the demo can switch customers.

    uvicorn supportbot.api:app --port 8000
"""

from __future__ import annotations

import os

import anthropic
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from supportbot.agent import answer
from supportbot.tools import Session, load_ledger

app = FastAPI(title="Fernhill Sports support agent")
MODEL = os.environ.get("SUPPORT_MODEL", "claude-opus-5-5")
_client: anthropic.Anthropic | None = None


class Ask(BaseModel):
    message: str


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL}


@app.post("/ask")
def ask(body: Ask, x_customer_id: str = Header(...)) -> dict:
    ledger = load_ledger()
    if not any(b["customer_id"] == x_customer_id for b in ledger["bets"]):
        raise HTTPException(404, "unknown customer")
    try:
        res = answer(client(), Session(x_customer_id, ledger), body.message, model=MODEL)
    except anthropic.APIError as e:
        raise HTTPException(502, f"model error: {type(e).__name__}") from e
    if res.answer is None:
        raise HTTPException(502, f"no answer ({res.error})")
    return {**res.answer, "escalations": res.escalations, "tools_called": [c["name"] for c in res.calls],
            "cost_usd": round(res.cost_usd, 6), "latency_s": round(res.latency_s, 2)}
