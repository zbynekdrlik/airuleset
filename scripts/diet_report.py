#!/usr/bin/env python3
"""Context-diet paragraph classifier (#859 batch 1).

REPORT-ONLY: classifies each paragraph of an always-on module as CORE or
HISTORY based on marker heuristics. Every actual move is judged by a Fable
design pass — this script only provides the input data.

Core markers (paragraph likely needed for a session to ACT):
  NEVER, ALWAYS, MUST, BANNED, FORBIDDEN, hook-enforced, hook-blocked,
  Applies to all rewordings, Anti-pattern, The rule, Hard rule,
  mechanically enforced, mechanically backstopped, WRONG, REQUIRED

History markers (paragraph is rationale / archaeology / incident narrative):
  dated refs like #NNN (YYYY-MM, owner quotes „…", verbatim:, incident:,
  measured:, live evidence:, burn:, precedent:, the old, was RETIRED,
  was ABOLISHED, REVISED, REVERSED, SUPERSEDED, DROPPED, live-reproduced

Usage:
    python3 scripts/diet_report.py [modules/core/foo.md ...]
    python3 scripts/diet_report.py --all      # top-6 batch-1 modules
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The batch-1 top-6 modules
BATCH1_MODULES = [
    "modules/core/statusline-vocabulary.md",
    "modules/core/model-awareness.md",
    "modules/core/ask-before-assuming.md",
    "modules/core/completion-report.md",
    "modules/core/autonomous-verification.md",
    "modules/core/claude-code-tooling.md",
]

# --- marker patterns ---

CORE_PATTERNS = re.compile(
    r"\b(?:NEVER|ALWAYS|MUST|BANNED|FORBIDDEN)\b"
    r"|hook-enforced|hook-blocked|mechanically enforced|mechanically backstopped"
    r"|Applies to all rewordings"
    r"|Anti-pattern|The rule|Hard rule"
    r"|WRONG|REQUIRED|do NOT|Do NOT"
    r"|\bBANNED\b"
    r"|`stop-check|`block-|`pre-push|`nudge-|`notify-|`post-record"
    r"|`hooks/"
)

HISTORY_PATTERNS = re.compile(
    r"#\d{2,4}\s*\(20\d\d"          # #NNN (2026-MM-DD
    r"|#\d{2,4},\s*20\d\d"          # #NNN, 2026-
    r'|„[^"]{4,}“'        # „…" owner quotes (at least 4 chars)
    r"|verbatim:"
    r"|incident[:\s]"
    r"|measured[:\s]"
    r"|live evidence[:\s]"
    r"|burn[:\s]"
    r"|precedent[:\s]"
    r"|the old\b"
    r"|was RETIRED|was ABOLISHED|REVISED|REVERSED|SUPERSEDED"
    r"|DROPPED entirely"
    r"|live-reproduced"
    r"|owner directive"
    r"|owner ruling"
    r"|owner.*verbatim"
    r"|owner.*2026"
    r"|2026-\d\d-\d\d"
)


def split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs (blank-line delimited blocks)."""
    paras = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() == "":
            if current:
                paras.append("".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paras.append("".join(current))
    return paras


def classify_paragraph(para: str) -> str:
    """Return 'CORE', 'HISTORY', or 'MIXED' for a paragraph."""
    has_core = bool(CORE_PATTERNS.search(para))
    has_history = bool(HISTORY_PATTERNS.search(para))

    if has_core and not has_history:
        return "CORE"
    if has_history and not has_core:
        return "HISTORY"
    if has_core and has_history:
        return "MIXED"
    # Neither marker — short paragraphs are likely headers/structure (core)
    if len(para) < 200:
        return "CORE"
    return "UNKNOWN"


def classify_module(path: Path) -> dict:
    """Classify all paragraphs in a module and return a summary."""
    text = path.read_text(encoding="utf-8")
    paras = split_paragraphs(text)

    results = {"CORE": 0, "HISTORY": 0, "MIXED": 0, "UNKNOWN": 0}
    para_details = []

    for para in paras:
        cls = classify_paragraph(para)
        results[cls] += len(para.encode("utf-8"))
        para_details.append((cls, len(para.encode("utf-8")), para[:80].strip()))

    total = len(text.encode("utf-8"))
    return {
        "path": str(path.relative_to(ROOT)),
        "total_bytes": total,
        "core_bytes": results["CORE"] + results["MIXED"],  # MIXED stays in module (conservative)
        "history_bytes": results["HISTORY"],
        "unknown_bytes": results["UNKNOWN"],
        "mixed_bytes": results["MIXED"],
        "paragraphs": para_details,
    }


def print_report(results: list[dict]) -> None:
    """Print a formatted report."""
    print("=" * 78)
    print("CONTEXT DIET REPORT — Batch 1 paragraph classifier (#859)")
    print("=" * 78)
    print()

    grand_total = 0
    grand_core = 0
    grand_history = 0

    for r in results:
        total = r["total_bytes"]
        core = r["core_bytes"]
        history = r["history_bytes"]
        unknown = r["unknown_bytes"]
        grand_total += total
        grand_core += core
        grand_history += history

        pct_core = core * 100 / total if total else 0
        pct_hist = history * 100 / total if total else 0

        print(f"  {r['path']}")
        print(f"    total: {total:,} B")
        print(f"    core+mixed: {core:,} B ({pct_core:.0f}%)")
        print(f"    history: {history:,} B ({pct_hist:.0f}%)")
        print(f"    unknown: {unknown:,} B")
        print()

        # Show HISTORY paragraphs (candidates for move)
        hist_paras = [
            (sz, preview) for cls, sz, preview in r["paragraphs"] if cls == "HISTORY"
        ]
        if hist_paras:
            print(f"    HISTORY paragraphs ({len(hist_paras)}):")
            for sz, preview in hist_paras[:15]:
                print(f"      [{sz:5,} B] {preview}")
            if len(hist_paras) > 15:
                print(f"      ... and {len(hist_paras) - 15} more")
            print()

    print("-" * 78)
    print(f"  TOTAL: {grand_total:,} B")
    print(f"  core+mixed: {grand_core:,} B ({grand_core * 100 / grand_total:.0f}%)")
    print(f"  history: {grand_history:,} B ({grand_history * 100 / grand_total:.0f}%)")
    print(f"  estimated post-move: ~{grand_core + grand_total // 50:,} B "
          f"(core + ~1-line pointers)")
    print("=" * 78)


def main() -> None:
    if "--all" in sys.argv:
        paths = [ROOT / m for m in BATCH1_MODULES]
    elif len(sys.argv) > 1:
        paths = [Path(a).resolve() for a in sys.argv[1:] if not a.startswith("-")]
    else:
        print(__doc__)
        sys.exit(0)

    results = []
    for p in paths:
        if not p.exists():
            print(f"WARNING: {p} not found, skipping", file=sys.stderr)
            continue
        results.append(classify_module(p))

    print_report(results)


if __name__ == "__main__":
    main()
