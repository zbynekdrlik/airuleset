"""cli_doctrine_audit — find and retire per-stream restatements of GRADUATED
fleet doctrine (#1028).

Root cause it exists for: a rule that GRADUATES to the fleet (becomes an
airuleset module/skill) leaves its per-stream restatement behind in
``~/.claude/projects/*/memory/*.md`` (auto-memory) and project
``.claude/rules/*.md`` files, each in slightly different wording, so the same
doctrine DRIFTS across streams. This is the #1027/#1033 incident: the ack-emoji
rule lived as a per-stream memory stopgap and had to be re-taught per sub-dev
("to ma byt airuleset zasada a nie jak blby to musim riesit s kazdym subdevom").

The module is the MATCHER + REWRITE only — pure functions, STDLIB ONLY, no side
effects at import (safe for the watchdog and tests to import at module load).
The three CONSUMERS live elsewhere and reuse existing machinery:
  (a) CLI  ``airuleset.py doctrine-audit [--fix]``          (SUBCOMMANDS dispatch)
  (b) install: ``airuleset._run_doctrine_audit_step()``     (cmd_install step)
  (c) conformance Job 34 dimension ``doctrine-drift``       (watchdog/conformance.py)

Confidence / action:
  HIGH   → a memory whose content anchor-matches an entry in the explicit
           GRADUATED-RULES allowlist (>= MIN_ANCHORS_HIGH anchors) AND carries no
           client-specific "keep" token AND is not an owner-preference memory.
           action = REWRITE (archive the original, replace body with a one-line
           pointer). Auto-fixable.
  MEDIUM → an anchor match that ALSO carries a client-specific token (a blanket
           rewrite would lose real content — e.g. `miva-zero-manual-attendance-
           work.md` keeps its MIVA-specific part), OR a fuzzy title/description
           match against an airuleset module/skill heading. action = LIST (human
           review). NEVER auto-rewritten.
  keep   → an owner-PREFERENCE memory (`feedback_*` filename or `type: feedback`
           frontmatter) OR a fleet-installed file (a symlink / a file under the
           airuleset repo dir). Never touched. action = KEEP (symlinks/repo files
           are skipped silently before classification and produce no row).
"""

import datetime
import difflib
import glob
import os
import re
from collections import namedtuple

# --------------------------------------------------------------------------- #
# Verdict record + constants
# --------------------------------------------------------------------------- #
DoctrineMatch = namedtuple(
    "DoctrineMatch",
    ["path", "fleet_source", "heading", "fleet_since",
     "confidence", "action", "anchors_hit"],
)

HIGH = "high"
MEDIUM = "medium"

ACTION_REWRITE = "rewrite"
ACTION_LIST = "list"
ACTION_KEEP = "keep"

MIN_ANCHORS_HIGH = 2
FUZZY_RATIO_THRESHOLD = 0.72

POINTER_PREFIX = "See airuleset "

# Client / stream identities. A tenant token present alongside an anchor match
# DOWNGRADES HIGH → MEDIUM (the file mixes graduated doctrine with client-
# specific content, so a blanket rewrite would lose the client part). It NEVER
# creates a match on its own, so a false positive only ever errs SAFE (listed,
# not auto-fixed). Matched at a word boundary, case-insensitively.
TENANT_TOKENS = ["miva", "montalu", "david", "simap", "dominika", "marek", "gk"]
_TENANT_RE = re.compile(r"\b(?:%s)\w*" % "|".join(TENANT_TOKENS), re.IGNORECASE)

# --------------------------------------------------------------------------- #
# GRADUATED-RULES allowlist — the client-messaging family (#1027 / #1033 and the
# neighbouring compose doctrine). Each entry: the fleet source path#heading, the
# fleet version it graduated in, and 3-6 SK+EN anchor phrases (lowercase; matched
# as case-insensitive substrings). Extend this list as more rules graduate.
# --------------------------------------------------------------------------- #
FLEET_SINCE = "0.1.303"          # the #1027 / #1033 merge version

