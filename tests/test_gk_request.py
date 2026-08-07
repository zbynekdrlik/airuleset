"""Canonical stream→supervisor action channel (airuleset #30, 2026-07-24).

Streams (montalu/david/marek) kept needing a gatekeeper/supervisor ACTION
(box access, workflow re-dispatch, infra) and the only real path was the USER
as a middleman — 3× in one day, explicitly rejected ("je to blbé, že robím
prostredníka medzi vami"). The canonical mechanism, owned by airuleset
(odoo-erp#2085 becomes a repo-adapter):

- REQUEST = a ticket labeled `needs-gatekeeper` in the upstream repo, filed
  via `airuleset.py gk-request` (label 403 → the `GATEKEEPER-ACTION:` title/
  comment prefix fallback for read-only-fork streams).
- DELIVERY = watchdog job (gk_request_backstop, the mirror of job 8): ~30 min
  sweep; IDLE supervisor pane gets a typed nudge, BUSY pane gets NOTHING (the
  label alone queues it for the master loop), no live pane → ONE deduped
  Discord ping. Reduced-stream homes are never nudged.
- VISIBILITY = `gkq N` statusline badge on full-authority boxes (was `gk-req
  N` before the footer's labels were shortened, #223).
"""

import json
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import statusbar
import watchdog as wd

IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
BUSY = ("● Validate issue\n  ⎿ running…\n"
        "✳ Baking… (2m · esc to interrupt)\n")


def seed_repo_cache(home, root, name, **extra):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    entry = {"open": 1, "name": name, "root": root, "ts": int(time.time())}
    entry.update(extra)
    (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(entry))


class FakeTmux:
    def __init__(self, panes=None, captured=IDLE):
        self.panes = panes or []
        self.captured = captured
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "\n".join("%s\tclaude\t%s" % (p, c) for p, c in self.panes)
        if "capture-pane" in j:
            return self.captured
        if "display" in j:
            return "0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class TestGkRequestBackstop(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")
        self.pings = []

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def _go(self, state, tickets, panes=None, captured=IDLE, now=None):
        tmux = FakeTmux(panes if panes is not None else [("%1", self.root)],
                        captured)
        logs = wd.gk_request_backstop(
            now or time.time(), tmux, state, self._send, home=self.home,
            gh_fetch=lambda root: tickets, user="gatekeeper")
        return logs, tmux

    def test_nudges_idle_supervisor_pane(self):
        logs, tmux = self._go({}, [2081, 2083])
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertTrue(typed[0].startswith("gk-request backstop:"), typed[0])
        self.assertIn("#2081", typed[0])
        self.assertIn("needs-gatekeeper", typed[0])
        self.assertTrue(any("gkreq-nudge" in ln for ln in logs), logs)

    def test_busy_pane_gets_nothing(self):
        _logs, tmux = self._go({}, [7], captured=BUSY)
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_no_pane_pings_discord_once(self):
        state = {}
        now = time.time()
        logs, _t = self._go(state, [7], panes=[], now=now)
        self.assertEqual(len(self.pings), 1)
        self.assertIn("needs-gatekeeper", self.pings[0][0])
        # same set within the renudge window → silent
        wd.gk_request_backstop(now + wd.GKREQ_INTERVAL + 5, FakeTmux([]),
                               state, self._send, home=self.home,
                               gh_fetch=lambda root: [7], user="gatekeeper")
        self.assertEqual(len(self.pings), 1)

    def test_stale_cache_root_never_pings(self):
        # LIVE false positive (2026-07-24): the no-pane Discord fallback fired
        # for a checkout untouched for 16 DAYS whose supervisor session lives
        # on ANOTHER box. Only a root with a FRESH cache entry (a session ran
        # here recently and is now gone) justifies the "session missing" ping.
        with TemporaryDirectory() as home2:
            root = str(Path(home2) / "devel" / "olddemo")
            Path(root).mkdir(parents=True)
            d = statusbar.cache_dir(home2)
            d.mkdir(parents=True, exist_ok=True)
            (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(
                {"open": 1, "name": "olddemo", "root": root,
                 "ts": int(time.time()) - 16 * 24 * 3600}))
            wd.gk_request_backstop(
                time.time(), FakeTmux([]), {}, self._send, home=home2,
                gh_fetch=lambda r: [7], user="gatekeeper")
        self.assertFalse(self.pings,
                         "a 16-day-stale root must never Discord-ping")

    def test_reduced_stream_home_never_nudged(self):
        # the requester must not be nudged about its own request — only a
        # supervisor session works gk-requests (fresh home: no cached roots)
        with TemporaryDirectory() as home2:
            root = "/home/david/devel/odoo-erp"
            tmux = FakeTmux([("%9", root)])
            logs = wd.gk_request_backstop(
                time.time(), tmux, {}, self._send, home=home2,
                gh_fetch=lambda r: [5], user="david")
        self.assertFalse(tmux.typed(), logs)
        self.assertFalse(self.pings)

    def test_gh_error_keeps_state_and_stays_silent(self):
        _logs, tmux = self._go({}, None)
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_empty_backlog_is_silent(self):
        _logs, tmux = self._go({}, [])
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)


