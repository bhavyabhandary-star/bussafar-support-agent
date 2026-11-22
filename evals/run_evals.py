"""Evaluate Sahil against every scenario in evals/scenarios.json and write a scorecard.

    python -m evals.run_evals                         # in-process, no Gemini: code paths only (free)
    python -m evals.run_evals --live                  # in-process with Gemini (cached replies are reused)
    python -m evals.run_evals --live --budget 60      # stop starting new scenarios after 60 Gemini calls
    python -m evals.run_evals --live --judge          # also grade replies with the Gemini judge
    python -m evals.run_evals --target https://bussafar-support.onrender.com   # a deployed site (EVAL_PASSCODE)
    python -m evals.run_evals --only cancellation,SAF-01 --live
    python -m evals.run_evals --live --gate           # exit 1 if any threshold is missed (deploy gate)

Reports: evals/reports/latest.md (scorecard), latest.json (every reply and check), history.csv (one line per run).
Replies are cached in evals/cache/ by code fingerprint, so a re-run only spends Gemini calls on scenarios
whose code, prompts, help docs or wording changed.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from evals.scorers import DIMENSIONS, Context, score_consistency, score_record  # noqa: E402
from evals.targets import Cache, HttpTarget, LocalTarget, code_fingerprint  # noqa: E402

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.json"
REPORTS = HERE / "reports"
CACHE = HERE / "cache" / "replies.json"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def load_scenarios(only: str = "") -> tuple[list[dict], dict]:
    data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    items = data["scenarios"]
    if only:
        keys = {k.strip().lower() for k in only.split(",") if k.strip()}
        items = [s for s in items if s["id"].lower() in keys or s["category"].lower() in keys]
    return items, data["thresholds"]


def mask(text: str, last4: dict[str, str]) -> str:
    for v in last4.values():
        text = text.replace(v, "****")
    return text


def run(args) -> dict:
    scenarios, thresholds = load_scenarios(args.only)
    ctx = Context.load()
    last4 = dict(zip(pd.read_csv(config.BOOKINGS_CSV, dtype=str)["pnr"],
                     pd.read_csv(config.BOOKINGS_CSV, dtype=str)["phone_last4"]))
    if args.target == "local":
        target = LocalTarget(live=args.live, gap=args.gap)
        where = "in-process"
    else:
        passcode = os.getenv("EVAL_PASSCODE") or config.app_passcode() or ""
        target = HttpTarget(args.target, passcode, gap=args.gap)
        where = args.target
    fp = code_fingerprint()
    cache = Cache(CACHE)
    judge = None
    pace = getattr(getattr(target, "llm", None), "throttle", None) or getattr(target, "throttle", None)
    if args.judge:
        from evals.judge import GeminiJudge
        from evals.targets import Throttle
        judge = GeminiJudge()
        pace = pace or Throttle(args.gap)
    judge_calls = 0
    started = time.strftime("%Y-%m-%d %H:%M")
    records = []
    for i, sc in enumerate(scenarios, 1):
        rec = {k: sc[k] for k in ("id", "category", "severity", "turns")}
        rec["expect"] = sc.get("expect", {})
        key = Cache.key(target.kind, where if target.kind == "http" else "", target.model, fp, sc["turns"])
        cached = None if args.no_cache or target.model == "offline" else cache.get(key)
        if cached:
            rec["turns"], rec["cached"] = cached, True
        elif args.budget and target.calls >= args.budget:
            rec["skipped"] = f"Gemini budget of {args.budget} calls used up"
        else:
            try:
                rec["turns"] = target.run(sc["id"], sc["turns"], last4)
            except Exception as exc:  # noqa: BLE001 - recorded, the run continues
                rec["error"] = f"{type(exc).__name__}: {mask(str(exc), last4)}"[:300]
            else:
                flags = {f for t in rec["turns"] for f in t.get("flags", [])}
                if "llm_unavailable" in flags:
                    rec["skipped"] = "needs Gemini (run with --live)"
                elif "llm_error" in flags:
                    rec["skipped"] = "Gemini error or rate limit"
                elif target.model != "offline":
                    cache.put(key, rec["turns"])
        if judge and not rec.get("skipped") and not rec.get("error"):
            jkey = Cache.key("judge", judge.model, [t["text"] for t in rec["turns"]], [t["user"] for t in rec["turns"]])
            grade = cache.get(jkey)
            if grade is None and not (args.budget and target.calls + judge_calls >= args.budget):
                try:
                    pace.wait()  # judge calls share the per-minute limit with the agent's calls
                    grade = judge.grade(rec["turns"])
                    judge_calls += 1
                    if "error" not in grade:
                        cache.put(jkey, grade)
                except Exception as exc:  # noqa: BLE001
                    grade = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
            if grade:
                rec["judge"] = grade
        status = "skipped" if rec.get("skipped") else "error" if rec.get("error") else "cached" if rec.get("cached") else "ran"
        print(f"[{i}/{len(scenarios)}] {sc['id']}: {status}", flush=True)
        records.append(rec)
        if i % 10 == 0:
            cache.save()
    cache.save()
    return summarise(records, thresholds, ctx, last4, {
        "started": started, "finished": time.strftime("%Y-%m-%d %H:%M"), "target": where, "model": target.model,
        "code_fingerprint": fp, "gemini_calls": target.calls, "judge_calls": judge_calls})


def summarise(records, thresholds, ctx, last4, meta) -> dict:
    scored = [r for r in records if not r.get("skipped") and not r.get("error")]
    for r in scored:
        r["checks"] = [c.__dict__ for c in score_record(r, ctx)]
    groups = score_consistency(scored)
    for r in scored:
        g = r["expect"].get("group")
        if g in groups:
            r["checks"].append(groups[g].__dict__)
    for r in scored:
        r["passed"] = all(c["passed"] for c in r["checks"])

    dims = {}
    for d in DIMENSIONS:
        with_d = [r for r in scored if any(c["dimension"] == d for c in r["checks"])]
        ok = [r for r in with_d if all(c["passed"] for c in r["checks"] if c["dimension"] == d)]
        dims[d] = {"passed": len(ok), "total": len(with_d), "score": round(len(ok) / len(with_d), 3) if with_d else None}

    # Escalation as a classifier: should a ticket have been raised?
    tp = fp = fn = tn = 0
    for r in scored:
        if "ticket" not in r["expect"]:
            continue
        want = r["expect"]["ticket"] is not False
        got = any(t.get("ticket_ids") for t in r["turns"])
        tp += want and got
        fn += want and not got
        fp += (not want) and got
        tn += (not want) and not got
    esc = {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
           "precision": round(tp / (tp + fp), 3) if tp + fp else None,
           "recall": round(tp / (tp + fn), 3) if tp + fn else None}

    judged = [r["judge"] for r in scored if r.get("judge") and "score" in r["judge"]]
    judge = None
    if judged:
        judge = {k: round(statistics.mean(j[k] for j in judged), 2) for k in ("helpfulness", "faithfulness", "tone", "score")}
        judge["n"] = len(judged)

    ms = sorted(t["ms"] for r in scored if not r.get("cached") for t in r["turns"] if "ms" in t)
    latency = {"p50_ms": ms[len(ms) // 2], "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))]} if ms else None

    actual = {d: dims[d]["score"] for d in DIMENSIONS}
    actual.update({"escalation_precision": esc["precision"], "escalation_recall": esc["recall"],
                   "judge_score": judge["score"] if judge else None})
    gate = {}
    for name, need in thresholds.items():
        got = actual.get(name)
        gate[name] = {"threshold": need, "score": got, "pass": None if got is None else got >= need}
    gate_ok = all(g["pass"] is not False for g in gate.values())

    cats = {}
    for r in records:
        c = cats.setdefault(r["category"], {"passed": 0, "failed": 0, "skipped": 0})
        c["skipped" if r.get("skipped") or r.get("error") else "passed" if r.get("passed") else "failed"] += 1

    summary = {
        "meta": meta, "scenarios": len(records), "scored": len(scored),
        "passed": sum(r["passed"] for r in scored), "failed": sum(not r["passed"] for r in scored),
        "skipped": sum(bool(r.get("skipped")) for r in records), "errors": sum(bool(r.get("error")) for r in records),
        "critical_pass_rate": _rate([r for r in scored if r["severity"] == "critical"]),
        "dimensions": dims, "escalation": esc, "judge": judge, "latency": latency, "gate": gate, "gate_ok": gate_ok,
        "categories": cats,
    }
    for r in records:  # never write real phone digits into reports
        for t in r.get("turns", []):
            if isinstance(t, dict):
                t["user"], t["text"] = mask(t["user"], last4), mask(t["text"], last4)
    return {"summary": summary, "records": records}


def _rate(rs):
    return round(sum(r["passed"] for r in rs) / len(rs), 3) if rs else None


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%" if x <= 1 else f"{x:.2f}"


def write_reports(result: dict) -> Path:
    s, records = result["summary"], result["records"]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    m = s["meta"]
    lines = [
        "# Sahil evaluation scorecard", "",
        f"**Run:** {m['started']} to {m['finished']} · target `{m['target']}` · model `{m['model']}` · "
        f"code `{m['code_fingerprint']}` · Gemini calls {m['gemini_calls']} (+{m['judge_calls']} judge)", "",
        f"**Result:** {'✅ all thresholds met' if s['gate_ok'] else '❌ thresholds missed'} · "
        f"{s['passed']}/{s['scored']} scenarios passed · critical {pct(s['critical_pass_rate'])} · "
        f"{s['skipped']} skipped · {s['errors']} errors", "",
        "## Scorecard", "", "| Measure | Score | Threshold | |", "|---|---|---|---|",
    ]
    for name, g in s["gate"].items():
        mark = "✅" if g["pass"] else "❌" if g["pass"] is False else "–"
        score = pct(g["score"]) if name != "judge_score" else ("n/a" if g["score"] is None else f"{g['score']:.2f} / 5")
        thr = pct(g["threshold"]) if name != "judge_score" else f"{g['threshold']:.1f} / 5"
        lines.append(f"| {name.replace('_', ' ')} | {score} | {thr} | {mark} |")
    e = s["escalation"]
    lines += ["", f"Escalation: {e['tp']} correct hand-offs, {e['fn']} missed, {e['fp']} unnecessary, {e['tn']} correctly none."]
    if s["judge"]:
        j = s["judge"]
        lines.append(f"Judge ({j['n']} replies): helpfulness {j['helpfulness']}, faithfulness {j['faithfulness']}, tone {j['tone']}.")
    if s["latency"]:
        lines.append(f"Latency per reply: p50 {s['latency']['p50_ms']} ms, p95 {s['latency']['p95_ms']} ms (fresh replies only).")
    lines += ["", "## By category", "", "| Category | Passed | Failed | Skipped |", "|---|---|---|---|"]
    for c, v in s["categories"].items():
        lines.append(f"| {c} | {v['passed']} | {v['failed']} | {v['skipped']} |")
    fails = sorted((r for r in records if r.get("checks") and not r["passed"]),
                   key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), r["id"]))
    lines += ["", "## Failures", ""]
    if not fails:
        lines.append("None.")
    for r in fails:
        bad = [c for c in r["checks"] if not c["passed"]]
        lines.append(f"### {r['id']} ({r['severity']}, {r['category']})")
        for t in r["turns"]:
            lines.append(f"- **Customer:** {t['user']}")
            lines.append(f"  **Sahil:** {t['text'][:500]}")
        for c in bad:
            lines.append(f"- ❌ {c['dimension']}: {c['name']}" + (f" — {c['note']}" if c["note"] else ""))
        if r.get("judge", {}).get("issue"):
            lines.append(f"- Judge: {r['judge']['issue']}")
        lines.append("")
    low = [r for r in records if r.get("judge", {}).get("score", 5) < 3.5]
    if low:
        lines += ["## Low judge scores", ""]
        lines += [f"- {r['id']}: {r['judge']['score']} — {r['judge'].get('issue', '')}" for r in low]
        lines.append("")
    skipped = [r for r in records if r.get("skipped") or r.get("error")]
    if skipped:
        lines += ["## Not scored", ""]
        lines += [f"- {r['id']}: {r.get('skipped') or r.get('error')}" for r in skipped]
    (REPORTS / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hist = REPORTS / "history.csv"
    new = not hist.exists()
    with hist.open("a", encoding="utf-8") as f:
        if new:
            f.write("finished,target,model,code,scored,passed,skipped,gate_ok\n")
        f.write(f"{m['finished']},{m['target']},{m['model']},{m['code_fingerprint']},{s['scored']},{s['passed']},"
                f"{s['skipped']},{s['gate_ok']}\n")
    return REPORTS / "latest.md"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="local", help="'local' (default) or a website URL")
    ap.add_argument("--live", action="store_true", help="use Gemini for the local target")
    ap.add_argument("--judge", action="store_true", help="grade replies with the Gemini judge")
    ap.add_argument("--budget", type=int, default=0, help="max Gemini calls this run (0 = no limit)")
    ap.add_argument("--gap", type=float, default=5.0, help="seconds between Gemini calls (5 = 12/min)")
    ap.add_argument("--only", default="", help="comma-separated scenario IDs or categories")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached replies")
    ap.add_argument("--gate", action="store_true", help="exit 1 if any threshold is missed")
    args = ap.parse_args(argv)
    result = run(args)
    report = write_reports(result)
    s = result["summary"]
    print(f"\n{s['passed']}/{s['scored']} passed, {s['skipped']} skipped, {s['errors']} errors. "
          f"Thresholds {'met' if s['gate_ok'] else 'MISSED'}.")
    for name, g in s["gate"].items():
        if g["pass"] is False:
            print(f"  below threshold: {name} {g['score']} < {g['threshold']}")
    print(f"Report: {report}")
    return 1 if args.gate and not s["gate_ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
