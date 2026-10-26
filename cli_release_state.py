"""#1083 — release-state derivation for the footer `M` segment + the
core-quals `--audit` release-hygiene line, from GIT (ground truth), never
labels.

Two states of a ticket whose fix PR is merged but whose GitHub `Closes #N`
did NOT auto-close it — the merge went to develop/staging, not the repo's
DEFAULT branch (the odoo-erp 3-branch model, develop → staging → main):

  merged_unreleased_issues(...)   — `M`: the fix is reachable from
                                    origin/develop (or origin/staging) but NOT
                                    yet from origin/main. Nothing for the box to
                                    ACT on until the release cut, so the ticket
                                    LEAVES `I` and counts release readiness.

  merged_released_oids(...)       — the END state: the fix's introducing
                                    commit is on origin/main (the release
                                    LANDED) yet the ticket is open: #1141 C/M.

Both read the SAME append-only per-repo cache
`~/.claude/tickets-status/pr-issues-<slug>.json`: a merged PR's issue refs and
its introducing-commit oid never change, so each PR costs exactly ONE REST read
EVER, and a refresh with no NEW merge commits makes ZERO API calls.

Pure over injected seams (`git_fn`, `pr_meta_fn`, `is_ancestor_fn`,
`cache_path`) so the whole module is hermetically testable; memoised per PROCESS
(a CLI invocation reads git + cache ONCE per repo) — the resolve-once/cache-first
shape watchdog.repo_identity (#1068) uses (its own `memoized` degrades to a plain
compute outside a watchdog sweep, so a genuine module-global memo is used here).
The memo is BYPASSED whenever a seam is injected, so tests always compute fresh.

Fail-safe EMPTY in every direction (no origin/develop, a git/REST error, an
unmapped PR): a ticket whose merge state cannot be derived STAYS in `I`, never
silently dropped into `M`. #1228: a docs/chore/style/ci change implements no
ticket (`cli_commit_kind`, the one stdlib leaf imported here — import-safe from
cli_quals / airuleset / statusbar).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import cli_commit_kind

# The develop→staging→main train's non-default branches. `origin/main..origin/X`
# is every commit on X not yet on main — the merged-but-unreleased set.
_UNRELEASED_BRANCHES = ("develop", "staging")

# A PR-introducing commit subject: a merge-commit merge ("Merge pull request #N
# from …") or a squash/rebase merge whose subject ENDS with the PR number as a
# trailing `(#N)`. We read the PR NUMBER ONLY from these two shapes.
_MERGE_PR_RE = re.compile(r"Merge pull request #(\d+)\b")
# END-ANCHORED (#1083 REWORK): only the TRAILING `(#N)` GitHub appends is the PR
# number, never a conventional-commit scope earlier in the subject
# ("docs(#7421): … (#7662)" -> 7662; a revert "X (#42)" (#50) -> 50).
_SQUASH_PR_RE = re.compile(r"\(#(\d+)\)\s*$")

# TITLE issue refs (#1083 REWORK) — on odoo-erp the implemented ticket(s) live
# in the PR TITLE with NO closing keyword (`#7644 (M1): …`, `#7631 #7632 …`, the
# scope form `fix(#7421): …`), so a title `#N` is a ticket (the caller drops the
# PR's own number; "úloha 980" has no `#`; #1228: a docs/chore title carries
# none). `(?<![\w/])` rejects a cross-repo `owner/repo#N` (delta review #1083).
_TITLE_REF_RE = re.compile(r"(?<![\w/])#(\d+)")

# A revert title (`Revert "<original>"`) UNDOES the fix, so it carries NO ticket:
# the reverted still-open ticket STAYS in `I` (never-falsely-done, #1083 review).
_REVERT_TITLE_RE = re.compile(r'^\s*Revert\s+"')

# BODY issue refs — GitHub's OWN auto-close semantics ONLY: a CLOSING KEYWORD
# (close/fix/resolve and their forms) or an `Issue: #N` line right before `#N`.
# A BARE body `#N` is a cross-reference (follow-up, epic, owner/repo#N) the PR
# does not close, so it never pulls an unrelated open ticket into `M` (#1083
# review BLOCKER); a cross-repo `owner/repo#N` has no space before `#`.
_CLOSE_KW_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)")
_ISSUE_LINE_RE = re.compile(r"(?im)^\s*Issue:\s*#(\d+)")

# Per-process memo (keyed ("mu", root) -> frozenset), the repo_identity shape;
# `_PARTIAL[root]` = why this process's M set is partial ("" = complete).
_MEMO = {}
_PARTIAL = {}
# #1228: PR-cache entries written under the docs rule carry `"v": 2`; an older
# entry is re-judged once from its introducing commit (`_legacy_nothing`).
_CACHE_V = 2


def _reset_memo():
    """Clear the per-process memo (tests / a long-lived process boundary)."""
    _MEMO.clear()
    _PARTIAL.clear()


def merged_unreleased_partial(root):
    """Why this process's M set for `root` is partial, or "" (#1141 rule 3)."""
    return _PARTIAL.get(str(root or "").rstrip("/"), "")


