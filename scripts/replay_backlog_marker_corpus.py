#!/usr/bin/env python3
"""Corpus replay for `backlog_marker_gate.classify_backlog_empty_claim` (#166,
Acceptance bullet 2) -- proves the mention-vs-use classifier discriminates on
REAL text, not just the hand-authored fixtures in
tests/test_backlog_marker_gate.py, and reports "newly-blocked" (naive=False,
careful=True -- should be structurally impossible, see
TestNaiveVsCarefulDelta) and "no-longer-blocked" (naive=True, careful=False --
the actual fix: a mention correctly no longer classified as a genuine claim)
as SEPARATE numbers, per the ticket's own acceptance wording.

Two corpora, replayed independently:

  REPO  -- every git-tracked text file in this repo (`git ls-files`), whole
           file content as one text blob per file. Reproducible on any
           clone/CI-less box, and itself a real instance of the exact
           self-tripping risk the ticket named ("any session working on this
           protocol writes that string -- including the one that implemented
           #159"): skills/autopilot/SKILL.md and tests/test_goal_backlog_
           proof.py both genuinely mention the marker without ever emitting
           a real claim.
  LOCAL -- this box's real Claude Code transcripts under
           ~/.claude/projects/**/*.jsonl, one assistant text block per item
           (top-level `type=="assistant"` entries only -- assistant content
           lists never contain nested tool_result blocks, so this is safe
           from the nested-tool_result contamination trap documented in
           .claude/rules/airuleset-internals.md for `scan_goal_markers`).
           Best-effort: an absent/unreadable projects dir yields an empty
           corpus rather than failing (private, machine-local data -- never
           assumed to exist).

Usage: python3 scripts/replay_backlog_marker_corpus.py [--limit N] [--projects-dir DIR]
Read-only: touches no files, calls no `gh`, no network.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import backlog_marker_gate as bmg                           # noqa: E402

# Skip obviously-binary / vendored / huge paths -- this is a text corpus,
# not a full-repo binary sweep.
_SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".jsonl",
                  ".lock", ".woff", ".woff2", ".ttf", ".zip", ".tar", ".gz")


def repo_text_corpus(limit=None):
    """`[(label, text), ...]` -- one entry per git-tracked text file."""
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return []
    out = []
    for rel in r.stdout.splitlines():
        rel = rel.strip()
        if not rel or rel.lower().endswith(_SKIP_SUFFIXES):
            continue
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.append((rel, text))
        if limit and len(out) >= limit:
            break
    return out


def local_transcript_corpus(projects_dir, limit=None):
    """`[(label, text), ...]` -- one entry per top-level assistant text
    block across every `*.jsonl` transcript under `projects_dir`. Fail-safe:
    a missing/unreadable dir or file yields nothing for it, never raises."""
    out = []
    try:
        root = Path(projects_dir)
        if not root.is_dir():
            return out
        files = sorted(root.glob("*/*.jsonl"))
    except OSError:
        return out
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as fh:
                for lineno, raw in enumerate(fh):
                    try:
                        d = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if d.get("type") != "assistant":
                        continue
                    content = d.get("message", {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            t = block.get("text") or ""
                            if t:
                                out.append(("%s:%d" % (fp.name, lineno), t))
                        if limit and len(out) >= limit:
                            return out
        except OSError:
            continue
    return out


def replay(corpus, corpus_name):
    n_total = 0
    n_agree_absent = 0
    n_agree_present = 0
    no_longer_blocked = []   # naive=True, careful=False (the intended fix)
    newly_blocked = []       # naive=False, careful=True (should not happen)

    for label, text in corpus:
        n_total += 1
        naive = bmg.naive_marker_present(text)
        careful, reason = bmg.classify_backlog_empty_claim(text)
        if not naive and not careful:
            n_agree_absent += 1
        elif naive and careful:
            n_agree_present += 1
        elif naive and not careful:
            no_longer_blocked.append((label, reason))
        else:  # not naive and careful -- structurally unexpected
            newly_blocked.append((label, reason))

    print("=== %s corpus ===" % corpus_name)
    print("total items: %d" % n_total)
    print("agree (no marker at all): %d" % n_agree_absent)
    print("agree (genuine claim): %d" % n_agree_present)
    print("no-longer-blocked (naive mention, careful correctly excludes): %d"
          % len(no_longer_blocked))
    print("newly-blocked (careful flags something naive missed): %d"
          % len(newly_blocked))
    for label, reason in no_longer_blocked[:10]:
        print("  no-longer-blocked: %s (%s)" % (label, reason))
    for label, reason in newly_blocked[:10]:
        print("  newly-blocked: %s (%s)" % (label, reason))
    print()
    return n_total, len(no_longer_blocked), len(newly_blocked)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--projects-dir",
                     default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    args = ap.parse_args(argv)

    repo_corpus = repo_text_corpus(args.limit)
    local_corpus = local_transcript_corpus(args.projects_dir, args.limit)

    replay(repo_corpus, "REPO (git-tracked files)")
    replay(local_corpus, "LOCAL (real Claude Code transcripts, best-effort)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
