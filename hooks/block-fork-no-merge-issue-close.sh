#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash)
# Blocks `gh issue close` (and the equivalent `gh api ... PATCH state=closed`) when
# THIS stream's autopilot authority is REDUCED (fork-no-merge OR branch-merge) —
# UNLESS the issue being closed is the stream's OWN (self-authored). Exit 2 = block;
# Claude sees stderr.
#
# Semantics (refined by the gatekeeper, 2026-07-11; widened to branch-merge #349,
# 2026-08-09):
#   - ASSIGNED / foreign-authored tickets: NEVER closed by a reduced-authority
#     stream itself BEFORE gatekeeper review — self-closing one removes the
#     READY-FOR-REVIEW hand-off event and bypasses review. The CLOSER depends on
#     the repo: on odoo-erp, per owner ruling #5378, once the gatekeeper posts its
#     review-verdict comment and drops the queue label the DELIVERING STREAM closes
#     the ticket itself with an evidence --comment (the #756 verdict-artifact
#     carve-out below permits exactly that); on a repo without that ruling the
#     gatekeeper maintainer closes it (fork-no-merge at cross-fork review/merge,
#     branch-merge only AFTER the full `/process-subdev` release pipeline).
#   - SELF-AUTHORED sub-findings (tickets the stream itself filed while working,
#     e.g. kvaskodev-authored kiosk sub-issues): closing them WITH evidence is the
#     stream's normal bookkeeping — ALLOWED, for BOTH profiles. The 2026-07-10
#     "drift" suspicion was falsified: those ~10 closes were David's own
#     sub-findings, review was NOT bypassed (the hand-off tickets stayed open).
#   The check is mechanical: issue author == the stream's authenticated gh login.
#   Undeterminable (gh error, no auth) → fail-SAFE: block, with the hand-off recipe.
#
# Scope (#349): every REDUCED-authority stream is gated (authority != `full`) — this
# used to exempt `branch-merge` on the assumption its PR "legitimately closes issues
# ... via a merged PR's `Closes #N`", which is FALSE: a branch-merge PR merges into
# the project's INTEGRATION branch, never the repository's actual DEFAULT branch, so
# GitHub's `Closes #N` auto-close never fires there either. A live incident
# (montalu3, 2026-08-09) self-closed three merged tickets with no hand-off at all as
# a direct result. Only `full` authority legitimately closes issues itself —
# resolved per-stream via `airuleset.py authority` (marker-aware).

# #824 review residuals (accepted; the structural fix is python segmenter #837):
#   - A-4: `_strip_value_flags` matches a `-c`/`--comment` INSIDE a double-quoted
#     argument (`echo "pad -c 'A"`) and its single-quote arm spans a REAL top-level
#     close, erasing it from _CMD_STRIPPED → the count under-counts (wrong-ALLOW).
#     Text-only it is indistinguishable from a comment that genuinely mentions the
#     close phrase (the case the strip EXISTS for), so it needs quote-context-aware
#     parsing — deferred to #837. Contrived-but-constructible.
#   - `-R x/y` inside a NON-value quoted arg with BALANCED quotes
#     (`… && echo 'foo -R x/y'`) poisons REPO_ARG. The A-2 unbalanced-quote leg (c)
#     catches only the ESCAPED-quote residue (odd quote count), not a balanced-quote
#     `-R` in a separate arg — also #837.
#   - flag-first widening (correct, not a residual): the #824 value-strip makes
#     `gh issue close --reason done 100` newly extract ISSUE_NUM=100 (the number is a
#     real close target); pinned by test_flag_first_reason_self_close_extracts_number.
command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# #837: the front gate, the `gh api PATCH` close detection (formerly the
# `_is_patch_close_cmd` shell helper), the interpreter/substitution detection, and
# ALL of the EXTRACTION + COUNTING that used to live here as sed/grep now run in the
# python quote/backslash-aware segmenter `hooks/close_guard_segment.py` (invoked once
# below). The signals it emits — IS_CLOSE / N_CLOSE / ISSUE_NUM / REPO_ARG /
# REPO_FLAG_PRESENT / HAS_INTERP / HAS_PATCH_CLOSE / D_REPO_ARG / D_NUMS — are what the
# Discuss gate + carve-outs read. The remaining shell is a thin driver + the network
# `gh` reads. See the #837 design comment for the six-round sed/grep treadmill this
# replaced (A-4 quote-context under-count, REPO_ARG-in-quoted-arg poison, N-6 standalone
# aliased command word, N-7 non-shell interpreter).

# #756: the gatekeeper REVIEW-VERDICT artifact — the SAME signal odoo-erp's
# `subdev-self-close-guard.yml` (#3784) keys on to prove a gk review happened
# (never WHO closed): a case-INSENSITIVE H1-H3 heading that STARTS (after
# non-word decoration) with "gatekeeper" and carries a verdict word
# (review|verification|verdict), OR a line-start GATEKEEPER-CLOSE: marker (#3712).
# The reopen-guard's jq test is `(^|\n)#{1,3}[^\w#\n]*gatekeeper[^\n]*\b(review|
# verification|verdict)\b`; here a PER-LINE grep makes its (^|\n) line-anchor a `^`
# and its [^\n]* stay within one line, so `.*` never spans lines. Requiring an
# actual #{1,3} heading (never #{0,3}) preserves the reopen-guard's self-exemption
# immunity — a bare "gatekeeper review ..." prose line with no leading hash never
# self-satisfies — and #{1,3} (never #{1,6}) mirrors the H1-H3 restriction, so an
# H4+ heading does not match. `[^[:alnum:]_#]` == the reopen-guard's `[^\w#]`
# decoration class (\w = alnum + underscore), so a "## Ready for gatekeeper review"
# readiness line stops at the word char "Ready" and never matches. Called ONLY from
# an `if` condition, so `set -e` is suspended for its whole body — a `grep && return
# 0` chain never aborts the hook on a no-match. It matches the SAME time-window-blind
# artifact the reopen-guard checks; the reopen-guard (POST-close, precise window) is
# the authoritative second net. A DELIBERATELY forged verdict heading is an accepted
# residual (this hook is a CONFUSION guard against a well-meaning agent, not
# adversarial security — the whole system keys on the ARTIFACT, never on WHO closed,
# per odoo-erp#5378).
_has_gk_verdict_artifact() {
    # #772: here-string reads, NOT `printf … | grep -q`. On a long-lived ticket
    # $1 (V_COMMENTS = all comment bodies) is hundreds of KB; under this hook's
    # `set -o pipefail` a `printf '%s\n' "$1" | grep -q` collapses to printf's
    # SIGPIPE-141 exit the moment grep -q short-circuits on the first-line match
    # (printf still blocked writing past the 64KB pipe buffer), so the artifact
    # read as ABSENT and a legitimate #756 self-close was spuriously BLOCKED
    # (recurrence of the #192 SIGPIPE-under-pipefail class). A here-string feeds
    # grep with NO producer process in a pipeline, so no SIGPIPE can arise and
    # pipefail never applies — the command's status is grep's own regardless of
    # input size. The ERE + per-line `^` anchoring are byte-for-byte preserved.
    grep -iqE '^#{1,3}[^[:alnum:]_#]*gatekeeper.*\b(review|verification|verdict)\b' <<< "$1" && return 0
    grep -qE '^GATEKEEPER-CLOSE:' <<< "$1" && return 0
    return 1
}

# #760: shared primitives factored out of the four near-parallel carve-out
# blocks below (author / #533 acceptance / #627 Discuss gate / #756 gk-verdict),
# which each used to re-implement these by COPY — the drift surface the
# #533/#540 lessons warn about (a fix to one parse had to be mirrored by hand to
# the others). Behaviour is BYTE-FOR-BYTE unchanged; only the duplication moves
# up here. Each helper is called ONLY from an `if`/`&&`/`!` condition (or a
# `$(...)` capture whose failure is swallowed by `|| echo ""`), so `set -e`
# never aborts the hook on a grep no-match — the same discipline the two helpers
# above already follow.

