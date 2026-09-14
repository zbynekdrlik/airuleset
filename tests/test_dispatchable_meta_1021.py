"""#1021 — the batched dependency-meta read must SPLIT (bodies-only batch +
per-row comments only where a body `Depends-on:` exists) so it survives a large
repo (odoo-erp: the 3-field `number,body,comments -L 1000` batch dies with
`unexpected end of JSON input`), and the `--count-dispatchable` /
`_lane_dispatchable_decision` inert-nudge must journal WHY (`meta read failed`),
never a bare `unmeasurable` — while the fail-safe direction is preserved
(unmeasurable never nudges).

Injected-runner tests (the `tests/test_dep_wait_993.py` fake-runner pattern),
no live gh.
"""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, main
import unittest.mock as mk

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_work_class as wc          # noqa: E402
import cli_quals_cmd                 # noqa: E402
import airuleset                     # noqa: E402
import watchdog.goal as goal         # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"


class _SplitRunner:
    """runner(argv, cwd) — models a repo where the 3-field batch FAILS (odoo-erp)
    but a bodies-only batch + per-row comments (gh api OR legacy gh issue view)
    SUCCEED. `bodies` maps issue# -> body; `comments` maps issue# ->
    [{"body","authorAssociation"}]; `states` maps (repo,num) -> state.
    `fail_bodies_batch` also fails the bodies-only `-L 1000` batch (forces the
    chunked fallback); `chunk_pages` is a list of pages (each a list of rows)
    served in created-asc order to the `--search` fallback."""

    def __init__(self, bodies=None, comments=None, states=None,
                 fail_bodies_batch=False, chunk_pages=None):
        self.bodies = bodies or {}
        self.comments = comments or {}
        self.states = states or {}
        self.fail_bodies_batch = fail_bodies_batch
        self.chunk_pages = list(chunk_pages or [])
        self.calls = []

    def __call__(self, argv, _cwd):
        self.calls.append(argv)
        j = " ".join(str(a) for a in argv)
        # 1) the OLD single 3-field batch — always fails here (the bug).
        if "number,body,comments" in j and "list" in j:
            return ""                       # _gh_out returns "" on rc!=0/timeout
        # 2) the chunked created-asc fallback (has --search).
        if "list" in j and "--search" in j:
            if self.chunk_pages:
                return json.dumps(self.chunk_pages.pop(0))
            return json.dumps([])
        # 3) the bodies-only batch.
        if "list" in j and "number,body" in j:
            if self.fail_bodies_batch:
                return ""
            return json.dumps([{"number": n, "body": b}
                               for n, b in sorted(self.bodies.items())])
        # 4) per-row comments via gh api (slug path) — ndjson (one obj per line).
        if "api" in j and "comments" in j:
            num = None
            for a in argv:
                if isinstance(a, str) and a.startswith("repos/") and "/issues/" in a:
                    num = int(a.split("/issues/")[1].split("/")[0])
            rows = self.comments.get(num, [])
            return "\n".join(
                json.dumps({"body": c["body"],
                            "author_association": c.get("authorAssociation")})
                for c in rows)
        # 5) per-row comments via legacy gh issue view (no-slug path).
        if "view" in j and "body,comments" in j:
            n = int(argv[argv.index("view") + 1])
            return json.dumps(
                {"body": self.bodies.get(n, ""),
                 "comments": [{"body": c["body"],
                               "authorAssociation": c.get("authorAssociation")}
                              for c in self.comments.get(n, [])]})
        # 6) dep state.
        if "view" in j and "state" in j:
            n = int(argv[argv.index("view") + 1])
            repo = argv[argv.index("-R") + 1] if "-R" in argv else None
            st = self.states.get((repo, n)) or self.states.get((None, n))
            return json.dumps({"state": st}) if st else "{}"
        return "{}"


