"""Run the eval set against a model, with a hard budget cap.

    python scripts/run_eval.py --model claude-opus-5-5 --limit 10 --budget-usd 1
    python scripts/run_eval.py --model claude-haiku-5-5 --budget-usd 5

Spends real money: every case makes 2-6 API calls. The run stops starting new
cases once the measured spend reaches --budget-usd. Writes
reports/runs/<stamp>-<model>.jsonl (one line per case) and
reports/summary-<model>.json.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supportbot.agent import answer  # noqa: E402
from supportbot.grader import HALLUCINATION, TAXONOMY, grade  # noqa: E402
from supportbot.tools import Session, load_ledger, load_rules, rule_ids  # noqa: E402


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def summarise(rows: list[dict], model: str, effort: str) -> dict:
    n = len(rows)
    ok = sum(r["passed"] for r in rows)
    by_cat = {}
    for r in rows:
        c = by_cat.setdefault(r["category"], {"n": 0, "passed": 0})
        c["n"] += 1
        c["passed"] += r["passed"]
    failures = {}
    for r in rows:
        for f in r["failures"]:
            failures[f] = failures.get(f, 0) + 1
    halluc = sum(any(f in HALLUCINATION for f in r["failures"]) for r in rows)
    costs = [r["cost_usd"] for r in rows]
    lat = [r["latency_s"] for r in rows]
    cache_read = sum(r["usage"]["cache_read"] for r in rows)
    cacheable = cache_read + sum(r["usage"]["cache_write"] + r["usage"]["input"] for r in rows)
    return {
        "model": model, "effort": effort, "cases": n,
        "task_success": ok / n if n else 0.0, "task_success_ci95": wilson(ok, n),
        "hallucinated_rule_rate": halluc / n if n else 0.0, "hallucinated_rule_ci95": wilson(halluc, n),
        "by_category": {k: {**v, "rate": v["passed"] / v["n"]} for k, v in sorted(by_cat.items())},
        "failures": {k: {"count": v, "meaning": TAXONOMY[k]} for k, v in sorted(failures.items(), key=lambda x: -x[1])},
        "cost_usd_total": sum(costs), "cost_usd_mean": statistics.mean(costs) if costs else 0.0,
        "cost_usd_p50": pct(costs, 0.5),
        "latency_s_mean": statistics.mean(lat) if lat else 0.0, "latency_s_p50": pct(lat, 0.5),
        "latency_s_p95": pct(lat, 0.95),
        "turns_mean": statistics.mean(r["turns"] for r in rows) if rows else 0.0,
        "cache_read_share_of_input": cache_read / cacheable if cacheable else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--categories", nargs="*", default=None)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    cases = [json.loads(line) for line in (ROOT / "evals" / "cases.jsonl").read_text().splitlines() if line]
    if args.categories:
        cases = [c for c in cases if c["category"] in args.categories]
    if args.limit:
        # spread a short run across categories instead of taking the first N
        cases = sorted(cases, key=lambda c: (int(c["id"][1:]) * 7919) % 1000)[:args.limit]

    client = anthropic.Anthropic(max_retries=4)
    valid = rule_ids(load_rules())
    spent = 0.0
    lock = threading.Lock()
    stop = threading.Event()
    rows = []
    out_dir = ROOT / "reports" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.model}.jsonl"

    def run(case):
        nonlocal spent
        if stop.is_set():
            return None
        session = Session(case["customer_id"], load_ledger())
        try:
            res = answer(client, session, case["message"], model=args.model, effort=args.effort)
        except anthropic.APIError as e:  # graded as no_answer, and recorded
            res = None
            err = f"{type(e).__name__}: {e}"
        failures = grade(case, res.answer if res else None, session.calls, session.escalations, valid)
        row = {
            "id": case["id"], "category": case["category"], "passed": not failures, "failures": failures,
            "answer": res.answer if res else None, "error": res.error if res else err,
            "calls": [c["name"] for c in session.calls], "escalations": session.escalations,
            "cost_usd": res.cost_usd if res else 0.0, "latency_s": res.latency_s if res else 0.0,
            "turns": len(res.turns) if res else 0,
            "served_by": sorted({t.model for t in res.turns}) if res else [],
            "usage": {k: sum(getattr(t, a) for t in res.turns) if res else 0 for k, a in
                      [("input", "input_tokens"), ("output", "output_tokens"),
                       ("cache_read", "cache_read"), ("cache_write", "cache_write")]},
        }
        with lock:
            spent += row["cost_usd"]
            rows.append(row)
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            if spent >= args.budget_usd:
                stop.set()
            print(f"{len(rows):>3}/{len(cases)} {case['id']} {'PASS' if row['passed'] else 'FAIL ' + ','.join(failures)}"
                  f"  ${row['cost_usd']:.4f}  {row['latency_s']:.1f}s  (spent ${spent:.3f})", flush=True)
        return row

    with ThreadPoolExecutor(args.concurrency) as pool:
        for _ in as_completed([pool.submit(run, c) for c in cases]):
            pass

    summary = summarise(rows, args.model, args.effort)
    summary["stopped_by_budget"] = stop.is_set() and len(rows) < len(cases)
    summary["run_file"] = str(out.relative_to(ROOT))
    (ROOT / "reports" / f"summary-{args.model}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in ["cases", "task_success", "task_success_ci95",
                                              "hallucinated_rule_rate", "cost_usd_total", "cost_usd_mean",
                                              "latency_s_p50", "latency_s_p95", "cache_read_share_of_input",
                                              "stopped_by_budget"]}, indent=1))
    print("failures:", {k: v["count"] for k, v in summary["failures"].items()})


if __name__ == "__main__":
    main()