# The `--comment`/`-c` presence check (a static presence test, deliberately no
# content grading) shared by the #533 acceptance and #756 verdict carve-outs.
# #824: here-strings (SIGPIPE-immune, the #772/#816 convention). Reads $CMD (the
# ORIGINAL, never $_CMD_STRIPPED): the strip removes `--comment`/`-c` VALUES, so
# running this on the stripped copy would delete its own detection target → the
# acceptance/verdict carve-outs would always read "no --comment" → over-block.
_cmd_has_comment_flag() {
    grep -qE -- '--comment([[:space:]=]|$)' <<< "$CMD" \
      || grep -qE -- '(^|[[:space:]])-c([[:space:]=]|$)' <<< "$CMD"
}

# #837 RETAINED-FOR-SOURCE-LOCK: `_stripped_has_unbalanced_quote` and
# `_repo_flag_unparseable` below are NO LONGER in the live flow — the segmenter's
# REPO_FLAG_PRESENT/REPO_ARG signals drive `_repo_unparseable_signal` instead. They stay
# DEFINED solely because the #816/#824 helper-level SIGPIPE regression tests
# (`TestRepoFlagUnparseableHereString`, `test_cmd_has_comment_flag_helper_is_sigpipe_immune`)
# EXTRACT and drive them from the hook source; their here-string form must not regress.
#
# #824 A-2: does the stripped copy have an UNBALANCED quote — an ODD number of `'`
# or `"`? The ORIGINAL $CMD always has balanced quotes (bash accepted the command),
# so an odd count in the value-stripped copy means the strip broke on an
# escaped-quote value (`'x'\''y'`) and left a residue. Used by
# `_repo_flag_unparseable` leg (c) to refuse a residual `-R`. `tr`/`wc` consume ALL
# stdin (no `grep -q` short-circuit), so no SIGPIPE can arise under pipefail; the
# `wc -c` count is de-whitespaced + numeric-guarded so the arithmetic never aborts.
# Called only from `_repo_flag_unparseable` (itself reached from `if`/`&&`/`!`), so
# `set -e` is suspended for its body.
_stripped_has_unbalanced_quote() {
    local _sq _dq
    _sq=$(tr -cd "'" <<< "$1" | wc -c | tr -d '[:space:]' || true)
    _dq=$(tr -cd '"' <<< "$1" | wc -c | tr -d '[:space:]' || true)
    case "$_sq" in ''|*[!0-9]*) _sq=0 ;; esac
    case "$_dq" in ''|*[!0-9]*) _dq=0 ;; esac
    [ $(( _sq % 2 )) -ne 0 ] || [ $(( _dq % 2 )) -ne 0 ]
}

# The `-R`/`--repo`-present-but-unparseable fail-safe shared by ALL FOUR carve-outs
# (author / #773 fallback / #533 acceptance / #756 verdict) ($1 = the extracted
# REPO_ARG). True (0) iff a -R/--repo FLAG is present in $CMD but the arg came back
# EMPTY (a glued `-Rowner/repo`, a QUOTED glued `'-Rowner/repo'`, or a form the
# REPO_ARG parser cannot read) — the label/comment/author reads would then fall
# back to the CWD repo, so the carve-out must refuse (fail SAFE). A parseable -R
# yields a non-empty arg, so a legit close is unaffected.
#
# #816: the boundary class is the WIDENED `_CLOSE_OPEN` class (#471/#540) PLUS a
# backslash — quote / shell-separator / backslash chars (`'`, `"`, `;`, `&`, `|`,
# `(`, `\`) as well as start/whitespace — NOT the narrow `(^|[[:space:]])`. The old
# class matched a flag ONLY after start-of-string or whitespace, so a QUOTED glued
# flag — `'-Rowner/repo'` / `"-Rowner/repo"` (a quote sits immediately before `-R`)
# — was MISSED: the helper returned FALSE ("not unparseable") though REPO_ARG was
# empty (the `[[:space:]=]+` REPO_ARG parser cannot read a glued form), and all four
# carve-outs then read from the CWD repo → wrong-ALLOW. Widening the OPENING class
# is a monotonic over-block (fail-SAFE): it only ADDS matches, and combined with
# `[ -z "$1" ]` the only new positives are a quote/separator/backslash-preceded flag
# WITH an empty REPO_ARG — exactly the glued wrong-allow. No end-anchor is added: a
# glued `-Rvalue` must still match on the `-R` prefix.
#   The extra `\` beyond `_CLOSE_OPEN`'s class closes the #816-review M2 sibling: a
#   BACKSLASH-escaped glued flag `\-Rowner/repo` — bash strips the `\` so gh receives
#   a valid glued `-R` and closes the named repo, while `$CMD` (the raw text) has `\`
#   immediately before `-R`, which `_CLOSE_OPEN`'s class also missed → the SAME
#   wrong-allow class across all four carve-outs. This is a DELIBERATE superset of
#   `_CLOSE_OPEN` (whose is_close/N_CLOSE gate is out of scope here — widening it
#   needs its own RED matrix, follow-up #824); the two classes are intentionally
#   NOT identical.
#   ACCEPTED over-block residual: a legit self-close carrying NO parseable `-R` flag
#   whose command text contains a quote/separator/backslash immediately before the
#   literal `-R`/`--repo` (most plausibly a `--comment` VALUE — but the grep scans
#   the WHOLE $CMD, so ANY quoted string in a compound command carries it, e.g.
#   `git commit -m "quote the '-Rfoo' example" && gh issue close N --comment ok`)
#   now over-blocks. It fails toward hand-off (the SAME residual `_CLOSE_OPEN`
#   already documents); the worker rephrases or adds an explicit `-R owner/repo`
#   (which makes `[ -z "$1" ]` false, so the text is irrelevant).
#   REMAINING residuals (undetectable by any text scan / out of this helper's scope,
#   all fail-SAFE or pre-existing): a fully `$VAR`-hidden flag (`R=-Rx/y; gh … $R`)
#   carries no literal `-R` in $CMD — a truly separate structural residual, and a
#   confusion-guard (not adversarial-security) accepts it; and the REPO_ARG parser
#   itself text-scans the whole command, so a separator-parseable `-R x/y`-looking
#   string inside a `--comment` VALUE yields a NON-empty REPO_ARG that defeats the
#   `[ -z "$1" ]` leg (pre-existing, not fixable inside this helper) — both tracked
#   in follow-up #824 (front-gate SIGPIPE + comment-value REPO_ARG poisoning).
#
# #816: reads $CMD via a HERE-STRING, NOT `printf '%s' "$CMD" | grep -q` (the #772
# fix for `_has_gk_verdict_artifact`). Under `set -o pipefail`, on a multi-line
# >64KB command whose `-R` match is on an early line with a large trailing tail,
# `grep -q` short-circuits and exits while printf is still blocked writing past the
# 64KB pipe buffer → SIGPIPE → the pipeline returns 141 → the helper returns
# non-zero (FALSE) → the fail-safe is DEFEATED (a wrong-ALLOW — the OPPOSITE fail
# direction from #772, which failed toward block). A here-string feeds grep with no
# producer process, so no SIGPIPE can arise and pipefail never applies. (Extreme
# environmental residual, same trade as the #772 sibling: bash materialises a >64KB
# here-string as a temp file, so an UNWRITABLE TMPDIR would fail the grep → FALSE →
# allow direction; a broken-TMPDIR box is out of this helper's scope.)
# #824: reads $_CMD_STRIPPED (the ORIGINAL $CMD with `--comment`/`-c`/`--reason`/`-r`
# VALUES removed), NOT $CMD — a `-R x/y`-looking string INSIDE a comment/reason value
# must not read as a present-but-unparseable flag (which would over-block a legit
# self-close whose comment merely mentions `-R`). THREE legs, all fail toward BLOCK
# (over-block is the safe direction for this fail-safe):
#   (a) a boundary -R/--repo flag is present in the stripped copy but the
#       STRIPPED-copy REPO_ARG came back empty (a glued `-Rowner/repo`, a
#       quote-glued `'-Rowner/repo'`, or a backslash `\-Rowner/repo` form the
#       `[[:space:]=]+` REPO_ARG parser cannot read) — the pre-#824 leg, now on
#       the stripped copy;
#   (b) the stripped copy carries >=2 -R/--repo boundary tokens — a genuinely
#       ambiguous command, OR a REAL `-R` plus a second `-R` residue from an
#       escaped-quote value. NOTE: this leg only fires when a SECOND `-R` is
#       present, so the SINGLE-residue escaped-quote poison (no real `-R`, just the
#       residue — `gh issue close N --comment 'it'\''s -R x/y'`) is NOT caught here
#       (the pre-#824 comment claimed it was — that overclaim is the #824 A-2 fix);
#   (c) #824 A-2: the stripped copy carries a residual -R/--repo AND an UNBALANCED
#       quote (an odd count of ' or "). The ORIGINAL $CMD has balanced quotes (bash
#       accepted it), so an odd count in the stripped copy proves the value-strip
#       broke on an escaped-quote value and left a `-R …` residue — the residual `-R`
#       is then suspect, so refuse. ACCEPTED over-block residual: a LEGIT self-close
#       carrying BOTH a real `-R` AND an escaped-quote (`'x'\''y'`) comment leaves an
#       odd quote count too → over-blocks (the worker double-quotes the comment).
# A legit close carries EXACTLY one -R and (normally) no escaped-quote residue, so
# these never false-block the common self-close.
_repo_flag_unparseable() {
    local _rf _n_r _src
    _src="${_CMD_STRIPPED:-$CMD}"
    _rf=$(grep -oE '(^|[\;&|[:space:]('\''"])(-R|--repo)' <<< "$_src" 2>/dev/null || true)
    _n_r=$(grep -c . <<< "$_rf" 2>/dev/null || true)
    case "$_n_r" in ''|*[!0-9]*) _n_r=0 ;; esac
    [ "$_n_r" -ge 2 ] && return 0                              # (b) >=2 -R boundary tokens
    if grep -qE '(^|[\;&|[:space:]('\''"])(-R|--repo)' <<< "$_src"; then
        [ -z "$1" ] && return 0                               # (a) -R present but REPO_ARG empty
        _stripped_has_unbalanced_quote "$_src" && return 0    # (c) escaped-quote residue
    fi
    return 1
}