class TestFetchMetaSplit(TestCase):
    def test_bodies_only_split_no_slug(self):
        # 3-field batch fails; bodies-only batch + legacy per-row comments serve
        # -> a real map (today: None, because only the 3-field call is made).
        r = _SplitRunner(
            bodies={5: "Depends-on: #4", 6: "no deps"},
            comments={5: [{"body": "Depends-on: #4",
                           "authorAssociation": "OWNER"}]})
        m = wc.fetch_meta({"5": {}, "6": {}}, r, "/root")
        self.assertIsNotNone(m)
        self.assertEqual(m[5]["body"], "Depends-on: #4")
        self.assertEqual(m[6]["body"], "no deps")
        # dep-free row gets [] comments; dep-carrying row's comments fetched.
        self.assertEqual(m[6]["comments"], [])
        self.assertEqual(m[5]["comments"][0]["authorAssociation"], "OWNER")

    def test_chunked_fallback_no_slug(self):
        # both the 3-field AND the bodies-only -L batch fail; the created-asc
        # chunked windows succeed -> a real map (today: None).
        r = _SplitRunner(
            fail_bodies_batch=True,
            chunk_pages=[[{"number": 7, "body": "Depends-on: #1",
                           "createdAt": "2026-01-01T00:00:00Z"}]])
        m = wc.fetch_meta({"7": {}}, r, "/root")
        self.assertIsNotNone(m)
        self.assertIn(7, m)
        self.assertEqual(m[7]["body"], "Depends-on: #1")

    def test_all_reads_fail_is_none(self):
        # fail-safe LOCK (holds before AND after the fix): if every read fails,
        # fetch_meta stays None so the caller prints unmeasurable, never a
        # silent 'no deps'.
        def boom(argv, _cwd):
            return ""
        self.assertIsNone(wc.fetch_meta({"5": {}}, boom, "/root"))


class TestEmitCountDispatchable(TestCase):
    """The CLI glue prints an integer when the split read succeeds, and
    `unmeasurable:meta read failed` (not bare `unmeasurable`) when it fails."""

    def _emit(self, runner):
        buf = io.StringIO()
        with mk.patch.object(cli_quals_cmd, "_slice_quals_runner",
                             return_value=runner), \
             mk.patch.object(airuleset, "_repo_slug", return_value=SLUG):
            with redirect_stdout(buf):
                cli_quals_cmd._emit_count_dispatchable(
                    {"5": {}, "6": {}}, "/root")
        return buf.getvalue().strip().splitlines()

    def test_prints_integer_after_split(self):
        # #5 dep-wait (#4 OPEN), #6 dispatchable -> count 1 (today: unmeasurable).
        r = _SplitRunner(
            bodies={5: "Depends-on: #4", 6: "no deps"},
            comments={5: []},
            states={(SLUG, 4): "OPEN"})
        out = self._emit(r)
        self.assertEqual(out[0], "1")
        self.assertNotIn("unmeasurable", out[0])

    def test_unmeasurable_carries_reason_when_all_reads_fail(self):
        # every read fails -> unmeasurable WITH a reason (today: bare
        # `unmeasurable`, no reason).
        def boom(argv, _cwd):
            return ""
        out = self._emit(boom)
        self.assertEqual(out[0], "unmeasurable:meta read failed")


class TestWatchdogReasonFlow(TestCase):
    def _fetch(self, stdout, rc=0):
        class _CP:
            returncode = rc

            def __init__(s):
                s.stdout = stdout
        with mk.patch("airuleset._repo_root", return_value="/root"), \
             mk.patch("airuleset.resolve_authority", return_value="full"), \
             mk.patch("subprocess.run", return_value=_CP()):
            return airuleset._watchdog_dispatchable_fetch("/root")

    def test_unmeasurable_with_reason_flows_through(self):
        # today: `int('unmeasurable:...')` fails -> None (reason lost).
        self.assertEqual(self._fetch("unmeasurable:meta read failed\n"),
                         [{"count": None, "reason": "meta read failed"}])

    def test_bare_unmeasurable_still_none(self):
        # a bare `unmeasurable` (no colon) stays None (legacy / unknown).
        self.assertIsNone(self._fetch("unmeasurable\n"))

    def test_lane_decision_journals_the_reason(self):
        # a reason-carrying fetch -> the journal says WHY (today: the res dict's
        # count is None -> the legacy `(candidate count unmeasurable)` text).
        skip, log, cand = goal._lane_dispatchable_decision(
            lambda cwd: [{"count": None, "reason": "meta read failed"}],
            "/c", {}, 100, "loc", 0, 0, 5)
        self.assertTrue(skip)
        self.assertIn("skip:dispatchable-unknown", log)
        self.assertIn("meta read failed", log)


if __name__ == "__main__":
    main()
