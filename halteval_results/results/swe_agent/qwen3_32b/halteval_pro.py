import argparse
import json
import math
import os
import re
from pathlib import Path
from collections import defaultdict


def normalize_function_name(function_name):
    """
    Normalize C++ function names by replacing :: with _
    This ensures consistency between ground truth (which may use ::)
    and filenames (which must use _)

    Example: CryptoPP::AlignedAllocate -> CryptoPP_AlignedAllocate
             X::Y::Z -> X_Y_Z
    """
    if function_name is None:
        return None
    return function_name.replace("::", "_")


def parse_filename_to_components(filename, projects):
    """
    Parse filename to extract project, commit, file, function, and line.
    `projects` is a list of known project names sorted longest-first so the
    correct project prefix is matched even when one project name is a prefix
    of another (e.g. "bind" vs "bind_extra").

    Example: FreeImage_8268e809_jdsample_c_int_upsample_211_success.json
    Returns: {
        'project': 'FreeImage',
        'commit': '8268e809',
        'file': 'jdsample_c',
        'function': 'int_upsample',
        'line': '211'
    }
    """
    # Remove suffix — partial_success before success to avoid partial match
    clean_name = (
        filename.replace("_partial_success.json", "")
        .replace("_success.json", "")
        .replace("_failure.json", "")
    )

    # Find which project this filename belongs to (longest match first)
    matched_project = None
    for proj in projects:
        if clean_name.startswith(proj + "_"):
            matched_project = proj
            break

    if matched_project is None:
        return None

    # Strip project prefix
    remainder = clean_name[len(matched_project):].lstrip("_")
    parts = remainder.split("_")

    # commit is first, line is last, everything in between is file path + function
    commit = parts[0]
    line = parts[-1]
    middle_parts = parts[1:-1]

    # Find where file extension is (like _c, _cpp, _py, _java, _h, etc.)
    # Use the LAST occurrence so that extensions in directory names are skipped
    # (e.g. completion_c/completion.c → last 'c' is the actual file extension)
    file_extensions = {
        "c", "cpp", "cc", "h", "hpp", "py", "java", "js", "go", "rs", "rb",
        "php", "y", "l", "s",
    }
    file_end_idx = -1
    for i, part in enumerate(middle_parts):
        if part in file_extensions:
            file_end_idx = i  # keep updating to get the last occurrence

    if file_end_idx == -1:
        return None

    # File path is everything up to and including the extension
    file_parts = middle_parts[:file_end_idx]
    extension = middle_parts[file_end_idx]
    file_path = "_".join(file_parts) + "_" + extension

    # Function name is everything after the file extension
    function_parts = middle_parts[file_end_idx + 1:]
    function = "_".join(function_parts).strip()

    return {
        "project": matched_project,
        "commit": commit,
        "file": file_path,
        "function": function,
        "line": line,
    }


def create_lookup_key(project, function):
    """Create lookup key in format: project#function.
    File path and line are excluded because the forager may truncate path
    prefixes differently per dataset, and line numbers in filenames can
    differ from those stored in the JSONL.
    """
    return f"{project}#{function}"


def extract_prediction(content, filename=None, missing_predictions_list=None):
    """Extract prediction label and p_non_terminating score from dialog content."""
    if "prediction" not in content:
        if missing_predictions_list is not None and filename:
            missing_predictions_list.append(
                {
                    "filename": filename,
                    "reason": "no_prediction_keyword",
                    "content": content,
                }
            )
        return None, None

    label = None
    try:
        match = re.search(r'"prediction"\s*:\s*"([^"]+)"', content)
        if match:
            label = match.group(1)
        else:
            if missing_predictions_list is not None and filename:
                missing_predictions_list.append(
                    {
                        "filename": filename,
                        "reason": "prediction_parse_failed",
                        "content": content,
                    }
                )
    except AttributeError:
        pass

    p_score = None
    # Match the last actual numeric value, skipping placeholders like "<0-100>"
    for m in re.finditer(r'p_non_terminating\\?"\s*:\s*"?(\d+(?:\.\d+)?)"?', content):
        try:
            p_score = float(m.group(1))
        except ValueError:
            continue

    return label, p_score