# Whole-line fixed-string label membership ($1 = newline-separated label list,
# $2 = exact label name). Label names carry `:`, so this is `grep -qxF` (whole
# line, fixed string), never a regex. Shared by the #533 (`_has_label`) and #756
# (`_v_has_label`) carve-outs, which differ ONLY in which list they pass.
_labels_contain() {
    grep -qxF "$2" <<< "$1"   # #824: here-string (SIGPIPE-immune convention)
}

# #837: `_strip_value_flags` (the sed value-strip that produced `_CMD_STRIPPED`) is
# GONE — the whole EXTRACTION-on-a-stripped-copy design it fed is replaced by the
# python segmenter, which reads each close segment's own TOKENS quote/backslash-aware
# (a `-c`/`-R` inside a quoted argument is a single token, never a flag), so the A-4
# under-count and the comment-value REPO_ARG poison the strip existed to fight are
# fixed structurally, not with an ever-growing set of pre-deletes and belt legs.

# Reduce a -R/--repo VALUE (or, when $1 is empty, the CWD git remote URL) to a
# full `owner/repo`. Handles the three URL shapes — gh's `owner/repo` -R value,
# https://host/owner/repo[.git], scp `git@host:owner/repo[.git]` — stripping a
# trailing slash + `.git`, then scheme://, user@ and (scp) the host: prefix, then
# keeping the LAST two `/`-separated segments. Shared by the #756 verdict carve-out
# (compares the FULL owner/repo to `zbynekdrlik/odoo-erp`) and the #627 Discuss gate
# (takes `${result##*/}` for its odoo-erp BASENAME compare); lowercasing stays at
# each comparison site (`,,`), exactly as before. The `%/`-`%.git`-`%/` order is
# #627's OWN pre-#760 normalisation → the #627 basename is byte-for-byte for every
# input it handles, and #756 is byte-for-byte for every realistic + tested input
# (owner/repo, https, scp, cwd remote). Documented residual (#319, widened by the
# #760 review): the two ORIGINAL parses already DIFFERED on any TRAILING `/`+`.git`
# JUNK combination — a hand-typed `owner/repo.git/`, a double slash `owner/repo//`,
# and the `.git/` URL variants (`https://…/repo.git/`, `git@…:owner/repo.git/`) —
# where the old #756 (one `%/`, `.git` first) kept the junk while the old #627 (two
# `%/` around `.git`) stripped it; the unified helper strips the whole class. This is
# a strict correctness improvement and NOT a reachable wrong-ALLOW: such a value is
# never emitted by `git remote get-url`, and passed via `-R` it is REJECTED by `gh`
# pre-network ("expected the OWNER/REPO format") → the carve-out's VGH_RC/`gh` fail-
# safe BLOCKS exactly as the old code did; via a hand-mangled cwd remote the owner
# must still literally match, so the strip cannot transmute a fork into the target,
# and both carve-outs still need their own thread/verdict evidence on top.
_repo_owner_repo_of() {
    local _r="$1"
    if [ -z "$_r" ]; then
        _r=$(git remote get-url origin 2>/dev/null || echo "")
    fi
    _r="${_r%/}"
    _r="${_r%.git}"
    _r="${_r%/}"
    _r="${_r#*://}"                                # drop scheme:// (https/ssh)
    _r="${_r##*@}"                                 # drop user@ (ssh)
    case "$_r" in *:*) _r="${_r##*:}" ;; esac      # scp host: prefix
    local _repo="${_r##*/}"
    local _rest="${_r%/*}"
    local _owner="${_rest##*/}"
    printf '%s' "${_owner}/${_repo}"
}

# #837: run the python quote/backslash-aware segmenter ONCE. It replaces the front
# gate, the `gh api PATCH` detection, the interpreter/substitution detection, and the
# whole EXTRACTION + COUNTING layer (ISSUE_NUM / REPO_ARG / N_CLOSE*). A cheap prefilter
# keeps the python spawn off every non-close Bash command: only a command whose
# de-backslashed text (so a `clo\se`/`g\h` obfuscation collapses to the keyword) carries
# `close`, `gh api`, `patch`, `state=`, `--input` or `--method` reaches the segmenter;
# anything else cannot be a close, so exit 0 (fast path). The de-backslash + lowercase is
# a strict SUPERSET (it can only ADD matches), so a genuine close never slips the filter.
# ACCEPTED RESIDUAL (Fable review #837, NOT a regression — IDENTICAL `$VAR`-expansion
# blindness to the pre-#837 front-gate grep): a keyword hidden behind a param expansion
# (`gh issue clo${x}se N`, bash expands `${x}`→empty→`close`) is not seen by this
# de-backslash-only prefilter, so python never runs. The SEGMENTER itself WOULD block
# it (IS_CLOSE=1, HAS_INTERP=1) — only this cheap prefilter misses it; expanding params
# here would reintroduce the very machinery this ticket removed, so a confusion guard
# accepts it, as the old hook did.
_PREFILTER="${CMD//\\/}"
_PREFILTER="${_PREFILTER,,}"
case "$_PREFILTER" in
    *close*|*"gh api"*|*patch*|*state=*|*--input*|*--method*) : ;;   # maybe a close → segment
    *) exit 0 ;;                                                      # definitely not → allow
