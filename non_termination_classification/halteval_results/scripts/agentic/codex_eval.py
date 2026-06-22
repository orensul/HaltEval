#!/usr/bin/env python3
"""
Halting-problem evaluation using OpenAI Codex CLI as the agentic backend.

Mirrors claude_code_eval.py: a fresh `codex` subprocess is spawned per row.
Codex handles all git work (clone, file fetch, code exploration) and termination
analysis via its shell tools.

Usage (run from the repo root):
    python scripts/codex_eval.py
    python scripts/codex_eval.py --model gpt-4.1
    python scripts/codex_eval.py --test-mode   # one sample per repo, smoke test

Note: verify flags with `codex --help` as CLI versions may differ.
"""

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from string import Template

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT         = Path(__file__).resolve().parent.parent.parent
DATA_FILE    = ROOT / "data" / "agentic" / "benchmark.jsonl"
REPOS_DIR    = ROOT / "repos"
PROMPTS_DIR  = ROOT / "prompts" / "agentic"
MODEL        = "gpt-5.5"
LINES_BEFORE = 30
LINES_AFTER  = 350

# ---------------------------------------------------------------------------
# Prompt  (loaded from prompts/agentic/agentic.txt — shared with claude_code_eval.py)
# ---------------------------------------------------------------------------
AGENTIC_PROMPT = Template((PROMPTS_DIR / "agentic.txt").read_text())

# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------
def load_done(output_file: Path) -> set:
    done: set = set()
    if not output_file.exists():
        return done
    with output_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((r["repo"], r["commit"], r["file"], r["function"]))
            except Exception:
                pass
    return done


def parse_verdict(text: str) -> dict:
    text = re.sub(r"```[a-z]*\n?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Scan from the end to find the last valid JSON object with a "label" key.
    end = len(text)
    while True:
        close = text.rfind("}", 0, end)
        if close == -1:
            break
        depth, start = 0, -1
        for i in range(close, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    start = i
                    break
        if start == -1:
            break
        try:
            obj = json.loads(text[start:close + 1])
            if "label" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        end = close
    return {"label": "parse-error", "confidence": "low", "reason": text[:300]}


# ---------------------------------------------------------------------------
# Codex CLI subprocess
# ---------------------------------------------------------------------------
def analyze(row: dict) -> str:
    import tempfile

    base_url = row["repo"].split("/tree/")[0]
    parts    = base_url.rstrip("/").split("/")
    repo_dir = str(REPOS_DIR / "_".join(parts[-2:]))

    prompt = AGENTIC_PROMPT.substitute(
        repo     = row["repo"],
        base_url = base_url,
        repo_dir = repo_dir,
        commit   = row["commit"],
        file     = row["file"],
        function = row["function"],
        line     = row["line"],
        before   = LINES_BEFORE,
        after    = LINES_AFTER,
    )

    # Write last agent message to a temp file so we can parse it cleanly.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    tmp.close()

    cmd = [
        "codex",
        "--dangerously-disable-osx-sandbox",
        "exec",
        "-m",    MODEL,
        "--full-auto",
        "--ephemeral",
        "-o",    tmp.name,
        prompt,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"codex exited {result.returncode}:\n"
                f"STDOUT: {result.stdout[:300]}\nSTDERR: {result.stderr[:300]}"
            )
        with open(tmp.name) as f:
            return f.read()
    finally:
        os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL, help="OpenAI model to use")
    parser.add_argument("--test-mode", action="store_true",
                        help="Run only the first sample per repo (smoke test)")
    args  = parser.parse_args()
    MODEL = args.model

    # Clean output name, e.g. results/agentic/agentic_codex_gpt_5_5.jsonl
    results_dir = ROOT / "results" / "agentic"
    results_dir.mkdir(parents=True, exist_ok=True)
    model_slug  = re.sub(r"[^a-zA-Z0-9]+", "_", MODEL).strip("_")
    OUTPUT_FILE = results_dir / f"agentic_codex_{model_slug}.jsonl"
    if OUTPUT_FILE.exists():
        timestamp   = datetime.now().strftime("%d%m%y_%H%M")
        OUTPUT_FILE = results_dir / f"agentic_codex_{model_slug}_{timestamp}.jsonl"

    REPOS_DIR.mkdir(exist_ok=True)

    rows: list[dict] = []
    with DATA_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"Loaded {len(rows)} rows")

    if args.test_mode:
        seen_repos: set = set()
        filtered: list[dict] = []
        for row in rows:
            if row["repo"] not in seen_repos:
                seen_repos.add(row["repo"])
                filtered.append(row)
        rows = filtered
        print(f"Test mode: reduced to {len(rows)} rows (first sample per repo)")

    done = load_done(OUTPUT_FILE)
    if done:
        print(f"Resuming — {len(done)} rows already done, skipping.")

    with OUTPUT_FILE.open("a") as out_f:
        for idx, row in enumerate(rows):
            key = (row["repo"], row["commit"], row["file"], row["function"])
            if key in done:
                continue

            print(f"\n[{idx+1}/{len(rows)}] {row['project']} · {row['function']}")

            try:
                result_text = analyze(row)
                prediction  = parse_verdict(result_text)

            except subprocess.CalledProcessError as e:
                prediction = {"label": "error", "confidence": "low",
                              "reason": f"git error: {(e.stderr or '')[:200]}"}
                print(f"    GIT ERROR: {(e.stderr or '')[:120]}")

            except Exception as exc:
                prediction = {"label": "error", "confidence": "low",
                              "reason": str(exc)}
                print(f"    ERROR: {exc}")

            label  = prediction.get("label", "?")
            conf   = prediction.get("confidence", "?")
            reason = prediction.get("reason", "")
            print(f"    → {label} [{conf}]  {reason}")

            out_f.write(json.dumps({**row, "prediction": prediction}) + "\n")
            out_f.flush()

    labels: Counter = Counter()
    with OUTPUT_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    labels[json.loads(line)["prediction"]["label"]] += 1
                except Exception:
                    pass
    print(f"\nDone → {OUTPUT_FILE}")
    print("Label distribution:", dict(labels))


if __name__ == "__main__":
    main()
