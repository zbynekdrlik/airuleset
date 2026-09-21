"""gates.spec_question -- the Stop-hook Check 10 runner (#1106 spec anchoring).

Reads the ❓ block text on STDIN, resolves the repo slug (a `--cwd` argument,
or the `AIRULESET_SPEC_QUESTION_SLUG` test seam), and checks it against the
per-repo settled-questions cache (`~/.claude/spec-settled/<slug>.json`, refreshed
off the tickets-status refresh -- no gh on this Stop path). A conflict (the
question re-asks a spec's Settled entry) exits 2 with the quoted settled answer
on STDOUT; every other outcome -- no conflict, cache absent, no slug, an empty
block, ANY error -- exits 0 (FAIL-OPEN), the same never-false-block direction
the sibling gates use.
"""
import argparse
import os
import sys


def _slug_via_notify(base):
    """The repo name via notify.repo_name_for, or None (unimportable / no
    remote) -- the first fallback after the test seam."""
    try:
        import notify
        return notify.repo_name_for(base) or None
    except Exception:
        return None


def _slug_via_ghread(base):
    """The repo name via gates.ghread.resolve_slug (last path component), or
    None -- the final fallback."""
    try:
        from gates import ghread
        slug = ghread.resolve_slug(base)
        return slug.rstrip("/").split("/")[-1] if slug else None
    except Exception:
        return None


def _resolve_slug(cwd):
    """The repo NAME (last path component) for `cwd`, or None. The test seam
    `AIRULESET_SPEC_QUESTION_SLUG` wins first; else `notify.repo_name_for`
    (the fleet's cwd->repo-name derivation); else `gates.ghread.resolve_slug`."""
    seam = os.environ.get("AIRULESET_SPEC_QUESTION_SLUG")
    if seam:
        return seam
    base = cwd or os.getcwd()
    return _slug_via_notify(base) or _slug_via_ghread(base)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=None)
    args = parser.parse_args(argv)
    try:
        block = sys.stdin.read()
    except Exception:
        return 0        # unreadable stdin -> fail-open (never a false block)
    if not block or not block.strip():
        return 0
    # Cheap short-circuit: on the vast majority of repos there is NO spec cache
    # at all, and resolving the slug costs a git/notify call. If the whole
    # spec-settled dir is absent or empty, skip before any slug resolution.
    settled_dir = os.path.join(os.path.expanduser("~"), ".claude", "spec-settled")
    try:
        if not os.listdir(settled_dir):
            return 0
    except OSError:
        return 0        # dir absent / unreadable -> fail-open
    try:
        import gates.spec as spec
        slug = _resolve_slug(args.cwd)
        if not slug:
            return 0
        blocked, reason = spec.check_question_against_cache(block, slug)
    except Exception:
        return 0        # any error -> fail-open
    if blocked and reason:
        sys.stdout.write(reason + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