esac

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo "")
REPO_DIR=$(dirname "$SCRIPT_DIR")
_SEG="$REPO_DIR/hooks/close_guard_segment.py"

# python3 missing / the segmenter missing → fail CLOSED (this prefilter already proved
# the command is close-ish, and a close we cannot analyze must not slip through).
if ! command -v python3 >/dev/null 2>&1 || [ ! -f "$_SEG" ]; then
    echo "BLOCKED (fail-safe): cannot analyze this close — python3 or close_guard_segment.py is unavailable." >&2
    echo "  Refusing 'gh issue close' until the segmenter can run; hand off via a comment and let the maintainer close." >&2
    exit 2
fi

# Here-string feeds the segmenter with NO producer process → SIGPIPE-immune (the
# #772/#816/#824 convention); the segmenter reads the WHOLE payload (never a short-circuit).
_SEG_OUT=$(python3 "$_SEG" <<< "$CMD" 2>/dev/null || true)

# The first line is `OK` iff the analysis completed cleanly; anything else (a crash,
# no output) → fail CLOSED, never a silent allow.
IS_CLOSE=0; N_CLOSE=0; ISSUE_NUM=""; REPO_ARG=""; REPO_FLAG_PRESENT=0
HAS_INTERP=0; HAS_PATCH_CLOSE=0; D_REPO_ARG=""; D_NUMS=""
_SEG_OK=0
while IFS= read -r _line; do
    case "$_line" in
        OK) _SEG_OK=1 ;;
        IS_CLOSE=*)          IS_CLOSE="${_line#IS_CLOSE=}" ;;
        N_CLOSE=*)           N_CLOSE="${_line#N_CLOSE=}" ;;
        ISSUE_NUM=*)         ISSUE_NUM="${_line#ISSUE_NUM=}" ;;
        REPO_ARG=*)          REPO_ARG="${_line#REPO_ARG=}" ;;
        REPO_FLAG_PRESENT=*) REPO_FLAG_PRESENT="${_line#REPO_FLAG_PRESENT=}" ;;
        HAS_INTERP=*)        HAS_INTERP="${_line#HAS_INTERP=}" ;;
        HAS_PATCH_CLOSE=*)   HAS_PATCH_CLOSE="${_line#HAS_PATCH_CLOSE=}" ;;
        D_REPO_ARG=*)        D_REPO_ARG="${_line#D_REPO_ARG=}" ;;
        D_NUMS=*)            D_NUMS="${_line#D_NUMS=}" ;;
    esac
done <<< "$_SEG_OUT"

if [ "$_SEG_OK" != "1" ]; then
    echo "BLOCKED (fail-safe): the close-guard segmenter did not return a clean analysis." >&2
    echo "  Refusing 'gh issue close' until it can; hand off via a comment and let the maintainer close." >&2
    exit 2
fi

# Normalise the numeric signals (defensive: the segmenter always emits digits).
case "$N_CLOSE" in ''|*[!0-9]*) N_CLOSE=0 ;; esac
[ "$IS_CLOSE" = "1" ] || exit 0

# Present-but-unparseable -R fail-safe, from the segmenter's structured signal (a
# -R/--repo flag is present in the close segment but its value is glued/unreadable, so
# a network read would fall back to the CWD repo). Replaces the old `_repo_flag_unparseable`
# text-scan; that helper stays DEFINED below only for the #816/#824 helper-level SIGPIPE
# source-lock tests that extract and drive it.
_repo_unparseable_signal() { [ "$REPO_FLAG_PRESENT" = "1" ] && [ -z "$REPO_ARG" ]; }

# ---------------------------------------------------------------------------
# #627 Discuss closing-note gate — authority-INDEPENDENT, odoo-erp-scoped.
# Runs BEFORE the authority resolution below, so it fires for EVERY closing
# hand: a sub-dev closing its own ticket AND the gatekeeper (full authority)
# closing a branch-merge ticket after the release pipeline. The obligation to
# leave the sub-dev's closing note as the LAST message in a bound Odoo Discuss
# thread FOLLOWS THE TICKET to its CURRENT owner / the closing hand — NEVER the
# author of the thread or the ticket (owner correction, airuleset #627,
# 2026-08-22: tickets move between streams by topic, so binding the obligation
# to the author would park it on a stream that no longer owns the topic). This
# gate never reads authorship — only the ticket's own text — so a ticket that
# MOVED between streams is handled natively (the `Discuss-thread:` binding is a
# durable comment, orthogonal to the mutable `stream:` label, so it survives
# the move — #627 STEP-0).
#
# Recognition = a line-anchored `Discuss-thread: <channel-id>` marker on the
# ticket OR (#695) a `discuss.channel_<N>` deep-URL token in the ticket text
# (the #657-mandated form — so a ticket whose stream forgot the manual mark
# still binds, the montalu5 hole); the close is blocked until a
# `Discuss-closed: <msg-id>` (note posted, last ticket) OR
# `Discuss-defer: <reason>` (deferred to the last ticket) disposition is ALSO
# present. The pure decision is in discuss_close_guard.py (network-free,
# unit-tested); this shell part only detects the issue + repo, fetches the
# ticket text, and pipes it to the module.
#
# EVERY `gh issue close <N>` in the command is checked, not just the first — a
# compound that batch-closes the sibling tickets of one thread (the likely
# N-tickets-one-thread flow) would otherwise smuggle a bound, note-less close
# past a head-1 check (#627 review MAJOR).
#
# FAIL-OPEN throughout — the gate's DEFAULT is ALLOW (most closes are not
# thread-bound), so any unverifiable state (no python3, no `gh issue close`
# number, a gh error, a non-odoo-erp repo) FALLS THROUGH to the authority logic
# below, never a false block (the sibling authority guard's default is BLOCK, so
# it keeps its OWN safe default — each fails toward its own default). Documented
# residuals (#319): a `gh api ... PATCH ... state=closed` REST close is not
# detected here (the dominant `gh issue close N` form — including a COMPOUND of
# several — IS covered); a MIXED-repo compound (a different -R per close) is
# resolved against ONE repo (the command's first -R, else the cwd remote), so a
# bound odoo-erp close smuggled behind a non-odoo-erp -R falls through — the
# natural SAME-repo sibling batch is fully covered; a marker QUOTED in prose
# without a real posted note is a false-PASS (falsifiability + review, the #516
# model). Bypass (rare, logged): `airuleset:discuss-close-ok` anywhere in the
# command (a deliberate, self-opt-in escape hatch — the token appears nowhere
# else, so a close carrying it is unambiguously opting out). Test seam:
# AIRULESET_DISCUSS_CLOSE_FIXTURE=<file> supplies the ticket JSON for EVERY
# target instead of a live `gh issue view`.
# ---------------------------------------------------------------------------
_DHERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
_DREPO="$(dirname "$_DHERE")"
_d_run_gate=1
if grep -q 'airuleset:discuss-close-ok' <<< "$CMD"; then   # #824: here-string; bypass token DETECTION on original $CMD
    _DLOG="/tmp/airuleset-discuss-close-bypass-${EUID:-$(id -u)}.log"
    { echo "$(date -Iseconds)  discuss-close bypass" >> "$_DLOG"; } 2>/dev/null || true
    _d_run_gate=0
fi
command -v python3 >/dev/null 2>&1 || _d_run_gate=0
[ -f "$_DREPO/discuss_close_guard.py" ] || _d_run_gate=0

