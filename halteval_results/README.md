# HaltEval Pro v1 — termination analysis evaluation

Evaluates LLMs on **halting/termination analysis** of real-world C/C++ functions:
given a function (at a specific repo + commit), decide whether it is
**terminating** (`T`) or **non-terminating** (`NT`).

Two regimes are supported:

- **Agentic** — the model runs as a CLI agent (Claude Code / Codex), does its own
  `git` work to fetch the code at the exact commit, then analyzes it.
- **Zero-shot** — the model gets a pre-extracted context (function body + full
  source file + project-local headers) and answers in a single pass, with no tools.

## Layout

```
scripts/                      # all code
  claude_code_eval.py         # agentic runner — spawns `claude -p` per function
  codex_eval.py               # agentic runner — spawns `codex` per function
  zero_shot_eval.py           # zero-shot runner — single API call per function
  extract_context.py          # builds the zero-shot context (tree-sitter)
  score.py                    # metrics: MCC, AUC-ROC, macro-F1, accuracy
data/                         # inputs
  benchmark.jsonl             # 189 functions (project/repo/commit/file/line/function), no labels
  benchmark_labeled.csv       # same entries + outcome/class/explanation labels
  benchmark_with_context.jsonl# benchmark + extracted context (zero-shot input)
  ground_truth.csv            # NT/T labels, keyed by (file, function) — used by score.py
  expert_review_claude_code_opus_4_7.csv  # human-expert curation of Claude Code predictions
results/                      # eval outputs (one JSON line per function + `prediction`)
  agentic_claude_code_opus_4_7.jsonl
  agentic_codex_gpt_5_5.jsonl
  zeroshot_claude_opus_4_7.jsonl
  zeroshot_gpt_5_5.jsonl
repos/                        # bare git-clone cache (gitignored, recreated by the evals)
```

## Pipeline

```
data/benchmark.jsonl
   ├─► scripts/claude_code_eval.py ─► results/agentic_claude_code_<model>.jsonl
   ├─► scripts/codex_eval.py       ─► results/agentic_codex_<model>.jsonl
   └─► scripts/extract_context.py  ─► data/benchmark_with_context.jsonl
                                          └─► scripts/zero_shot_eval.py ─► results/zeroshot_<model>.jsonl

results/<any>.jsonl + data/ground_truth.csv ─► scripts/score.py ─► MCC / AUC-ROC / macro-F1 / accuracy
```

Output files are named cleanly by default (e.g. `agentic_claude_code_opus_4_7.jsonl`);
a timestamp is appended only if a file of that name already exists, so re-runs never overwrite.

## Usage (run from the repo root)

```bash
# Agentic runs
python scripts/claude_code_eval.py --model claude-opus-4-7 --effort high
python scripts/codex_eval.py       --model gpt-5.5

# Zero-shot (build context once, then run any model)
python scripts/extract_context.py
python scripts/zero_shot_eval.py   --model <model-id>

# Score any result file against ground truth
python scripts/score.py results/agentic_claude_code_opus_4_7.jsonl data/ground_truth.csv
```

## Results (n = 189)

Sorted by **AUC-ROC (primary metric)**. Class balance: 57 NT / 132 T (30.2 % positive).

| Setup | Script | AUC-ROC | MCC | Macro-F1 | Accuracy |
|---|---|---|---|---|---|
| Claude Code, Opus 4.7 (agentic) | `claude_code_eval.py` | **0.912** | **0.675** | **0.829** | **0.868** |
| Claude Opus 4.7 (zero-shot)     | `zero_shot_eval.py`   | 0.902 | 0.662 | 0.819 | 0.862 |
| Codex GPT-5.5 (agentic)         | `codex_eval.py`       | 0.844 | 0.453 | 0.672 | 0.677 |
| GPT-5.5 (zero-shot)             | `zero_shot_eval.py`   | 0.837 | 0.488 | 0.705 | 0.714 |

### Per-class precision / recall / F1

| Setup | NT&nbsp;P | NT&nbsp;R | NT&nbsp;F1 | T&nbsp;P | T&nbsp;R | T&nbsp;F1 |
|---|---|---|---|---|---|---|
| Claude Code, Opus 4.7 (agentic) | 0.881 | 0.649 | 0.747 | 0.864 | 0.962 | 0.910 |
| Claude Opus 4.7 (zero-shot)     | 0.897 | 0.614 | 0.729 | 0.853 | 0.970 | 0.908 |
| Codex GPT-5.5 (agentic)         | 0.481 | 0.912 | 0.630 | 0.938 | 0.576 | 0.714 |
| GPT-5.5 (zero-shot)             | 0.515 | 0.895 | 0.654 | 0.933 | 0.636 | 0.757 |

**AUC-ROC is the primary metric** — it is computed from the model's
`p_non_terminating` score (NT = positive class) and, unlike accuracy or MCC, is both
threshold-independent and robust to the 30/70 NT/T class imbalance. MCC is reported as
a secondary single-number summary of the thresholded labels; Macro-F1 is the unweighted
mean of the NT and T F1. **NT** = non-terminating, **T** = terminating.

### Conclusions

- **Claude Opus 4.7 ≫ GPT-5.5 on discrimination.** The AUC gap is ~0.06–0.07 in both
  regimes (0.912 / 0.902 vs 0.844 / 0.837). The score from Claude separates terminating
  from non-terminating functions substantially better.
- **Agentic edges zero-shot on AUC for both families** (Claude 0.912 vs 0.902; GPT 0.844
  vs 0.837). Letting the model fetch the exact code yields slightly better-*ranked*
  confidence, though the margin is small (~0.01).
- **AUC and MCC disagree on GPT, and that is informative.** By thresholded labels GPT's
  zero-shot beats its agentic run (MCC 0.488 vs 0.453), yet by ranking quality agentic
  wins (AUC 0.844 vs 0.837). The agentic scores are better *ordered* even when the hard
  T/NT decisions are not — i.e. there is headroom from threshold tuning, not from the
  scores themselves.
- **All models rank well (AUC ≥ 0.84) but threshold poorly on the minority class.**
  Claude pairs a high AUC with low NT recall (0.61–0.65): the separating score is strong,
  but the operating point is set too conservatively to flag NT. GPT shows the opposite
  bias — high NT recall (0.90–0.91) at the cost of many false positives (NT precision
  0.48–0.52). Both confirm that the weak per-class numbers stem from where the decision
  boundary sits, not from poor ranking — which is exactly why AUC-ROC is the fairer
  headline metric here.

### Validation

All four result files carry a `p_non_terminating` score for every one of the 189 cases,
and all reported numbers reproduce exactly from `scripts/score.py`. AUC-ROC was
additionally cross-checked with an independent rank-based (trapezoidal ROC) computation,
and MCC and the per-class precision/recall against a direct confusion-matrix calculation
— all agree to 4 decimals. One benign data note: `sqlite/tool/lemon.c:3563 / tplt_print`
is duplicated in both the ground truth and every prediction file with an identical
outcome (`T`), so it is counted twice; this has no effect on any metric.

> Note: `scripts/score.py` uses `X | None` type syntax and requires Python ≥ 3.10
> (run with e.g. `python3.12`).
