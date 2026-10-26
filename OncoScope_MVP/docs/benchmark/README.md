# Extraction benchmark results

Reproduce: `cd backend; python -m app.benchmark` (main corpus) and `python -m app.benchmark --holdout`. Both exit non-zero
if a gate fails. Corpus and ground truth: `fixtures/benchmark/` (fictional).

| Run | Docs | Precision (issue-free) | Recall (complete) | Ambiguity kept visible | Anchor accuracy | Silent errors | Report |
|---|---|---|---|---|---|---|---|
| v1 on main corpus (baseline) | 26 | 90.9% | 76.9% (10/13) | 55.6% | 100% | **1** | `BASELINE_ca125-lab_v1.md` |
| v2.1 on main corpus | 26 | 100% | 100% (13/13) | 100% (9/9) | 100% | **0** | `BENCHMARK_ca125-lab_v2.1.md` |
| v2 on held-out (first and only blind run) | 8 | 66.7% | 28.6% | 50% | 100% | **1** | `HOLDOUT_ca125-lab_v2.md` |
| v2.1 on held-out (after two safety rules) | 8 | 100% | 28.6% | 100% | 100% | **0** | `HOLDOUT_ca125-lab_v2.1.md` |

How to read this honestly:
- The main corpus and the parser were written by the same developer; 100% there shows the rules cover the patterns they
  were written for, not that they generalise.
- The held-out set was written after v2 was frozen (commit `54161a5`). Its blind result — 28.6% recall and one silently
  wrong value — is the better estimate of behaviour on unfamiliar layouts. Two **safety** rules (dotted thousands
  separators, glued test names) were then added in v2.1; the held-out set is no longer blind for those two rules. No
  recall-oriented change was made from held-out results.
- On unfamiliar layouts the parser mostly *flags* rather than *fails silently*: every held-out statement reaches the review
  queue, and nothing wrong is accepted without a person.
- System run (API, throwaway database): proposals are not duplicated on re-run, documents without a text layer are refused
  as unsupported, every issue-free proposal accepted by the most permissive reviewer matches ground truth, every accepted
  record's anchor re-verifies against the document text, and all expected cross-document findings are reported.