if [ "$_d_run_gate" = "1" ]; then
    # #837: EVERY clean top-level `gh issue close <N>` number (D_NUMS) + the first
    # close segment's -R (D_REPO_ARG, GLUED-tolerant — `-Rx` reads `x`), both from the
    # segmenter, which reads each close segment's own tokens quote/backslash-aware (a
    # `gh issue close N` mentioned inside a comment value is not a real close; a
    # `-R x/y` inside a quoted argument is never the repo). A compound batch-close of
    # one thread's sibling tickets has EACH target in D_NUMS.
    _D_NUMS="$D_NUMS"
    _D_REPO_ARG="$D_REPO_ARG"
    if [ -n "$_D_NUMS" ]; then
        # odoo-erp repo-scope (Odoo Discuss threads are an odoo-erp / client
        # thing): a non-odoo-erp close never engages the gate, killing the
        # cross-repo meta false-positive (e.g. this very airuleset ticket #627,
        # whose prose names these markers). Resolve the repo from _D_REPO_ARG
        # (this gate's OWN glued-tolerant -R extraction above), else the cwd git
        # remote, via the shared #760 _repo_owner_repo_of helper, then take the
        # BASENAME and compare case-insensitively.
        _D_REPONAME=$(_repo_owner_repo_of "$_D_REPO_ARG")
        _D_REPONAME="${_D_REPONAME##*/}"
        if [ "${_D_REPONAME,,}" = "odoo-erp" ]; then
            # Check EACH close target (numbers are pure digits — safe to word-split).
            # Block on the FIRST bound-no-disposition target found.
            _D_BLOCK_NUM=""
            for _D_NUM in $_D_NUMS; do
                _D_JSON=""
                if [ -n "${AIRULESET_DISCUSS_CLOSE_FIXTURE:-}" ] && [ -f "${AIRULESET_DISCUSS_CLOSE_FIXTURE}" ]; then
                    _D_JSON=$(cat "${AIRULESET_DISCUSS_CLOSE_FIXTURE}" 2>/dev/null || echo "")
                elif [ -n "$_D_REPO_ARG" ]; then
                    _D_JSON=$(gh issue view "$_D_NUM" -R "$_D_REPO_ARG" --json body,comments 2>/dev/null || echo "")
                else
                    _D_JSON=$(gh issue view "$_D_NUM" --json body,comments 2>/dev/null || echo "")
                fi
                if [ -n "$_D_JSON" ]; then
                    _D_VERDICT=$(printf '%s' "$_D_JSON" | python3 "$_DREPO/discuss_close_guard.py" 2>/dev/null || echo "OK")
                    if [ "$_D_VERDICT" = "BLOCK" ]; then
                        _D_BLOCK_NUM="$_D_NUM"
                        break
                    fi
                fi
            done
            if [ -n "$_D_BLOCK_NUM" ]; then
                cat >&2 <<MSG

