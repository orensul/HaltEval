#!/usr/bin/env python3
"""
Context extractor v3 — 1-shot baseline (paper §3.1).

Extracts exactly the three components described in the paper:
  (i)  Target function body  — tree-sitter AST, exact source text
  (ii) Full source file      — complete translation unit
  (iii) Project-local headers — all #include "..." files at the same commit

No callee lookup, no cross-file dependency tracing.

Output: data/benchmark_with_context.jsonl

Usage (run from the repo root):
    python scripts/extract_context.py
    python scripts/extract_context.py --resume
    python scripts/extract_context.py --limit 5
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_c
import tree_sitter_cpp

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT        = Path(__file__).resolve().parent.parent
DATA_FILE   = ROOT / "data" / "benchmark.jsonl"
REPOS_DIR   = ROOT / "repos"
OUTPUT_FILE = ROOT / "data" / "benchmark_with_context.jsonl"

MAX_HEADER_BYTES    = 300_000   # bytes; headers larger than this are truncated
SOURCE_WINDOW_LINES = 600       # lines stored for source files > MAX_HEADER_BYTES

C_LANG   = Language(tree_sitter_c.language())
CPP_LANG = Language(tree_sitter_cpp.language())


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def repo_dir_for(repo_url: str) -> Path:
    base_url = repo_url.split("/tree/")[0]
    parts    = base_url.rstrip("/").split("/")
    return REPOS_DIR / "_".join(parts[-2:])


def git_show(repo_dir: Path, commit: str, file_path: str,
             max_bytes: int | None = MAX_HEADER_BYTES) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "show", f"{commit}:{file_path}"],
        capture_output=True, text=True, errors="replace",
    )
    if result.returncode != 0:
        return None
    content = result.stdout
    if max_bytes is not None and len(content) > max_bytes:
        content = content[:max_bytes] + "\n... [TRUNCATED]\n"
    return content


def git_show_full(repo_dir: Path, commit: str, file_path: str) -> str | None:
    return git_show(repo_dir, commit, file_path, max_bytes=None)


def resolve_source_file(repo_dir: Path, commit: str,
                        file_path: str) -> tuple[str | None, str]:
    """
    Fetch the full source file at commit.  Falls back to:
      1. Common dataset typo correction (sqllite → sqlite)
      2. Basename search across the full tree.
    Returns (content, resolved_path).
    """
    content = git_show_full(repo_dir, commit, file_path)
    if content is not None:
        return content, file_path

    fixed = file_path.replace("sqllite", "sqlite")
    if fixed != file_path:
        content = git_show_full(repo_dir, commit, fixed)
        if content is not None:
            return content, fixed

    basename = Path(file_path).name
    ls = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-tree", "-r", "--name-only", commit],
        capture_output=True, text=True,
    )
    for candidate in ls.stdout.splitlines():
        if Path(candidate).name == basename:
            content = git_show_full(repo_dir, commit, candidate)
            if content is not None:
                return content, candidate

    return None, file_path


def windowed_source(content: str, center_line: int) -> str:
    lines = content.split("\n")
    half  = SOURCE_WINDOW_LINES // 2
    start = max(0, center_line - 1 - half)
    end   = min(len(lines), center_line - 1 + half)
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Tree-sitter: function body extraction
# ---------------------------------------------------------------------------

def _lang_for(path: str) -> Language:
    return C_LANG if path.endswith(".c") else CPP_LANG


def _parse(content: str, path: str):
    parser = Parser(_lang_for(path))
    return parser.parse(content.encode("utf-8", errors="replace")).root_node


def _declarator_name(node) -> str | None:
    if node is None:
        return None
    t = node.type
    if t in ("identifier", "field_identifier",
             "destructor_name", "operator_name"):
        return node.text.decode("utf-8", errors="replace")
    if t == "qualified_identifier":
        scope = node.child_by_field_name("scope")
        name  = node.child_by_field_name("name")
        s = _declarator_name(scope) if scope else None
        n = _declarator_name(name)
        return f"{s}::{n}" if s and n else n
    if t in ("pointer_declarator", "reference_declarator"):
        return _declarator_name(node.child_by_field_name("declarator"))
    if t == "function_declarator":
        return _declarator_name(node.child_by_field_name("declarator"))
    name_child = node.child_by_field_name("name")
    if name_child:
        return _declarator_name(name_child)
    return None


def _matches(full: str | None, target: str) -> bool:
    if full is None:
        return False
    if full == target:
        return True
    if full.split("::")[-1] == target.split("::")[-1]:
        return True
    if target in full:
        return True
    return False


def extract_function_body(source: str, path: str,
                          func_name: str, hint_line: int) -> str | None:
    """
    Parse source with tree-sitter and return the exact text of the
    function_definition node whose name matches func_name.
    Falls back to a 300-line window around hint_line on failure.
    """
    root = _parse(source, path)

    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            decl = node.child_by_field_name("declarator")
            name = _declarator_name(decl)
            body = node.child_by_field_name("body")
            if _matches(name, func_name) and body and body.type == "compound_statement":
                return source[node.start_byte: node.end_byte]
        stack.extend(reversed(node.children))

    # Fallback: plain line window (handles macro-wrapped functions etc.)
    lines = source.split("\n")
    start = max(0, hint_line - 15)
    end   = min(len(lines), hint_line + 300)
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Header collection
# ---------------------------------------------------------------------------

def collect_headers(source: str, resolved_path: str,
                    repo_dir: Path, commit: str) -> dict[str, str]:
    """
    Fetch all project-local headers reachable via #include "..." in source.
    Also always attempts the companion header (file.cpp → file.h / .hpp).
    Returns {resolved_path: content}.
    """
    headers: dict[str, str] = {}
    seen:    set[str]        = set()
    file_dir = str(Path(resolved_path).parent)

    # Candidate paths to try for each include directive
    def candidates_for(inc: str) -> list[str]:
        paths = []
        if file_dir and file_dir != ".":
            paths.append(f"{file_dir}/{inc}")
        paths.append(inc)
        paths.append(Path(inc).name)
        return paths

    def try_fetch(path: str) -> bool:
        if path in seen:
            return False
        seen.add(path)
        content = git_show(repo_dir, commit, path)
        if content:
            headers[path] = content
            return True
        return False

    def try_fetch_by_basename(basename: str) -> None:
        """Fallback: search the full tree for a header with this filename."""
        ls = subprocess.run(
            ["git", "-C", str(repo_dir), "ls-tree", "-r", "--name-only", commit],
            capture_output=True, text=True,
        )
        for candidate in ls.stdout.splitlines():
            if (Path(candidate).name == basename
                    and candidate.endswith((".h", ".hpp", ".hxx"))
                    and candidate not in seen):
                seen.add(candidate)
                content = git_show(repo_dir, commit, candidate)
                if content:
                    headers[candidate] = content
                    return   # one match is enough per basename

    # (i) Companion header
    base = Path(resolved_path)
    for suffix in (".h", ".hpp"):
        companion = str(base.with_suffix(suffix))
        try_fetch(companion)

    # (ii) All #include "..." directives in the source file
    for m in re.finditer(r'#\s*include\s+"([^"]+)"', source):
        inc = m.group(1)
        fetched = any(try_fetch(p) for p in candidates_for(inc))
        if not fetched:
            try_fetch_by_basename(Path(inc).name)

    return headers


# ---------------------------------------------------------------------------
# Main context builder
# ---------------------------------------------------------------------------

def build_context(row: dict) -> dict:
    repo_dir  = repo_dir_for(row["repo"])
    commit    = row["commit"]
    file_path = row["file"]
    func_name = row["function"]
    line      = int(row["line"])

    context = {
        "source_file_path": file_path,
        "function_body":    None,
        "source_file":      None,
        "local_headers":    {},     # {path: content}
        "errors":           [],
    }

    # (i) + (ii)  Fetch source file (no size limit — needed for body extraction)
    source, resolved = resolve_source_file(repo_dir, commit, file_path)
    context["source_file_path"] = resolved

    if source is None:
        context["errors"].append(f"Could not fetch {file_path} at {commit}")
        return context

    context["function_body"] = extract_function_body(source, resolved, func_name, line)

    # Store source file; window large files to keep output size reasonable
    context["source_file"] = (
        windowed_source(source, line)
        if len(source) > MAX_HEADER_BYTES
        else source
    )

    # (iii) Local headers
    context["local_headers"] = collect_headers(source, resolved, repo_dir, commit)

    return context


# ---------------------------------------------------------------------------
# Resume / main
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true",
                        help="Skip rows already in the output file")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only the first N rows")
    args = parser.parse_args()

    rows: list[dict] = []
    with DATA_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"Loaded {len(rows)} rows")

    if args.limit:
        rows = rows[: args.limit]
        print(f"Limiting to {args.limit} rows")

    done: set = set()
    if args.resume:
        done = load_done(OUTPUT_FILE)
        print(f"Resuming — {len(done)} already done")

    with OUTPUT_FILE.open("a") as out_f:
        for idx, row in enumerate(rows):
            key = (row["repo"], row["commit"], row["file"], row["function"])
            if key in done:
                continue

            print(f"[{idx+1}/{len(rows)}] {row['project']} · {row['function']}")

            try:
                ctx    = build_context(row)
                errors = ctx.pop("errors", [])
                if errors:
                    print(f"  WARN: {errors}")
                print(f"  → headers:{len(ctx['local_headers'])}  "
                      f"body:{len(ctx['function_body'] or '') // 1024}KB  "
                      f"src:{len(ctx['source_file'] or '') // 1024}KB")
            except Exception as exc:
                import traceback; traceback.print_exc()
                ctx = {
                    "source_file_path": row["file"],
                    "function_body":    None,
                    "source_file":      None,
                    "local_headers":    {},
                    "error":            str(exc),
                }

            out_f.write(json.dumps({**row, "context": ctx}) + "\n")
            out_f.flush()

    print(f"\nDone → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
