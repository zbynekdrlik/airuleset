"""#1036 — task-hygiene A/B/C computation, CLI, status persistence, nudge text.

Every test drives `compute_hygiene` through an injected FAKE `call(model,
method, **body)` — NEVER a real Odoo network call (hard rule of this lane).
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_task_hygiene as th  # noqa: E402

NOW = datetime.datetime(2026, 9, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)

CFG = {
    "instance_url": "https://erp.montalu.cloud",
    "api_key_env_file": "~/.secrets/odoo-montalu.env",
    "project_ids": [1],
    "stage_ids": {"verifikacia": 2880, "realizacia": 2879,
                  "potrebuje_ujasnit": 3350, "hotovo": 2881},
    "stream_partner_ids": [17244],
    "own_author_names": ["ZbynekAI", "Marek Greňa"],
    "client_confirm_days": 3,
}


class FakeOdoo:
    """A minimal in-memory Odoo the `call(model, method, **body)` interface
    talks to. `tasks` = [{id,name,stage_id}]; `messages` = {res_id: [msg,...]}
    newest-LAST; `reactions` = {message_id: [{content, partner_id}, ...]}
    served through the GUARDED method `message_reactions_guarded` (never the
    403-by-design raw `mail.message.reaction` model). Honours res_id/message_type
    filtering, `order='date desc'`, and `limit`. `guarded_unavailable=True`
    simulates an instance where the guarded method is not released yet (404)."""

    def __init__(self, tasks, messages=None, reactions=None,
                 guarded_unavailable=False):
        self.tasks = tasks
        self.messages = messages or {}
        self.reactions = reactions or {}
        self.guarded_unavailable = guarded_unavailable

    def call(self, model, method, **body):
        if model == "project.task" and method == "search_read":
            hotovo = None
            for cond in body.get("domain", []):
                if cond[0] == "stage_id" and cond[1] == "!=":
                    hotovo = cond[2]
            rows = [t for t in self.tasks if t["stage_id"][0] != hotovo]
            return rows
        if model == "mail.message" and method == "search_read":
            res_id = None
            for cond in body.get("domain", []):
                if cond[0] == "res_id":
                    res_id = cond[2]
            msgs = list(self.messages.get(res_id, []))
            order = body.get("order", "")
            if "date desc" in order:
                msgs = list(reversed(msgs))
            limit = body.get("limit")
            if limit:
                msgs = msgs[:limit]
            return msgs
        if model == "mail.message" and method == "message_reactions_guarded":
            if self.guarded_unavailable:
                import cli_odoo_ro as ro
                raise ro.OdooError("Odoo mail.message/message_reactions_guarded "
                                   "HTTP 404: method does not exist")
            ids = body.get("ids", [])
            out = []
            for mid in ids:
                out.extend(self.reactions.get(mid, []))
            return out
        raise AssertionError("unexpected call %s/%s %r" % (model, method, body))


def _msg(mid, author_id, author_name, date, reaction_ids=None):
    return {"id": mid, "author_id": [author_id, author_name], "date": date,
            "reaction_ids": reaction_ids or []}


class TestComputeA(unittest.TestCase):
    def test_last_comment_by_client_no_reaction_is_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 518, "name": "Test 518", "stage_id": [2880, "Verifikácia"]}],
            messages={518: [_msg(1, 17244, "ZbynekAI", "2026-09-01 10:00:00"),
                            _msg(2, 999, "Patrik Klient", "2026-09-08 09:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        ids = [x["task_id"] for x in r["A"]]
        self.assertIn(518, ids)

    def test_last_comment_by_stream_not_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 600, "name": "t", "stage_id": [2879, "Realizácia"]}],
            messages={600: [_msg(1, 999, "Patrik", "2026-09-01 10:00:00"),
                            _msg(2, 17244, "ZbynekAI", "2026-09-08 09:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertNotIn(600, [x["task_id"] for x in r["A"]])

    def test_client_comment_with_stream_reaction_not_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 700, "name": "t", "stage_id": [2879, "Realizácia"]}],
            messages={700: [_msg(2, 999, "Patrik", "2026-09-08 09:00:00",
                                 reaction_ids=[55])]},
            reactions={2: [{"content": "👷", "partner_id": [17244, "ZbynekAI"]}]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertNotIn(700, [x["task_id"] for x in r["A"]])

    def test_client_comment_with_foreign_reaction_still_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 701, "name": "t", "stage_id": [2879, "Realizácia"]}],
            messages={701: [_msg(2, 999, "Patrik", "2026-09-08 09:00:00",
                                 reaction_ids=[56])]},
            reactions={2: [{"content": "👍", "partner_id": [999, "Patrik"]}]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertIn(701, [x["task_id"] for x in r["A"]])

    def test_guarded_method_unavailable_falls_back_to_reaction_ids(self):
        # instance without message_reactions_guarded released yet: any reaction
        # on the client comment is treated as ACKed (the ticket's else-branch),
        # never a raw mail.message.reaction read (403 by design, #784).
        fake = FakeOdoo(
            tasks=[{"id": 702, "name": "t", "stage_id": [2879, "Realizácia"]}],
            messages={702: [_msg(2, 999, "Patrik", "2026-09-08 09:00:00",
                                 reaction_ids=[57])]},
            guarded_unavailable=True,
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertNotIn(702, [x["task_id"] for x in r["A"]])


class TestComputeB(unittest.TestCase):
    def test_verif_without_stream_message_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 873, "name": "t", "stage_id": [2880, "Verifikácia"]}],
            messages={873: [_msg(1, 999, "Patrik", "2026-09-01 10:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertIn(873, [x["task_id"] for x in r["B"]])

    def test_verif_with_stream_message_not_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 874, "name": "t", "stage_id": [2880, "Verifikácia"]}],
            messages={874: [_msg(1, 17244, "ZbynekAI", "2026-09-01 10:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertNotIn(874, [x["task_id"] for x in r["B"]])

    def test_todo_stage_not_in_B(self):
        # a task NOT in verif/realiz/potreb (e.g. ToDo stage 2878) never enters B
        fake = FakeOdoo(
            tasks=[{"id": 100, "name": "t", "stage_id": [2878, "ToDo"]}],
            messages={100: []},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertNotIn(100, [x["task_id"] for x in r["B"]])

    def test_realiz_and_potreb_without_stream_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 671, "name": "t", "stage_id": [2879, "Realizácia"]},
                   {"id": 682, "name": "t", "stage_id": [3350, "Potrebuje ujasniť"]}],
            messages={671: [], 682: []},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        got = set(x["task_id"] for x in r["B"])
        self.assertEqual(got, {671, 682})


class TestComputeC(unittest.TestCase):
    def test_verif_old_stream_handover_no_client_reply_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 518, "name": "t", "stage_id": [2880, "Verifikácia"]}],
            messages={518: [_msg(1, 17244, "ZbynekAI", "2026-09-08 10:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)  # 8 days > 3
        self.assertIn(518, [x["task_id"] for x in r["C"]])

    def test_verif_recent_stream_handover_not_flagged(self):
        fake = FakeOdoo(
            tasks=[{"id": 519, "name": "t", "stage_id": [2880, "Verifikácia"]}],
            messages={519: [_msg(1, 17244, "ZbynekAI", "2026-09-15 10:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)  # 1 day < 3
        self.assertNotIn(519, [x["task_id"] for x in r["C"]])


class TestSummaryAndUrls(unittest.TestCase):
    def test_summary_line(self):
        fake = FakeOdoo(
            tasks=[{"id": 518, "name": "t", "stage_id": [2880, "Verifikácia"]}],
            messages={518: [_msg(2, 999, "Patrik", "2026-09-08 09:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertEqual(r["summary"], "task-hygiene: A=%d B=%d C=%d"
                         % (len(r["A"]), len(r["B"]), len(r["C"])))

    def test_task_deep_url(self):
        url = th.task_url(CFG, {"task_id": 518})
        self.assertEqual(url, "https://erp.montalu.cloud/odoo/project/1/tasks/518")

    def test_report_lines_carry_deep_url(self):
        fake = FakeOdoo(
            tasks=[{"id": 518, "name": "Vec", "stage_id": [2880, "Verifikácia"]}],
            messages={518: [_msg(2, 999, "Patrik", "2026-09-08 09:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        text = th.format_report(r, CFG)
        self.assertIn("erp.montalu.cloud/odoo/project/1/tasks/518", text)
        self.assertIn("task-hygiene: A=", text)


class TestStatusPersistence(unittest.TestCase):
    def _home(self):
        d = tempfile.mkdtemp(prefix="i1036-status-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        return d

    def test_persist_and_read_roundtrip(self):
        home = self._home()
        fake = FakeOdoo(
            tasks=[{"id": 518, "name": "t", "stage_id": [2880, "Verifikácia"]},
                   {"id": 873, "name": "t", "stage_id": [2880, "Verifikácia"]}],
            messages={518: [_msg(2, 999, "Patrik", "2026-09-08 09:00:00")],
                      873: []},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        th.persist_status(r, home=home, now=NOW.timestamp())
        st = th.read_status(home=home)
        self.assertEqual(st["a"], len(r["A"]))
        self.assertEqual(st["b"], len(r["B"]))
        self.assertIsInstance(st["ts"], (int, float))
        # oldest A comment ts present and matches the client comment
        self.assertIsInstance(st["a_oldest_ts"], (int, float))
        # b_verif counts only Verifikácia B members (873 is verif with no stream msg)
        self.assertGreaterEqual(st["b_verif"], 1)

    def test_read_absent_status_is_none(self):
        home = self._home()
        self.assertIsNone(th.read_status(home=home))


class TestNudgeText(unittest.TestCase):
    def test_compose_nudge_bounded_and_actionable(self):
        fake = FakeOdoo(
            tasks=[{"id": 518, "name": "Dlhý názov úlohy", "stage_id": [2880, "V"]}],
            messages={518: [_msg(2, 999, "Patrik", "2026-09-08 09:00:00")]},
        )
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        txt = th.compose_nudge(r, CFG)
        self.assertTrue(txt.startswith("task-hygiene:"))
        self.assertLessEqual(len(txt), 700)
        self.assertIn("👷", txt)

    def test_compose_nudge_empty_when_all_clear(self):
        fake = FakeOdoo(tasks=[], messages={})
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertEqual(th.compose_nudge(r, CFG), "")


class TestCLI(unittest.TestCase):
    def _home(self):
        d = tempfile.mkdtemp(prefix="i1036-cli-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        return d

    def _args(self, **kw):
        class A:
            pass
        a = A()
        a.init = kw.get("init", False)
        a.check = kw.get("check", False)
        a.json = kw.get("json", False)
        a.path = kw.get("path")
        a.home = kw.get("home")
        return a

    def test_init_writes_template(self):
        home = self._home()
        p = os.path.join(home, ".claude", "odoo-task-tracking.json")
        rc = th.cmd_task_hygiene(self._args(init=True, path=p))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(p))
        import cli_odoo_ro as ro
        cfg = ro.load_config(p)
        ok, missing = ro.config_valid(cfg)
        self.assertTrue(ok, missing)

    def test_check_not_configured(self):
        home = self._home()
        p = os.path.join(home, ".claude", "does-not-exist.json")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = th.cmd_task_hygiene(self._args(check=True, path=p))
        self.assertEqual(rc, 0)
        self.assertIn("not configured", buf.getvalue())

    def test_check_valid_config(self):
        home = self._home()
        p = os.path.join(home, ".claude", "odoo-task-tracking.json")
        th.cmd_task_hygiene(self._args(init=True, path=p))
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = th.cmd_task_hygiene(self._args(check=True, path=p))
        self.assertEqual(rc, 0)
        self.assertIn("ok", buf.getvalue().lower())


class TestQualsTaskHygieneFlag(unittest.TestCase):
    """#1036 — slice-quals/core-quals --task-hygiene prints the persisted A count
    (never a live Odoo call), short-circuiting BEFORE the authority check."""

    def setUp(self):
        self._old_home = os.environ.get("HOME")
        d = tempfile.mkdtemp(prefix="i1036-quals-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        os.environ["HOME"] = d
        self.addCleanup(self._restore_home)
        self.home = d

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def _args(self):
        class A:
            pass
        a = A()
        for f in ("count", "list", "waiting", "ops_wait", "audit", "bounces",
                  "dep_wait", "count_dispatchable"):
            setattr(a, f, False)
        a.extra = None
        a.task_hygiene = True
        return a

    def _run(self, fn):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(self._args())
        return buf.getvalue().strip()

    def test_slice_quals_prints_a_count(self):
        th.persist_status({"A": [{"task_id": 1, "ts": 1.0},
                                 {"task_id": 2, "ts": 2.0}], "B": [], "C": []},
                          home=self.home, now=1000.0)
        import cli_quals_cmd
        self.assertEqual(self._run(cli_quals_cmd.cmd_slice_quals), "2")

    def test_core_quals_prints_a_count(self):
        th.persist_status({"A": [{"task_id": 9, "ts": 1.0}], "B": [], "C": []},
                          home=self.home, now=1000.0)
        import cli_quals_cmd
        self.assertEqual(self._run(cli_quals_cmd.cmd_core_quals), "1")

    def test_no_status_prints_zero(self):
        import cli_quals_cmd
        self.assertEqual(self._run(cli_quals_cmd.cmd_slice_quals), "0")


class TestRegistration(unittest.TestCase):
    def test_subcommand_registered(self):
        import airuleset
        self.assertIn("task-hygiene", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["task-hygiene"], th.cmd_task_hygiene)


if __name__ == "__main__":
    unittest.main()