🚫 BLOCKED: this ticket has a bound Odoo Discuss thread (a Discuss-thread: line
OR a discuss.channel_<N> deep URL on the ticket — the URL alone binds, #695)
but carries no closing-note evidence — closing it now would leave
the client thread with our message (or their question) as the LAST message, then
silence (airuleset #627, owner directive 2026-08-22).

Whoever closes the ticket carries the obligation — it FOLLOWS THE TICKET to its
current owner, never the author. Before this ticket is closed, the sub-dev that
CURRENTLY owns the thread must post a closing note INTO that Odoo Discuss thread
("všetko vyriešené, tému uzatvárame"), so the LAST message in the thread is
always from the sub-dev — then record the evidence on THIS ticket. Add ONE of:

  • the closing note was posted (this is the LAST ticket bound to the thread):
      gh issue comment ${_D_BLOCK_NUM} --body "Discuss-closed: msg <message-id>  (thread <channel-id>)"

  • the thread STAYS OPEN because sibling tickets remain (the closing note goes
    at the LAST close, not here — name the still-open siblings):
      gh issue comment ${_D_BLOCK_NUM} --body "Discuss-defer: siblings #<A> #<B> still open — note goes at the last close"

Then re-run the close.

Both paths:
  • a sub-dev closing its own ticket: YOU post the note + record the line + close.
  • the gatekeeper closing a branch-merge ticket after the release pipeline: the
    OWNING stream posts the note + records Discuss-closed: at hand-off; the
    gatekeeper's close then finds the evidence. The gatekeeper does NOT post to
    the client thread — the stream that owns the thread does.

How to compose + post the closing note (body_is_html, owner on partner_ids,
the identity signature "<MarekAI|ZbynekAI> <N>", #641): skills/odoo-discuss-xmlrpc/handover-compose.md.

Bypass (rare, logged, ONLY a genuine non-client / meta ticket that merely names
these markers in prose): put  airuleset:discuss-close-ok  in the close command.
MSG
                exit 2
            fi
        fi
    fi
fi
# ---- end #627 Discuss gate; fall through to the authority logic below ----

# Resolve THIS stream's authority (marker-aware; the python reads the project
# CLAUDE.md override at the REPO ROOT — #829 anchors `authority` at
# `_repo_root()`, so the marker is honored even when the hook's cwd is a
# SUBDIRECTORY of the project — else the per-user map).
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(dirname "$SCRIPT_DIR")
AUTH=$(python3 "$REPO_DIR/airuleset.py" authority 2>/dev/null || echo "")

# Fail-SAFE: a close is being attempted but authority could not be resolved (the CLI
# errored / is missing). Do NOT silently allow it (that would re-enable the exact
# self-close drift on any infra breakage) — BLOCK with a clear diagnostic. This only
# bites when airuleset.py itself is broken, which already breaks the whole stream.
if [ -z "$AUTH" ]; then
    echo "BLOCKED (fail-safe): could not resolve autopilot authority — 'python3 $REPO_DIR/airuleset.py authority' produced no output (CLI missing/broken?)." >&2
    echo "  Refusing 'gh issue close' until authority can be verified. Fix airuleset.py, then retry — or hand off via a comment and let the maintainer close." >&2
    exit 2
fi
[ "$AUTH" = "full" ] && exit 0

# reduced-authority stream (fork-no-merge OR branch-merge): allow closing a
# SELF-AUTHORED issue (the stream's own sub-finding). Extract the issue number +
# optional -R/--repo from the `gh issue close` form; the `gh api PATCH` form is
# never exempted (use `gh issue close` for legit self-closes).
#
# #349 adversarial review, CRITICAL: a branch-merge stream that authenticates as
# the SAME shared gh identity as the repo's MAINTAINER (the montalu family, marek,
# and miva1 today — treat this as a CLASS, not an enumeration to keep current, as
# streams are added: #821 promoted miva1) — on such a box `ME` (below) resolves to
# the maintainer's own login, and so does the AUTHOR of virtually every
# assigned ticket (the maintainer files them). `ME == AUTHOR` is then ALWAYS
# true regardless of whether the ticket is a genuine self-filed sub-finding or
# the maintainer-assigned work this whole guard exists to protect — a verbatim
# replay of the montalu3 incident closes cleanly through this exemption. The
# discriminator is unusable once `ME` equals the maintainer's own identity, so
# the exemption is refused outright in that case (fail toward hand-off, never
# toward guessing — same direction as the fail-safe above): a shared-identity
# stream loses the self-close bookkeeping shortcut and hands off EVERY closed
# ticket, including its own sub-findings, which costs one extra comment and
# closes the actual regression. A genuinely separate-identity stream
# (fork-no-merge's david, whose gh login is never the maintainer's) is
# unaffected.
# #837: ISSUE_NUM (the top-level close target), REPO_ARG (its -R value, CLEAN — empty
# for a glued form → the present-but-unparseable fail-safe), REPO_FLAG_PRESENT, N_CLOSE
# (top-level close COUNT), HAS_INTERP and HAS_PATCH_CLOSE are ALL provided by the
# segmenter above. The segmenter reads each close segment's own TOKENS quote/backslash-
# aware, so the whole #824 family this block used to fight in sed/grep is gone: a
# quoted/backslashed/aliased command word (`"gh"`/`\gh`/`/usr/bin/gh`) resolves to a
# clean `gh` close and is COUNTED (N-6); an empty-expansion-glued/redirect/brace/ANSI-C
# obfuscation fails CLOSED via HAS_INTERP; a nested `$(…)`/backtick/`${ }`/bash-c/eval/
# python-c close sets HAS_INTERP (N-7); a `-R`/`gh issue close` MENTIONED inside a
# comment value is a single token, never a flag or a counted close (A-4 + the poison).
#
# #533 review C1 single-action guard, UNCHANGED in intent: an exemption may fire ONLY
# when the command is EXACTLY one clean top-level `gh issue close` — not a compound of
# several, not a `gh api PATCH` close, and not a command hiding a nested/obfuscated
# close. Otherwise BLANK ISSUE_NUM, which skips BOTH carve-outs below (each gated on
# `[ -n "$ISSUE_NUM" ]`) and falls through to the BLOCK (fail toward hand-off).
if [ "${N_CLOSE:-0}" -ne 1 ] || [ "$HAS_PATCH_CLOSE" = "1" ] || [ "$HAS_INTERP" = "1" ]; then
    ISSUE_NUM=""   # not a single simple close — fail toward hand-off (no exemption)
fi

if [ -n "$ISSUE_NUM" ]; then
    # THIS box's own gh identity. NOT a raw `gh api user` — on a GitHub
    # App-token box (odoo-erp #3284: montalu/2/3/4, marek, david2/3/4) that
    # 403s structurally ("Resource not accessible by integration"), leaving ME
    # empty and the self-authored carve-out permanently unreachable even for a
    # genuinely App-authored sub-finding (#463). `authority --self-login`
    # returns the fixed stream bot login on such a box (no network call) and
    # the real `gh api user` login (via `_gh_login()`) on every other box — a
    # box whose gh resolves identically stays behaviourally the same, and a
    # git-credentials-only box (david's fork-no-merge) is a strict FAIL-SAFE
    # improvement: the old raw `gh api user` failed there (empty ME -> carve-out
    # broken) while `_gh_login()` resolves its real login; every divergence
    # still fails toward BLOCK, never a wrong-allow (#463 adversarial review).
    # The App identity (distinct from the maintainer) restores the
    # self-vs-assigned distinguishability the pre-App shared-PAT setup destroyed.
    ME=$(python3 "$REPO_DIR/airuleset.py" authority --self-login 2>/dev/null || echo "")
    MAINTAINER_LOGIN=$(python3 "$REPO_DIR/airuleset.py" authority --maintainer-login 2>/dev/null || echo "")
    if [ -n "$REPO_ARG" ]; then
        AUTHOR=$(gh issue view "$ISSUE_NUM" -R "$REPO_ARG" --json author -q .author.login 2>/dev/null || echo "")
    else
        AUTHOR=$(gh issue view "$ISSUE_NUM" --json author -q .author.login 2>/dev/null || echo "")
    fi
    # MAINTAINER_LOGIN unresolvable -> cannot PROVE $ME is not the maintainer
    # -> refuse the exemption (same fail-SAFE direction as the rest of this
    # hook: undeterminable never means "allow").
    # #807: a glued `-Rowner/repo` (no separator) leaves REPO_ARG empty, so the
    # AUTHOR above was read from the CWD repo while the close targets the named
    # one — refuse the exemption via the SAME `_repo_flag_unparseable` fail-safe
    # the #533/#756/#773 carve-outs already use (fail toward hand-off, never a
    # wrong-allow). The author block was kept byte-frozen across #463/#533/#756
    # (the additive-carve-out convention), but this residual can only be closed
    # by tightening the carve-out's own condition; see the #807 design comment.
    if [ -n "$ME" ] && [ -n "$AUTHOR" ] && [ "$ME" = "$AUTHOR" ] \
       && [ -n "$MAINTAINER_LOGIN" ] && [ "$ME" != "$MAINTAINER_LOGIN" ] \
       && ! _repo_unparseable_signal; then
        exit 0   # self-authored sub-finding — the stream's own bookkeeping, allowed
    fi
    # #773: identity FALLBACK for a bot box whose own login could not be
    # resolved. On a GitHub App-token box `authority --self-login` returns the
    # fixed bot login ONLY when `_is_gh_app_token_box()` detects the box (a LOCAL
    # ~/.config/gh-app-tokens/ check); when that detection does not fire it falls
    # to `gh api user`, which 403s on an App token -> ME EMPTY -> the carve-out
    # above is skipped and the stream's OWN bot-authored ticket is blocked (live:
    # montalu2 on odoo-erp #5560). With a SHARED bot identity, "my box's own
    # ticket" and "any stream's own ticket" are already identical (the accepted
    # #463 residual), so when ME is unresolvable the ownership test degenerates
    # to: is this ticket authored by the shared stream App bot? A ticket authored
    # by it was FILED by a stream, NEVER maintainer-assigned (maintainer-assigned
    # work is authored by MAINTAINER_LOGIN) — so a reduced-authority stream may
    # self-close it as its own bookkeeping, exactly as the ME==bot==AUTHOR path
    # above does, without needing the box's own login. The `[ -z "$ME" ]` guard
    # keeps this a STRICT fallback: a resolved (non-bot) identity is unaffected.
    # The #349 discriminator is preserved verbatim — AUTHOR must be the App bot
    # (a maintainer-authored ticket is excluded), and the bot login is != the
    # maintainer by construction (checked defensively). `_repo_flag_unparseable`
    # is the SAME fail-safe the #533/#756 carve-outs use: a -R flag present but
    # unparseable (a glued `-Rowner/repo`) would read AUTHOR from the CWD repo
    # while the close targets the named one — refuse the exemption (fail SAFE).
    # APP_BOT_LOGIN (a static constant, no network call, no App-token-box
    # detection) is fetched lazily INSIDE this branch, so a resolved-identity
    # close never spawns the extra python3.
    if [ -z "$ME" ] && [ -n "$AUTHOR" ] && ! _repo_unparseable_signal; then
        APP_BOT_LOGIN=$(python3 "$REPO_DIR/airuleset.py" authority --app-bot-login 2>/dev/null || echo "")
        if [ -n "$APP_BOT_LOGIN" ] && [ "$AUTHOR" = "$APP_BOT_LOGIN" ] \
           && [ -n "$MAINTAINER_LOGIN" ] && [ "$APP_BOT_LOGIN" != "$MAINTAINER_LOGIN" ]; then
            exit 0   # #773: stream-filed (bot-authored) ticket, self-login unresolvable — allowed
        fi
    fi
fi

# #533: acceptance-close carve-out (ADDITIVE — the author carve-out above is
# BYTE-UNTOUCHED; the `gh api PATCH` form is NEVER exempted here, since ISSUE_NUM
# is empty for it). Authorship is a DEAD ownership signal on a shared-bot-identity
# box (every montalu-stream ticket is authored by the maintainer, so the author
# carve-out refuses even a genuine stream close). Ownership is instead the
# `stream:<user>` LABEL — the SAME signal cli_quals.py's `_ticket_is_stream_labeled`
# / `_slice_quals` already use, and it survives a shared gh identity. A
# REDUCED-authority stream may CLOSE its OWN stream-labeled `needs-acceptance`
# ticket WITH an evidence --comment. Allowed IFF ALL of:
#   (1) `authority --stream-label` is non-empty (this box's own stream label —
#       empty on a full-authority box, so the exemption never fires there);
#   (2) the ticket carries THAT label AND `needs-acceptance` AND NONE of the #512
#       re-hand-off/bounce override labels (ready-for-review / needs-gatekeeper /
#       prio:bounce — a re-hand-off/bounce is NOT an acceptance state);
#   (3) the command carries --comment/-c (close WITH a citation of the acceptance
#       evidence — a static presence check, deliberately no content grading).
# Every failure (empty stream label, unreadable labels, foreign/absent label,
# missing needs-acceptance, any override label present, missing --comment) falls
# through to the BLOCK below — fail toward hand-off, the SAME direction as the
# fail-safe above (#349/#463), never toward a wrong-allow. Why the #349 hole stays
# closed: `needs-acceptance` is applied EXCLUSIVELY by the gatekeeper AFTER the
# /process-subdev release pipeline, so a verbatim montalu3 replay (merged into the
# integration branch, no release yet) carries no such label and still blocks.
ACCEPTANCE_MISSING_COMMENT=0
if [ -n "$ISSUE_NUM" ]; then
    STREAM_LABEL=$(python3 "$REPO_DIR/airuleset.py" authority --stream-label 2>/dev/null || echo "")
    # #533 review m5: if the command carries a -R/--repo flag but REPO_ARG came
    # back EMPTY (a glued `-Rowner/repo`, or a form the parser cannot read), the
    # labels read below would run against the CWD repo instead of the named one —
    # a potential wrong-allow if the cwd repo happens to carry issue N with this
    # stream's acceptance labels. Fail SAFE: mark it unparseable and refuse the
    # exemption. A parseable -R gives a non-empty REPO_ARG, so a legit acceptance
    # close is unaffected.
    # Detect a -R/--repo FLAG token in ANY form — separated (`-R foo`, `-R=foo`),
    # end-of-string, OR GLUED (`-Rfoo`, which the REPO_ARG parser cannot read). If
    # such a flag is present but REPO_ARG is empty, the labels read would fall back
    # to the cwd repo → fail SAFE (shared #760 _repo_flag_unparseable helper).
    REPO_UNPARSEABLE=0
    if _repo_unparseable_signal; then
        REPO_UNPARSEABLE=1
    fi
    if [ -n "$STREAM_LABEL" ] && [ "$REPO_UNPARSEABLE" -eq 0 ]; then
        GH_RC=0
        if [ -n "$REPO_ARG" ]; then
            LABEL_NAMES=$(gh issue view "$ISSUE_NUM" -R "$REPO_ARG" --json labels -q '.labels[].name' 2>/dev/null) || GH_RC=$?
        else
            LABEL_NAMES=$(gh issue view "$ISSUE_NUM" --json labels -q '.labels[].name' 2>/dev/null) || GH_RC=$?
        fi
        # A gh error reading labels is fail-SAFE: never exempt on an unverifiable
        # label set (mirror of the empty-AUTH / empty-AUTHOR fail direction).
        if [ "$GH_RC" -eq 0 ]; then
            # Whole-line fixed-string membership (label names carry `:`; never a
            # regex) — a thin wrapper over the shared #760 _labels_contain helper,
            # bound to THIS block's LABEL_NAMES so _has_own_stream_label stays
            # byte-untouched. Only ever called inside `if`/`&&`/`!` conditions, so
            # `set -e` never aborts on a grep no-match.
            _has_label() { _labels_contain "$LABEL_NAMES" "$1"; }
            # #564: STREAM_LABEL may be MULTIPLE newline-separated equivalents.
            # A base-stream rename (montalu -> montalu1) means this box's own
            # tickets can still carry the OLD `stream:montalu` label during the
            # transition, so `authority --stream-label` emits every equivalent.
            # The acceptance carve-out matches if the ticket carries ANY of them.
            # Read line-by-line (a here-string runs in THIS shell, so `return`
            # works; `IFS= read -r` avoids any word-split / glob expansion — a
            # defensive choice, since `stream:<unix-user>` values never contain
            # whitespace or glob chars anyway). Only ever called inside `if`/`&&`
            # conditions, so `set -e` never aborts on a grep no-match (same reason
            # `_has_label` is safe).
            _has_own_stream_label() {
                local _lbl
                while IFS= read -r _lbl; do
                    [ -n "$_lbl" ] || continue
                    if _has_label "$_lbl"; then return 0; fi
                done <<< "$STREAM_LABEL"
                return 1
            }
            if _has_own_stream_label && _has_label "needs-acceptance" \
               && ! _has_label "ready-for-review" \
               && ! _has_label "needs-gatekeeper" \
               && ! _has_label "prio:bounce"; then
                # Conditions 1+2 hold; condition 3 (--comment/-c) is the last gate
                # (shared #760 _cmd_has_comment_flag helper).
                if _cmd_has_comment_flag; then
                    exit 0   # acceptance close WITH evidence citation — allowed (#533)
                else
                    ACCEPTANCE_MISSING_COMMENT=1
                fi
            fi
        fi
    fi
fi

# #756: gk-verdict-artifact carve-out (ADDITIVE — the author carve-out AND the #533
# acceptance carve-out above are BYTE-UNTOUCHED; the `gh api PATCH` form is NEVER
# exempted here, since ISSUE_NUM is empty for it and the #533 single-action guard
# blanks it for a compound/PATCH/interpreter smuggle). Aligns airuleset with the
# odoo-erp owner ruling #5378: the gatekeeper reviews+merges, posts its verdict,
# DROPS the queue label, and HANDS THE TICKET BACK — the delivering STREAM closes it
# after review. A REDUCED-authority stream may CLOSE a foreign-authored ODOO-ERP
# ticket WITH an evidence --comment IFF ALL of:
#   (1) the repo is EXACTLY `zbynekdrlik/odoo-erp` (full owner/repo, not just the
#       `odoo-erp` basename — a fork/same-named repo has no reopen-guard net) — the
#       ONLY repo where #5378 applies AND where the reopen-guard
#       `subdev-self-close-guard.yml` (POST-close, precise time-window) provides the
#       SECOND net; a `-R` present-but-unparseable falls SAFE (block);
#   (2) the ticket carries a gk review-verdict ARTIFACT in its comments (the SAME
#       #3784 detection — never WHO closed);
#   (3) the ticket carries NONE of `ready-for-review`/`needs-gatekeeper` (gk still
#       owns it), `prio:bounce` (a returned bounce), or `needs-acceptance` (that
#       state is closed via the #533 acceptance carve-out above, which REQUIRES its
#       own --comment client-confirmation citation — excluding it here stops the
#       verdict path from bypassing that requirement);
#   (4) the command carries --comment/-c (an evidence citation).
# Every failure (non-odoo-erp, unparseable -R, unreadable ticket, no artifact, any
# override label, missing --comment) falls through to the BLOCK below — fail toward
# hand-off, the SAME direction as the author/acceptance carve-outs (#349/#463). Why
# the #349 hole stays closed: an unreviewed merged-into-integration ticket carries
# no verdict artifact (gk has not reviewed it) → condition 2 fails → still blocks.
VERDICT_MISSING_COMMENT=0
if [ -n "$ISSUE_NUM" ]; then
    # Repo for the close: -R, else the cwd git remote. Reduce to a FULL `owner/repo`
    # (via the shared #760 _repo_owner_repo_of helper) and require EXACTLY
    # `zbynekdrlik/odoo-erp` (compared lowercased) — a basename-only match (the #627
    # gate's compare) would engage the carve-out for `anyowner/odoo-erp` (a fork or a
    # same-named repo) whose safety net (the reopen-guard) does NOT exist, so the
    # carve-out would GRANT an exemption where #627 only fails OPEN. The helper handles
    # the three URL shapes (gh's `owner/repo` -R value, https, scp `git@host:`).
    VERDICT_REPOFULL=$(_repo_owner_repo_of "$REPO_ARG")
    # #533 review m5 mirror: a -R flag present but REPO_ARG empty (a glued
    # `-Rowner/repo`) → the reads below would target the CWD repo → fail SAFE
    # (shared #760 _repo_flag_unparseable helper).
    VERDICT_REPO_UNPARSEABLE=0
    if _repo_unparseable_signal; then
        VERDICT_REPO_UNPARSEABLE=1
    fi
    if [ "${VERDICT_REPOFULL,,}" = "zbynekdrlik/odoo-erp" ] && [ "$VERDICT_REPO_UNPARSEABLE" -eq 0 ]; then
        VGH_RC=0
        if [ -n "$REPO_ARG" ]; then
            VERDICT_JSON=$(gh issue view "$ISSUE_NUM" -R "$REPO_ARG" --json labels,comments 2>/dev/null) || VGH_RC=$?
        else
            VERDICT_JSON=$(gh issue view "$ISSUE_NUM" --json labels,comments 2>/dev/null) || VGH_RC=$?
        fi
        # A gh error / empty payload reading the ticket is fail-SAFE (never exempt
        # on an unverifiable ticket — the #349/#463 fail direction). Documented
        # residual (#756 review F8, low-confidence, fail-SAFE): `gh issue view --json
        # comments` returns only the first page (~100), so a verdict comment past #100
        # on a very long thread is invisible → false BLOCK (never a false allow). If it
        # ever bites, fetch comments via `gh api …/comments --paginate` like the
        # reopen-guard; the reopen-guard (POST-close) is the authoritative net anyway.
        if [ "$VGH_RC" -eq 0 ] && [ -n "$VERDICT_JSON" ]; then
            # `|| true`: fail-safe by CONSTRUCTION — a jq error (malformed payload)
            # must leave the vars empty (→ `_has_gk_verdict_artifact` fails → block),
            # never abort the whole hook mid-way under `set -e` (which would exit with
            # jq's status and no stderr) (#756 review F5).
            V_LABELS=$(printf '%s' "$VERDICT_JSON" | jq -r '.labels[].name' 2>/dev/null || true)
            V_COMMENTS=$(printf '%s' "$VERDICT_JSON" | jq -r '.comments[].body' 2>/dev/null || true)
            # Whole-line fixed-string membership (label names carry `:`; never a
            # regex) — a thin wrapper over the shared #760 _labels_contain helper,
            # bound to THIS block's V_LABELS. Only ever called inside `if`/`!`
            # conditions, so `set -e` never aborts on a grep no-match.
            _v_has_label() { _labels_contain "$V_LABELS" "$1"; }
            if _has_gk_verdict_artifact "$V_COMMENTS" \
               && ! _v_has_label "ready-for-review" \
               && ! _v_has_label "needs-gatekeeper" \
               && ! _v_has_label "prio:bounce" \
               && ! _v_has_label "needs-acceptance"; then
                # Conditions 1+2+3 hold; condition 4 (--comment/-c) is the last gate
                # (shared #760 _cmd_has_comment_flag helper).
                if _cmd_has_comment_flag; then
                    exit 0   # post-gk-review stream self-close WITH evidence — allowed (#756)
                else
                    VERDICT_MISSING_COMMENT=1
                fi
            fi
        fi
    fi
fi

if [ "$AUTH" = "branch-merge" ]; then
    echo "BLOCKED: branch-merge stream — you may close ONLY your OWN (self-authored) issues." >&2
    echo "" >&2
    echo "  This issue is assigned / foreign-authored (or its author could not be verified):" >&2
    echo "  merging into the project's INTEGRATION branch does NOT close it — that branch is" >&2
    echo "  not the repo's default branch, so GitHub's Closes #N auto-close never fires there." >&2
    echo "  Your authority ENDS at that merge; the gatekeeper closes the ticket only AFTER the" >&2
    echo "  full /process-subdev release pipeline (integration→staging→main + deploy + verify)." >&2
    echo "  Closing it yourself hides the hand-off and skips that review. (Self-authored" >&2
    echo "  sub-findings ARE closable — the hook verifies author == your gh login; if gh failed" >&2
    echo "  just now, fix auth and retry.)" >&2
    echo "" >&2
    echo "  odoo-erp #5378: once the gatekeeper posts its review-verdict comment AND drops the" >&2
    echo "  queue label, the DELIVERING STREAM closes this ticket itself with an evidence" >&2
    echo "  --comment (airuleset #756). Until that verdict lands, hand off and wait." >&2
    echo "" >&2
    echo "  HAND OFF instead, leaving the issue OPEN:" >&2
    echo "    - DONE (merged into integration): gh issue comment <N> --body \"READY-FOR-REVIEW: <PR/branch> — <local verify evidence>\"" >&2
    echo "                       (the repo's hand-off automation labels it ready-for-review; /process-subdev picks it up)" >&2
    echo "                       then fire the card:" >&2
    echo "                       airuleset.py notify --run-card --handoff --repo <owner/name> --issue <N> --goal \"…\" --achieved \"…\"" >&2
    echo "    - OBSOLETE ticket: gh issue comment <N> --body \"OBSOLETE: <evidence>\"   (do NOT close)" >&2
    echo "" >&2
    echo "  See agents/autopilot-worker.md (branch-merge) + skills/process-subdev/SKILL.md." >&2
else
    echo "BLOCKED: fork-no-merge stream — you may close ONLY your OWN (self-authored) issues." >&2
    echo "" >&2
    echo "  This issue is assigned / foreign-authored (or its author could not be verified):" >&2
    echo "  the gatekeeper MAINTAINER closes it at cross-fork review/merge. Closing it yourself" >&2
    echo "  removes the READY-FOR-REVIEW hand-off event and bypasses the review this authority" >&2
    echo "  stream exists to enforce. (Self-authored sub-findings ARE closable — the hook" >&2
    echo "  verifies author == your gh login; if gh failed just now, fix auth and retry.)" >&2
    echo "" >&2
    echo "  odoo-erp #5378: once the gatekeeper posts its review-verdict comment AND drops the" >&2
    echo "  queue label, the DELIVERING STREAM closes this ticket itself with an evidence" >&2
    echo "  --comment (airuleset #756). Until that verdict lands, hand off and wait." >&2
    echo "" >&2
    echo "  HAND OFF instead, leaving the issue OPEN:" >&2
    echo "    - DONE ticket:     gh issue comment <N> --body \"READY-FOR-REVIEW: <branch> — <local verify evidence>\"" >&2
    echo "                       then fire the card:" >&2
    echo "                       airuleset.py notify --run-card --handoff --repo <owner/name> --issue <N> --goal \"…\" --achieved \"…\"" >&2
    echo "    - OBSOLETE ticket: gh issue comment <N> --body \"OBSOLETE: <evidence>\"   (do NOT close)" >&2
    echo "" >&2
    echo "  See agents/autopilot-worker.md (fork-no-merge) + pr-merge-policy.md (reduced-authority scope)." >&2
fi

# #533: ONLY condition 3 failed — ownership + acceptance state were both OK, the
# close was refused solely because it carried no evidence citation. Name the
# acceptance recipe (this hint fires for NO other block reason, so it never
# invites a workaround on a genuinely foreign/assigned ticket).
if [ "${ACCEPTANCE_MISSING_COMMENT:-0}" = "1" ]; then
    echo "" >&2
    echo "  #533 NOTE: this issue carries YOUR stream label + needs-acceptance and no" >&2
    echo "  re-hand-off/bounce label — a stream ACCEPTANCE close IS allowed, but ONLY WITH" >&2
    echo "  a citation of the acceptance evidence. Re-run adding a --comment:" >&2
    echo "    gh issue close $ISSUE_NUM --comment \"<acceptance evidence — client confirmed, ref …>\"" >&2
fi

# #756: ONLY condition 4 failed — this odoo-erp ticket carries a gatekeeper
# review-verdict artifact and no re-hand-off/bounce/acceptance label, so a
# post-review STREAM self-close IS allowed (odoo-erp#5378) but ONLY WITH an
# evidence citation. This hint fires for NO other block reason.
if [ "${VERDICT_MISSING_COMMENT:-0}" = "1" ]; then
    echo "" >&2
    echo "  #756 NOTE: this odoo-erp issue carries a gatekeeper review-verdict comment" >&2
    echo "  and no re-hand-off/bounce/acceptance label — the gatekeeper reviewed+merged" >&2
    echo "  and handed it back, so a stream self-close IS allowed (odoo-erp#5378), but ONLY" >&2
    echo "  WITH an evidence citation. Re-run adding a --comment:" >&2
    echo "    gh issue close $ISSUE_NUM --comment \"<merge + gatekeeper-verdict evidence, ref …>\"" >&2
fi
exit 2