# #1090 INCIDENT GUARDS — the footer `M` sweep must be BOUNDED, FORK-AWARE and
# QUOTA-SAFE (on the david1-4 fork clones each refresh walked 3 800+ PRs with one
# `gh api` call each, never saved, and drained the stream App budget hourly):
# canonical range (a), 60-PR cap (b), 15-meta budget + cache (c), quota stop (d).

# (b) A healthy 3-branch repo holds a few dozen PRs between cuts (gk: 1-14); more
# means stale/forked refs, not release readiness — hide `M`, ZERO REST calls.
MERGED_UNRELEASED_MAX_PRS = 60

# (c) At most this many NEW PR metas per refresh; the cache is saved after EVERY
# new entry, so a killed refresh keeps its progress (the rest fill later).
MERGED_UNRELEASED_META_BUDGET = 15

# #1112 — a SANITY cap on the two-branch `<main>..<prefix>/dev` walk (ZERO
# network, unlike (b)): only a PATHOLOGICAL range (a re-created dev, a graft)
# exceeds it, FAR above a real backlog (~80 tickets × ~5 commits ≈ 400 must NOT
# be hidden); a broken range hides M with a journal line.
MERGED_UNRELEASED_MAX_COMMITS = 2000


class _QuotaSentinel:
    """(d) A distinct marker a `pr_meta_fn` returns when `gh` reported a
    rate-limit / 403 / 429 — NOT the same as `None` (a plain transient miss the
    loop skips-and-retries). Seeing it, the loop stops making requests at once
    rather than hammering the remaining PRs with calls that would all fail."""
    __slots__ = ()

    def __repr__(self):   # pragma: no cover - debugging aid only
        return "QUOTA"


QUOTA = _QuotaSentinel()

# gh prints a rate-limit / 403 / 429 to STDERR; a plain not-found does not.
_QUOTA_STDERR_RE = re.compile(r"(?i)(rate limit|HTTP 403|\b403\b|\b429\b)")


def _default_git_log(root, rng):
    """`git -C <root> log --format=%H<TAB>%s <rng>` -> [(oid, subject), ...],
    OLDEST last (git default). Returns [] on any error OR a missing ref (the
    two-branch case: `origin/develop` does not exist → non-zero rc → []). Reads
    remote-tracking refs only; relies on the session-start fetch discipline and
    NEVER fetches here (a footer/`--count` refresh must not shell a network
    fetch)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "--format=%H%x09%s", rng],
            capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    rows = []
    for line in (r.stdout or "").splitlines():
        oid, _, subj = line.partition("\t")
        if oid:
            rows.append((oid, subj))
    return rows


def _default_git_log_full(root, rng):
    """#1112 — `git -C <root> log --format=<oid US subject US body RS> <rng>` ->
    [(oid, subject, body), ...] for the TWO-BRANCH source, which reads the ticket
    from each commit's SUBJECT + BODY (not per-PR REST). Unit/record separators
    (US=\\x1f, RS=\\x1e) survive a body with newlines/tabs. Returns [] on any
    error OR a missing ref (the caller gates on ref existence first, so a missing
    range never reaches here). No network (remote-tracking refs only; never
    fetches, exactly like `_default_git_log`)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log",
             "--format=%H%x1f%s%x1f%b%x1e", rng],
            capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    rows = []
    for rec in (r.stdout or "").split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f", 2)
        if len(parts) >= 3 and parts[0]:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _default_is_ancestor(oid, root):
    """True IFF commit `oid` is an ancestor of origin/main in the repo at `root`
    (the /goal release proof's own predicate; cf. cli_quals._commit_is_released).
    Fail-safe False (git error, unknown/unfetched commit, missing origin/main)."""
    if not oid or not root:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor",
             str(oid), "origin/main"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return r.returncode == 0