def read_jsonl_ground_truth(jsonl_path):
    """
    Reads JSONL file and creates a mapping from lookup_key to golden_outcome.
    Lookup key format: project#function
    """
    ground_truth = {}
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())
                    project = data.get("project", "").strip()
                    file_path = data.get("file", "")
                    function = data.get("function", "").strip()
                    golden_outcome = data.get("golden_outcome")

                    # Normalize whitespace and C++ namespace separators
                    function = " ".join(function.split())
                    function = normalize_function_name(function)

                    lookup_key = create_lookup_key(project, function)
                    if lookup_key in ground_truth:
                        print(f"⚠️  Duplicate key in ground truth: {lookup_key}")
                    ground_truth[lookup_key] = {
                        "outcome": golden_outcome,
                        "file": file_path,
                    }
                except json.JSONDecodeError as e:
                    print(f"Error decoding line {line_num} in JSONL: {e}")

        print(f"Loaded {len(ground_truth)} ground truth entries from JSONL")
    except IOError as e:
        print(f"Error reading JSONL file: {e}")

    return ground_truth


def read_success_json_files_with_ground_truth(directory=".", jsonl_path=None):
    """
    Reads all JSON files ending with '_success.json' or '_partial_success.json'
    and merges with ground truth.

    Projects are auto-detected from the JSONL.
    Lookup key is project#function (file path and line excluded due to
    cross-dataset inconsistencies in how the forager encodes them).
    """
    result = []
    missing_predictions_debug = []
    no_dialog_count = 0

    ground_truth = {}
    if jsonl_path:
        ground_truth = read_jsonl_ground_truth(jsonl_path)

    directory_path = Path(directory)

    success_files = list(directory_path.glob("*_success.json"))
    partial_success_files = list(directory_path.glob("*_partial_success.json"))
    failure_files = list(directory_path.glob("*_failure.json"))
    all_files = success_files + partial_success_files + failure_files

    print(f"Found {len(success_files)} files ending with '_success.json'")
    print(f"Found {len(partial_success_files)} files ending with '_partial_success.json'")
    print(f"Found {len(failure_files)} files ending with '_failure.json'")
    print(f"Total: {len(all_files)} files")
    print(f"Ground truth has {len(ground_truth)} entries\n")

    # Build sorted project list from JSONL (longest first to avoid prefix collisions)
    if ground_truth:
        unique_projects = set(key.split("#")[0] for key in ground_truth)
        all_projects = sorted(unique_projects, key=len, reverse=True)
        print(f"Auto-detected projects: {all_projects}\n")
    else:
        all_projects = []

    missing_count = 0
    matched_count = 0
    parse_failed = 0

    for file_path in all_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            components = parse_filename_to_components(file_path.name, all_projects)

            if not components:
                parse_failed += 1
                if parse_failed <= 20:
                    print(f"⚠️  Failed to parse filename: {file_path.name}\n")
                continue

            normalized_function = normalize_function_name(components["function"])
            lookup_key = create_lookup_key(components["project"], normalized_function)
            instance_id = lookup_key

            # Extract prediction from last dialog turn
            prediction = None
            p_non_terminating = None
            content = ""
            if "dialog" in data and len(data["dialog"]) > 0:
                last_dialog = data["dialog"][-1]
                content = last_dialog.get("content", "")
                prediction, p_non_terminating = extract_prediction(
                    content,
                    filename=file_path.name,
                    missing_predictions_list=missing_predictions_debug,
                )
            else:
                no_dialog_count += 1
                if no_dialog_count <= 10:
                    print(f"⚠️  NO DIALOG FOUND IN FILE: {file_path.name}")
                missing_predictions_debug.append(
                    {
                        "filename": file_path.name,
                        "reason": "no_dialog",
                        "content": "N/A - no dialog field in JSON",
                    }
                )

            outcome = None
            gt_file = None
            if lookup_key in ground_truth:
                outcome = ground_truth[lookup_key]["outcome"]
                gt_file = ground_truth[lookup_key]["file"]
                matched_count += 1
            else:
                missing_count += 1
                if missing_count <= 10:
                    print(f"⚠️  Missing ground truth")
                    print(f"   Filename: {file_path.name}")
                    print(f"   Lookup key: {lookup_key}")
                    print(f"   Components: {components}")
                    sample_keys = list(ground_truth.keys())[:3]
                    print(f"   Sample GT keys: {sample_keys}\n")

            result.append(
                {
                    instance_id: {
                        "prediction": prediction,
                        "p_non_terminating": p_non_terminating,
                        "outcome": outcome,
                        "filename": file_path.name,
                        "project": components["project"],
                        "commit": components["commit"],
                        "file": components["file"],
                        "gt_file": gt_file,
                        "function": normalized_function,
                        "line": components["line"],
                        "lookup_key": lookup_key,
                        "model_output": content,
                    }
                }
            )

        except json.JSONDecodeError as e:
            print(f"Error decoding JSON in {file_path.name}: {e}")
        except IOError as e:
            print(f"Error reading file {file_path.name}: {e}")

    print(f"\nProcessing summary:")
    print(f"  Matched: {matched_count}")
    print(f"  Missing: {missing_count}")
    print(f"  Parse failed: {parse_failed}")
    print(f"  No dialog found: {no_dialog_count}")

    if missing_predictions_debug:
        with open("missing_predictions_debug.json", "w", encoding="utf-8") as f:
            json.dump(missing_predictions_debug, f, indent=2, ensure_ascii=False)
        print(
            f"\n⚠️  Saved {len(missing_predictions_debug)} missing prediction examples to 'missing_predictions_debug.json'"
        )
        reason_counts = defaultdict(int)
        for item in missing_predictions_debug:
            reason_counts[item["reason"]] += 1
        print("\nMissing predictions breakdown:")
        for reason, count in reason_counts.items():
            print(f"  {reason}: {count}")

    return result


