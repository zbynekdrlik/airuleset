#!/usr/bin/env python3
"""#95 item 9 — baseline measurement for the `user-questions-slovak.md`
middle-path decision (comment 5230653165).

The user's explicit condition: quality is measured by CLAUDE, never by the
user living through it ("ideal keby si si to ty pomeral... zasa robit zo
mna testera"). This script is the baseline half of that measurement -- the
SAME yardstick `hooks/stop-check-question-quality.sh` already enforces live,
run once now (before the middle-path split) and again after a few days of
post-change collection, so the two numbers are provably comparable.

Method -- REPLAY, never a live-firing count: walk every real transcript on
this box (`~/.claude/projects/**/*.jsonl`, main sessions AND subagents),
extract every real assistant turn that carries a `❓` question marker
(well-formed or not -- the population under test is every REAL attempt,
not just the compliant ones), and feed EACH ONE through the ACTUAL shipped
`stop-check-question-quality.sh` with a fresh, unique session id (so the
per-session retry cap and presence marker never leak across samples). The
hook's own verdict (block / clean, and WHICH check fired) is the metric --
never a re-implementation of its regexes, for the same reason
`measure_design_compliance.py` reuses `design_gate.classify_design_comment`
instead of guessing at the shape.

Usage::

    python3 scripts/measure_question_quality_baseline.py [--limit N] [--json]

Read-only: only ever reads `~/.claude/projects/**/*.jsonl` and shells out to
the (also read-only, non-blocking) hook script. Never writes anything other
than the report it prints.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import uuid

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO_ROOT, "hooks", "stop-check-question-quality.sh")

# Same marker shape the hook itself keys on (ASKED_RX / the bare-❓-last-line
# form) -- broad on purpose: this measures every REAL attempt at a ❓
# question, well-formed or not, not just the ones that already comply.
QUESTION_MARKER_RE = re.compile(
    r"❓[\s]*\**[\s]*(NEEDS\s+YOU|ASKED)[\s]*\**[\s]*:", re.IGNORECASE
)


def assistant_texts(path):
    """Yield the joined text of every `type == "assistant"` entry in one
    transcript file that contains a real ❓ question marker. Malformed
    lines/entries are skipped silently -- a transcript corpus this large
    always has a few, and this is a sampling measurement, not an audit."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or "❓" not in line:
                    continue
                try:
                    entry = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    continue
                if QUESTION_MARKER_RE.search(text or ""):
                    yield text
    except OSError:
        return


def iter_transcripts(projects_dir):
    pattern = os.path.join(projects_dir, "**", "*.jsonl")
    yield from glob.iglob(pattern, recursive=True)


def classify(text):
    """Feed `text` through the REAL shipped hook, fresh session id per
    call. Returns (blocked: bool, reason: str|None) -- reason is the hook's
    own REASON line when blocked, else None. Never raises; a hook failure
    (missing jq, timeout, ...) is reported as "unmeasurable", not guessed
    either way."""
    payload = json.dumps({
        "session_id": "qbaseline-" + uuid.uuid4().hex[:12],
        "last_assistant_message": text,
    })
    try:
        r = subprocess.run(
            ["bash", HOOK], input=payload, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "unmeasurable (hook invocation failed)"
    out = (r.stdout or "").strip()
    if not out:
        return False, None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return None, "unmeasurable (non-JSON hook output)"
    if data.get("decision") == "block":
        reason = str(data.get("reason", ""))
        return True, reason
    return False, None


# Literal substrings of hooks/stop-check-question-quality.sh's own REASON=
# strings (grepped from the shipped script, not guessed) -- checked in
# order, first match wins, so a more specific phrase can be listed before a
# more general one if that ever becomes necessary.
REASON_BUCKETS = [
    ("no briefing", "no-briefing"),
    ("crams MULTIPLE decisions", "multi-decision"),
    ("wall of text", "wall-of-text"),
    ("no option bullets", "no-options"),
    ("references an OLD question", "history-allusion"),
]


def bucket(reason):
    if not reason:
        return "unmeasurable"
    for needle, name in REASON_BUCKETS:
        if needle in reason:
            return name
    return "other"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--limit", type=int, default=0,
                     help="stop after sampling this many question turns (0 = no limit)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    seen_texts = set()  # de-dup byte-identical repeats (re-poked turns)
    samples = []
    files_scanned = 0
    t0 = time.time()

    for path in iter_transcripts(args.projects_dir):
        files_scanned += 1
        if files_scanned % 2000 == 0:
            print(f"...scanned {files_scanned} files, {len(samples)} samples so far",
                  file=sys.stderr)
        for text in assistant_texts(path):
            key = text.strip()
            if key in seen_texts:
                continue
            seen_texts.add(key)
            samples.append(text)
            if args.limit and len(samples) >= args.limit:
                break
        if args.limit and len(samples) >= args.limit:
            break

    total = len(samples)
    blocked = 0
    unmeasurable = 0
    buckets = {}
    for text in samples:
        is_blocked, reason = classify(text)
        if is_blocked is None:
            unmeasurable += 1
            continue
        if is_blocked:
            blocked += 1
            b = bucket(reason)
            buckets[b] = buckets.get(b, 0) + 1

    result = {
        "files_scanned": files_scanned,
        "distinct_question_turns_sampled": total,
        "blocked": blocked,
        "clean": total - blocked - unmeasurable,
        "unmeasurable": unmeasurable,
        "blocked_by_reason": buckets,
        "hit_rate_pct": round(100.0 * blocked / total, 1) if total else None,
        "elapsed_s": round(time.time() - t0, 1),
    }

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Scanned {result['files_scanned']} transcript files in "
              f"{result['elapsed_s']}s")
        print(f"Distinct ❓ question turns sampled: {result['distinct_question_turns_sampled']}")
        print(f"  blocked (would fail stop-check-question-quality.sh today): "
              f"{result['blocked']}  ({result['hit_rate_pct']}%)")
        print(f"  clean: {result['clean']}")
        print(f"  unmeasurable: {result['unmeasurable']}")
        if buckets:
            print("  by reason:")
            for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]):
                print(f"    {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
