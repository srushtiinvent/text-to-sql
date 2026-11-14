# Bug journal

Every time an eval run or a test exposes a failure, add an entry. This file is the evidence that you understand the system: what broke, why, and how you know the fix worked.

Template:

```
## YYYY-MM-DD — short title
**Symptom:** what you saw (question id, numbers, error)
**Root cause:** why it happened
**Fix:** what changed (commit)
**Proof:** the test or metric that shows it's fixed (before → after)
```

The first two entries come from the initial build, as examples of the format. Replace them with your own as you go.

---

## Example — multiple statements misclassified as a database error
**Symptom:** `SELECT 1; DELETE FROM orders` was rejected (good) but reported as `kind="db"`, so the generator sent it back for "repair".
**Root cause:** on Python 3.12+, `sqlite3` raises `ProgrammingError` ("You can only execute one statement at a time") instead of `sqlite3.Warning`, so it fell into the generic DB-error branch.
**Fix:** classify that message as `kind="guard"`, which ends the candidate without a repair round.
**Proof:** `tests/test_safety.py::test_writes_are_rejected` covers the multi-statement case with the static guard on and off.

## Example — gateway cache could collapse candidate diversity
**Symptom (anticipated):** two candidates with the same model and prompt but temperature 0.7 return identical SQL.
**Root cause:** the gateway caches by conversation; identical messages give a cache hit regardless of sampling.
**Fix:** add an "Independent sample #n" note to the system prompt when a (model, prompt) pair repeats.
**Proof:** check `unanimous_rate` and `avg_clusters` in a real run before and after.