_GITHUB_SLUG_RE = re.compile(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


def _default_remote_slug(root, name):
    """`owner/repo` parsed LOCALLY from `git -C <root> remote get-url <name>` —
    no network, unlike `gh repo view`. Returns None on any failure or when the
    remote does not exist. Generalised (#1090) from the origin-only reader so
    the fork-aware range resolver can compare `origin` against `upstream`."""
    if not root or not name:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", name],
            capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = _GITHUB_SLUG_RE.search((r.stdout or "").strip())
    return m.group(1) if m else None


def _slug_from_git_remote(root):
    """`owner/repo` from the LOCAL `origin` remote (no network, unlike
    `gh repo view`). Keeps the hot `--count`/footer path off a `gh repo view`
    per invocation (adversarial review #1083): the slug NAMEs the cache + builds
    the PR-meta REST url, both derivable from the local remote."""
    return _default_remote_slug(root, "origin")


def _default_ref_exists(root, ref):
    """True IFF a remote-tracking `ref` (e.g. `upstream/main`) is present in the
    repo at `root` — `git rev-parse --verify --quiet <ref>`. Fail-safe False (a
    fork clone that never fetched `upstream` has no `upstream/*` refs, so `M` is
    hidden rather than computed off the wrong branch). No network."""
    if not root or not ref:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet",
             "%s^{commit}" % ref],
            capture_output=True, text=True, timeout=8)
    except Exception:
        return False
    return r.returncode == 0


def _default_ref_date(root, ref):
    """The committer date of a ref's tip (`git log -1 --format=%ci <ref>`), for
    the cap journal line — best-effort, None on any error. No network."""
    if not root or not ref:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ci", ref],
            capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def _canonical_ref_prefix(root, slug, origin_slug, remote_fn, ref_exists_fn):
    """(a) The branch prefix (`origin` / `upstream`) whose `main..develop` range
    IS the canonical release train, fork-aware. `origin_slug` is the local
    `origin` remote's slug, read ONCE by the caller and passed in (so the hot
    footer path never re-spawns `git remote get-url origin` — review #1090).
    Returns `(prefix, reason)`:

      * `("origin", None)` — the normal/canonical clone (or the canonical slug
        is unknown / the local origin remote is unreadable): keep origin,
        byte-identical to #1083.
      * `("upstream", None)` — a FORK clone (local `origin` slug != the resolved
        canonical `slug`) whose `upstream` remote IS the canonical slug AND whose
        `upstream/main` + `upstream/develop` tracking refs are present.
      * `(None, "<reason>")` — a fork clone that cannot reach the canonical refs
        (no matching `upstream`, or its tracking refs were never fetched): `M`
        is HIDDEN, the caller journals `reason` and makes ZERO REST calls."""
    if not slug:
        return ("origin", None)   # canonical unknown -> origin (unchanged)
    if not origin_slug or origin_slug == slug:
        # unreadable local remote -> keep origin (the cap still protects it);
        # or origin IS the canonical slug -> a normal clone.
        return ("origin", None)
    # A FORK: local origin != the canonical slug. Use upstream IFF it is the
    # canonical slug and its tracking refs were actually fetched.
    if remote_fn(root, "upstream") == slug and \
            ref_exists_fn(root, "upstream/main") and \
            ref_exists_fn(root, "upstream/develop"):
        return ("upstream", None)
    return (None,
            "merged-unreleased: fork clone without canonical refs — M hidden")


def _resolve_slug(root, slug, slug_fn, remote_fn=None):
    """The `owner/repo` for `root`: an explicit `slug` wins; else `slug_fn()`
    (a caller-supplied resolver); else the LOCAL `origin` remote (no network).
    Shared by both public functions so they name the SAME cache file by
    construction. `remote_fn` (#1090) overrides the local origin read (tests)."""
    if slug:
        return slug
    if slug_fn is not None:
        s = slug_fn()
        if s:
            return s
    if remote_fn is not None:
        return remote_fn(root, "origin")
    return _slug_from_git_remote(root)


