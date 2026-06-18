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

Everything is organized into one folder per regime — `static_prompting` (zero-shot),
`agentic`, and `swe_agent` — across `scripts/`, `data/`, `prompts/`, and `results/`.

```
scripts/
  static_prompting/           # zero-shot regime
    zero_shot_eval.py         # zero-shot runner — single API call per function
    extract_context.py        # builds the zero-shot context (tree-sitter)
  agentic/                    # agentic regime
    claude_code_eval.py       # spawns `claude -p` per function
    codex_eval.py             # spawns `codex` per function
  shared/
    score.py                  # metrics: MCC, AUC-ROC, macro-F1, accuracy (all regimes)
data/                         # inputs, one folder per regime (+ master label sheet)
  benchmark_labeled.csv       # master: all entries + outcome/class/explanation labels
  static_prompting/
    benchmark.jsonl           # 187 functions (project/repo/commit/file/line/function), no labels
    benchmark_with_context.jsonl  # benchmark + extracted context (zero-shot input)
    ground_truth.csv          # NT/T labels, keyed by (file, function) — used by score.py
  agentic/
    benchmark.jsonl
    ground_truth.csv
    expert_review_claude_code_opus_4_7.csv  # human-expert curation of Claude Code predictions
  swe_agent/
    benchmark.jsonl           # benchmark variant for the SWE-agent regime
    ground_truth.csv
prompts/
  static_prompting/           # zero-shot prompts
    zero_shot_system.txt
    zero_shot_user.txt
  agentic/                    # agentic prompt (shared by claude_code_eval & codex_eval)
    agentic.txt
results/                      # eval outputs (one JSON line per function + `prediction`)
  static_prompting/
    zeroshot_claude_opus_4_7.jsonl
    zeroshot_gpt_5_5.jsonl
  agentic/
    agentic_claude_code_opus_4_7.jsonl
    agentic_codex_gpt_5_5.jsonl
  swe_agent/                  # SWE-agent regime — raw per-function *_success/_failure.json
    claude_code_opus_4.7/outputs/     # per-function dialogs + halteval_pro.py scorer
    qwen3_32b/outputs/                # per-function dialogs + halteval_pro.py scorer
repos/                        # bare git-clone cache (gitignored, recreated by the evals)
```

> `ground_truth.csv` is duplicated into each regime folder; keep the copies in sync if labels change.

## Pipeline

```
data/agentic/benchmark.jsonl
   ├─► scripts/agentic/claude_code_eval.py ─► results/agentic/agentic_claude_code_<model>.jsonl
   └─► scripts/agentic/codex_eval.py       ─► results/agentic/agentic_codex_<model>.jsonl

data/static_prompting/benchmark.jsonl
   └─► scripts/static_prompting/extract_context.py ─► data/static_prompting/benchmark_with_context.jsonl
          └─► scripts/static_prompting/zero_shot_eval.py ─► results/static_prompting/zeroshot_<model>.jsonl

results/**/<any>.jsonl + data/<regime>/ground_truth.csv ─► scripts/shared/score.py ─► MCC / AUC-ROC / macro-F1 / accuracy
```

Output files are named cleanly by default (e.g. `agentic/agentic_claude_code_opus_4_7.jsonl`);
a timestamp is appended only if a file of that name already exists, so re-runs never overwrite.

## Usage (run from the repo root)

```bash
# Agentic runs
python scripts/agentic/claude_code_eval.py --model claude-opus-4-7 --effort high
python scripts/agentic/codex_eval.py       --model gpt-5.5

# Zero-shot / static prompting
python scripts/static_prompting/extract_context.py          # build context once
python scripts/static_prompting/zero_shot_eval.py --model <model-id>

# Score any result file against its regime ground truth
python scripts/shared/score.py results/agentic/agentic_claude_code_opus_4_7.jsonl data/agentic/ground_truth.csv
```

## Results (n = 187)

Sorted by **AUC-ROC (primary metric)**. Class balance: 57 NT / 130 T (30.5 % positive).

| Setup | Script | AUC-ROC | MCC | Macro-F1 | Accuracy |
|---|---|---|---|---|---|
| Claude Code, Opus 4.7 (agentic) | `claude_code_eval.py` | **0.911** | 0.674 | 0.828 | 0.866 |
| Claude Code, Opus 4.7 (SWE-agent)¹ | `halteval_pro.py`     | 0.909 | **0.687** | **0.838** | **0.872** |
| Claude Opus 4.7 (zero-shot)     | `zero_shot_eval.py`   | 0.901 | 0.661 | 0.818 | 0.861 |
| Codex GPT-5.5 (agentic)         | `codex_eval.py`       | 0.843 | 0.455 | 0.674 | 0.679 |
| GPT-5.5 (zero-shot)             | `zero_shot_eval.py`   | 0.835 | 0.492 | 0.708 | 0.717 |
| Qwen3-32B (SWE-agent)²          | `halteval_pro.py`     | 0.552 | 0.202 | 0.512 | 0.701 |

¹ **Claude Code, Opus 4.7**, SWE-agent regime. Returned a verdict on **all 187 / 187** functions
(0 missing). Scored from the raw per-function `*_success.json` dialogs in
`results/swe_agent/claude_code_opus_4.7/outputs/` via `halteval_pro.py` against
`data/swe_agent/benchmark.jsonl`; confusion matrix NT/T = (TN 39, FP 18, FN 6, TP 124).

