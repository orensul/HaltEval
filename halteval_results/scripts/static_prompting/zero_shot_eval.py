#!/usr/bin/env python3
"""
Zero-shot termination analysis evaluation.

The model receives the pre-constructed context (function body + source file +
local headers) and must classify the function as terminating or non-terminating
in a single pass, without tool or repository access.

Usage (run from the repo root):
    python scripts/zero_shot_eval.py
    python scripts/zero_shot_eval.py --model claude-4-6-sonnet-genai-vertex
    python scripts/zero_shot_eval.py --limit 5
    python scripts/zero_shot_eval.py --resume
"""

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from string import Template

from openai import OpenAI

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT         = Path(__file__).resolve().parent.parent.parent
CONTEXT_FILE = ROOT / "data" / "static_prompting" / "benchmark_with_context.jsonl"
PROMPTS_DIR  = ROOT / "prompts" / "static_prompting"
DEFAULT_MODEL = "claude-4-6-sonnet-genai-vertex"

# Prompts are loaded from prompts/static_prompting/  (system message + user template).
SYSTEM_PROMPT = (PROMPTS_DIR / "zero_shot_system.txt").read_text().strip()
USER_PROMPT   = Template((PROMPTS_DIR / "zero_shot_user.txt").read_text())

# Per-header formatting block (structural glue, kept inline).
HEADER_BLOCK_TEMPLATE = """\

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HEADER  ({path})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{content}
"""

MAX_SOURCE_CHARS  = 120_000   # ~30K tokens; cap very large source files
MAX_HEADER_CHARS  = 60_000    # cap per individual header
MAX_HEADERS_TOTAL = 180_000   # total budget for all headers combined


# ---------------------------------------------------------------------------
# Llama / OpenAI-compatible client
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.llama.com/v1"


def make_client(base_url: str | None = None) -> OpenAI:
    # base_url precedence: explicit arg > LLM_BASE_URL env > llama.com default.
    # api key: OPENAI_API_KEY (used by most OpenAI-compatible providers) > LLAMA_API_KEY.
    base_url = base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLAMA_API_KEY", "")
    return OpenAI(
        api_key=api_key or "EMPTY",  # local servers (vLLM/Ollama) accept any non-empty key
        base_url=base_url,
        timeout=None,
        max_retries=0,
    )