def _default_cache_path(slug, home=None):
    """`~/.claude/tickets-status/pr-issues-<owner>__<repo>.json` — keyed on the
    FULL `owner/repo` slug (sanitized), so two same-named repos under DIFFERENT
    owners (a fork + upstream, two clients' `erp`) never collide on one cache
    (adversarial review #1083; statusbar's own cache is cwd-keyed, not name-keyed,
    so it never collided — this one would have)."""
    base = Path(home) if home else Path.home()
    safe = re.sub(r"[^0-9A-Za-z._-]", "__",
                  (slug or "unknown").strip("/")) or "unknown"
    return base / ".claude" / "tickets-status" / ("pr-issues-%s.json" % safe)


def _default_pr_meta_fn(slug):
    """A `pr_meta_fn(pr) -> (title, body) | QUOTA | None` that reads ONE PR via
    `gh api repos/<slug>/pulls/<N>`.

      * (title, body) on success;
      * QUOTA (#1090 guard d) when `gh` failed with a rate-limit / 403 / 429 —
        the loop stops at once instead of hammering the remaining PRs;
      * None on any OTHER error, so the PR is left UNCACHED (retried next
        refresh) and contributes no issues — the ticket stays in `I` (never
        falsely dropped to `M`)."""
    def _meta(pr):
        try:
            r = subprocess.run(
                ["gh", "api", "repos/%s/pulls/%d" % (slug, pr),
                 "--jq", "{title: .title, body: .body}"],
                capture_output=True, text=True, timeout=20)
        except Exception:
            return None
        if r.returncode != 0:
            if _QUOTA_STDERR_RE.search(r.stderr or ""):
                return QUOTA
            return None
        try:
            d = json.loads(r.stdout or "{}")
        except (ValueError, TypeError):
            return None
        return (d.get("title") or "", d.get("body") or "")
    return _meta


def _load_cache(cache_path):
    try:
        return json.loads(Path(cache_path).read_text())
    except Exception:
        return {}


def _save_cache(cache_path, data):
    # airuleset:script-ok never-raise: a cache write failure must never break a
    # footer/`--count` refresh; the derivation just re-reads git next time.
    try:
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # UNIQUE tmp per writer: the footer/`--count` refresh runs CONCURRENTLY
        # across every pane + the watchdog on the SAME repo cache, so a shared
        # `<p>.tmp` name would let two writers interleave into one inode and
        # os.replace a truncated file (adversarial review #1083). os.replace is
        # atomic; a per-pid tmp makes each writer's replace independent.
        tmp = "%s.%d.tmp" % (p, os.getpid())
        Path(tmp).write_text(json.dumps(data))
        os.replace(tmp, p)
    except Exception:
        pass


def _issue_refs(title, body, exclude_pr):
    """The implemented-ticket numbers for a PR, with the PR's OWN number removed.
    Two carriers (#1083 REWORK): the TITLE, where EVERY `#N` is a ticket (the
    odoo-erp convention, incl. the `fix(#N):` scope and `#7631 #7632` forms; a
    revert title carries none), and the BODY, ONLY closing-keyword refs +
    `Issue: #N` lines (a bare body `#N` is a cross-reference, `_CLOSE_KW_RE`).
    #1228: a docs/chore/style/ci change (or `[no-test: docs…]`) carries none."""
    if cli_commit_kind.implements_nothing(title, body):
        return set()
    title_text = title or ""
    refs = set() if _REVERT_TITLE_RE.match(title_text) else {
        int(m) for m in _TITLE_REF_RE.findall(title_text)}
    body_text = body or ""
    refs |= {int(m.group(1)) for m in _CLOSE_KW_RE.finditer(body_text)}
    refs |= {int(m.group(1)) for m in _ISSUE_LINE_RE.finditer(body_text)}
    refs.discard(int(exclude_pr))
    return refs


def _legacy_nothing(entry, root):
    """#1228: True for a PR-cache entry written before the docs rule (no `v`)
    whose introducing commit is a docs/chore/style/ci change (local git)."""
    return (entry.get("v") != _CACHE_V and cli_commit_kind
            .introducing_commit_implements_nothing(root, entry.get("oid")))