² **Qwen3-32B**, SWE-agent regime. Returned a usable NT/T verdict on **184 / 187** functions —
**2** had no parseable prediction and **1** echoed the template placeholder `<T|NT>`; all three
count as wrong (accuracy is over all 187). Scored from the per-function dialogs in
`results/swe_agent/qwen3_32b/outputs/` via `halteval_pro.py` against `data/swe_agent/benchmark.jsonl`;
AUC is over the **185** cases carrying a `p_non_terminating` score.

### Per-class precision / recall / F1

| Setup | NT&nbsp;P | NT&nbsp;R | NT&nbsp;F1 | T&nbsp;P | T&nbsp;R | T&nbsp;F1 |
|---|---|---|---|---|---|---|
| Claude Code, Opus 4.7 (SWE-agent)¹ | 0.867 | 0.684 | 0.765 | 0.873 | 0.954 | 0.912 |
| Claude Code, Opus 4.7 (agentic) | 0.881 | 0.649 | 0.747 | 0.862 | 0.962 | 0.909 |
| Claude Opus 4.7 (zero-shot)     | 0.897 | 0.614 | 0.729 | 0.851 | 0.969 | 0.906 |
| Codex GPT-5.5 (agentic)         | 0.486 | 0.912 | 0.634 | 0.938 | 0.577 | 0.714 |
| GPT-5.5 (zero-shot)             | 0.520 | 0.895 | 0.658 | 0.933 | 0.638 | 0.758 |
| Qwen3-32B (SWE-agent)²          | 0.700 | 0.123 | 0.209 | 0.713 | 0.954 | 0.816 |

**AUC-ROC is the primary metric** — it is computed from the model's
`p_non_terminating` score (NT = positive class) and, unlike accuracy or MCC, is both
threshold-independent and robust to the 30/70 NT/T class imbalance. MCC is reported as
a secondary single-number summary of the thresholded labels; Macro-F1 is the unweighted
mean of the NT and T F1. **NT** = non-terminating, **T** = terminating.

### Conclusions

- **Claude Code Opus 4.7 in the SWE-agent setup leads on the thresholded metrics.**
  Its AUC (0.909) is essentially tied with the agentic run (0.911), and it edges ahead on
  MCC (0.687), Macro-F1 (0.838) and accuracy (0.872) while being the only run to return a
  verdict on all 187 / 187 functions. Notably it lifts NT recall to 0.684 (vs 0.649 agentic)
  without sacrificing NT precision (0.867) — a better operating point on the minority class.
- **Claude Opus 4.7 ≫ GPT-5.5 on discrimination.** The AUC gap is ~0.06–0.07 in both
  regimes (0.911 / 0.901 vs 0.843 / 0.835). The score from Claude separates terminating
  from non-terminating functions substantially better.
- **Agentic edges zero-shot on AUC for both families** (Claude 0.911 vs 0.901; GPT 0.843
  vs 0.835). Letting the model fetch the exact code yields slightly better-*ranked*
  confidence, though the margin is small (~0.01).
- **AUC and MCC disagree on GPT, and that is informative.** By thresholded labels GPT's
  zero-shot beats its agentic run (MCC 0.492 vs 0.455), yet by ranking quality agentic
  wins (AUC 0.843 vs 0.835). The agentic scores are better *ordered* even when the hard
  T/NT decisions are not — i.e. there is headroom from threshold tuning, not from the
  scores themselves.
- **All four frontier models rank well (AUC ≥ 0.84) but threshold poorly on the minority class.**
  Claude pairs a high AUC with low NT recall (0.61–0.65): the separating score is strong,
  but the operating point is set too conservatively to flag NT. GPT shows the opposite
  bias — high NT recall (0.90–0.91) at the cost of many false positives (NT precision
  0.48–0.52). Both confirm that the weak per-class numbers stem from where the decision
  boundary sits, not from poor ranking — which is exactly why AUC-ROC is the fairer
  headline metric here.
- **Qwen3-32B (SWE-agent) barely beats the majority-class baseline.** Its 70.1 % accuracy is
  only a hair above always-guessing-`T` (130 / 187 = 69.5 %), because that is essentially
  what it does: it predicts `T` almost everywhere and **almost never commits to `NT`**
  (only 10 NT predictions for 57 true NT cases → NT recall 0.123). Interestingly, when it
  *does* flag `NT` it is usually right — **7 / 10 correct** (NT precision 0.70) — so the
  problem is missed non-termination, not noisy false alarms. With AUC 0.552 its
  `p_non_terminating` score hardly orders functions better than random (it also failed to
  emit a usable verdict on 3 / 187 functions). This gap underscores that the benchmark is
  genuinely hard: Qwen3-32B is **far behind Claude Opus 4.7 and GPT-5.5**, which both detect
  non-termination far more reliably (NT recall 0.61–0.91, AUC ≥ 0.84).

### Validation

The four frontier-model result files carry a `p_non_terminating` score for every one of the
187 cases (187 matched, 0 unmatched), and all reported numbers reproduce exactly from
`scripts/shared/score.py`. AUC-ROC was additionally cross-checked with an independent rank-based
(trapezoidal ROC) computation, and MCC and the per-class precision/recall against a direct
confusion-matrix calculation — all agree to 4 decimals. The Claude Code Opus 4.7 SWE-agent
row was reproduced end-to-end with `halteval_pro.py --jsonl data/swe_agent/benchmark.jsonl`
(187 matched, 0 missing) and the accuracy / MCC / AUC-ROC / per-class numbers reproduce exactly.
The Qwen3-32B SWE-agent row was scored the same way (185 files matched; 2 with no prediction and
1 placeholder counted as wrong; AUC over the 185 cases carrying a score).

> Note: `scripts/shared/score.py` uses `X | None` type syntax and requires Python ≥ 3.10
> (run with e.g. `python3.12`).
