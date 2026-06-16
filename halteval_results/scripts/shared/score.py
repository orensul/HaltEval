#!/usr/bin/env python3
"""
Evaluation metrics for halting-problem predictions.

Primary metric : AUC-ROC (from p_non_terminating; threshold- and prevalence-independent)
Secondary      : MCC (Matthews Correlation Coefficient), Macro-F1

Usage (run from the repo root):
    python scripts/shared/score.py <predictions.jsonl> <ground_truth.csv>
    python scripts/shared/score.py results/agentic/agentic_claude_code_opus_4_7.jsonl data/agentic/ground_truth.csv
"""

import csv
import json
import math
import sys


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_ground_truth(csv_path: str) -> dict:
    """Return {(file:line, function): 'NT'|'T'}"""
    gt = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["file"].strip(), row["function"].strip())
            gt[key] = row["outcome"].strip()
    return gt


def load_predictions(jsonl_path: str) -> list[dict]:
    """
    Return list of dicts with keys:
      file_line, function, label, p_non_terminating (float 0-1, or None)
    Normalises label variants (non_terminating -> non-terminating).
    """
    preds = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            file_line = f"{d['file']}:{d['line']}"
            func      = d["function"]
            pred      = d.get("prediction", {})

            # Normalise label
            label = pred.get("label", "")
            label = label.replace("non_terminating", "non-terminating")

            # Extract soft score (0-100 integer -> 0.0-1.0 float)
            raw_p = pred.get("p_non_terminating")
            try:
                p = float(raw_p) / 100.0 if raw_p is not None else None
                p = max(0.0, min(1.0, p)) if p is not None else None
            except (TypeError, ValueError):
                p = None

            preds.append({
                "file_line": file_line,
                "function":  func,
                "label":     label,
                "p":         p,
            })
    return preds


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

LABEL_MAP = {"non-terminating": "NT", "terminating": "T"}
CLASSES   = ["NT", "T"]


def confusion(gt: dict, preds: list) -> tuple[dict, dict, dict, int, int]:
    tp = {c: 0 for c in CLASSES}
    fp = {c: 0 for c in CLASSES}
    fn = {c: 0 for c in CLASSES}
    matched = unmatched = 0

    for p in preds:
        key = (p["file_line"], p["function"])
        if key not in gt:
            unmatched += 1
            continue
        pred_class = LABEL_MAP.get(p["label"], p["label"])
        if pred_class not in CLASSES:
            unmatched += 1
            continue
        matched += 1
        true_class = gt[key]
        if pred_class == true_class:
            tp[true_class] += 1
        else:
            fp[pred_class] += 1
            fn[true_class] += 1

    return tp, fp, fn, matched, unmatched


def mcc(tp: dict, fp: dict, fn: dict) -> float:
    """Binary MCC computed from the NT/T confusion matrix."""
    # TP, TN, FP, FN from NT's perspective
    TP = tp["NT"]
    TN = tp["T"]
    FP = fp["NT"]   # predicted NT, actually T
    FN = fn["NT"]   # predicted T,  actually NT

    num = TP * TN - FP * FN
    den = math.sqrt((TP + FP) * (TP + FN) * (TN + FP) * (TN + FN))
    return num / den if den > 0 else 0.0


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def auc_roc(gt: dict, preds: list) -> float | None:
    """
    Compute AUC-ROC from p_non_terminating scores.
    NT is the positive class.
    Returns None if no predictions have a soft score.
    """
    scored = [
        (p["p"], gt.get((p["file_line"], p["function"])))
        for p in preds
        if p["p"] is not None and (p["file_line"], p["function"]) in gt
    ]
    if not scored:
        return None

    # Wilcoxon-Mann-Whitney statistic (equivalent to AUC, O(n^2) but n<=189)
    positives = [score for score, label in scored if label == "NT"]
    negatives = [score for score, label in scored if label == "T"]

    if not positives or not negatives:
        return None

    n_pos, n_neg = len(positives), len(negatives)
    n_concordant = sum(
        1 for pos in positives for neg in negatives if pos > neg
    ) + sum(
        0.5 for pos in positives for neg in negatives if pos == neg
    )
    return n_concordant / (n_pos * n_neg)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(gt: dict, preds: list) -> None:
    tp, fp, fn, matched, unmatched = confusion(gt, preds)

    print(f"Matched: {matched}  |  Unmatched (no ground truth): {unmatched}")
    print()

    # Per-class precision / recall / F1, plus macro averages.
    metrics  = {c: prf(tp[c], fp[c], fn[c]) for c in CLASSES}   # c -> (P, R, F1)
    macro_p  = sum(v[0] for v in metrics.values()) / len(CLASSES)
    macro_r  = sum(v[1] for v in metrics.values()) / len(CLASSES)
    macro_f1 = sum(v[2] for v in metrics.values()) / len(CLASSES)
    total_tp = sum(tp.values())

    # ── Headline metrics ────────────────────────────────────────────────────
    auc = auc_roc(gt, preds)
    if auc is not None:
        n_scored = sum(1 for p in preds if p["p"] is not None
                       and (p["file_line"], p["function"]) in gt)
        print(f"AUC-ROC  : {auc:.4f}   (primary metric; from p_non_terminating scores, n={n_scored}; 0.5 = random, 1 = perfect)")
    else:
        print("AUC-ROC  : n/a   (no p_non_terminating field in predictions)")

    print(f"MCC      : {mcc(tp, fp, fn):.4f}   (−1 = perfectly wrong, 0 = random, +1 = perfect)")
    print(f"Macro-F1 : {macro_f1:.4f}   (unweighted mean of NT and T F1)")
    if matched:
        print(f"Accuracy : {total_tp/matched:.4f}   ({total_tp}/{matched})")
    print()

    # ── Per-class precision / recall / F1 ───────────────────────────────────
    col    = 11
    header = (f"{'Class':<16} {'TP':>4} {'FP':>4} {'FN':>4} "
              f"{'Precision':>{col}} {'Recall':>{col}} {'F1':>{col}}")
    rule   = "-" * len(header)
    names  = {"NT": "NT (non-term)", "T": "T (terminating)"}

    print(header)
    print(rule)
    for c in CLASSES:
        p_val, r_val, f1_val = metrics[c]
        print(f"{names[c]:<16} {tp[c]:>4} {fp[c]:>4} {fn[c]:>4} "
              f"{p_val:>{col}.3f} {r_val:>{col}.3f} {f1_val:>{col}.3f}")
    print(rule)
    print(f"{'macro avg':<16} {'':>4} {'':>4} {'':>4} "
          f"{macro_p:>{col}.3f} {macro_r:>{col}.3f} {macro_f1:>{col}.3f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    predictions_path, gt_path = sys.argv[1], sys.argv[2]
    gt    = load_ground_truth(gt_path)
    preds = load_predictions(predictions_path)

    # Attach path for display
    for p in preds:
        p["_path"] = predictions_path

    print(f"Predictions : {predictions_path}")
    print(f"Ground truth: {gt_path}")
    print_report(gt, preds)


if __name__ == "__main__":
    main()
