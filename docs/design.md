# Design decisions

Each section: what was chosen, what was rejected, and why. Update this as you change things. It's the document to reread before an interview.

## 1. Python agent, Go gateway

**Chosen:** the pipeline is Python and talks to the LLM Gateway (Go) over HTTP in OpenAI format.
**Why:** the agent needs sqlglot, numpy, scikit-learn and the Hugging Face stack, which only exist properly in Python. The gateway's job (low-latency proxying, quotas, circuit breakers) suits Go. A plain HTTP boundary means either side can be replaced.

## 2. Small schemas skip retrieval

**Chosen:** if the schema has at most `full_schema_column_threshold` columns (default 60), the whole schema goes into the prompt.
**Why:** retrieval can only lose recall. A missed table can't be recovered later, while 60 columns cost about 2k tokens. Retrieval only pays for itself on large schemas.
**Measure it:** the `no_schema_retrieval` ablation, plus table recall in every report.

## 3. No vector database

**Chosen:** BM25 implemented in about 40 lines, plus optional embeddings in a numpy matrix, fused by reciprocal-rank fusion.
**Rejected:** pgvector or a hosted vector DB.
**Why:** a schema has hundreds to a few thousand columns. A dot product over a 3,000×768 matrix takes microseconds, while a vector DB adds a network hop, infrastructure and failure modes for no gain. Column names like `cust_id` are also lexical, which is why BM25 stays as the base signal.

## 4. Value index

Questions mention literals ("Pune", "Electronics") that appear nowhere in the schema. Distinct text values per column are indexed by character trigrams, and spans of the question (1–4 words, plus quoted strings) are matched with a trigram prefilter, then `SequenceMatcher`. A value match also forces its table into retrieval.
**Limits:** capped values per column (`value_index_max_values_per_column`); ID-like and date-like strings are skipped; numbers aren't indexed.

## 5. Safety layers, strongest first

| Layer | SQLite | Postgres |
|---|---|---|
| Can't write at all | `mode=ro` file handle | role with only `SELECT` grants |
| Rejects non-reads at prepare time | authorizer callback (allows only SELECT/READ/FUNCTION/RECURSIVE) | `BEGIN TRANSACTION READ ONLY` |
| Bounded cost | progress-handler deadline, `fetchmany(row_limit+1)` | `SET LOCAL statement_timeout`, `fetchmany` |
| Friendly errors | sqlglot tree walk (`guard.py`) | same |

**Rejected:** relying on the parser. String checks like `startswith("SELECT")` are bypassed by `WITH d AS (DELETE …) SELECT …` and `SELECT … INTO new_table`. Parsers have gaps too, so when sqlglot can't parse a query the guard defers to the database rather than blocking. The tests run the write attacks with the static guard *off* to prove the database layer holds on its own.
**Guard never repairs:** if a candidate is rejected for attempting a write, it isn't sent back for "repair". That would teach the loop to launder writes.

## 6. Cluster by results, not by SQL text

**Chosen:** candidates are grouped by the *multiset* of returned rows. Values within a row are compared order-insensitively, numbers are normalised (`3.0 == 3`, rounded to 6 places), and row order is ignored.
**Why:** whether two SQL queries are equivalent is undecidable in general, and comparing text or ASTs misses equivalent queries written differently. Results on the real database are cheap and concrete.
**Trade-offs:**
- Ignoring row order means "rank the top 3" questions where order matters aren't distinguished by order. BIRD's metric ignores order too.
- Multiset (not set) means a missing `DISTINCT` splits clusters, which is what we want. The distinguisher then shows the duplicate.
- Two queries can agree on today's data and still differ in meaning. That's a known blind spot of execution-based checking, and the reason the judge also verifies unanimous answers.
**Representative:** the simplest SQL (fewest tokens) in a cluster (Occam's razor), and the easiest for a judge to read.

## 7. Distinguishing examples (the core idea)

**Problem:** a judge comparing two long queries is unreliable. Show it six rows and two different outputs and it usually gets it right.
**Algorithm:** sample k rows per referenced table (deterministic rowid hash), add FK parent rows so joins aren't empty, load into in-memory SQLite, run both queries. Grow k (8 → 2048) until the outputs differ, then minimise with delta debugging (ddmin, complement removal, bounded test budget). Every test runs under the same authorizer and deadline.
**Rejected (for v1):** generating synthetic rows with a constraint solver. Real rows are realistic, which makes the example meaningful to a judge ("a cancelled order"), and they need no type/constraint modelling. Synthetic generation is the natural extension for differences that sampling can't reach (e.g. the difference needs a NULL that doesn't exist in the data).
**Postgres:** rows are copied into SQLite and the queries transpiled with sqlglot. Dialect-specific functions can fail to transpile; then the judge falls back to raw outputs.
**Limits:** only the top two clusters are compared, and table detection is a word-boundary regex (it can over-include a table whose name is also used as an alias, which only costs speed).

## 8. The judge can overrule the majority

When clusters disagree, the judge's pick (informed by the distinguishing example) decides the answer, not the vote. If 3 candidates share a mistake (for example, all forget the cancelled status) the majority is wrong in a correlated way, and the concrete example is better evidence than a headcount. The `no_judge` ablation measures whether this actually helps on your data.
**Self-preference:** a judge may favour SQL written by its own model. `eval/metrics.py` reports how often the judge picks the option containing its own model's candidates, and how often it does so when that option is wrong. Use a different model for the judge than for most generators.

## 9. Confidence: learned, but evaluated honestly

Features: cluster agreement, margin over the runner-up, share of valid candidates, judge support, judge self-confidence, whether an example was needed. Hand-set logistic weights to start. `eval/calibrate.py` fits a logistic regression on half the questions (split by a hash of `question_id`), picks the threshold for a target accuracy on that half, and reports coverage and accuracy on the *other* half. Choosing a threshold and reporting on the same data overstates results.

## 10. Gateway caching vs candidate diversity

The gateway caches identical conversations. Two candidates with the same model and prompt but different sampling would get the *same* cached answer, collapsing diversity silently. When a (model, prompt) pair repeats, the system prompt gets an "Independent sample #n" note, so the requests differ. Alternatively, make sure the gateway's cache key includes temperature.

## 11. Evaluation design

- One run yields three baselines (first candidate, majority vote, oracle), so the verification gain is visible without extra LLM calls.
- Fixed, difficulty-stratified samples (`--limit`, `--seed`) for iteration; the full set only for final numbers.
- Resumable runs (append-only JSONL), because free-tier quotas end runs midway.
- Gold queries run with a longer timeout and no static guard; broken gold queries are skipped and counted, not scored as failures.

## 12. Known limitations / next steps

- The judge now ranks up to `judge_max_options` clusters (default 4), not two. On BIRD dev
  the old top-two limit made a correct minority cluster unreachable on 2 of 50 questions.
  The distinguishing example still compares only the top two, since shrinking a database
  that separates N queries at once is a harder problem than the pairwise case.
- `check_order_bias` asks the judge twice with the options reversed and drops the verdict
  when it flips, because LLM judges tend to favour whichever option is listed first. The
  evaluation reports the flip rate alongside self-preference.
- The rate limiter is in-process memory (fine for one instance; multiple instances would need shared state).
- Postgres opens a connection per query (simple and thread-safe); use `psycopg_pool` under load.
- No multi-turn conversation yet: a clarifying question's answer isn't fed back automatically.
