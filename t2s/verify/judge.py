"""LLM judge: pick between disagreeing queries, verify a single query, or ask the user.

The judge is treated as a *measured* component, not an oracle: eval/metrics.py
reports how often it agrees with the benchmark and whether it prefers queries
written by its own model (self-preference bias).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..llm import extract_json

JUDGE_SYSTEM = (
    "You are a meticulous SQL reviewer. You decide whether a query answers a question "
    "exactly as asked: correct filters (dates, statuses), correct joins, no double counting, "
    "DISTINCT where needed, and the right granularity. Reply with JSON only."
)

PICK = """Question: {question}
{evidence}
Relevant schema:
{schema}

Candidate queries:
{options}

{example}

Which query answers the question as a careful analyst would interpret it?
Focus on the concrete difference in the outputs above. If none of them is right, answer "none".

Reply with JSON: {{"choice": "<label or none>", "confidence": <0.0-1.0>, "reason": "<one or two sentences naming the decisive difference>"}}"""

VERIFY = """Question: {question}
{evidence}
Relevant schema:
{schema}

Query:
```sql
{sql}
```

First rows of its output ({row_count} rows total):
{preview}

Does this query answer the question exactly? Check filters the question implies but does not state
(cancelled/refunded records, date boundaries, NULLs), join fan-out that duplicates rows, and missing DISTINCT.

Reply with JSON: {{"verdict": "pass" or "fail", "confidence": <0.0-1.0>, "issues": ["..."], "reason": "<one sentence>"}}"""

CLARIFY = """A user asked a database question, and there are two reasonable readings of it.

Question: {question}

{difference}

Write ONE short clarifying question (max 25 words) that asks the user which reading they mean,
in plain business language without SQL. Reply with JSON: {{"question": "..."}}"""


@dataclass
class JudgeDecision:
    mode: str  # pick | verify
    choice: str | None  # chosen label (pick) or the verified label if it passed
    confidence: float = 0.5
    reason: str = ""
    issues: list[str] = field(default_factory=list)
    model: str = ""
    raw: str = ""
    used_example: bool = False
    n_options: int = 0          # how many clusters the judge could choose between
    order_swapped: bool = False # options were presented in reverse cluster order
    flipped: bool = False       # a second ask with the order reversed disagreed

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("raw")
        return d


async def judge_pick(llm, model: str, *, question: str, evidence: str, schema_text: str,
                     options: dict[str, str], example_text: str | None,
                     previews: dict[str, str] | None = None,
                     swap_order: bool = False) -> JudgeDecision:
    """Choose between any number of candidate queries.

    LLM judges are known to favour whichever option is listed first. `swap_order`
    presents the same options in reverse, so the caller can ask twice and keep the
    verdict only when both orders agree (see judge_pick_stable).
    """
    labels = list(options)
    if swap_order:
        labels = labels[::-1]
    opt_text = "\n\n".join(f"Query {label}:\n```sql\n{options[label]}\n```" for label in labels)
    if example_text:
        example = ("These queries return different results. Here is a minimal example "
                   "that shows the difference:\n\n" + example_text)
    else:
        example = "Outputs on the real database:\n\n" + "\n\n".join(
            f"Query {label}:\n{(previews or {}).get(label, '')}" for label in labels)
    prompt = PICK.format(question=question, evidence=_ev(evidence), schema=schema_text,
                         options=opt_text, example=example)
    resp = await llm.chat([{"role": "system", "content": JUDGE_SYSTEM},
                           {"role": "user", "content": prompt}], model, 0.0, 600, tag="judge")
    data = extract_json(resp.text)
    choice = str(data.get("choice", "none")).strip().strip('"').upper().replace("QUERY ", "")
    if choice not in {k.upper() for k in options}:
        choice = None
    return JudgeDecision("pick", choice, _conf(data), str(data.get("reason", ""))[:500],
                         model=resp.model, raw=resp.text, used_example=bool(example_text),
                         n_options=len(options), order_swapped=swap_order)


async def judge_pick_stable(llm, model: str, *, check_order_bias: bool, **kw) -> JudgeDecision:
    """Ask once; with check_order_bias, ask again with the options reversed.

    If the two answers disagree, the verdict is position bias rather than judgement,
    so the decision is dropped (choice=None) and the majority stands. `flipped`
    records it so the evaluation can report how often this happens.
    """
    first = await judge_pick(llm, model, **kw)
    if not check_order_bias or first.choice is None:
        return first
    second = await judge_pick(llm, model, swap_order=True, **kw)
    if second.choice == first.choice:
        return first
    original = first.choice
    first.flipped = True
    first.choice = None
    first.reason = (f"Order bias: chose {original} first, "
                    f"{second.choice or 'none'} with the options reversed. "
                    f"Original reason: {first.reason}")[:500]
    return first


async def judge_verify(llm, model: str, *, question: str, evidence: str, schema_text: str,
                       label: str, sql: str, preview: str, row_count: int) -> JudgeDecision:
    prompt = VERIFY.format(question=question, evidence=_ev(evidence), schema=schema_text,
                           sql=sql, preview=preview, row_count=row_count)
    resp = await llm.chat([{"role": "system", "content": JUDGE_SYSTEM},
                           {"role": "user", "content": prompt}], model, 0.0, 600, tag="judge")
    data = extract_json(resp.text)
    passed = str(data.get("verdict", "")).lower() == "pass"
    issues = data.get("issues") or []
    return JudgeDecision("verify", label if passed else None, _conf(data),
                         str(data.get("reason", ""))[:500],
                         issues=[str(i)[:200] for i in issues][:5] if isinstance(issues, list) else [],
                         model=resp.model, raw=resp.text)


async def clarifying_question(llm, model: str, *, question: str, difference: str) -> str:
    resp = await llm.chat([{"role": "user", "content": CLARIFY.format(
        question=question, difference=difference)}], model, 0.2, 200, tag="clarify")
    q = str(extract_json(resp.text).get("question", "")).strip()
    return q or "Could you say a little more about what you want included in this result?"


def _ev(evidence: str) -> str:
    return f"Hint from the user: {evidence}\n" if evidence else ""


def _conf(data: dict) -> float:
    try:
        return max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        return 0.5
