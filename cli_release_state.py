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
# from …") or a squash/rebase merge ("Subject (#N)"). We read PR numbers ONLY
# from these two shapes, never a bare `#N` in an arbitrary commit subject.
_MERGE_PR_RE = re.compile(r"Merge pull request #(\d+)\b")
_SQUASH_PR_RE = re.compile(r"\(#(\d+)\)")

# Issue refs inside a PR title+body: bare `#N` plus the GitHub closing keywords
# (Closes/Fixes/Resolves) and `Issue: #N`. The bare form subsumes the keyword
# forms, so ONE bare pass suffices; the PR's own number is excluded by the
# caller. (Design item 1: refs via `#N` / `Closes|Fixes|Resolves #N` / `Issue:`.)
_ISSUE_REF_RE = re.compile(r"#(\d+)")

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


def _default_cache_path(slug, home=None):
    """`~/.claude/tickets-status/pr-issues-<repo-name>.json` — keyed on the repo
    NAME (last path segment of `slug`), the same convention statusbar's cwd cache
    and repo_identity use, so a multi-repo box never collides."""
    base = Path(home) if home else Path.home()
    name = (slug or "unknown").rstrip("/").split("/")[-1] or "unknown"
    return base / ".claude" / "tickets-status" / ("pr-issues-%s.json" % name)


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
        tmp = str(p) + ".tmp"
        Path(tmp).write_text(json.dumps(data))
        os.replace(tmp, p)
    except Exception:
        pass


def _issue_refs(title, body, exclude_pr):
    """The issue numbers referenced in a PR's title+body (bare `#N`, which
    subsumes Closes/Fixes/Resolves/Issue: `#N`), with the PR's OWN number
    removed."""
    text = "%s\n%s" % (title or "", body or "")
    refs = {int(m.group(1)) for m in _ISSUE_REF_RE.finditer(text)}
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
            m = _MERGE_PR_RE.search(subj) or _SQUASH_PR_RE.search(subj)
            if not m:
                continue
            out.setdefault(int(m.group(1)), oid)
    return out


def _compute_merged_unreleased(root, git_fn, pr_meta_fn, cache_path, slug):
    pr_commits = _pr_introducing_commits(root, git_fn)
    if not pr_commits:
        return frozenset()
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
                             cache_path=None, now=None, slug=None):
    """The set of issue numbers whose fix PR is merged into develop/staging but
    NOT yet in main (`M`). `now` is accepted for signature stability (the cache
    is append-only; a merged PR never changes, so no freshness clock is needed).
    Memoised per process, BYPASSED when a git/PR seam is injected (tests)."""
    root = str(root or "").rstrip("/")
    if not root:
        return frozenset()
    injected = git_fn is not None or pr_meta_fn is not None
    if not injected:
        memo = _MEMO.get(("mu", root))
        if memo is not None:
            return memo
    result = _compute_merged_unreleased(
        root, git_fn or _default_git_log, pr_meta_fn, cache_path, slug)
    if not injected:
        _MEMO[("mu", root)] = result
    return result


def merged_released_still_open(root, open_numbers, is_ancestor_fn=None,
                               cache_path=None, slug=None):
    """From the append-only PR cache, the OPEN tickets whose fix PR's introducing
    commit is now reachable from origin/main (the release landed) — a
    release-hygiene defect (the ticket should have closed at the cut). Sorted
    ascending. Empty on a cold cache / no open set / any error."""
    root = str(root or "").rstrip("/")
    open_set = {int(n) for n in (open_numbers or [])}
    if not root or not open_set:
        return []
    if cache_path is None:
        cache_path = _default_cache_path(slug)
    cache = _load_cache(cache_path)
    if not isinstance(cache, dict) or not cache:
        return []
    if is_ancestor_fn is None:
        is_ancestor_fn = _default_is_ancestor
    out = set()
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        oid = entry.get("oid")
        if not oid or not is_ancestor_fn(oid, root):
            continue
        for n in entry.get("issues", []):
            if int(n) in open_set:
                out.add(int(n))
    return sorted(out)
