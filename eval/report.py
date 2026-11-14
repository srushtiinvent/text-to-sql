"""Write report.md and charts for an eval run."""
from __future__ import annotations

import json
from pathlib import Path


def write_report(summary: dict, records: list[dict], out_dir: Path) -> None:
    charts = _charts(summary, out_dir)
    h = summary["headline"]
    pct = lambda x: "—" if x is None else f"{100 * x:.1f}%"
    lines = [
        f"# Eval report: {out_dir.name}",
        "",
        f"{h['questions']} questions · errors: {summary['errors']} · skipped: {summary['skipped']}",
        "",
        "## Headline",
        "",
        "| Method | Execution accuracy |",
        "|---|---|",
        f"| First candidate only (baseline) | {pct(h['first_candidate_accuracy'])} |",
        f"| Majority vote over result clusters | {pct(h['majority_vote_accuracy'])} |",
        f"| **Verified pipeline** | **{pct(h['execution_accuracy'])}** |",
        f"| Oracle (any candidate correct) | {pct(h['oracle_accuracy'])} |",
        "",
        f"Selection efficiency (pipeline ÷ oracle): **{pct(h['selection_efficiency'])}**",
        "",
        f"At confidence threshold {h['threshold']}: answers **{pct(h['coverage_at_threshold'])}** of questions "
        f"with **{pct(h['accuracy_at_threshold'])}** accuracy. AURC: {h['aurc']:.4f} (lower is better).",
        "",
        "## By difficulty",
        "",
        "| Difficulty | n | Accuracy | Oracle |",
        "|---|---|---|---|",
    ]
    for d, s in summary["by_difficulty"].items():
        lines.append(f"| {d} | {s['n']} | {pct(s['execution_accuracy'])} | {pct(s['oracle_accuracy'])} |")
    r, g, v, c = summary["retrieval"], summary["generation"], summary["verification"], summary["cost"]
    j = v["judge"]
    lines += [
        "",
        "## Retrieval",
        "",
        f"- Questions that used retrieval (schema too large for the prompt): {r['questions_using_retrieval']}",
        f"- Table recall (every gold table retrieved): {pct(r['table_recall'])}",
        f"- Average tables retrieved: {r['avg_tables_retrieved']}",
        "",
        "## Generation",
        "",
        f"- Valid candidates per question: {g['avg_valid_candidates']}",
        f"- Result clusters per question: {g['avg_clusters']} (unanimous: {pct(g['unanimous_rate'])})",
        f"- Questions where self-repair was needed: {pct(g['repair_rate'])}",
        "",
        "## Verification",
        "",
        f"- Distinguishing example found when candidates disagreed: {pct(v['distinguishing_found_rate'])}",
        f"- Median rows in a distinguishing example: {v['median_distinguishing_rows']}",
        f"- Judge **pick** accuracy: {pct(j['pick']['accuracy'])} (n={j['pick']['n']}) — "
        f"with example {pct(j['pick']['with_example'])}, without {pct(j['pick']['without_example'])}",
        f"- Judge **verify**: catches {pct(j['verify']['catches_wrong_rate'])} of wrong unanimous answers, "
        f"false alarms on {pct(j['verify']['false_alarm_rate'])} of right ones (n={j['verify']['n']})",
        f"- Self-preference: picks its own model's option {pct(j['self_preference']['picks_own_model_rate'])} "
        f"of the time; when that option is wrong, still picks it {pct(j['self_preference']['picks_own_when_wrong_rate'])} "
        f"(n={j['self_preference']['comparable_decisions']})",
        "",
        "## Cost",
        "",
        f"- LLM calls per question: {c['avg_llm_calls']}",
        f"- Tokens per question: {c['avg_prompt_tokens']:.0f} prompt / {c['avg_completion_tokens']:.0f} completion",
        f"- Latency p50 / p95: {c['latency_p50_ms']} ms / {c['latency_p95_ms']} ms",
        "",
    ]
    for name in charts:
        lines.append(f"![{name}]({name})")
    wrong = [x for x in records if not x.get("correct") and "skipped" not in x][:15]
    if wrong:
        lines += ["", "## First wrong answers (for the bug journal)", ""]
        for x in wrong:
            lines += [f"**q{x['question_id']}** ({x['db_id']}, {x['difficulty']}): {x['question']}",
                      f"- oracle: {'yes' if x.get('any_candidate_correct') else 'no'}, "
                      f"clusters: {x.get('cluster_sizes')}, confidence: {x.get('confidence')}",
                      f"- predicted: `{(x.get('pred_sql') or x.get('error') or '')[:300]}`",
                      f"- gold: `{x['gold_sql'][:300]}`", ""]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _charts(summary: dict, out_dir: Path) -> list[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    names = []
    pts = summary["risk_coverage"]
    if pts:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot([p["coverage"] for p in pts], [p["accuracy"] for p in pts], color="#2563eb", lw=2)
        ax.axhline(summary["headline"]["execution_accuracy"], color="#9ca3af", ls="--", lw=1,
                   label="answer everything")
        ax.set_xlabel("Coverage (share of questions answered)")
        ax.set_ylabel("Accuracy on answered questions")
        ax.set_ylim(0, 1.02)
        ax.set_xlim(0, 1.0)
        ax.grid(alpha=0.25)
        ax.legend(frameon=False)
        ax.set_title("Accuracy vs coverage")
        fig.tight_layout()
        fig.savefig(out_dir / "risk_coverage.png", dpi=150)
        plt.close(fig)
        names.append("risk_coverage.png")
    h = summary["headline"]
    labels = ["First\ncandidate", "Majority\nvote", "Verified", "Oracle"]
    vals = [h["first_candidate_accuracy"], h["majority_vote_accuracy"],
            h["execution_accuracy"], h["oracle_accuracy"]]
    fig, ax = plt.subplots(figsize=(6, 3.6))
    bars = ax.bar(labels, vals, color=["#cbd5e1", "#94a3b8", "#2563eb", "#e5e7eb"])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{100 * v:.1f}%", ha="center", fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Execution accuracy")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_dir / "methods.png", dpi=150)
    plt.close(fig)
    names.append("methods.png")
    return names


if __name__ == "__main__":
    import sys
    d = Path(sys.argv[1])
    s = json.loads((d / "summary.json").read_text())
    recs = [json.loads(l) for l in (d / "results.jsonl").read_text().splitlines() if l.strip()]
    write_report(s, recs, d)
