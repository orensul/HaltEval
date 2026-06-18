import argparse
import json
import math
import os
import re
from pathlib import Path
from collections import defaultdict


def normalize_function_name(function_name):
    """
    Normalize C++ function names to match the filename encoding produced by
    normalize_identifier() in forager/utils/__init__.py.
    Applies the same transformations in the same order.
    """
    if function_name is None:
        return None
    x = function_name.strip()
    x = x.replace("::", "_")
    x = x.replace("<", "_").replace(">", "_")
    x = x.replace(",", "_")
    x = x.replace(" ", "")
    return x


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
        "c", "cpp", "cc", "h", "hpp", "py", "java", "js", "rs", "rb",
        "php", "y", "l",
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
    """
    Extract prediction label and optional p_non_terminating from dialog content.
    Returns {"label": str, "p_non_terminating": float|None} or None if no prediction found.
    """
    if "prediction" not in content:
        if missing_predictions_list is not None and filename:
            missing_predictions_list.append(
                {
                    "filename": filename,
                    "reason": "no_prediction_keyword",
                    "content": content,
                }
            )
        return None

    try:
        match = re.search(r'"prediction"\s*:\s*"([^"]+)"', content)
        if match:
            label = match.group(1)
            p_nt = None
            p_match = re.search(r'"p_non_terminating"\s*:\s*([0-9]+(?:\.[0-9]*)?)', content)
            if p_match:
                try:
                    raw = float(p_match.group(1))
                    # Accept 0-100 integer scale or 0.0-1.0 float
                    p_nt = raw / 100.0 if raw > 1.0 else raw
                    p_nt = max(0.0, min(1.0, p_nt))
                except ValueError:
                    pass
            return {"label": label, "p_non_terminating": p_nt}
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

    return None


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

                    bug_class = data.get("class", "")
                    if bug_class == "Latent":
                        continue

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

            # Extract prediction from dialog — scan in reverse to find the last submission
            prediction = None
            p_non_terminating = None
            content = ""
            if "dialog" in data and len(data["dialog"]) > 0:
                for turn in reversed(data["dialog"]):
                    turn_content = turn.get("content", "")
                    pred = extract_prediction(turn_content)
                    if pred is not None:
                        prediction = pred["label"]
                        p_non_terminating = pred["p_non_terminating"]
                        content = turn_content
                        break
                if prediction is None:
                    # Record failure reason based on last turn
                    content = data["dialog"][-1].get("content", "")
                    extract_prediction(
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


def calculate_mcc(confusion_matrix):
    """Binary MCC from NT's perspective using the confusion matrix."""
    TP = confusion_matrix.get(("NT", "NT"), 0)
    TN = confusion_matrix.get(("T", "T"), 0)
    FP = confusion_matrix.get(("NT", "T"), 0)   # predicted NT, actually T
    FN = confusion_matrix.get(("T", "NT"), 0)   # predicted T, actually NT
    num = TP * TN - FP * FN
    den = math.sqrt((TP + FP) * (TP + FN) * (TN + FP) * (TN + FN))
    return num / den if den > 0 else 0.0


def calculate_auc_roc(predictions_list, ground_truth):
    """
    Compute AUC-ROC via Wilcoxon-Mann-Whitney statistic.
    NT is the positive class. Uses p_non_terminating as the soft score.
    Returns (auc, n_scored) or (None, 0) if no soft scores are available.
    """
    gt_map = {iid: gt_data["outcome"] for iid, gt_data in ground_truth.items()}
    scored = []
    for item in predictions_list:
        for instance_id, data in item.items():
            p = data.get("p_non_terminating")
            true_label = gt_map.get(instance_id)
            if p is not None and true_label in ("NT", "T"):
                scored.append((p, true_label))

    if not scored:
        return None, 0

    positives = [s for s, l in scored if l == "NT"]
    negatives = [s for s, l in scored if l == "T"]
    if not positives or not negatives:
        return None, len(scored)

    n_concordant = sum(1 for pos in positives for neg in negatives if pos > neg)
    n_concordant += sum(0.5 for pos in positives for neg in negatives if pos == neg)
    return n_concordant / (len(positives) * len(negatives)), len(scored)


def calculate_accuracy_and_confusion_matrix(predictions_list, ground_truth):
    """
    Calculate accuracy metrics and confusion matrix from predictions list.
    Uses ground truth for recall denominators.
    """
    pred_map = {}
    content_map = {}
    for item in predictions_list:
        for instance, data in item.items():
            pred_map[instance] = data["prediction"]
            content_map[instance] = data.get("model_output", "")

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

    mcc_score = calculate_mcc(dict(confusion_matrix))
    auc, n_scored = calculate_auc_roc(predictions_list, ground_truth)

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
        "mcc": mcc_score,
        "auc_roc": auc,
        "auc_roc_n": n_scored,
    }


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

    print(f"\n{'=' * 80}")
    print(f"ACCURACY REPORT")
    print(f"{'=' * 80}")
    print(f"Total Samples: {stats['total']}")
    print(f"Matches: {stats['matches']} ✅")
    print(f"Mismatches: {stats['mismatches']} ❌")
    print(f"Missing Prediction: {len(stats['missing_pred_instances'])} ⚠️")
    print(f"Accuracy: {stats['accuracy']:.2f}%")
    print(f"MCC: {stats['mcc']:.4f}   (-1 = perfectly wrong, 0 = random, +1 = perfect)")
    if stats["auc_roc"] is not None:
        print(f"AUC-ROC: {stats['auc_roc']:.4f}   (from p_non_terminating, n={stats['auc_roc_n']})")
    else:
        print("AUC-ROC: n/a   (no p_non_terminating field in predictions)")
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
        "mcc": stats["mcc"],
        "auc_roc": stats["auc_roc"],
        "auc_roc_n": stats["auc_roc_n"],
        "confusion_matrix": confusion_matrix_serializable,
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