ALLOWLIST = [
    {
        "id": "ack-reaction",
        "fleet_source": "skills/odoo-client-messaging/ack-reaction.md",
        "heading": "Acknowledging a client message — worker reaction",
        "fleet_since": FLEET_SINCE,
        # DESCRIPTIVE rule phrases, not bare identifiers. `ack_reaction_emoji`
        # (a config key) and 👷 (an emoji) appear in TRACKING / meta notes that
        # merely REFERENCE the rule's implementation (a plan-of-record surfaced
        # HIGH in the #1028 smoke, saved from auto-rewrite only by luck of a
        # tenant token) — so the identifier is kept but needs a DESCRIPTIVE
        # anchor to corroborate before a HIGH (>= 2) verdict.
        "anchors": [
            "add the ack reaction",
            "ack reaction before replying",
            "ack reaction before composing",
            "react with the worker",
            "worker reaction on the client",
            "ack_reaction_emoji",
            "evidujeme, pracujeme na tom",
        ],
    },
    {
        "id": "no-interim-workaround",
        "fleet_source": "skills/odoo-client-messaging/handover-compose.md",
        "heading": "No interim workaround reply while a fix is in flight",
        "fleet_since": FLEET_SINCE,
        # DISTINCTIVE, client-scoped phrases only — a bare "workaround" / a lone
        # Slovak "zatiaľ"/"medzitým" is far too generic (it false-matched a
        # user-terminal-environment memory in the #1028 live smoke test, whose
        # "interim workaround preňho" is about the USER, not a client message).
        "anchors": [
            "never push manual work onto the client",
            "manual work pushed onto the client",
            "human work pushed onto the client",
            "no interim workaround",
            "interim workaround while",
            "interim manual-workaround",
            "how to work around it",
        ],
    },
    {
        "id": "greeting-etiquette",
        "fleet_source": "skills/odoo-client-messaging/handover-compose.md",
        "heading": "The greeting belongs only in the first message",
        "fleet_since": FLEET_SINCE,
        # DISTINCTIVE multi-word phrases only — a bare "greeting" / "oslovenie" /
        # "no greeting" is far too generic (#1028 review-1 🟡: a UI memory saying
        # "shows no greeting banner" or "show it in the first message only"
        # false-matched HIGH). Each anchor must be specific to the client-message
        # greeting-etiquette rule.
        "anchors": [
            "greeting belongs only in the first message",
            "oslovenie patrí len do prvej správy",
            "continuing message carries no greeting",
            "continuation message carries no greeting",
            "no greeting in a continuing message",
            "greeting only in the first client message",
        ],
    },
    {
        "id": "explain-the-concept",
        "fleet_source": "skills/odoo-client-messaging/handover-compose.md",
        "heading": "Explain the concept to the client",
        "fleet_since": FLEET_SINCE,
        "anchors": [
            "explain the concept to the client",
            "vysvetli klientovi koncept",
            "explain the feature to the client",
            "explain the concept, not the implementation",
            "vysvetli klientovi ako to funguje",
        ],
    },
    {
        "id": "no-promises",
        "fleet_source": "skills/odoo-client-messaging/handover-compose.md",
        "heading": "No promises on the client's behalf",
        "fleet_since": FLEET_SINCE,
        # "no promises on the user's behalf" ⊃ "no promises" — the substring
        # dedup in _anchor_hits already collapses them, but keep only the
        # distinctive full phrases so a bare "no promises" cannot corroborate.
        "anchors": [
            "no promises on the user's behalf",
            "no promises on the client's behalf",
            "never promise on the client's behalf",
            "nesľubuj klientovi v mene",
            "žiadne sľuby v mene klienta",
        ],
    },
    {
        "id": "functional-url",
        "fleet_source": "skills/odoo-client-messaging/handover-compose.md",
        "heading": "Every client message carries a functional URL",
        "fleet_since": FLEET_SINCE,
        "anchors": [
            "functional url in every client message",
            "direct deep-link url",
            "verified live before sending",
            "never a prose menu path",
            "funkčná url v každej klientskej správe",
            "priamy odkaz na živú funkciu",
        ],
    },
]


