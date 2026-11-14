# Evaluation guide

## Get BIRD

Download the BIRD dev set from https://bird-bench.github.io/ and unpack it so you have:

```
bird/dev.json
bird/dev_databases/<db_id>/<db_id>.sqlite
bird/dev_databases/<db_id>/database_description/*.csv
```

## Iterate on a fixed sample

```bash
python -m eval.run --dataset bird/dev.json --db-root bird/dev_databases --limit 300 --seed 7 --name dev300
```

The sample is difficulty-stratified and deterministic for a given seed, so runs are comparable. A free-tier run of 300 questions × ~7 LLM calls is slow. Runs are resumable (just rerun the same command), and your gateway's cache makes reruns of unchanged prompts free.

## What the numbers mean

| Metric | Meaning | Why it matters |
|---|---|---|
| execution_accuracy | final answer rows == gold rows (set comparison, BIRD's rule) | the headline |
| first_candidate_accuracy | one direct prompt, no verification | your baseline |
| majority_vote_accuracy | largest result cluster | what you get without a judge |
| oracle_accuracy | at least one candidate was right | ceiling for any selection method |
| selection_efficiency | accuracy ÷ oracle | how much of the ceiling verification captures |
| table_recall | every gold table was retrieved | retrieval misses can't be fixed later |
| judge pick accuracy, with/without example | judge chose a correct option | shows the distinguishing example helps |
| judge verify catches / false alarms | single-answer verification as a classifier | is the judge worth its cost? |
| self-preference | judge picks its own model's option, even when wrong | known LLM-judge bias, measured |
| coverage / accuracy at threshold, AURC | selective answering | "answers 80% at 85% accuracy" |

**Interpreting gaps:**
- oracle − pipeline = selection loss (verification picked wrong).
- 100% − oracle = generation loss (no candidate was right; fix prompts, retrieval or models).

## Calibrate, then report holdout

```bash
python -m eval.calibrate runs/dev300 --target-accuracy 0.9
```

Put `recommended_threshold` in `config.toml`. Report the **holdout** coverage and accuracy, not the fit numbers.

## Ablations

```bash
python -m eval.ablate --dataset bird/dev.json --db-root bird/dev_databases --limit 200 --name abl200
```

Writes `runs/abl200/ablation.md`. If a component doesn't improve accuracy or calibration, say so in the README. An honest negative result is more convincing in an interview than a table where everything helps.

## Writing it up

For the README and interviews, keep:
1. The headline table (first candidate → majority → verified → oracle).
2. The ablation table.
3. The accuracy-vs-coverage chart with the holdout operating point marked.
4. Two or three real examples from `traces.jsonl` where the distinguishing example changed the outcome.
5. Cost per question (calls, tokens, p50/p95 latency).