def call_model(client: OpenAI, model: str, messages: list[dict],
               max_retries: int = 5) -> str:
    """Call the API and return the assistant's text content, with exponential backoff on 429."""
    delay = 10
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                n=1,
                max_completion_tokens=4096,
            )
            # Llama API returns completion_message; standard OpenAI returns choices
            if response.choices:
                return response.choices[0].message.content
            content = response.completion_message["content"]
            return content["text"] if isinstance(content, dict) else content
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            if "429" in str(e) or "quota" in str(e).lower() or "rate" in str(e).lower():
                print(f"    Rate limited — retrying in {delay}s (attempt {attempt+1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
            else:
                raise


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(row: dict) -> str:
    ctx = row["context"]

    function_body = (ctx.get("function_body") or "").strip()
    source_file   = (ctx.get("source_file")   or "").strip()
    headers       = ctx.get("local_headers", {})

    # Cap source file size
    if len(source_file) > MAX_SOURCE_CHARS:
        source_file = source_file[:MAX_SOURCE_CHARS] + "\n... [TRUNCATED]"

    # Build headers section within total budget
    headers_section = ""
    total_hdr_chars = 0
    for path, content in headers.items():
        if total_hdr_chars >= MAX_HEADERS_TOTAL:
            break
        content = content.strip()
        if len(content) > MAX_HEADER_CHARS:
            content = content[:MAX_HEADER_CHARS] + "\n... [TRUNCATED]"
        headers_section += HEADER_BLOCK_TEMPLATE.format(path=path, content=content)
        total_hdr_chars += len(content)

    return USER_PROMPT.substitute(
        project          = row["project"],
        file             = row["file"],
        function         = row["function"],
        function_body    = function_body or "(not extracted)",
        source_file_path = ctx.get("source_file_path", row["file"]),
        source_file      = source_file or "(not available)",
        headers_section  = headers_section,
    )


# ---------------------------------------------------------------------------
# Verdict parser  (same logic as claude_code_eval.py)
# ---------------------------------------------------------------------------

def parse_verdict(text: str) -> dict:
    text = re.sub(r"```[a-z]*\n?", "", text).strip()
    # Quote unquoted string values for known enum fields (e.g. confidence:medium)
    text = re.sub(r'"(confidence|label)"\s*:\s*([a-zA-Z][a-zA-Z-]*)', r'"\1": "\2"', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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
            obj = json.loads(text[start: close + 1])
            if "label" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        end = close
    return {"label": "parse-error", "confidence": "low", "reason": text[:300]}


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------

def load_done(output_file: Path) -> set:
    """Return keys of successfully predicted rows (excludes error/parse-error so they get retried)."""
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
                label = r.get("prediction", {}).get("label", "")
                if label not in ("error", "parse-error"):
                    done.add((r["repo"], r["commit"], r["file"], r["function"]))
            except Exception:
                pass
    return done


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",     default=DEFAULT_MODEL,
                        help=f"Model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--base-url",  default=None,
                        help="OpenAI-compatible endpoint (or set LLM_BASE_URL env); "
                             f"default {DEFAULT_BASE_URL}")
    parser.add_argument("--limit",     type=int, default=None,
                        help="Process only the first N rows")
    parser.add_argument("--resume",    action="store_true",
                        help="Skip rows already written to the output file")
    parser.add_argument("--test-mode", action="store_true",
                        help="One sample per repo — smoke test")
    args = parser.parse_args()

    timestamp  = datetime.now().strftime("%d%m%y_%H%M")
    model_slug = re.sub(r"[^a-zA-Z0-9]+", "_", args.model).strip("_")
    base_dir   = ROOT / "results" / "static_prompting"
    base_dir.mkdir(parents=True, exist_ok=True)

    if args.resume:
        # Find the most recent existing output file for this model to append to.
        candidates = sorted(
            base_dir.glob(f"zeroshot_{model_slug}*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        output_file = candidates[0] if candidates else (
            base_dir / f"zeroshot_{model_slug}.jsonl"
        )
        print(f"Resuming into: {output_file.name}")
    else:
        # Clean output name, e.g. results/static_prompting/zeroshot_claude_opus_4_7.jsonl
        output_file = base_dir / f"zeroshot_{model_slug}.jsonl"
        if output_file.exists():
            output_file = base_dir / f"zeroshot_{model_slug}_{timestamp}.jsonl"

    rows: list[dict] = []
    with CONTEXT_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"Loaded {len(rows)} rows from {CONTEXT_FILE.name}")

    if args.test_mode:
        seen_repos: set = set()
        rows = [r for r in rows
                if r["repo"] not in seen_repos and not seen_repos.add(r["repo"])]
        print(f"Test mode — {len(rows)} rows (first per repo)")

    if args.limit:
        rows = rows[: args.limit]
        print(f"Limiting to {args.limit} rows")

    done: set = set()
    if args.resume:
        done = load_done(output_file)
        print(f"Resuming — {len(done)} already done")

    client = make_client(args.base_url)

    with output_file.open("a") as out_f:
        for idx, row in enumerate(rows):
            key = (row["repo"], row["commit"], row["file"], row["function"])
            if key in done:
                continue

            print(f"\n[{idx+1}/{len(rows)}] {row['project']} · {row['function']}")

            try:
                prompt = build_prompt(row)
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ]
                raw_text   = call_model(client, args.model, messages)
                prediction = parse_verdict(raw_text)

            except Exception as exc:
                prediction = {
                    "label":      "error",
                    "confidence": "low",
                    "reason":     str(exc)[:300],
                }
                print(f"  ERROR: {exc}")

            label  = prediction.get("label", "?")
            conf   = prediction.get("confidence", "?")
            reason = prediction.get("reason", "")
            print(f"  → {label} [{conf}]  {reason}")

            out_f.write(json.dumps({**row, "prediction": prediction}) + "\n")
            out_f.flush()

    # Summary
    labels: Counter = Counter()
    with output_file.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    labels[json.loads(line)["prediction"]["label"]] += 1
                except Exception:
                    pass

    print(f"\nDone → {output_file}")
    print("Label distribution:", dict(labels))


if __name__ == "__main__":
    main()