# --------------------------------------------------------------------------- #
# Parsing helpers (pure)
# --------------------------------------------------------------------------- #
def split_frontmatter(text):
    """Return ``(fm, body)`` — ``fm`` a flat lowercase-keyed dict of the YAML
    frontmatter block's ``key: value`` lines (nested keys flattened, quotes
    stripped), ``body`` the markdown after it. No YAML dependency (stdlib only);
    a shape parse sufficient for the ``name`` / ``description`` / ``type`` keys
    this module reads."""
    fm = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_block, body = parts[1], parts[2]
            for line in fm_block.splitlines():
                mm = re.match(r"\s*([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
                if mm:
                    key = mm.group(1).strip().lower()
                    val = mm.group(2).strip().strip('"').strip("'")
                    fm[key] = val
    return fm, body


def first_heading(body):
    """First markdown heading (``#``.. text) in ``body``, hashes/whitespace
    stripped, or ``""``."""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


def is_owner_preference(path, fm):
    """An owner/user-context memory (NEVER-touch): a ``feedback_*`` /
    ``feedback-*`` filename, or a ``type: feedback`` / ``type: user`` frontmatter
    node. Both record the owner's PREFERENCE or the user's own environment, not a
    copy of a graduated fleet RULE (a rule copy is a plain / ``type: project``
    memory), so neither is ever rewritten."""
    base = os.path.basename(path).lower()
    if base.startswith("feedback_") or base.startswith("feedback-"):
        return True
    return (fm.get("type", "") or "").lower() in ("feedback", "user")


def has_tenant_token(path, text):
    """True when a known client/stream identity appears in the filename or body
    (a client-specific "keep" signal)."""
    return bool(_TENANT_RE.search(os.path.basename(path))
                or _TENANT_RE.search(text))


def is_already_pointer(text):
    """A file already reduced to a fleet pointer — idempotency guard."""
    return text.lstrip().startswith(POINTER_PREFIX)


def _anchor_hits(text_low, anchors):
    """Count DISTINCT, non-overlapping anchor phrases present in ``text_low``.
    A matched anchor that is a substring of another matched anchor is dropped
    (they cannot independently corroborate — e.g. ``"greeting"`` inside
    ``"no greeting"`` must count once, the #1028 review-1 🟡). So a single phrase
    can never satisfy two overlapping anchors and inflate the HIGH threshold."""
    matched = sorted({a.lower() for a in anchors if a and a.lower() in text_low})
    maximal = [a for a in matched
               if not any(a != b and a in b for b in matched)]
    return len(maximal)


def best_allowlist_match(text):
    """Return ``(entry, hits)`` for the allowlist entry with the most anchor
    hits in ``text`` (ties → allowlist order), or ``(None, 0)``. Whitespace is
    collapsed first so a multi-word anchor still matches across a WRAPPED line
    (real memory files hard-wrap mid-phrase — the #1028 fixture caught this)."""
    low = re.sub(r"\s+", " ", text.lower())
    best, best_hits = None, 0
    for entry in ALLOWLIST:
        hits = _anchor_hits(low, entry["anchors"])
        if hits > best_hits:
            best, best_hits = entry, hits
    return best, best_hits


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def fuzzy_module_match(heading, module_headings):
    """Best fuzzy (``difflib.SequenceMatcher``) match of ``heading`` against
    ``module_headings``; returns ``(best_heading, ratio)`` or ``(None, 0.0)``."""
    h = _norm(heading)
    if not h:
        return None, 0.0
    best, best_ratio = None, 0.0
    for mh in module_headings or []:
        r = difflib.SequenceMatcher(None, h, _norm(mh)).ratio()
        if r > best_ratio:
            best, best_ratio = mh, r
    return best, best_ratio


def load_module_headings(repo_dir):
    """Every airuleset module ``### `` heading + every skill's ``name`` /
    ``description`` / ``# `` heading under ``repo_dir`` — the fuzzy-match corpus.
    Best-effort: returns ``[]`` on any error / a repo without those dirs."""
    out = []
    try:
        for md in sorted(glob.glob(os.path.join(repo_dir, "modules", "**", "*.md"),
                                   recursive=True)):
            try:
                with open(md, encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("### "):
                            out.append(line[4:].strip())
                            break
            except OSError:
                continue
        for sk in sorted(glob.glob(os.path.join(repo_dir, "skills", "*", "SKILL.md"))):
            try:
                with open(sk, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            fm, body = split_frontmatter(text)
            if fm.get("name"):
                out.append(fm["name"])
            if fm.get("description"):
                out.append(fm["description"])
            h = first_heading(body)
            if h:
                out.append(h)
    except OSError:
        return []
    return out


# --------------------------------------------------------------------------- #
# Classification (pure)
# --------------------------------------------------------------------------- #
def classify_file(path, text, module_headings=None):
    """Classify ONE file's content → a ``DoctrineMatch`` or ``None``."""
    if is_already_pointer(text):
        return None
    fm, body = split_frontmatter(text)
    heading = first_heading(body) or fm.get("name", "") or os.path.basename(path)
    owner_pref = is_owner_preference(path, fm)

    entry, hits = best_allowlist_match(text)
    if entry and hits >= MIN_ANCHORS_HIGH:
        src, head, since = entry["fleet_source"], entry["heading"], entry["fleet_since"]
        if owner_pref:
            return DoctrineMatch(path, src, head, since, HIGH, ACTION_KEEP, hits)
        if has_tenant_token(path, text):
            return DoctrineMatch(path, src, head, since, MEDIUM, ACTION_LIST, hits)
        return DoctrineMatch(path, src, head, since, HIGH, ACTION_REWRITE, hits)

    # No strong anchor match → fuzzy title/description match against the fleet
    # module/skill headings (a rule that graduated but is not yet allowlisted).
    if module_headings and not owner_pref:
        best, ratio = fuzzy_module_match(heading, module_headings)
        if best and ratio >= FUZZY_RATIO_THRESHOLD:
            return DoctrineMatch(path, best, best, "", MEDIUM, ACTION_LIST, 0)
    return None


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def scan_home(home, repo_dir=None, extra_project_roots=None, module_headings=None):
    """Scan ``home``'s auto-memory (``~/.claude/projects/*/memory/*.md``) — and,
    when ``extra_project_roots`` is given, each root's ``.claude/rules/*.md`` +
    ``.claude/skills/*/SKILL.md`` — for graduated-rule restatements. SKIPS
    symlinks (fleet-installed), any file whose realpath is under ``repo_dir``
    (the fleet SOURCE), and the ``MEMORY.md`` index (never a rule copy). A match
    from ``extra_project_roots`` is FORCED to ``ACTION_LIST`` (read-only — a
    project's committed ``.claude/`` is never auto-rewritten, #1028 review-1 🔵).
    Returns a list of ``DoctrineMatch``. Pure over the FS — reads only, never
    writes."""
    home = os.path.abspath(os.path.expanduser(home))
    if module_headings is None and repo_dir:
        module_headings = load_module_headings(repo_dir)
    module_headings = module_headings or []
    repo_real = os.path.realpath(repo_dir) if repo_dir else None

    files = []          # (path, from_project_root)
    for mem in sorted(glob.glob(os.path.join(home, ".claude", "projects", "*", "memory"))):
        files.extend((f, False) for f in sorted(glob.glob(os.path.join(mem, "*.md"))))
    for pr in (extra_project_roots or []):
        files.extend((f, True) for f in
                     sorted(glob.glob(os.path.join(pr, ".claude", "rules", "*.md"))))
        files.extend((f, True) for f in
                     sorted(glob.glob(os.path.join(pr, ".claude", "skills", "*", "SKILL.md"))))

    out = []
    for f, from_project in files:
        if os.path.basename(f) == "MEMORY.md":
            continue                              # the memory index, never a copy
        if os.path.islink(f):
            continue                              # fleet-installed → never touch
        if repo_real and os.path.realpath(f).startswith(repo_real + os.sep):
            continue                              # fleet source → never touch
        try:
            text = _read(f)
        except OSError:
            continue
        m = classify_file(f, text, module_headings=module_headings)
        if m is None:
            continue
        # A project-root match is READ-ONLY: never auto-rewritten (only the
        # per-user memory store is fixable). Downgrade a rewrite to a listing.
        if from_project and m.action == ACTION_REWRITE:
            m = m._replace(action=ACTION_LIST)
        out.append(m)
    return out


def doctrine_counts(matches):
    """Actionable counts: HIGH = auto-fixable rewrites still present (0 after a
    successful install fix; > 0 means a fix FAILED), MEDIUM = human-review
    listings. Owner-preference ``keep`` rows are excluded."""
    return {
        "high": sum(1 for m in matches if m.action == ACTION_REWRITE),
        "medium": sum(1 for m in matches if m.action == ACTION_LIST),
    }


# --------------------------------------------------------------------------- #
# Rewrite (the only write path) — archive + one-line pointer, idempotent,
# home-scoped
# --------------------------------------------------------------------------- #
def pointer_line(match, today):
    return "%s%s#%s — fleet rule since %s; local copy retired %s" % (
        POINTER_PREFIX, match.fleet_source, match.heading,
        match.fleet_since or "the fleet", today)


def archive_and_rewrite(match, home, today=None):
    """Back ``match``'s file up under ``<home>/.claude/doctrine-archive/<date>/
    <path-relative-to-.claude>`` then replace it with a one-line fleet pointer.
    REFUSES (``ValueError``) any target outside the current user's own
    ``~/.claude`` — the module never writes another user's home."""
    today = today or datetime.date.today().isoformat()
    home_real = os.path.realpath(os.path.expanduser(home))
    claude_real = os.path.join(home_real, ".claude")
    src_real = os.path.realpath(match.path)
    if not src_real.startswith(home_real + os.sep):
        raise ValueError("refusing to rewrite outside home: %s" % match.path)
    rel = os.path.relpath(src_real, claude_real)
    if rel.startswith(".."):
        raise ValueError("refusing to rewrite outside ~/.claude: %s" % match.path)
    archive = os.path.join(claude_real, "doctrine-archive", today, rel)
    os.makedirs(os.path.dirname(archive), exist_ok=True)
    orig = _read(match.path)
    with open(archive, "w", encoding="utf-8") as fh:
        fh.write(orig)
    with open(match.path, "w", encoding="utf-8") as fh:
        fh.write(pointer_line(match, today) + "\n")
    return True


def apply_fixes(matches, home, today=None):
    """Rewrite every HIGH ``rewrite`` match; leave MEDIUM/keep untouched.
    Returns ``{"rewritten": [...], "skipped_medium": [...], "failed": [(path,err)]}``."""
    res = {"rewritten": [], "skipped_medium": [], "failed": []}
    for m in matches:
        if m.action == ACTION_REWRITE:
            try:
                archive_and_rewrite(m, home, today=today)
                res["rewritten"].append(m.path)
            except Exception as e:                # never abort the batch
                res["failed"].append((m.path, str(e)))
        elif m.action == ACTION_LIST:
            res["skipped_medium"].append(m.path)
    return res


def audit(home, repo_dir=None, fix=False, extra_project_roots=None,
          today=None, module_headings=None):
    """Scan ``home`` and (when ``fix``) apply the HIGH rewrites. Returns
    ``(matches, results)``."""
    matches = scan_home(home, repo_dir=repo_dir,
                        extra_project_roots=extra_project_roots,
                        module_headings=module_headings)
    results = {"rewritten": [], "skipped_medium": [], "failed": []}
    if fix:
        results = apply_fixes(matches, home, today=today)
    return matches, results


def format_table(matches):
    """A ``file | matched fleet source | confidence | action`` table."""
    if not matches:
        return "doctrine-audit: no graduated-rule local copies found."
    rows = ["file | matched fleet source | confidence | action",
            "---- | -------------------- | ---------- | ------"]
    for m in sorted(matches, key=lambda x: x.path):
        rows.append("%s | %s | %s | %s" % (
            os.path.basename(m.path), m.fleet_source, m.confidence, m.action))
    return "\n".join(rows)
