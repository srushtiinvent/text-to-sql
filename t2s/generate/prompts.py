"""Prompt templates. Different styles give the candidates genuinely different reasoning paths."""
from __future__ import annotations

DIALECT_NOTES = {
    "sqlite": ("SQLite: use strftime('%Y-%m', col) for months, date('now') for today, "
               "|| for string concatenation, CAST(x AS REAL) before dividing integers."),
    "postgres": ("PostgreSQL: use date_trunc('month', col), CURRENT_DATE, ILIKE for "
                 "case-insensitive matching, and ::numeric before dividing integers."),
}

SYSTEM = (
    "You are an expert data analyst who writes correct, minimal {dialect} SQL. "
    "Use only tables and columns that exist in the schema. Return exactly one read-only "
    "SELECT statement (CTEs allowed) inside a ```sql code block."
)

DIRECT = """Database schema:
{schema}

{hints}{evidence}Dialect notes: {dialect_notes}

Question: {question}

Write the SQL query."""

PLAN = """Database schema:
{schema}

{hints}{evidence}Dialect notes: {dialect_notes}

Question: {question}

First write a short plan:
1. Which tables are needed and how they join (name the key columns).
2. Which filters the question implies, including ones it states only indirectly
   (date ranges, statuses such as cancelled/refunded, NULLs).
3. What is being aggregated or returned, and whether DISTINCT is needed.
Then write the SQL query."""

DECOMPOSE = """Database schema:
{schema}

{hints}{evidence}Dialect notes: {dialect_notes}

Question: {question}

Break the question into small steps and write each step as a CTE (WITH step1 AS (...), ...),
then write the final SELECT that combines them. Keep every step simple enough to check by eye."""

TEMPLATES = {"direct": DIRECT, "plan": PLAN, "decompose": DECOMPOSE}

REPAIR = """The query failed when it was executed.

Query:
```sql
{sql}
```

Error: {error}

Fix the query. Check table and column names against the schema exactly. Return the corrected
query in a ```sql code block."""

REPAIR_EMPTY = """The query ran but returned no rows. That can be correct, but it often means a
literal value is spelled differently in the database or a join/filter is too strict.

Query:
```sql
{sql}
```

{hints}If the query is right, return it unchanged; otherwise return a corrected query, in a
```sql code block."""


def build_messages(style: str, *, dialect: str, schema_text: str, question: str,
                   evidence: str = "", hints: str = "", variant_note: str = "") -> list[dict]:
    template = TEMPLATES.get(style, DIRECT)
    user = template.format(
        schema=schema_text,
        hints=(hints + "\n\n") if hints else "",
        evidence=(f"Hint from the user: {evidence}\n\n" if evidence else ""),
        dialect_notes=DIALECT_NOTES.get(dialect, ""),
        question=question,
    )
    system = SYSTEM.format(dialect=dialect)
    if variant_note:
        system += " " + variant_note
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