def calculate_accuracy_and_confusion_matrix(predictions_list, ground_truth):
    """
    Calculate accuracy metrics and confusion matrix from predictions list.
    Uses ground truth for recall denominators.
    """
    pred_map = {}
    content_map = {}
    score_map = {}
    for item in predictions_list:
        for instance, data in item.items():
            pred_map[instance] = data["prediction"]
            content_map[instance] = data.get("model_output", "")
            score_map[instance] = data.get("p_non_terminating")

    gt_map = {}
    for instance_id, gt_data in ground_truth.items():
        gt_map[instance_id] = gt_data["outcome"]

    confusion_matrix = defaultdict(int)
    missing_pred_instances = []
    match_instances = []
    mismatch_instances = []

    for instance_id, gt_class in gt_map.items():
        pred_class = pred_map.get(instance_id)
        if pred_class is None:
            confusion_matrix[("MISSING", gt_class)] += 1
            missing_pred_instances.append(instance_id)
            mismatch_instances.append(
                {
                    "instance": instance_id,
                    "prediction": None,
                    "ground_truth": gt_class,
                    "model_output": content_map.get(instance_id, ""),
                }
            )
        else:
            confusion_matrix[(pred_class, gt_class)] += 1
            if pred_class == gt_class:
                match_instances.append(
                    {
                        "instance": instance_id,
                        "prediction": pred_class,
                        "ground_truth": gt_class,
                    }
                )
            else:
                mismatch_instances.append(
                    {
                        "instance": instance_id,
                        "prediction": pred_class,
                        "ground_truth": gt_class,
                        "model_output": content_map.get(instance_id, ""),
                    }
                )

    real_labels = sorted(set(gt_map.values()))
    metrics = {}
    for label in real_labels:
        tp = confusion_matrix.get((label, label), 0)
        fp = sum(
            confusion_matrix.get((label, gt), 0) for gt in real_labels if gt != label
        )
        fn = sum(
            confusion_matrix.get((pred, label), 0)
            for pred in real_labels
            if pred != label
        )
        fn += confusion_matrix.get(("MISSING", label), 0)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = (
            2 * (precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0
        )
        metrics[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    total = len(gt_map)
    matches = sum(confusion_matrix.get((label, label), 0) for label in real_labels)
    mismatches = total - matches
    accuracy = matches / total * 100 if total > 0 else 0

    return {
        "total": total,
        "matches": matches,
        "mismatches": mismatches,
        "metrics": metrics,
        "confusion_matrix": dict(confusion_matrix),
        "missing_pred_instances": missing_pred_instances,
        "match_instances": match_instances,
        "mismatch_instances": mismatch_instances,
        "accuracy": accuracy,
        "score_map": score_map,
        "gt_map": gt_map,
    }


def compute_binary_metrics(gt_map, pred_map, score_map):
    """
    Compute AUC-ROC, MCC, Macro-F1, and per-class P/R/F1 for the binary
    NT vs T problem. MISSING predictions are excluded from MCC / per-class /
    Macro-F1 calculations (kept consistent with the inspiration script which
    only counts matched predictions).
    """
    classes = ["NT", "T"]
    tp = {c: 0 for c in classes}
    fp = {c: 0 for c in classes}
    fn = {c: 0 for c in classes}

    for instance_id, gt_class in gt_map.items():
        if gt_class not in classes:
            continue
        pred_class = pred_map.get(instance_id)
        if pred_class not in classes:
            continue
        if pred_class == gt_class:
            tp[gt_class] += 1
        else:
            fp[pred_class] += 1
            fn[gt_class] += 1

    def prf(tp_i, fp_i, fn_i):
        precision = tp_i / (tp_i + fp_i) if (tp_i + fp_i) else 0.0
        recall = tp_i / (tp_i + fn_i) if (tp_i + fn_i) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        return precision, recall, f1

    per_class = {c: prf(tp[c], fp[c], fn[c]) for c in classes}
    macro_p = sum(v[0] for v in per_class.values()) / len(classes)
    macro_r = sum(v[1] for v in per_class.values()) / len(classes)
    macro_f1 = sum(v[2] for v in per_class.values()) / len(classes)

    # Binary MCC, NT = positive
    TP = tp["NT"]
    TN = tp["T"]
    FP = fp["NT"]
    FN = fn["NT"]
    num = TP * TN - FP * FN
    den = math.sqrt((TP + FP) * (TP + FN) * (TN + FP) * (TN + FN))
    mcc_val = num / den if den > 0 else 0.0

    # AUC-ROC from p_non_terminating, NT = positive class
    scored = []
    for instance_id, gt_class in gt_map.items():
        if gt_class not in classes:
            continue
        score = score_map.get(instance_id)
        if score is None:
            continue
        scored.append((score, gt_class))

    positives = [s for s, lab in scored if lab == "NT"]
    negatives = [s for s, lab in scored if lab == "T"]
    if positives and negatives:
        concordant = sum(1 for p in positives for n in negatives if p > n)
        ties = sum(1 for p in positives for n in negatives if p == n)
        auc = (concordant + 0.5 * ties) / (len(positives) * len(negatives))
    else:
        auc = None

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "per_class": per_class,
        "macro_precision": macro_p,
        "macro_recall": macro_r,
        "macro_f1": macro_f1,
        "mcc": mcc_val,
        "auc_roc": auc,
        "n_scored": len(scored),
        "n_pos": len(positives),
        "n_neg": len(negatives),
    }


def print_binary_metrics(bm):
    print(f"\n{'=' * 80}")
    print("BINARY METRICS (NT vs T, MISSING excluded)")
    print(f"{'=' * 80}\n")

    if bm["auc_roc"] is not None:
        print(
            f"AUC-ROC  : {bm['auc_roc']:.4f}   "
            f"(from p_non_terminating, n={bm['n_scored']}; "
            f"NT={bm['n_pos']}, T={bm['n_neg']}; 0.5=random, 1=perfect)"
        )
    else:
        print("AUC-ROC  : n/a   (insufficient p_non_terminating scores)")

    print(f"MCC      : {bm['mcc']:.4f}   (-1 wrong, 0 random, +1 perfect)")
    print(f"Macro-F1 : {bm['macro_f1']:.4f}   (unweighted mean of NT and T F1)")
    print()

    col = 11
    header = (
        f"{'Class':<16} {'TP':>4} {'FP':>4} {'FN':>4} "
        f"{'Precision':>{col}} {'Recall':>{col}} {'F1':>{col}}"
    )
    rule = "-" * len(header)
    names = {"NT": "NT (non-term)", "T": "T (terminating)"}
    print(header)
    print(rule)
    for c in ["NT", "T"]:
        p_val, r_val, f1_val = bm["per_class"][c]
        print(
            f"{names[c]:<16} {bm['tp'][c]:>4} {bm['fp'][c]:>4} {bm['fn'][c]:>4} "
            f"{p_val:>{col}.3f} {r_val:>{col}.3f} {f1_val:>{col}.3f}"
        )
    print(rule)
    print(
        f"{'macro avg':<16} {'':>4} {'':>4} {'':>4} "
        f"{bm['macro_precision']:>{col}.3f} {bm['macro_recall']:>{col}.3f} "
        f"{bm['macro_f1']:>{col}.3f}"
    )
    print()


def print_confusion_matrix(confusion_matrix):
    """Print a formatted confusion matrix."""

    def safe_label(label):
        return label if label is not None else "MISSING"

    all_labels = set()
    for pred, gt in confusion_matrix.keys():
        all_labels.add(safe_label(pred))
        all_labels.add(safe_label(gt))
    labels = sorted(list(all_labels))
    if not labels:
        print("No data for confusion matrix")
        return

    print(f"\n{'=' * 80}")
    print(f"CONFUSION MATRIX")
    print(f"{'=' * 80}")
    print("\nRows = Ground Truth, Columns = Predicted\n")

    max_label_width = max(len(str(label)) for label in labels)
    col_width = max(max_label_width, 8)

    header_label = "GT / Pred"
    print(f"{header_label:<15}", end="")
    for label in labels:
        print(f"{label:>{col_width}}", end="  ")
    print()
    print("-" * (15 + (col_width + 2) * len(labels)))

    for gt_label in labels:
        print(f"{gt_label:<15}", end="")
        for pred_label in labels:
            count = confusion_matrix.get((pred_label, gt_label), 0)
            print(f"{count:>{col_width}}", end="  ")
        print()

    print()

    print(f"\n{'=' * 80}")
    print(f"PER-CLASS METRICS")
    print(f"{'=' * 80}\n")

    for label in labels:
        tp = confusion_matrix.get((label, label), 0)
        fp = sum(confusion_matrix.get((label, gt), 0) for gt in labels if gt != label)
        fn = sum(
            confusion_matrix.get((pred, label), 0) for pred in labels if pred != label
        )
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = (
            2 * (precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0
        )
        print(f"Class '{label}':")
        print(f"  Precision: {precision:.4f} ({tp}/{tp + fp})")
        print(f"  Recall:    {recall:.4f} ({tp}/{tp + fn})")
        print(f"  F1-Score:  {f1:.4f}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Evaluate forager predictions against ground truth."
    )
    parser.add_argument(
        "--jsonl",
        required=True,
        help="Path to the ground-truth JSONL file",
    )
    args = parser.parse_args()

    predictions_list = read_success_json_files_with_ground_truth(".", args.jsonl)
    ground_truth = read_jsonl_ground_truth(args.jsonl)

    print(f"\n{'=' * 80}")
    print(f"Successfully loaded {len(predictions_list)} files")
    print(f"{'=' * 80}\n")

    stats = calculate_accuracy_and_confusion_matrix(predictions_list, ground_truth)

    if stats["match_instances"]:
        print(f"\n{'=' * 80}")
        print(f"MATCHED INSTANCES ({len(stats['match_instances'])})")
        print(f"{'=' * 80}\n")
        for item in stats["match_instances"]:
            print(
                f"✅ {item['instance']} | Pred: {item['prediction']} | GT: {item['ground_truth']}"
            )
        print()

    if stats["mismatch_instances"]:
        print(f"\n{'=' * 80}")
        print(f"MISMATCHED INSTANCES ({len(stats['mismatch_instances'])})")
        print(f"{'=' * 80}\n")
        for item in stats["mismatch_instances"]:
            print(
                f"❌ {item['instance']} | Pred: {item['prediction']} | GT: {item['ground_truth']}"
            )
            print(f"   Model output:\n{item.get('model_output', '')}\n")
        print()

    if stats["missing_pred_instances"]:
        print(f"\n{'=' * 80}")
        print(f"MISSING PREDICTION INSTANCES ({len(stats['missing_pred_instances'])})")
        print(f"{'=' * 80}\n")
        for instance in stats["missing_pred_instances"]:
            print(f"⚠️  {instance}")
        print()

    print_confusion_matrix(stats["confusion_matrix"])

    pred_map = {}
    for item in predictions_list:
        for instance, data in item.items():
            pred_map[instance] = data["prediction"]
    binary_metrics = compute_binary_metrics(
        stats["gt_map"], pred_map, stats["score_map"]
    )
    print_binary_metrics(binary_metrics)

    print(f"\n{'=' * 80}")
    print(f"ACCURACY REPORT")
    print(f"{'=' * 80}")
    print(f"Total Samples: {stats['total']}")
    print(f"Matches: {stats['matches']} ✅")
    print(f"Mismatches: {stats['mismatches']} ❌")
    print(f"Missing Prediction: {len(stats['missing_pred_instances'])} ⚠️")
    print(f"Accuracy: {stats['accuracy']:.2f}%")
    print(f"{'=' * 80}\n")

    with open("predictions_with_ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(predictions_list, f, indent=2, ensure_ascii=False)

    confusion_matrix_serializable = [
        {"predicted": pred, "ground_truth": gt, "count": count}
        for (pred, gt), count in stats["confusion_matrix"].items()
    ]
    stats_to_save = {
        "total": stats["total"],
        "matches": stats["matches"],
        "mismatches": stats["mismatches"],
        "missing_prediction": len(stats["missing_pred_instances"]),
        "accuracy": stats["accuracy"],
        "confusion_matrix": confusion_matrix_serializable,
        "binary_metrics": {
            "auc_roc": binary_metrics["auc_roc"],
            "mcc": binary_metrics["mcc"],
            "macro_precision": binary_metrics["macro_precision"],
            "macro_recall": binary_metrics["macro_recall"],
            "macro_f1": binary_metrics["macro_f1"],
            "n_scored": binary_metrics["n_scored"],
            "n_pos": binary_metrics["n_pos"],
            "n_neg": binary_metrics["n_neg"],
            "per_class": {
                c: {
                    "precision": binary_metrics["per_class"][c][0],
                    "recall": binary_metrics["per_class"][c][1],
                    "f1": binary_metrics["per_class"][c][2],
                    "tp": binary_metrics["tp"][c],
                    "fp": binary_metrics["fp"][c],
                    "fn": binary_metrics["fn"][c],
                }
                for c in ["NT", "T"]
            },
        },
    }
    with open("accuracy_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats_to_save, f, indent=2)

    with open("instance_details.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "match_instances": stats["match_instances"],
                "mismatch_instances": stats["mismatch_instances"],
                "missing_pred_instances": stats["missing_pred_instances"],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"Saved results to 'predictions_with_ground_truth.json'")
    print(f"Saved statistics to 'accuracy_stats.json'")
    print(f"Saved instance details to 'instance_details.json'")