class TestGkreqFetch(unittest.TestCase):
    def test_label_and_title_fallback_queries_union(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            out = ([{"number": 5}] if "--label" in argv
                   else [{"number": 9,
                          "title": "GATEKEEPER-ACTION: obnov docker sock"}])
            return m.Mock(returncode=0, stdout=json.dumps(out))

        with m.patch("subprocess.run", side_effect=run):
            got = wd._fetch_gkreq_tickets("/tmp/x")
        self.assertEqual(got, [5, 9])
        flat = json.dumps(calls)
        self.assertIn("needs-gatekeeper", flat)
        self.assertIn("GATEKEEPER-ACTION", flat)

    def test_tokenized_search_match_is_filtered_client_side(self):
        # LIVE false positive (2026-07-24, first minutes of the job): GitHub
        # search TOKENIZES — '"GATEKEEPER-ACTION:" in:title' matched odoo-erp
        # #1768 "P1 hardening: … gatekeeper GitHub Actions runner" (tokens
        # gatekeeper + actions) and pinged the user's Discord about a
        # non-request. Only a title carrying the LITERAL marker counts.
        def run(argv, **kw):
            out = ([] if "--label" in argv
                   else [{"number": 1768,
                          "title": "P1 hardening: dedicated non-sudo OS "
                                   "identity for the gatekeeper GitHub "
                                   "Actions runner"}])
            return m.Mock(returncode=0, stdout=json.dumps(out))

        with m.patch("subprocess.run", side_effect=run):
            self.assertEqual(wd._fetch_gkreq_tickets("/tmp/x"), [])

    def test_any_query_error_returns_none(self):
        with m.patch("subprocess.run",
                     return_value=m.Mock(returncode=1, stdout="")):
            self.assertIsNone(wd._fetch_gkreq_tickets("/tmp/x"))


class TestMachinePrefixes(unittest.TestCase):
    def test_gkreq_nudge_is_a_machine_prompt(self):
        self.assertTrue(any(
            p.startswith("gk-request backstop") for p in
            wd._MACHINE_PROMPT_PREFIXES))

    def test_job10_auto_enters_gkreq_nudges(self):
        # MACHINE_NUDGE_PREFIX is consumed via str.startswith → tuple form
        self.assertTrue(
            "gk-request backstop:" in wd.MACHINE_NUDGE_PREFIX
            if isinstance(wd.MACHINE_NUDGE_PREFIX, tuple)
            else wd.MACHINE_NUDGE_PREFIX.startswith("gk-request"))

    def test_run_once_wires_the_job(self):
        src = Path(wd.__file__).read_text()
        self.assertIn("gkreq_fetch", src)
        i = src.index("def run_once")
        self.assertIn("gk_request_backstop(", src[i:])


class TestStatuslineBadge(unittest.TestCase):
    def _segment(self, **extra):
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "demo")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "demo", **extra)
            return statusbar.tickets_segment(root, home=home, spawn=False)

    def test_badge_renders_when_requests_open(self):
        # label shortened 'gk-req' -> 'gkq' (#223)
        seg = self._segment(gk_req=3)
        self.assertIn("gkq 3", seg)

    def test_badge_hidden_at_zero(self):
        seg = self._segment(gk_req=0)
        self.assertNotIn("gkq", seg)

    def test_refresh_collects_the_count_for_full_authority(self):
        src = Path(airuleset.__file__).read_text()
        i = src.index('entry["scope"] = "core"')
        # Window widened for the streamy bucket (#164) that now sits between
        # the core-count query and the gk_req query, and again for the #181
        # I5/I6 round-2 comments (-L 1000, _core_search_excl()) between them.
        self.assertIn("needs-gatekeeper", src[i:i + 3000])
        self.assertIn('entry["gk_req"]', src[i:i + 3000])


