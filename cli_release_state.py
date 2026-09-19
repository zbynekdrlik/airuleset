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

  merged_released_still_open(...) — the complementary END state: the fix's
                                    introducing commit is now an ancestor of
                                    origin/main (the release LANDED) yet the
                                    ticket never closed — a release-hygiene
                                    defect surfaced by `core-quals --audit`.

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

Fail-safe EMPTY in every direction — a two-branch repo with no origin/develop,
a git/REST error, an unmapped PR — the never-falsely-done direction: a ticket
whose merge state cannot be derived simply STAYS in `I`, never silently dropped
into `M`. stdlib only; imports nothing from the airuleset package, so it is
import-safe from cli_quals / airuleset / statusbar.
"""

import json
import os
import re
import subprocess
from pathlib import Path

# The develop→staging→main train's non-default branches. `origin/main..origin/X`
# is every commit on X not yet on main — the merged-but-unreleased set.
_UNRELEASED_BRANCHES = ("develop", "staging")

# A PR-introducing commit subject: a merge-commit merge ("Merge pull request #N
# from …") or a squash/rebase merge whose subject ENDS with the PR number as a
# trailing `(#N)`. We read the PR NUMBER ONLY from these two shapes.
_MERGE_PR_RE = re.compile(r"Merge pull request #(\d+)\b")
# END-ANCHORED (#1083 REWORK): GitHub appends the squash/rebase PR number as a
# TRAILING `(#N)`, so a conventional-commit scope earlier in the subject
# ("docs(#7421): … (#7662)") is NEVER read as the PR number — only the final
# `(#7662)` is. A revert "X (#42)" (#50) likewise yields the outer #50.
_SQUASH_PR_RE = re.compile(r"\(#(\d+)\)\s*$")

# TITLE issue refs (#1083 REWORK) — on the target 3-branch repo (odoo-erp) the
# implemented ticket number(s) live in the PR TITLE, not the body, and carry NO
# closing keyword: `#7644 (M1): …`, the multi-ticket `#7631 #7632 …`, and the
# conventional-commit scope `docs(#7421): …`. So EVERY `#N` in the TITLE is an
# implemented ticket (the PR's own number is excluded by the caller); a bare
# number with no `#` ("úloha 980") never matches.
_TITLE_REF_RE = re.compile(r"#(\d+)")

# BODY issue refs — GitHub's OWN auto-close semantics ONLY: a CLOSING KEYWORD
# (close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved) OR a
# `Issue: #N` line, each immediately before `#N`. A BARE `#N` in the BODY is a
# cross-reference (follow-up to #N, part of epic #N, cross-repo owner/repo#N) the
# PR does NOT close, so it must NOT pull an UNRELATED open ticket out of `I` into
# `M` (the never-falsely-done contract; adversarial review #1083, both reviewers,
# BLOCKER). A cross-repo `owner/repo#N` has no space before `#`, so `\s+#` never
# matches it. (The TITLE, unlike the body, IS the ticket carrier — see above.)
_CLOSE_KW_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)")
_ISSUE_LINE_RE = re.compile(r"(?im)^\s*Issue:\s*#(\d+)")

# Per-process memo (keyed ("mu", root) -> frozenset), the repo_identity shape.
_MEMO = {}


def _reset_memo():
    """Clear the per-process memo (tests / a long-lived process boundary)."""
    _MEMO.clear()


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


def _slug_from_git_remote(root):
    """`owner/repo` parsed LOCALLY from `git -C <root> remote get-url origin` —
    no network, unlike `gh repo view`. Returns None on any failure. This keeps
    the hot `--count`/footer path off a `gh repo view` per invocation
    (adversarial review #1083): the slug is needed to NAME the cache + build the
    PR-meta REST url, both derivable from the local remote."""
    if not root:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = _GITHUB_SLUG_RE.search((r.stdout or "").strip())
    return m.group(1) if m else None


def _resolve_slug(root, slug, slug_fn):
    """The `owner/repo` for `root`: an explicit `slug` wins; else `slug_fn()`
    (a caller-supplied resolver); else the LOCAL git remote (no network). Shared
    by both public functions so they name the SAME cache file by construction."""
    if slug:
        return slug
    if slug_fn is not None:
        s = slug_fn()
        if s:
            return s
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
    """A `pr_meta_fn(pr) -> (title, body) | None` that reads ONE PR via
    `gh api repos/<slug>/pulls/<N>`. Returns None on any error so the PR is left
    UNCACHED (retried next refresh) and contributes no issues — the ticket stays
    in `I` (never falsely dropped to `M`)."""
    def _meta(pr):
        try:
            r = subprocess.run(
                ["gh", "api", "repos/%s/pulls/%d" % (slug, pr),
                 "--jq", "{title: .title, body: .body}"],
                capture_output=True, text=True, timeout=20)
        except Exception:
            return None
        if r.returncode != 0:
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
    Two carriers (#1083 REWORK):
      * TITLE — EVERY `#N` is an implemented ticket (the odoo-erp convention: the
        ticket number(s) live in the title, incl. the `docs(#N):` scope form and
        the multi-ticket `#7631 #7632` form);
      * BODY — ONLY GitHub closing-keyword refs + `Issue: #N` lines; a bare body
        `#N` is a cross-reference and deliberately NOT counted (see `_CLOSE_KW_RE`)."""
    refs = {int(m) for m in _TITLE_REF_RE.findall(title or "")}
    body_text = body or ""
    refs |= {int(m.group(1)) for m in _CLOSE_KW_RE.finditer(body_text)}
    refs |= {int(m.group(1)) for m in _ISSUE_LINE_RE.finditer(body_text)}
    refs.discard(int(exclude_pr))
    return refs


def _pr_introducing_commits(root, git_fn):
    """{pr_number: oid} for every PR whose introducing commit is in
    origin/main..origin/develop or origin/main..origin/staging. A range with a
    missing ref yields [] (the two-branch case). First-seen oid wins per PR."""
    out = {}
    for br in _UNRELEASED_BRANCHES:
        rng = "origin/main..origin/%s" % br
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


def _compute_merged_unreleased(root, git_fn, pr_meta_fn, cache_path, slug,
                               slug_fn):
    pr_commits = _pr_introducing_commits(root, git_fn)
    if not pr_commits:
        return frozenset()   # two-branch repo / no merged-unreleased commits
    # Resolve the slug only now that there IS a non-empty range — from the LOCAL
    # git remote (no network) so the hot `--count`/footer path pays zero gh; a
    # two-branch repo short-circuited above and never reaches here.
    if slug is None:
        slug = _resolve_slug(root, None, slug_fn)
    if cache_path is None:
        cache_path = _default_cache_path(slug)
    if pr_meta_fn is None:
        pr_meta_fn = _default_pr_meta_fn(slug) if slug else (lambda pr: None)
    cache = _load_cache(cache_path)
    if not isinstance(cache, dict):
        cache = {}
    dirty = False
    issues = set()
    for pr, oid in pr_commits.items():
        key = str(pr)
        entry = cache.get(key)
        if not isinstance(entry, dict) or "issues" not in entry:
            meta = pr_meta_fn(pr)
            if meta is None:
                continue   # REST failed — leave uncached, retry next refresh
            title, body = meta
            entry = {"issues": sorted(_issue_refs(title, body, pr)), "oid": oid}
            cache[key] = entry
            dirty = True
        elif not entry.get("oid"):
            entry["oid"] = oid   # backfill an oid-less (legacy-shaped) entry
            dirty = True
        for n in entry.get("issues", []):
            issues.add(int(n))
    if dirty:
        _save_cache(cache_path, cache)
    return frozenset(issues)


def merged_unreleased_issues(root, git_fn=None, pr_meta_fn=None,
                             cache_path=None, now=None, slug=None, slug_fn=None):
    """The set of issue numbers whose fix PR is merged into develop/staging but
    NOT yet in main (`M`). `slug` names the `owner/repo` for the PR-meta REST
    read + the cache filename; pass `slug_fn` instead to resolve it LAZILY (only
    when the git range is non-empty), so a two-branch / no-merge `--count`
    refresh pays zero gh. `now` is accepted for signature stability (the cache is
    append-only; a merged PR never changes). Memoised per process, BYPASSED when
    a git/PR seam is injected (tests)."""
    root = str(root or "").rstrip("/")
    if not root:
        return frozenset()
    injected = git_fn is not None or pr_meta_fn is not None
    if not injected:
        memo = _MEMO.get(("mu", root))
        if memo is not None:
            return memo
    result = _compute_merged_unreleased(
        root, git_fn or _default_git_log, pr_meta_fn, cache_path, slug, slug_fn)
    if not injected:
        _MEMO[("mu", root)] = result
    return result


def merged_released_still_open(root, open_numbers, is_ancestor_fn=None,
                               cache_path=None, slug=None, slug_fn=None):
    """From the append-only PR cache, the OPEN tickets whose fix PR's introducing
    commit is now reachable from origin/main (the release landed) — a
    release-hygiene defect (the ticket should have closed at the cut). Sorted
    ascending. Empty on a cold cache / no open set / any error.

    ACCEPTED COVERAGE LIMIT (adversarial review #1083): only PRs the box OBSERVED
    while they were in `main..develop` are cached, so a PR that transited
    develop→main between two refreshes is never flagged — a best-effort hygiene
    nag, not an exhaustive audit."""
    root = str(root or "").rstrip("/")
    open_set = {int(n) for n in (open_numbers or [])}
    if not root or not open_set:
        return []
    if cache_path is None:
        cache_path = _default_cache_path(_resolve_slug(root, slug, slug_fn))
    cache = _load_cache(cache_path)
    if not isinstance(cache, dict) or not cache:
        return []
    if is_ancestor_fn is None:
        is_ancestor_fn = _default_is_ancestor
    out = set()
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        # #1083 review: intersect with the OPEN set FIRST, so the per-entry
        # `git merge-base --is-ancestor` subprocess runs ONLY for a PR that
        # references a still-open ticket (a small, bounded set) — never once per
        # cached PR ever (the append-only cache grows unboundedly).
        open_hits = [int(n) for n in entry.get("issues", []) if int(n) in open_set]
        if not open_hits:
            continue
        oid = entry.get("oid")
        if oid and is_ancestor_fn(oid, root):
            out.update(open_hits)
    return sorted(out)
