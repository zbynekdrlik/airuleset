#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash matcher) — #136, design-before-code gate, half 1/3.
#
# The ONLY place a design marker (~/.claude/design-posted/<repo>#<issue>) is
# ever written. Never speculative, never trusted from the command that was
# about to run — this hook re-reads the ACTUAL posted comment back from
# GitHub (`gh issue view --json comments`), the same #135 lesson
# `notify.marker_delivered` encodes: a marker's presence must be backed by
# an OBSERVED delivery, not an intent.
#
# Why re-query instead of trusting Bash's own tool_response: this repo has
# no prior art anywhere for a Bash PostToolUse hook reading tool_response
# stdout (grep confirms it — every existing PostToolUse hook either ignores
# the result entirely or does its own separate query), and the field names
# for that payload are undocumented here. Never guess a payload shape;
# GitHub itself is the one source of truth this hook needs anyway.
#
# DETECTION is deliberately light — a `gh issue comment` invocation's issue
# number/URL and an optional `-R`/`--repo`. The comment BODY is never parsed
# out of the command text at all (unlike block-ungated-issue-filing.sh's
# heavy heredoc/body-file resolution) — irrelevant here, since the body we
# classify is the one read back from GitHub, not the one in the command.
#
# FRESHNESS — only a comment authored by the current viewer AND posted in
# the last 180s counts as evidence for THIS invocation (an old, already-
# compliant comment from earlier work must not retroactively excuse a
# *different*, just-failed or off-topic `gh issue comment` call).
#
# Never blocks (PostToolUse can't undo a command that already ran) — this is
# an observer, always exits 0. hooks/block-commit-without-design.sh is the
# one that actually stops anything, keyed on the marker this hook writes.

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0
command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0
command -v gh &>/dev/null || exit 0

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[ -n "$CMD" ] || exit 0

# Cheap prefilter before touching python/gh at all.
echo "$CMD" | grep -qE '(^|[;&|]|&&)[[:space:]]*(sudo[[:space:]]+|env[[:space:]]+)?gh[[:space:]]+issue[[:space:]]+comment\b' || exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"

# Data via ARGV, never a pipe into a `python3 -` heredoc — the heredoc
# already claims stdin for the SCRIPT SOURCE (this repo's own recurring
# trap, see subagent-stop-check-run-card.sh).
python3 - "$REPO_ROOT" "$CMD" "$CWD" <<'PYEOF' 2>/dev/null || true
import datetime
import json
import re
import subprocess
import sys

repo_root, cmd, cwd = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo_root)
try:
    import design_gate as dg
    import notify
except Exception:
    sys.exit(0)

if not cwd:
    sys.exit(0)

m = re.search(r'gh\s+issue\s+comment\s+(\S+)', cmd)
if not m:
    sys.exit(0)
target = m.group(1).strip("'\"")

issue = None
mnum = re.match(r'^(\d+)$', target)
if mnum:
    issue = int(mnum.group(1))
else:
    murl = re.search(r'/issues/(\d+)', target)
    if murl:
        issue = int(murl.group(1))
if issue is None:
    sys.exit(0)

mrepo = re.search(r'(?:-R|--repo)[= ]+([^\s\'"]+)', cmd)
explicit_repo = mrepo.group(1) if mrepo else None

if explicit_repo:
    repo_key = explicit_repo.rstrip("/").split("/")[-1]
    gh_repo_args = ["-R", explicit_repo]
else:
    repo_key = notify.repo_name_for(cwd)
    gh_repo_args = []

if not repo_key:
    sys.exit(0)

# Already recorded — never re-hit the network for a settled issue.
if dg.marker_exists(repo_key, issue):
    sys.exit(0)

try:
    r = subprocess.run(
        ["gh", "issue", "view", str(issue)] + gh_repo_args + ["--json", "comments"],
        capture_output=True, text=True, timeout=10, cwd=cwd)
except Exception:
    sys.exit(0)
if r.returncode != 0:
    sys.exit(0)
try:
    comments = json.loads(r.stdout or "{}").get("comments", [])
except (ValueError, TypeError, AttributeError):
    sys.exit(0)
if not isinstance(comments, list):
    sys.exit(0)

FRESH_WINDOW_S = 180
now = datetime.datetime.now(datetime.timezone.utc)


def parsed_ts(c):
    raw = c.get("createdAt") or ""
    try:
        return datetime.datetime.strptime(
            raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


candidates = []
for c in comments:
    if not isinstance(c, dict) or not c.get("viewerDidAuthor"):
        continue
    ts = parsed_ts(c)
    if ts is None or (now - ts).total_seconds() > FRESH_WINDOW_S:
        continue
    candidates.append((ts, c))

if not candidates:
    sys.exit(0)
candidates.sort(key=lambda pair: pair[0])
_, latest = candidates[-1]

ok, reason = dg.classify_design_comment(latest.get("body", ""))
if not ok:
    sys.exit(0)

dg.write_marker(repo_key, issue, latest.get("url", ""), reason)
PYEOF

exit 0