class TestCmdGkRequest(unittest.TestCase):
    def _args(self, **kw):
        base = dict(repo=None, issue=None, title=None, body=None,
                    body_file=None, comment=None)
        base.update(kw)
        return m.Mock(**base)

    def test_create_with_label(self):
        # #221 fix: the label is applied via its OWN `--add-label` call
        # AFTER a bare create, never baked into the create call itself —
        # baking it in is exactly the shape GitHub silently drops the
        # label from when the actor lacks push access. This test used to
        # assert `needs-gatekeeper` was present in the CREATE call itself
        # (the pre-#221-fix, vulnerable shape); it now asserts the correct
        # split.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0,
                          stdout="https://github.com/o/r/issues/31\n",
                          stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="Obnov prístup na box", body="detail"))
        self.assertIn(rc, (0, None))
        create = calls[0]
        self.assertIn("create", create)
        self.assertNotIn("needs-gatekeeper", " ".join(create), create)
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertTrue(
            any("needs-gatekeeper" in c for c in add_label_calls),
            add_label_calls)
        # #221 adversarial review, MINOR: a "retitle unconditionally,
        # ignore whether the label landed" mutant must NOT pass this
        # test -- the label succeeded here, so no --title edit (the
        # GATEKEEPER-ACTION degrade) should ever be attempted.
        self.assertFalse(
            any("edit" in c and "--title" in c for c in calls), calls)

    def test_create_label_denied_falls_back_to_title_prefix(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "needs-gatekeeper" in " ".join(argv):
                return m.Mock(returncode=1, stdout="",
                              stderr="could not add label")
            return m.Mock(returncode=0,
                          stdout="https://github.com/o/r/issues/32\n",
                          stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(self._args(title="Re-dispatch CI"))
        self.assertIn(rc, (0, None))
        titles = [argv[argv.index("--title") + 1] for argv in calls
                  if "--title" in argv]
        self.assertTrue(any(t.startswith("GATEKEEPER-ACTION:")
                            for t in titles), titles)

    def test_create_label_silently_dropped_degrades_to_prefix(self):
        # #221 LIVE bug: GitHub's issue-create endpoint silently DROPS a
        # `labels` field the actor lacks push access for -- unlike the
        # dedicated add-label endpoint, it does NOT 403 the whole request
        # (documented GitHub REST behavior: "Only users with push access
        # can set labels for new issues... labels are silently dropped
        # otherwise"). A read-only-fork actor's `gh issue create --label
        # needs-gatekeeper` therefore returned rc=0 with the issue created
        # and NO label on it at all, and cmd_gk_request reported "filed"
        # as if the escalation were visible. Simulate the real split: the
        # label must be applied in its OWN edit call (not baked into
        # create), and that dedicated call correctly fails (403) for a
        # read-only actor -- prove the command then degrades to the
        # GATEKEEPER-ACTION title prefix instead of silently declaring
        # success with neither signal present.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "create" in argv and "issue" in argv:
                return m.Mock(returncode=0,
                              stdout="https://github.com/zbynekdrlik/"
                                     "odoo-erp/issues/2779\n",
                              stderr="")
            if "--add-label" in argv and "needs-gatekeeper" in argv:
                return m.Mock(returncode=1, stdout="",
                              stderr="HTTP 403: Resource not accessible")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="DNS chyba blokuje hand-off",
                           body="detail"))
        self.assertIn(rc, (0, None))
        # the initial create must NEVER bake the label into the same call
        # -- that is precisely the field GitHub silently drops
        create_call = [c for c in calls
                       if "create" in c and "issue" in c][0]
        self.assertNotIn("needs-gatekeeper", create_call, create_call)
        # a real, separate add-label attempt must have been made and
        # denied (the dedicated label endpoint correctly 403s)
        self.assertTrue(any("--add-label" in c and "needs-gatekeeper" in c
                            for c in calls), calls)
        # denial must degrade to the GATEKEEPER-ACTION title prefix so the
        # escalation stays discoverable by job 11's `in:title` query
        edits = [c for c in calls if "edit" in c and "--title" in c]
        self.assertTrue(edits, calls)
        self.assertIn("GATEKEEPER-ACTION:", json.dumps(edits))

    def test_create_neither_label_nor_prefix_fails_loudly(self):
        # both the label add AND the retitle are denied -- must NEVER
        # report success while the escalation is invisible to the
        # supervisor (script-failure-policy: fail loudly, never guess).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "create" in argv and "issue" in argv:
                return m.Mock(returncode=0,
                              stdout="https://github.com/o/r/issues/2780\n",
                              stderr="")
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "edit" in argv and "--title" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="Nejaky problem", body="detail"))
        self.assertEqual(rc, 1)

    def test_create_unparseable_issue_number_fails_loudly(self):
        # #221 adversarial review, MAJOR: a `gh issue create` success whose
        # stdout does NOT end in a parseable issue number must not be
        # allowed to short-circuit past both the label-add AND the
        # retitle attempt straight to a false "gk-request filed" — that
        # silently reproduces the exact invisible-escalation class this
        # ticket exists to kill, just triggered by anomalous `gh` stdout
        # instead of a denied label.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "create" in argv and "issue" in argv:
                return m.Mock(returncode=0, stdout="done\n", stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="Neparsovatelne cislo", body="detail"))
        self.assertEqual(rc, 1)
        # never attempted a label/retitle against a garbage "issue number"
        self.assertFalse(any("--add-label" in c for c in calls), calls)
        self.assertFalse(
            any("edit" in c and "--title" in c for c in calls), calls)

    def test_issue_mode_labels_and_comments(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            airuleset.cmd_gk_request(
                self._args(issue=2081, comment="obnov docker sock prístup"))
        flat = json.dumps(calls)
        self.assertIn("--add-label", flat)
        self.assertIn("needs-gatekeeper", flat)
        self.assertIn("comment", flat)

    def test_issue_mode_label_denied_comment_carries_marker(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0, stdout="Stary titulok\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            airuleset.cmd_gk_request(self._args(issue=7, comment="akcia"))
        comments = [argv for argv in calls if "comment" in argv]
        self.assertTrue(comments)
        self.assertIn("GATEKEEPER-ACTION:", json.dumps(comments))
        # a comment-only marker is INVISIBLE to job 11's queries (label +
        # in:title only) — the fallback must ALSO best-effort retitle the
        # issue so the request stays machine-discoverable
        edits = [argv for argv in calls
                 if "edit" in argv and "--title" in argv]
        self.assertTrue(edits, calls)
        self.assertIn("GATEKEEPER-ACTION: Stary titulok", json.dumps(edits))

    def test_issue_mode_already_prefixed_title_not_retitled(self):
        # boundary (review 2026-07-24): a title already carrying the marker
        # must not be double-prefixed
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0,
                              stdout="GATEKEEPER-ACTION: uz oznacene\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            airuleset.cmd_gk_request(self._args(issue=8, comment="akcia"))
        edits = [argv for argv in calls
                 if "edit" in argv and "--title" in argv]
        self.assertFalse(edits, "already-prefixed title must not be retitled")

    def test_issue_mode_neither_label_nor_retitle_succeeds_fails_loudly(self):
        # #283: mirrors #221's create-mode hardening for the --issue
        # (mark-existing-ticket) branch — label denied AND the retitle
        # edit also denied must NEVER report success while the escalation
        # is invisible to job 11's needs-gatekeeper/in:title queries
        # (script-failure-policy: fail loudly, never guess).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0, stdout="Stary titulok\n",
                              stderr="")
            if "edit" in argv and "--title" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(issue=9, comment="akcia"))
        self.assertEqual(rc, 1)
        # the comment itself must still have been posted (it's the retitle
        # that failed, not the comment) -- the failure is about visibility,
        # not about the comment call
        self.assertTrue(any("comment" in c for c in calls), calls)

    def test_issue_mode_view_fallback_failure_fails_loudly(self):
        # #283: the retitle fallback's OWN `gh issue view` read can fail
        # (network hiccup, permissions) -- when it does, we cannot tell
        # whether the title already carries the GATEKEEPER-ACTION marker,
        # so a denied label plus an unreadable title must ALSO fail loudly
        # rather than silently assuming the escalation is fine.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=1, stdout="",
                              stderr="could not view issue")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(issue=10, comment="akcia"))
        self.assertEqual(rc, 1)
        # never attempted a retitle against an unread title
        self.assertFalse(
            any("edit" in c and "--title" in c for c in calls), calls)

    def test_issue_mode_already_prefixed_title_still_returns_success(self):
        # #283 regression guard: the existing already-prefixed boundary
        # case (test_issue_mode_already_prefixed_title_not_retitled) must
        # keep returning 0 -- the escalation IS already visible via the
        # title, even though no retitle *call* was made, so the new
        # loud-failure gate must not treat "retitled == False" alone as
        # a failure signal.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0,
                              stdout="GATEKEEPER-ACTION: uz oznacene\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(issue=11, comment="akcia"))
        self.assertEqual(rc, 0)

    def test_registered_in_cli(self):
        src = Path(airuleset.__file__).read_text()
        self.assertIn('"gk-request"', src)
        self.assertIn("cmd_gk_request", src)

    def test_handoff_label_never_enters_the_goal_stop_proof_slice(self):
        # #191 adversarial review, CRITICAL C1: a needs-gatekeeper ticket
        # tagged with Part C's origin marker must NEVER appear in
        # `slice-quals --count` (the `/goal` stop-proof's own termination
        # check) -- that is exactly what reusing `stream:<user>` as the
        # marker would have broken (the loop could never reach a real 0
        # while any such ticket stayed open). `_slice_quals()` for a
        # shared-account stream is `label:stream:<user>` ALONE and is
        # completely unmodified by this fix; this proves `handed-by:<user>`
        # structurally cannot match it.
        import contextlib
        import io

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if a[:2] == ("label", "list"):
                # _label_exists_on_repo's own probe: stream:simap genuinely
                # IS a defined repo label (the pre-existing ownership
                # convention) -- it is simply never APPLIED to this ticket.
                return '[{"name": "stream:simap"}]'
            if "label:stream:simap" in j:
                return "[]"    # the ticket carries handed-by:, not stream:
            if "sort:created-desc" in j:
                # proves the search index genuinely works (#181's own C2
                # health guard) so the 0 above is trusted, not refused
                return '[{"number": 999}]'
            return "[]"

        buf = io.StringIO()
        with m.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"), \
                m.patch.object(airuleset, "_current_user", return_value="simap"), \
                m.patch.object(airuleset, "_gh_out", side_effect=gh):
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_slice_quals(
                    m.Mock(count=True, list=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_issue_mode_also_applies_origin_handoff_label(self):
        # #191 Part C: a registered sub-dev stream's gk-request ALSO applies
        # its own handed-by:<user> label at hand-off time -- the origin
        # marker cmd_tickets_status's re-attribution step
        # (_last_origin_owner) reads later, even after a subsequent relabel
        # removes it.
        #
        # #191 adversarial review, CRITICAL C1: deliberately handed-by:,
        # NEVER stream: -- reusing the ownership label would have made a
        # needs-gatekeeper ticket permanently part of the stream's own
        # /goal stop-proof slice (slice-quals --count could never reach 0).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        # Patch `_current_user` (exists both pre- and post-fix) rather than
        # the not-yet-existing `_own_handoff_label` -- this drives the SAME
        # real entry point pre-fix, so a red run fails on genuine missing
        # behaviour (no origin-label calls) rather than on an AttributeError
        # from mocking an attribute the pre-fix module never had.
        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            airuleset.cmd_gk_request(
                self._args(issue=2081, comment="obnov docker sock prístup"))
        flat = json.dumps(calls)
        self.assertIn("label", flat)
        self.assertIn("create", flat)           # gh label create (checked-first)
        self.assertIn("handed-by:simap", flat)
        self.assertNotIn("stream:simap", flat)  # never the ownership label
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertTrue(
            any("handed-by:simap" in c for c in add_label_calls),
            add_label_calls)

    def test_ensure_origin_label_never_overwrites_an_existing_label(self):
        # #191 adversarial review, MAJOR M1 (live-verified against real
        # odoo-erp stream:* labels, each hand-curated with its own colour +
        # Slovak description): the original --force version overwrote an
        # EXISTING label's colour/description on EVERY call. A label
        # `gh label list --search` reports as already present must never be
        # passed to `gh label create` at all.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "list" in argv:
                return m.Mock(returncode=0, stdout="handed-by:simap\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            airuleset.cmd_gk_request(
                self._args(issue=3, comment="akcia"))
        create_calls = [c for c in calls
                        if "label" in c and "create" in c]
        self.assertEqual(create_calls, [], create_calls)
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertTrue(
            any("handed-by:simap" in c for c in add_label_calls),
            add_label_calls)

    def test_create_mode_also_applies_origin_handoff_label(self):
        # #191 adversarial review, MAJOR M4: the origin label must be
        # applied AFTER the primary `gh issue create` succeeds, in a
        # SEPARATE call -- baking it into the create call meant a rejected
        # origin label failed the WHOLE create (dropping needs-gatekeeper
        # too) and silently fell through to the title-prefix fallback.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0,
                          stdout="https://github.com/o/r/issues/40\n",
                          stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            rc = airuleset.cmd_gk_request(
                self._args(title="Adopt the pipeline", body="detail"))
        self.assertIn(rc, (0, None))
        create = [c for c in calls if "issue" in c and "create" in c][0]
        # #221 fix: needs-gatekeeper is ALSO applied via its own separate
        # `--add-label` call now, never baked into create -- same reason
        # as the origin label this test was originally about.
        self.assertNotIn("needs-gatekeeper", create, create)
        self.assertNotIn("handed-by:simap", create)   # NOT baked into create
        edit_calls = [c for c in calls
                     if "issue" in c and "edit" in c and "--add-label" in c]
        self.assertTrue(
            any("needs-gatekeeper" in c for c in edit_calls), edit_calls)
        self.assertTrue(
            any("40" in c and "handed-by:simap" in c for c in edit_calls),
            edit_calls)

    def test_create_mode_origin_label_failure_never_drops_needs_gatekeeper(self):
        # A rejected origin --add-label (after a successful create) must
        # never retroactively undo the create or its needs-gatekeeper label
        # -- the origin label's own denial is deliberately independent of
        # the primary needs-gatekeeper --add-label call (#221: also its own
        # separate call now, never baked into create), so only the ORIGIN
        # label is denied here to prove the two are not coupled.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "edit" in argv and "--add-label" in argv \
                    and "handed-by:simap" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "issue" in argv and "create" in argv:
                return m.Mock(returncode=0,
                              stdout="https://github.com/o/r/issues/41\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            rc = airuleset.cmd_gk_request(
                self._args(title="Adopt the pipeline", body="detail"))
        self.assertIn(rc, (0, None))
        creates = [c for c in calls if "issue" in c and "create" in c]
        self.assertEqual(len(creates), 1, creates)   # never a fallback retry
        self.assertNotIn("needs-gatekeeper", creates[0], creates[0])
        add_label_calls = [c for c in calls
                           if "issue" in c and "edit" in c
                           and "--add-label" in c]
        self.assertTrue(
            any("needs-gatekeeper" in c for c in add_label_calls),
            add_label_calls)

    def test_origin_label_skipped_when_not_a_registered_stream(self):
        # A full-authority box (dev1/gatekeeper) must never stamp a
        # meaningless origin label -- `_current_user` here resolves to
        # whatever box actually runs this test, never a registered stream,
        # so no `gh label` call should be attempted at all.
        #
        # #191 adversarial review, MINOR m5: this must patch `_current_user`
        # to a KNOWN non-registered name -- unpatched, the assertion would
        # fail outright if the suite is ever run AS one of the streams this
        # feature targets (marek/montalu/david/simap).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="newlevel"):
            airuleset.cmd_gk_request(
                self._args(issue=99, comment="akcia"))
        flat = json.dumps(calls)
        self.assertNotIn("handed-by:", flat)
        label_subcmds = [c for c in calls
                        if len(c) > 1 and c[0] == "gh" and c[1] == "label"]
        self.assertEqual(label_subcmds, [], label_subcmds)

    def test_origin_label_create_failure_is_logged_not_fatal(self):
        # The needs-gatekeeper hand-off (gk-request's PRIMARY job) must
        # never be blocked by the best-effort origin-label enrichment
        # failing -- and the --add-label attempt must never even fire once
        # the ensure-step reports the label unusable.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "label" in argv and "create" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            rc = airuleset.cmd_gk_request(
                self._args(issue=5, comment="akcia"))
        self.assertIn(rc, (0, None))
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertFalse(
            any("handed-by:simap" in c for c in add_label_calls),
            add_label_calls)
        # needs-gatekeeper itself still went through
        self.assertTrue(any("needs-gatekeeper" in c for c in add_label_calls))


class TestProtocolDocs(unittest.TestCase):
    def test_autopilot_skill_documents_the_channel(self):
        txt = (Path(airuleset.__file__).parent / "skills" / "autopilot"
               / "SKILL.md").read_text()
        self.assertIn("needs-gatekeeper", txt)
        self.assertIn("gk-request", txt)
        self.assertIn("GATEKEEPER-ACTION", txt)

    def test_statusline_vocabulary_documents_the_badge(self):
        txt = (Path(airuleset.__file__).parent / "modules" / "core"
               / "statusline-vocabulary.md").read_text()
        self.assertIn("gk-req", txt)


if __name__ == "__main__":
    unittest.main()
