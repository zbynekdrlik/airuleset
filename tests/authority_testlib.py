"""Shared preamble + module-level helpers for the split-out `tests/test_authority_*.py`
family (#830). Every helper here was moved VERBATIM out of the former
monolithic `tests/test_authority_profiles.py` (4027 lines) so each per-concern
file imports one copy instead of duplicating a body. Not a test file itself
(no `test_*` methods) — imported by the per-concern files."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset  # noqa: E402
# #433 cluster I: `_refuse_unless_empty_is_trustworthy` + `cmd_slice_quals`/
# `cmd_core_quals` moved into the cli_quals_cmd leaf. A test that intercepts the
# SHARED refusal helper must patch it where those commands RESOLVE the bare name
# (the leaf's own globals), not on the airuleset facade attr (K-seam, internals
# #1482).
import cli_quals_cmd  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _bump_done_of(call_args):
    """The EFFECTIVE `bump_done` a call passed, read from either a keyword or
    a positional argument (#181 M-3).

    The round-2 tests asserted `kwargs.get("bump_done")`, which couples the
    lock to call STYLE rather than behaviour: a correct implementation that
    passes the flag positionally false-fails, and a wrong one that accepts the
    kwarg and ignores it passes. `_write_autopilot_progress(name, remaining,
    bump_done=True)` puts it at positional index 2."""
    args, kwargs = call_args
    if "bump_done" in kwargs:
        return kwargs["bump_done"]
    return args[2] if len(args) > 2 else True


def _fake_gh_by_search(populations):
    """A `_gh_out` stand-in that answers an `--search` query with whichever
    populations' keys occur in the search string, unioned.

    It deliberately serves BOTH query shapes so the same fixture measures the
    round-2 implementation (one query, `--json number -q length`, an integer
    on stdout) and this one (per-qual queries, JSON rows unioned in Python) —
    a fixture that only served one of them would fail pre-fix for a parsing
    reason instead of a behavioural one."""
    import json as _json

    searches = []

    def gh(*a, **k):
        args = [str(x) for x in a]
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        searches.append(search)
        nums = set()
        for key, val in populations.items():
            if key in search:
                nums |= set(val)
        if "-q" in args:
            return str(len(nums))
        return _json.dumps(
            [{"number": n, "title": "t%d" % n,
              "createdAt": "2026-07-%02dT00:00:00Z" % (n % 28 + 1)}
             for n in sorted(nums)])

    return gh, searches


def _renamed_repo_gh(rest='[{"number": 4242}]', healthy=False,
                     workflow_state="active", newest_run="success",
                     workflows=None, _NORUN=object()):
    """A `_gh_out` stand-in reproducing the LIVE renamed-repo state.

    Measured 2026-07-30 against a checkout whose `origin` still points at the
    pre-rename `zbynekdrlik/odoo-slovnormal`: GitHub's issue SEARCH index does
    not follow a repo rename, the REST/repository-listing path does. REST 110,
    every `--search` 0, and `core-quals --count` printed `0` with rc 0.
    """
    import json as _json

    def gh(*a, **k):
        args = [str(x) for x in a]
        joined = " ".join(args)
        if args and args[0] == "label":
            return '[{"name": "stream:david"}]'
        if args and args[0] == "workflow":
            if workflows is not None:
                return _json.dumps(workflows)
            return _json.dumps([
                {"name": "Sub-dev Handoff Label", "state": workflow_state,
                 "path": ".github/workflows/subdev-handoff-label.yml"}])
        if args and args[0] == "run":
            if newest_run is None:
                return "[]"                     # the workflow has never run
            if newest_run == "in_progress":
                return _json.dumps([{"conclusion": None,
                                     "status": "in_progress"}])
            return _json.dumps([{"conclusion": newest_run,
                                 "status": "completed"}])
        if "sort:created-desc" in joined:
            return '[{"number": 999}]' if healthy else "[]"
        if "--search" in args:
            return "[]"
        return rest

    return gh


def _drive(cmd, gh, authority="full", user="newlevel", login="zbynekdrlik",
           **flags):
    """Run a real stop-proof command and capture stdout / stderr / exit."""
    import contextlib
    import io
    import unittest.mock as mk

    out, err, exc = io.StringIO(), io.StringIO(), None
    args = dict(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None)
    args.update(flags)
    with mk.patch.object(airuleset, "resolve_authority", return_value=authority):
        with mk.patch.object(airuleset, "_current_user", return_value=user):
            with mk.patch.object(airuleset, "_gh_login", return_value=login):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(out):
                        with contextlib.redirect_stderr(err):
                            try:
                                cmd(mk.Mock(**args))
                            except SystemExit as e:
                                exc = e
    return out.getvalue(), err.getvalue(), exc


def _labelled_rows_gh(healthy=True):
    """A `_gh_out` stand-in whose issue rows carry real `labels` values."""
    import json as _json

    populations = {
        "-label:stream:": [
            {"number": 11, "title": "core work", "labels": [],
             "createdAt": "2026-07-01T00:00:00Z"}],
        "label:needs-gatekeeper": [],
        "label:prio:bounce": [
            {"number": 2150, "title": "bounced stream ticket",
             "createdAt": "2026-06-01T00:00:00Z",
             "labels": [{"name": "prio:bounce"}, {"name": "stream:david"}]}],
        "label:ready-for-review": [],
    }
    seen = []

    def gh(*a, **k):
        args = [str(x) for x in a]
        joined = " ".join(args)
        seen.append(args)
        if args and args[0] == "label":
            return '[{"name": "stream:montalu"}]'
        if args and args[0] == "workflow":
            return "[]"
        if args and args[0] == "run":
            return "[]"
        if "sort:created-desc" in joined:
            return '[{"number": 999}]' if healthy else "[]"
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        rows = []
        for key, val in populations.items():
            if key in search:
                rows.extend(val)
        return _json.dumps(rows)

    return gh, seen


def _fake_gh_search_filtered(items):
    """A `_gh_out` stand-in that evaluates a `gh issue list --search "..."`
    query against `items` (a list of `{"number", "labels": set(...)}`) with
    REAL exclusion semantics -- `-label:X` genuinely removes an item
    carrying X, `label:A,B` matches an item carrying A OR B (gh's own
    documented comma-OR-within-one-qualifier behaviour, #181 M8/M-6) --
    unlike `_fake_gh_by_search`'s pure substring-INCLUSION model above,
    which cannot prove that an EXCLUSION term added to the built query
    actually removes a ticket (#362): that fake would report a ticket as
    still present just because SOME population key's substring occurs in
    the search string, with no regard for whether a `-label:` term should
    have removed it. `assignee:`/`author:`/`sort:` tokens are accepted but
    ignored -- this fixture is only ever driven with label-only quals."""
    searches = []

    def gh(*a, **k):
        args = [str(x) for x in a]
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        searches.append(search)
        want_length = "-q" in args
        matched = []
        for it in items:
            labels = it.get("labels") or set()
            ok = True
            for tok in search.split():
                if tok.startswith("-label:"):
                    names = tok[len("-label:"):].split(",")
                    if any(n in labels for n in names):
                        ok = False
                        break
                elif tok.startswith("label:"):
                    names = tok[len("label:"):].split(",")
                    if not any(n in labels for n in names):
                        ok = False
                        break
            if ok:
                matched.append(it)
        if want_length:
            return str(len(matched))
        import json as _json
        return _json.dumps([
            {"number": it["number"],
             "title": it.get("title", "t%d" % it["number"]),
             "createdAt": it.get("createdAt", "2026-01-01T00:00:00Z"),
             "labels": [{"name": n} for n in sorted(it.get("labels") or [])]}
            for it in matched])

    return gh, searches
