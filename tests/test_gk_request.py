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
- VISIBILITY = `gk-req N` statusline badge on full-authority boxes.
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
        seg = self._segment(gk_req=3)
        self.assertIn("gk-req 3", seg)

    def test_badge_hidden_at_zero(self):
        seg = self._segment(gk_req=0)
        self.assertNotIn("gk-req", seg)

    def test_refresh_collects_the_count_for_full_authority(self):
        src = Path(airuleset.__file__).read_text()
        i = src.index('entry["scope"] = "core"')
        self.assertIn("needs-gatekeeper", src[i:i + 2000])
        self.assertIn('entry["gk_req"]', src[i:i + 2000])


class TestCmdGkRequest(unittest.TestCase):
    def _args(self, **kw):
        base = dict(repo=None, issue=None, title=None, body=None,
                    body_file=None, comment=None)
        base.update(kw)
        return m.Mock(**base)

    def test_create_with_label(self):
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
        self.assertIn("needs-gatekeeper", " ".join(create))

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

    def test_registered_in_cli(self):
        src = Path(airuleset.__file__).read_text()
        self.assertIn('"gk-request"', src)
        self.assertIn("cmd_gk_request", src)


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