def _pr_introducing_commits(root, git_fn, prefix="origin"):
    """{pr_number: oid} for every PR whose introducing commit is in
    <prefix>/main..<prefix>/develop or <prefix>/main..<prefix>/staging. A range
    with a missing ref yields [] (the two-branch case). First-seen oid wins per
    PR. `prefix` (#1090) is `origin` for a normal clone and `upstream` for a
    fork clone whose canonical refs live on the upstream remote; it defaults to
    `origin` so callers that do not resolve a canonical prefix are unchanged."""
    out = {}
    for br in _UNRELEASED_BRANCHES:
        rng = "%s/main..%s/%s" % (prefix, prefix, br)
        for oid, subj in git_fn(root, rng):
            mm = _MERGE_PR_RE.search(subj)
            if mm:
                pr = mm.group(1)
            else:
                sq = _SQUASH_PR_RE.search(subj)   # trailing (#N) = the PR number
                if not sq:
                    continue
                pr = sq.group(1)
            out.setdefault(int(pr), oid)
    return out


def _two_branch_main_ref(root, prefix, ref_exists_fn):
    """#1112 — the release/default branch a two-branch repo cuts INTO:
    `<prefix>/main`, else `<prefix>/master` where main is absent. None when
    neither exists (M cannot be derived → the caller returns empty)."""
    for base in ("main", "master"):
        ref = "%s/%s" % (prefix, base)
        if ref_exists_fn(root, ref):
            return ref
    return None


def _compute_two_branch(root, git_full_fn, prefix, ref_exists_fn):
    """#1112 — the M set for a TWO-BRANCH repo (dev -> main, no develop/staging).
    Walks `<main-ref>..<prefix>/dev` and derives implemented tickets from each
    commit's SUBJECT + BODY through the SAME `_issue_refs` the 3-branch path uses
    (subject `#N` incl. `feat(#N):`/`(#N)`/`[#N]` forms + body closing-keyword
    refs, with the revert exclusion) — `exclude_pr=0` never discards a real
    ticket. No REST, no cache, no network. Fail-safe EMPTY (missing main ref,
    empty range, or a range over the cap).

    ACCEPTED over-count: like the 3-branch PR-TITLE path, EVERY `#N` in an
    implementing subject is read as a ticket (the fleet writes the ticket as a
    bare subject ref, `fix: [green] #1114 …`); an extra context `#N` or a
    `Merge pull request #N` number is bounded by `bucketize` (#1141), which
    keeps M to open workable tickets. #1228: a docs/chore/style/ci commit (a
    design doc naming an epic) carries no ticket at all (`_issue_refs`).

    ACCEPTED limitations: (a) a revert whose ORIGINAL `feat(#N)` is ALSO in
    range re-adds `#N`; (b) a raw RS byte (\\x1e) in a body truncates its later
    refs; (c) a two-branch FORK yields empty (`_canonical_ref_prefix` returns
    None first) — fail-safe, never false."""
    main_ref = _two_branch_main_ref(root, prefix, ref_exists_fn)
    if main_ref is None:
        return frozenset()
    dev_ref = "%s/dev" % prefix
    commits = git_full_fn(root, "%s..%s" % (main_ref, dev_ref))
    if not commits:
        return frozenset()
    # The two-branch analog of the (b) cap: a range far past a normal
    # between-cuts backlog means stale/forked refs, not real release readiness —
    # hide M with a journal reason (mirrors the 3-branch cap's stderr line).
    if len(commits) > MERGED_UNRELEASED_MAX_COMMITS:
        d_main = _default_ref_date(root, main_ref)
        d_dev = _default_ref_date(root, dev_ref)
        sys.stderr.write(
            "merged-unreleased: %d commits in %s..%s range "
            "(main %s, dev %s) — stale/oversized range, M hidden\n"
            % (len(commits), main_ref, dev_ref, d_main or "?", d_dev or "?"))
        _PARTIAL[root] = "M hidden: %d commits in range" % len(commits)
        return frozenset()
    issues = set()
    for oid, subj, body in commits:
        for n in _issue_refs(subj, body, 0):
            issues.add(int(n))
    return frozenset(issues)


