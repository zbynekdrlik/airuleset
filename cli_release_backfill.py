"""#1399: released PRs the in-range M sweep never saw, and the canonical main ref.

The footer's `C` (fix on main, close it) reads the append-only PR cache of
`cli_release_state`. That cache is filled from `main..develop|staging` only, so a
PR that went develop -> staging -> main while PRs were unreadable (the david3
App token had no `pull_requests:read`) was never cached and its ticket stayed
in `I` for good. `backfill` caches such PRs from the recent `<prefix>/main`
history within the per-refresh REST budget; `on_main_fn` tests ancestry against
the canonical main (`upstream/main` on a fork clone, whose `origin/main` is the
fork's own stale branch)."""
import subprocess
from pathlib import Path

import cli_release_state as rs

# How far back on `<prefix>/main` the backfill looks: PR merge commits, newest
# first (david3: 3000 reach back two months in 0.4 s; plain commits never count).
BACKFILL_COMMITS = 3000
_PR_GREP = r"^Merge pull request #[0-9]+|\(#[0-9]+\)$"


def _default_git_backfill(root, ref, limit):
    """`git log --max-count=<limit> <ref>` as [(oid, subject)], newest first;
    [] on any error. Local refs only, never a fetch (the footer path)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "--format=%H%x09%s", "-E",
             "--grep=" + _PR_GREP, "--max-count=%d" % limit, ref],
            capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    return [tuple(line.split("\t", 1)) for line in (r.stdout or "").splitlines()
            if "\t" in line]


def backfill(root, prefix, cache, cache_path, pr_meta_fn, budget, git_fn=None):
    """#1399: cache PRs already RELEASED to `<prefix>/main` that no in-range sweep
    saw (they went develop -> staging -> main while PRs were unreadable), newest
    first, at most `budget` REST reads. `merged_released_oids` then finds their
    tickets on main (C) instead of leaving them in I. Returns rs.QUOTA /
    rs.NO_PR_ACCESS when gh refused, else None."""
    git_fn = git_fn or _default_git_backfill
    ref = "%s/main" % prefix
    done_path, tip = Path(str(cache_path) + ".backfilled"), _tip(root, ref)
    if tip and _read(done_path) == tip:
        return None     # every PR up to this main tip is cached: no scan
    asked = 0
    for oid, subj in git_fn(root, ref, BACKFILL_COMMITS):
        if asked >= budget:
            break
        pr = rs._pr_number(subj)
        if pr is None or str(pr) in cache:
            continue
        asked += 1
        meta = pr_meta_fn(pr)
        if meta is rs.QUOTA or meta is rs.NO_PR_ACCESS:
            return meta
        # None = no readable PR (a `(#N)` naming an issue, a 404): cached empty, so
        # it never eats the budget again (review); it names no ticket.
        entry = rs._new_entry(*(meta or ("", "")), pr, oid)
        cache[str(pr)] = entry
        fresh = rs._load_cache(cache_path)   # merge: other panes refresh too (review)
        rs._save_cache(cache_path, {**(fresh if isinstance(fresh, dict) else {}),
                                    str(pr): entry})
    if tip and asked < budget:   # the whole window is cached
        _write(done_path, tip)
    return None


def _tip(root, ref):
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
                           capture_output=True, text=True, timeout=8)
    except Exception:   # airuleset:script-ok no tip = scan as before, never raise
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _write(path, text):
    # airuleset:script-ok never-raise: a lost marker only costs one more scan.
    try:
        path.write_text(text)
    except OSError:
        pass


def on_main_fn(root, slug):
    """`is_ancestor_fn(oid, root)` against the canonical `<prefix>/main`, or None
    for a fork clone without canonical refs (no C, never a false one)."""
    prefix, _ = rs._canonical_ref_prefix(root, slug, rs._default_remote_slug(root, "origin"),
                                         rs._default_remote_slug, rs._default_ref_exists)
    if prefix is None:
        return None
    main_ref = "%s/main" % prefix
    return lambda oid, r: rs._default_is_ancestor(oid, r, main_ref)
