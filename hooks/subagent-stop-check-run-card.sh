#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop — the per-ticket Discord completion card can no longer be
# silently skipped (#134).
#
# THE FAILURE. Five days, ~85 merged PRs and ~103 closed issues across two
# repos produced ZERO completion cards on the user's phone. Nothing was
# broken: the card is an ACTION WITH NO ARTIFACT ANYONE CHECKS. The mandate
# lives in `agents/autopilot-worker.md` — which does reach the worker, it IS
# its system prompt — but the full-authority evidence block had no card field
# at all, so the supervisor's re-verification had nothing to verify.
# Measured: 2 of 339 real worker evidence blocks mention `cards_fired`.
#
# WHAT THIS GATE DOES. When an `autopilot-worker` stops having claimed a REAL
# merge and at least one CLOSED issue, every such issue must have a DELIVERED
# card marker. Missing → block the stop once, naming the exact command.
#
# WHY "DELIVERED" AND NOT "A MARKER EXISTS". `_dedup_claim` writes the marker
# BEFORE the POST — it has to, or a racing duplicate could double-post — so
# marker presence proves a CLAIM, never a delivery. `notify.marker_delivered`
# is the distinction (#135); this gate keys on it so a card that failed to
# send is caught exactly like one that was never fired.
#
# BOUNDED BY CONSTRUCTION. At most ONE block per (session, repo#issue). A
# worker that genuinely cannot deliver — no Discord config, a dead network —
# is nudged once and then finishes; it is never wedged. That is deliberate:
# this gate is the IN-BAND half, and it is allowed to miss, because watchdog
# job 25 reconciles merged-but-unreported tickets independently and catches
# what this cannot see at all (a worker that DIED mid-run never reaches
# SubagentStop).
#
# NEVER GUESS. A cwd that is not a git repo, an unresolvable `origin`, a
# missing `jq`/`python3`, an unparsable payload — every one of them exits 0
# silently. Unmeasurable is never a block.
#
# Payload fields are live-captured, not assumed (2026-07-28): `agent_type`,
# `cwd`, and `last_assistant_message` as a PLAIN STRING carrying the
# subagent's final message verbatim — so this needs no transcript parsing and
# is immune to the async-transcript-write caveat (#28).

command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

_field() { printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""; }

AGENT_TYPE=$(_field '.agent_type // empty')
[ "$AGENT_TYPE" = "autopilot-worker" ] || exit 0

SID=$(_field '.session_id // empty')
CWD=$(_field '.cwd // empty')
MSG=$(_field '.last_assistant_message // empty')
[ -n "$SID" ] || exit 0
[ -n "$CWD" ] || exit 0
[ -n "$MSG" ] || exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"

# The classifier lives in `notify` (single source of truth for both the
# evidence-block grammar and the marker semantics — watchdog job 25 imports
# the same functions). Data via ARGV, never a pipe into a `python3 -` heredoc:
# the heredoc already claims stdin for the SCRIPT SOURCE, so piped data never
# arrives (the repo's own recurring trap).
MISSING=$(python3 - "$HOOK_DIR" "$CWD" "$MSG" <<'PYEOF' 2>/dev/null || true
import sys, os
sys.path.insert(0, os.path.dirname(sys.argv[1]))
try:
    import notify
except Exception:
    sys.exit(0)                       # unmeasurable -> say nothing
cwd, msg = sys.argv[2], sys.argv[3]
ev = notify.parse_worker_evidence(msg)
if not ev["merged"] or not ev["closed"]:
    sys.exit(0)
# #220 -- prefer the evidence block's own `pr: #<N> <url>` line (the real
# repo the PR landed against) over the payload's static cwd, which can be a
# DIFFERENT repo than the one this worker actually worked in -- this also
# fixes the `--repo <owner>/${REPO}` example command below, since it
# interpolates whatever this resolves to.
repo = notify.resolve_repo_key(cwd, msg=msg)
if not repo:
    sys.exit(0)                       # no resolvable origin -> never guess
missing = [n for n in ev["closed"]
           if not notify.marker_delivered("%s#%d" % (repo, n))]
if missing:
    print(repo)
    print(" ".join(str(n) for n in missing))
PYEOF
)
[ -n "$MISSING" ] || exit 0

REPO=$(printf '%s\n' "$MISSING" | sed -n '1p')
NUMS=$(printf '%s\n' "$MISSING" | sed -n '2p')
[ -n "$REPO" ] && [ -n "$NUMS" ] || exit 0

# One block per (session, repo#issue). The state file is the whole bound.
STATE="/tmp/airuleset-runcard-gate-$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')"
SEEN=$(cat "$STATE" 2>/dev/null || echo "")
FRESH=""
for n in $NUMS; do
    case " $SEEN " in *" ${REPO}#${n} "*) continue ;; esac
    FRESH="${FRESH}${FRESH:+ }${n}"
done
[ -n "$FRESH" ] || exit 0
for n in $FRESH; do SEEN="${SEEN}${SEEN:+ }${REPO}#${n}"; done
printf '%s' "$SEEN" > "$STATE" 2>/dev/null || true

LIST=$(printf '%s' "$FRESH" | sed 's/\([0-9][0-9]*\)/#\1/g')
REASON="Ticket(s) ${LIST} were merged and closed, but no completion card ever reached Discord — the user's phone has no report for them. This is the #134 failure: five days of merged work produced zero reports because the card is prose nobody checks.

Fire one card PER issue now, from this repo:

  python3 ~/devel/airuleset/airuleset.py notify --run-card --repo <owner>/${REPO} --issue <N> --goal \"<jedna jednoduchá veta po slovensky: čo ticket chcel>\" --achieved \"<jedna jednoduchá veta po slovensky: čo sa zmenilo pre používateľa>\" --version \"<verzia prečítaná z DOM pri post-deploy overení>\" --url \"<Popis=https://…kde to vidno naživo>\"

--goal/--achieved must be PLAIN non-technical Slovak (the card is read on a phone), --url is the deep link to SEE the change live, never a PR/diff link. The command now exits non-zero if delivery fails (#135) — if it does, say so on the evidence block's cards_fired: line rather than moving on.

Then add the cards_fired: line to your evidence block. You are blocked once per issue; if the card genuinely cannot be delivered, report that and stop — the watchdog reconciles it independently."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