def _compute_merged_unreleased(root, git_fn, git_full_fn, pr_meta_fn, cache_path,
                               slug, slug_fn, remote_fn, ref_exists_fn):
    _PARTIAL[root] = ""    # set below when this sweep is truncated
    if remote_fn is None:
        remote_fn = _default_remote_slug
    if ref_exists_fn is None:
        ref_exists_fn = _default_ref_exists
    if git_full_fn is None:
        git_full_fn = _default_git_log_full
    # Read the LOCAL `origin` remote ONCE (no network) and reuse it for BOTH the
    # slug resolution and the fork check (review #1090 — avoids a redundant
    # `git remote get-url origin` on the hot footer path).
    origin_slug = remote_fn(root, "origin")
    # Resolve the canonical slug FIRST — from an explicit slug, a caller
    # resolver, the SHARED fork-aware resolver, or that origin read. Cheap on
    # every path, and (#1090) needed BEFORE the range so a fork clone reads the
    # canonical branches, not its own stale `origin/*`.
    if slug is None:
        slug = (slug_fn() if slug_fn is not None else None)
        if not slug:
            # #1094: the ONE fleet resolver (gh-resolved=base / upstream /
            # origin) shared with the open-issue snapshot, so the M sweep and the
            # footer agree on one slug + its branches on a fork clone. LOCAL git
            # only — zero network on the hot `--count`/footer path.
            try:
                from gates import ghread
                slug = ghread.canonical_slug(root)
            except Exception:
                slug = None
        if not slug:
            slug = origin_slug
    # (a) Fork-aware range prefix. A fork clone whose canonical refs are absent
    # hides `M` here with a journal reason and makes ZERO REST calls.
    prefix, reason = _canonical_ref_prefix(
        root, slug, origin_slug, remote_fn, ref_exists_fn)
    if prefix is None:
        sys.stderr.write(reason + "\n")
        _PARTIAL[root] = "M hidden: " + reason
        return frozenset()
    # #1112 — a TWO-BRANCH repo (dev -> main, no develop/staging): commits land
    # on `dev` directly carrying the ticket in the SUBJECT, no PR merge commits,
    # so `_pr_introducing_commits` finds nothing. When `<prefix>/dev` exists but
    # NEITHER `<prefix>/develop` NOR `<prefix>/staging` does, derive M from the
    # `<main>..<prefix>/dev` range's commit subjects + bodies. The `dev`-first
    # check short-circuits so a 3-branch (odoo-erp: no `dev`) or a no-`dev` repo
    # pays exactly ONE local `git rev-parse`, and the 3-branch path below stays
    # byte-identical (this branch is NEVER taken when develop/staging exist).
    if (ref_exists_fn(root, "%s/dev" % prefix)
            and not ref_exists_fn(root, "%s/develop" % prefix)
            and not ref_exists_fn(root, "%s/staging" % prefix)):
        return _compute_two_branch(root, git_full_fn, prefix, ref_exists_fn)
    pr_commits = _pr_introducing_commits(root, git_fn, prefix)
    if not pr_commits:
        return frozenset()   # two-branch repo / no merged-unreleased commits
    # (b) Range cap. A range far past a normal between-cuts backlog means stale /
    # forked refs, not real release readiness — hide `M`, ZERO REST calls.
    if len(pr_commits) > MERGED_UNRELEASED_MAX_PRS:
        d_main = _default_ref_date(root, "%s/main" % prefix)
        d_dev = _default_ref_date(root, "%s/develop" % prefix)
        sys.stderr.write(
            "merged-unreleased: %d PRs in %s/main..%s/develop range "
            "(main %s, develop %s) — stale/forked refs, M hidden\n"
            % (len(pr_commits), prefix, prefix, d_main or "?", d_dev or "?"))
        _PARTIAL[root] = "M hidden: %d PRs in range" % len(pr_commits)
        return frozenset()
    if cache_path is None:
        cache_path = _default_cache_path(slug)
    if pr_meta_fn is None:
        pr_meta_fn = _default_pr_meta_fn(slug) if slug else (lambda pr: None)
    cache = _load_cache(cache_path)
    if not isinstance(cache, dict):
        cache = {}
    issues = set()
    new_metas = 0          # (c) new PR metas fetched THIS refresh
    quota_hit = False      # (d) gh reported a rate-limit / 403 / 429
    unread = 0             # PRs left for a later refresh (#1141 ruling 3)
    for pr, oid in pr_commits.items():
        key = str(pr)
        entry = cache.get(key)
        if not isinstance(entry, dict) or "issues" not in entry:
            # (c) budget: never fetch more than the budget of NEW metas per
            # refresh; the rest fill on later refreshes. Cached PRs below still
            # contribute at zero cost.
            if new_metas >= MERGED_UNRELEASED_META_BUDGET:
                unread += 1
                continue
            meta = pr_meta_fn(pr)
            if meta is QUOTA:
                quota_hit = True
                break        # (d) stop hammering; M is partial this refresh
            if meta is None:
                unread += 1  # REST failed — leave uncached, retry next refresh
                continue
            title, body = meta
            entry = {"issues": sorted(_issue_refs(title, body, pr)), "oid": oid,
                     "v": _CACHE_V}
            cache[key] = entry
            new_metas += 1
            _save_cache(cache_path, cache)   # (c) save after EVERY new entry
        elif entry.get("v") != _CACHE_V:
            entry["oid"] = entry.get("oid") or oid   # backfill an oid-less one
            if _legacy_nothing(entry, root):         # #1228: re-judged ONCE
                entry["issues"] = []
            entry["v"] = _CACHE_V
            _save_cache(cache_path, cache)
        for n in entry.get("issues", []):
            issues.add(int(n))
    if quota_hit:
        sys.stderr.write(
            "merged-unreleased: gh quota hit — M partial this refresh\n")
    _PARTIAL[root] = ("gh quota hit" if quota_hit else "%d merged PRs not "
                      "read yet" % unread if unread else "")
    return frozenset(issues)


