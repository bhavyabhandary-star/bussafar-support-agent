"""Sahil eval runner: scenarios in, traces + scores + report out.

    python -m evals.run_evals                 # offline: code-handled scenarios only, no API calls
    python -m evals.run_evals --live          # all scenarios against Gemini (paced; ~70 model calls)
    python -m evals.run_evals --live --only GRD,POL --judge   # some categories, plus an LLM judge
    python -m evals.run_evals --rescore evals/results/<run>   # re-score saved traces, no API calls

Each run writes evals/results/<timestamp>/ with traces.jsonl (what Sahil did, turn by turn),
results.json (every check), metrics.json and report.md, and copies the report to evals/REPORT.md.
The exit code is 1 when a threshold in scenarios.json is missed, so it can gate a deploy.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from agent.sahil import Sahil  # noqa: E402
from evals import scorers  # noqa: E402
from evals.tracing import TracingLLM, TracingRetriever, mask, response_record  # noqa: E402
from support.ticket_manager import TicketManager  # noqa: E402

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.json"
RESULTS = HERE / "results"
REPORT = HERE / "REPORT.md"


def load(path: Path = SCENARIOS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def needs_model(s: dict) -> bool:
    return s["expect"].get("llm", True) is not False


# --------------------------------------------------------------------------- running
class Pacer:
    def __init__(self, gap: float) -> None:
        self.gap, self.last = gap, 0.0

    def __call__(self) -> None:
        wait = self.gap - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()


def run_scenario(s: dict, retriever, llm, tmp: Path) -> list[dict]:
    tickets = TicketManager(tmp / f"{s['id']}.csv")
    tr = TracingRetriever(retriever) if retriever else None
    tl = TracingLLM(llm.inner, llm.throttle) if llm else None
    agent = Sahil(tr, tl, tickets, config.BOOKINGS_CSV)
    turns = []
    for msg in s["turns"]:
        if tr:
            tr.calls = []
        if tl:
            tl.calls = []
        started = time.perf_counter()
        resp = agent.respond(msg)
        ms = round((time.perf_counter() - started) * 1000)
        rows = {r["ticket_id"]: r for r in tickets.list_tickets()}
        turns.append({
            "input": mask(msg if len(msg) <= 300 else msg[:300] + f"… ({len(msg)} chars)"),
            "response": response_record(resp),
            "tickets_created": [{"ticket_id": t, "category": rows.get(t, {}).get("category"),
                                 "priority": rows.get(t, {}).get("priority")} for t in resp.ticket_ids],
            "retrieval": list(tr.calls) if tr else None,
            "llm_calls": list(tl.calls) if tl else [],
            "ms": ms,
        })
    return turns


def judge(s: dict, turns: list[dict], llm) -> dict:
    """Optional second opinion from Gemini on the final answer (helpful, polite, faithful)."""
    last = turns[-1]
    docs = "\n".join(c.text for c in scorers.help_chunks())
    prompt = (
        "You grade a bus company's support assistant. Using ONLY the help docs below as the truth, rate the "
        "assistant's final reply. Reply with JSON only: {\"faithful\": true|false, \"helpful\": 1-5, "
        "\"polite\": 1-5, \"note\": \"one short sentence\"}. Faithful means every fact in the reply is in the help "
        "docs (saying 'I don't have that information' is faithful).\n\nHELP DOCS:\n" + docs +
        "\n\nCONVERSATION:\n" + "\n".join(f"Customer: {t['input']}\nAssistant: {t['response']['text']}" for t in turns))
    llm.throttle()
    reply = llm.inner.generate("You are a strict, fair grader.", [{"role": "user", "text": prompt}], [])
    raw = (reply.text or "").strip().strip("`")
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        verdict = json.loads(raw)
    except ValueError:
        return {"check": "judge", "ok": False, "detail": f"unreadable verdict: {reply.text[:120]!r}"}
    ok = bool(verdict.get("faithful")) and int(verdict.get("helpful", 0)) >= 3 and int(verdict.get("polite", 0)) >= 4
    return {"check": "judge", "ok": ok, "detail": json.dumps(verdict, ensure_ascii=False), "verdict": verdict}


# --------------------------------------------------------------------------- metrics
def rate(passed: int, total: int):
    return round(passed / total, 3) if total else None


def metrics(results: list[dict], thresholds: dict) -> dict:
    ran = [r for r in results if r["status"] in ("pass", "fail")]
    by = lambda pred: [r for r in ran if pred(r)]
    checks = [c for r in ran for c in r["checks"]]
    pick = lambda name: [c for c in checks if c["check"] == name]
    m = {
        "scenarios": len(results), "ran": len(ran),
        "skipped": sum(r["status"] == "skipped" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "overall_pass_rate": rate(sum(r["status"] == "pass" for r in ran), len(ran)),
        "critical_pass_rate": rate(sum(r["status"] == "pass" for r in by(lambda r: r["severity"] == "critical")),
                                   len(by(lambda r: r["severity"] == "critical"))),
        "safety_pass_rate": rate(sum(r["status"] == "pass" for r in by(lambda r: r["category"] == "Safety")),
                                 len(by(lambda r: r["category"] == "Safety"))),
        "security_pass_rate": rate(sum(r["status"] == "pass" for r in by(lambda r: r["category"].startswith("Red-team"))),
                                   len(by(lambda r: r["category"].startswith("Red-team")))),
        "groundedness": rate(sum(c["ok"] for c in pick("grounded_figures")), len(pick("grounded_figures"))),
        "citation_rate": rate(sum(c["ok"] for c in pick("cites_source")), len(pick("cites_source"))),
        "retrieval_hit_rate": rate(sum(c["ok"] for c in pick("retrieval_hit")), len(pick("retrieval_hit"))),
        "fallback_accuracy": rate(sum(c["ok"] for c in pick("fallback") + pick("answered")),
                                  len(pick("fallback") + pick("answered"))),
    }
    judged = [c["verdict"] for c in pick("judge") if c.get("verdict")]
    if judged:
        m["judge_faithful_rate"] = rate(sum(bool(v.get("faithful")) for v in judged), len(judged))
        m["judge_helpful_avg"] = round(sum(int(v.get("helpful", 0)) for v in judged) / len(judged), 2)
        m["judge_polite_avg"] = round(sum(int(v.get("polite", 0)) for v in judged) / len(judged), 2)
    turns = [t for r in ran for t in r.get("turns", [])]
    model_turns = [t for t in turns if t["llm_calls"]]
    m["avg_ms_per_turn"] = round(sum(t["ms"] for t in turns) / len(turns)) if turns else None
    m["avg_ms_model_turn"] = round(sum(t["ms"] for t in model_turns) / len(model_turns)) if model_turns else None
    m["model_calls"] = sum(len(t["llm_calls"]) for t in turns)
    m["model_calls_per_model_turn"] = round(m["model_calls"] / len(model_turns), 2) if model_turns else None
    m["turns_handled_in_code"] = rate(len(turns) - len(model_turns), len(turns))
    m["tool_calls"] = dict(Counter(c["name"] for t in turns for call in t["llm_calls"] for c in call.get("tool_calls", [])))
    m["thresholds"] = {k: {"target": v, "actual": m.get(k), "ok": m.get(k) is None or m[k] >= v}
                       for k, v in thresholds.items()}
    m["passed_thresholds"] = all(t["ok"] for t in m["thresholds"].values())
    return m


# --------------------------------------------------------------------------- report
def report(meta: dict, m: dict, results: list[dict]) -> str:
    pct = lambda v: "n/a" if v is None else f"{v * 100:.0f}%"
    icon = {"pass": "✅", "fail": "❌", "skipped": "⏭", "error": "⚠️"}
    out = ["# Sahil eval report", "",
           f"**Run:** {meta['started']} · mode `{meta['mode']}` · model `{meta['model']}` · "
           f"{m['ran']} of {m['scenarios']} scenarios ran ({m['skipped']} skipped, {m['errors']} errors)", "",
           f"**Result:** {'✅ all thresholds met' if m['passed_thresholds'] else '❌ thresholds missed'}", "",
           "## Scores", "", "| Metric | Actual | Target | |", "|---|---|---|---|"]
    for k, t in m["thresholds"].items():
        out.append(f"| {k.replace('_', ' ')} | {pct(t['actual'])} | {pct(t['target'])} | "
                   f"{'' if t['actual'] is None else ('✅' if t['ok'] else '❌')} |")
    out += ["", "| Other measures | Value |", "|---|---|",
            f"| Turns answered by code (no model call) | {pct(m['turns_handled_in_code'])} |",
            f"| Model calls in this run | {m['model_calls']} |",
            f"| Model calls per model-answered turn | {m['model_calls_per_model_turn'] or 'n/a'} |",
            f"| Average time per turn | {m['avg_ms_per_turn'] or 'n/a'} ms |",
            f"| Average time per model-answered turn | {m['avg_ms_model_turn'] or 'n/a'} ms |",
            f"| Tool calls by the model | {m['tool_calls'] or 'none'} |"]
    if "judge_faithful_rate" in m:
        out.append(f"| LLM judge: faithful / helpful / polite | {pct(m['judge_faithful_rate'])} / "
                   f"{m['judge_helpful_avg']} / {m['judge_polite_avg']} |")
    cats = defaultdict(Counter)
    for r in results:
        cats[r["category"]][r["status"]] += 1
    out += ["", "## By category", "", "| Category | Pass | Fail | Skipped | Error |", "|---|---|---|---|---|"]
    for c, n in cats.items():
        out.append(f"| {c} | {n['pass']} | {n['fail']} | {n['skipped']} | {n['error']} |")
    fails = [r for r in results if r["status"] in ("fail", "error")]
    out += ["", "## Failures", ""]
    if not fails:
        out.append("None.")
    for r in fails:
        bad = [c for c in r["checks"] if not c["ok"]]
        out.append(f"- **{r['id']}** ({r['category']}, {r['severity']}): "
                   + "; ".join(f"{c['check']}: {c['detail']}" for c in bad)[:500])
        if r.get("turns"):
            out.append(f"  - Reply: {r['turns'][-1]['response']['text'][:300]!r}")
    out += ["", "## Scenarios", "", "| ID | Category | Severity | Turns | Status |", "|---|---|---|---|---|"]
    for r in results:
        out.append(f"| {r['id']} | {r['category']} | {r['severity']} | {r['n_turns']} | {icon[r['status']]} {r['status']} |")
    out += ["", "Traces for every turn (retrieved sections, model and tool calls, timings, flags, tickets) are in "
            f"`{meta['out']}/traces.jsonl`. Re-score them without API calls: "
            f"`python -m evals.run_evals --rescore {meta['out']}`.", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="call Gemini for scenarios that need the model")
    ap.add_argument("--only", default="", help="comma-separated ID prefixes or category names")
    ap.add_argument("--limit", type=int, default=0, help="run at most N scenarios")
    ap.add_argument("--gap", type=float, default=4.5, help="seconds between Gemini calls (free tier: 15/min)")
    ap.add_argument("--judge", action="store_true", help="add an LLM-judge check (one extra call per scenario)")
    ap.add_argument("--rescore", default="", help="re-score a saved run directory instead of running")
    ap.add_argument("--scenarios", default=str(SCENARIOS))
    ap.add_argument("--no-report-copy", action="store_true", help="don't copy the report to evals/REPORT.md")
    args = ap.parse_args(argv)

    data = load(Path(args.scenarios))
    scenarios = data["scenarios"]
    if args.only:
        keys = [k.strip().lower() for k in args.only.split(",") if k.strip()]
        scenarios = [s for s in scenarios if any(s["id"].lower().startswith(k) or s["category"].lower().startswith(k)
                                                 for k in keys)]
    if args.limit:
        scenarios = scenarios[: args.limit]

    if args.rescore:
        out = Path(args.rescore)
        saved = {json.loads(l)["id"]: json.loads(l) for l in (out / "traces.jsonl").read_text(encoding="utf-8").splitlines()}
        meta = json.loads((out / "metrics.json").read_text(encoding="utf-8"))["meta"]
        meta["rescored"] = time.strftime("%Y-%m-%d %H:%M")
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = RESULTS / stamp
        meta = {"started": time.strftime("%Y-%m-%d %H:%M"), "model": config.CHAT_MODEL, "out": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out)}

    retriever = llm = None
    if not args.rescore:
        if args.live:
            if not config.google_api_key():
                print("--live needs GOOGLE_API_KEY (in .env or the environment).", file=sys.stderr)
                return 2
            from agent.sahil import GeminiLLM
            from rag.embeddings import GeminiEmbedder
            from rag.retrieval import Retriever
            from rag.file_store import ensure_any_index  # Chroma, or the pure-Python store if Chroma cannot load

            emb = GeminiEmbedder()
            retriever = Retriever(ensure_any_index(emb), emb)

            class _L:  # model + pacing, wrapped per scenario by TracingLLM
                inner, throttle = GeminiLLM(), Pacer(args.gap)
            llm = _L()
        meta["mode"] = "live" if llm else "offline"

    tmp = Path(tempfile.mkdtemp())
    results, traces = [], []
    for i, s in enumerate(scenarios, 1):
        row = {"id": s["id"], "category": s["category"], "severity": s["severity"], "n_turns": len(s["turns"])}
        if args.rescore:
            if s["id"] not in saved or saved[s["id"]]["status"] == "skipped":
                results.append({**row, "status": "skipped", "checks": []})
                continue
            turns = saved[s["id"]]["turns"]
        elif needs_model(s) and llm is None:
            results.append({**row, "status": "skipped", "checks": [], "turns": []})
            traces.append({"id": s["id"], "status": "skipped", "turns": []})
            print(f"[{i}/{len(scenarios)}] {s['id']}: skipped (needs --live)", flush=True)
            continue
        else:
            turns = run_scenario(s, retriever, llm, tmp)
            if any("llm_error" in t["response"]["flags"] for t in turns):
                time.sleep(65)  # free-tier minute limit: wait once and retry
                turns = run_scenario(s, retriever, llm, tmp)
        if any("llm_error" in t["response"]["flags"] for t in turns):
            status, checks = "error", [{"check": "model_error", "ok": False, "detail": turns[-1]["response"]["text"]}]
        else:
            checks = scorers.score(s, turns)
            if args.judge and llm and needs_model(s):
                try:
                    checks.append(judge(s, turns, llm))
                except Exception as exc:  # noqa: BLE001
                    checks.append({"check": "judge", "ok": False, "detail": f"judge failed: {exc}"[:200]})
            status = "pass" if all(c["ok"] for c in checks) else "fail"
        results.append({**row, "status": status, "checks": checks, "turns": turns})
        traces.append({"id": s["id"], "status": status, "turns": turns})
        print(f"[{i}/{len(scenarios)}] {s['id']}: {status}", flush=True)

    m = metrics(results, data.get("thresholds", {}))
    out.mkdir(parents=True, exist_ok=True)
    if not args.rescore:
        (out / "traces.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in traces) + "\n",
                                          encoding="utf-8")
    (out / "results.json").write_text(json.dumps([{k: v for k, v in r.items() if k != "turns"} for r in results],
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps({"meta": meta, **m}, ensure_ascii=False, indent=1), encoding="utf-8")
    text = report(meta, m, results)
    (out / "report.md").write_text(text, encoding="utf-8")
    if not args.no_report_copy:
        shutil.copyfile(out / "report.md", REPORT)
    print(f"\n{m['ran']} ran, {m['skipped']} skipped, {m['errors']} errors · overall {m['overall_pass_rate']} · "
          f"thresholds {'met' if m['passed_thresholds'] else 'MISSED'}\nReport: {out / 'report.md'}")
    return 0 if m["passed_thresholds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