def merged_unreleased_issues(root, git_fn=None, pr_meta_fn=None,
                             cache_path=None, now=None, slug=None, slug_fn=None,
                             remote_fn=None, ref_exists_fn=None,
                             git_full_fn=None):
    """The set of issue numbers whose fix PR is merged into develop/staging but
    NOT yet in main (`M`). `slug` (or `slug_fn`) names the canonical owner/repo
    for the PR-meta read + the cache file, else it is resolved from the LOCAL
    remote before the range (#1090: zero gh on the hot path, fork-aware). `now`
    is kept for signature stability. Seams: `remote_fn`/`ref_exists_fn` (#1090),
    `git_full_fn` (#1112, two-branch oid+subject+body). Memoised per process,
    BYPASSED when any seam is injected (tests)."""
    root = str(root or "").rstrip("/")
    if not root:
        return frozenset()
    injected = (git_fn is not None or pr_meta_fn is not None
                or remote_fn is not None or ref_exists_fn is not None
                or git_full_fn is not None)
    if not injected:
        memo = _MEMO.get(("mu", root))
        if memo is not None:
            return memo
    result = _compute_merged_unreleased(
        root, git_fn or _default_git_log, git_full_fn, pr_meta_fn, cache_path,
        slug, slug_fn, remote_fn, ref_exists_fn)
    if not injected:
        _MEMO[("mu", root)] = result
    return result


def merged_released_still_open(root, open_numbers, **kw):
    """The `core-quals --audit` hygiene list: `merged_released_oids` numbers."""
    return sorted(merged_released_oids(root, open_numbers, **kw))


def merged_released_oids(root, open_numbers, is_ancestor_fn=None,
                         cache_path=None, slug=None, slug_fn=None):
    """From the append-only PR cache, `{ticket: [introducing commits]}` for the
    OPEN tickets whose fix PR's introducing commit is reachable from
    origin/main (the release landed). #1141 slice 3 turns them into C / M by
    deploy state. Empty on a cold cache / no open set / any error. COVERAGE
    LIMIT: 3-branch repos only (the two-branch path caches no PR), and only
    PRs OBSERVED in `main..develop` (#1083 review); anything else is never
    found, so the ticket stays I — never falsely done."""
    root = str(root or "").rstrip("/")
    open_set = {int(n) for n in (open_numbers or [])}
    if not root or not open_set:
        return {}
    if cache_path is None:
        cache_path = _default_cache_path(_resolve_slug(root, slug, slug_fn))
    cache = _load_cache(cache_path)
    if not isinstance(cache, dict) or not cache:
        return {}
    if is_ancestor_fn is None:
        is_ancestor_fn = _default_is_ancestor
    out = {}
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        # #1083 review: intersect with the OPEN set FIRST, so the per-entry
        # `git merge-base --is-ancestor` subprocess runs ONLY for a PR that
        # references a still-open ticket (a small, bounded set) — never once per
        # cached PR ever (the append-only cache grows unboundedly).
        open_hits = [int(n) for n in entry.get("issues", []) if int(n) in open_set]
        oid = entry.get("oid")
        if (open_hits and oid and not _legacy_nothing(entry, root)   # #1228
                and is_ancestor_fn(oid, root)):
            for n in open_hits:
                out.setdefault(n, []).append(oid)
    return out
