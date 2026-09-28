"""#1167 round 2 — the machinery respects the montalu 21-day Verifikácia
auto-close wait (client-board-stages.md rule 6, odoo-erp#8507).

(a) Job 49 class C uses the board profile's wait: a montalu Verifikácia task
    inside the wait is NOT "reminder due"; past 21 days (+ one daily cron cycle
    of grace) with no auto-close it is flagged as overdue for the mechanism.
(b) Class A ignores OdooBot-authored messages on the auto-close profile (the
    same author set odoo-erp#8507 excludes from a "reaction"), without hiding
    a real client comment that sits under the bot message.
(c) `stale!` / `converge!` do not fire for a montalu W ticket whose task sits
    in Verifikácia inside its wait (read from the Job 49 status file, never a
    live Odoo call).
miva and slovnormal behaviour is locked unchanged.

Every Odoo read goes through an in-memory fake `call(model, method, **body)`;
every clock is injected. No network, no live Odoo.
"""
import datetime
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_task_hygiene as th  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
VERIF = 2880
STREAM_PID = 17244
CLIENT_PID = 5001
BOT_PID = 2


def _cfg(instance, **extra):
    cfg = {
        "instance_url": instance,
        "api_key_env_file": "~/.secrets/odoo-x.env",
        "project_ids": [1],
        "stage_ids": {"verifikacia": VERIF, "realizacia": 2879,
                      "potrebuje_ujasnit": 3350, "hotovo": 2881},
        "stream_partner_ids": [STREAM_PID],
        "own_author_names": ["ZbynekAI"],
        "client_confirm_days": 3,
    }
    cfg.update(extra)
    return cfg


MONTALU = _cfg("https://erp.montalu.cloud")
MIVA = _cfg("https://erp.miva.sk")
SLOVNORMAL = _cfg("https://erp.slovnormal.sk")


def _ts(days_ago, now=NOW):
    return (now - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _stream(mid, days_ago, now=NOW):
    return {"id": mid, "author_id": [STREAM_PID, "ZbynekAI"],
            "date": _ts(days_ago, now), "reaction_ids": []}


def _client(mid, days_ago, now=NOW):
    return {"id": mid, "author_id": [CLIENT_PID, "Patrik Javorský"],
            "date": _ts(days_ago, now), "reaction_ids": []}


def _bot(mid, days_ago, now=NOW):
    return {"id": mid, "author_id": [BOT_PID, "OdooBot"],
            "date": _ts(days_ago, now), "reaction_ids": []}


class Fake:
    """Minimal Odoo: tasks as given; messages newest-first per res_id (the
    production `order="res_id, date desc, id desc"` contract)."""

    def __init__(self, tasks, messages):
        self.tasks = tasks
        self.messages = messages

    def call(self, model, method, **body):
        if model == "project.task" and method == "search_read":
            return [dict(t) for t in self.tasks]
        if model == "mail.message" and method == "search_read":
            rows = []
            for rid, msgs in self.messages.items():
                rows.extend(dict(m, res_id=rid) for m in msgs)
            rows.sort(key=lambda m: m["id"], reverse=True)
            rows.sort(key=lambda m: m["date"], reverse=True)
            rows.sort(key=lambda m: m["res_id"])
            return rows
        if model == "mail.message" and method == "message_reactions_guarded":
            return []
        raise AssertionError("unexpected call %s/%s" % (model, method))


def _verif_task(tid, entered_days_ago, ticket=None, now=NOW):
    desc = "<p>Hotovo pre vás.</p><p>Je nasadené. (GitHub #%d)</p>" % ticket \
        if ticket else "<p>Je nasadené.</p>"
    return {"id": tid, "name": "Úloha %d" % tid,
            "stage_id": [VERIF, "Verifikácia"],
            "date_last_stage_update": _ts(entered_days_ago, now),
            "description": desc}


def _compute(cfg, tasks, messages, now=NOW):
    return th.compute_hygiene(Fake(tasks, messages).call, cfg, now=now)


def _c_ids(res):
    return [it["task_id"] for it in res["C"]]


# --------------------------------------------------------------------------- #
# (a) class C — the profile's wait replaces the fixed 3 days on montalu
# --------------------------------------------------------------------------- #
class ClassCMontaluWait(unittest.TestCase):
    def test_5_days_in_verifikacia_is_not_reminder_due(self):
        res = _compute(MONTALU, [_verif_task(7, 5)], {7: [_stream(70, 5)]})
        self.assertEqual([], _c_ids(res))

    def test_20_days_in_verifikacia_is_not_reminder_due(self):
        res = _compute(MONTALU, [_verif_task(7, 20)],
                       {7: [_bot(71, 6), _stream(70, 20)]})
        self.assertEqual([], _c_ids(res))

    def test_22_days_without_auto_close_is_overdue_for_the_mechanism(self):
        res = _compute(MONTALU, [_verif_task(7, 22)], {7: [_stream(70, 22)]})
        self.assertEqual([7], _c_ids(res))
        self.assertEqual("overdue", res["C"][0].get("auto_close"))
        self.assertIn("auto-close", th.format_report(res, MONTALU))

    def test_reminder_less_than_7_days_ago_keeps_the_wait(self):
        # rollout case (odoo-erp#8507 rule 6): a task already past 21 days got
        # its reminder late; the close comes >= 7 days after the reminder.
        res = _compute(MONTALU, [_verif_task(7, 25)],
                       {7: [_bot(71, 3), _stream(70, 25)]})
        self.assertEqual([], _c_ids(res))

    def test_reminder_long_ago_and_no_close_is_overdue(self):
        res = _compute(MONTALU, [_verif_task(7, 25)],
                       {7: [_bot(71, 11), _stream(70, 25)]})
        self.assertEqual([7], _c_ids(res))
        self.assertEqual("overdue", res["C"][0].get("auto_close"))

    def test_a_client_reaction_cancels_the_countdown_back_to_the_3_day_rule(self):
        # the client wrote after the task entered Verifikácia, the stream
        # answered 5 days ago: the auto-close will not come, the stream owns it.
        res = _compute(MONTALU, [_verif_task(7, 10)],
                       {7: [_stream(72, 5), _client(71, 8), _stream(70, 10)]})
        self.assertEqual([7], _c_ids(res))
        self.assertIsNone(res["C"][0].get("auto_close"))

    def test_explicit_config_opt_out_restores_the_3_day_rule(self):
        cfg = dict(MONTALU, verif_auto_close=False)
        res = _compute(cfg, [_verif_task(7, 5)], {7: [_stream(70, 5)]})
        self.assertEqual([7], _c_ids(res))

    def test_config_can_enable_the_wait_on_another_board(self):
        cfg = dict(MIVA, verif_auto_close={"reminder_days": 14, "close_days": 21,
                                           "after_reminder_days": 7})
        res = _compute(cfg, [_verif_task(7, 5)], {7: [_stream(70, 5)]})
        self.assertEqual([], _c_ids(res))

    def test_wait_is_recorded_with_its_ticket(self):
        res = _compute(MONTALU, [_verif_task(7, 10, ticket=4321)],
                       {7: [_stream(70, 10)]})
        waits = res.get("verif_wait") or []
        self.assertEqual([7], [w["task_id"] for w in waits])
        self.assertEqual([4321], waits[0]["tickets"])


class ClassCOtherBoardsUnchanged(unittest.TestCase):
    def test_miva_5_day_handover_is_still_reminder_due(self):
        res = _compute(MIVA, [_verif_task(7, 5)], {7: [_stream(70, 5)]})
        self.assertEqual([7], _c_ids(res))
        self.assertIsNone(res["C"][0].get("auto_close"))
        self.assertEqual([], res.get("verif_wait") or [])

    def test_slovnormal_5_day_handover_is_still_reminder_due(self):
        res = _compute(SLOVNORMAL, [_verif_task(7, 5)], {7: [_stream(70, 5)]})
        self.assertEqual([7], _c_ids(res))

    def test_miva_2_day_handover_is_not_yet_due(self):
        res = _compute(MIVA, [_verif_task(7, 2)], {7: [_stream(70, 2)]})
        self.assertEqual([], _c_ids(res))


# --------------------------------------------------------------------------- #
# (b) class A — OdooBot is not a client
# --------------------------------------------------------------------------- #
class ClassABotAuthor(unittest.TestCase):
    def test_odoobot_reminder_is_not_an_unanswered_client_comment(self):
        res = _compute(MONTALU, [_verif_task(7, 15)],
                       {7: [_bot(71, 1), _stream(70, 15)]})
        self.assertEqual([], res["A"])

    def test_client_comment_under_a_bot_message_is_still_flagged(self):
        res = _compute(MONTALU, [_verif_task(7, 15)],
                       {7: [_bot(72, 1), _client(71, 2), _stream(70, 15)]})
        self.assertEqual([7], [it["task_id"] for it in res["A"]])
        self.assertEqual("Patrik Javorský", res["A"][0]["author"])

    def test_miva_bot_message_behaviour_is_unchanged(self):
        res = _compute(MIVA, [_verif_task(7, 1)],
                       {7: [_bot(71, 0.5), _stream(70, 1)]})
        self.assertEqual([7], [it["task_id"] for it in res["A"]])


# --------------------------------------------------------------------------- #
# (c) stale! / converge! stay quiet inside the wait
# --------------------------------------------------------------------------- #
def _old_ages(now):
    old = now - 20 * 86400
    return {"own": old, "any": old, "own_cited": old, "own_oldest": old,
            "own_final_reminder": None, "own_target": None,
            "own_target_event": None}


def _w_row(n, now):
    created = datetime.datetime.fromtimestamp(now - 20 * 86400, tz=UTC)
    return {"number": n, "title": "montalu ticket %d" % n,
            "createdAt": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "labels": [{"name": "ops-wait"}, {"name": "needs-acceptance"}]}


class QualsRespectTheWait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.now = time.time()
        self.now_dt = datetime.datetime.fromtimestamp(self.now, tz=UTC)

    def _persist(self, cfg, entered_days_ago, ticket, status_age_s=0):
        res = _compute(cfg, [_verif_task(7, entered_days_ago, ticket,
                                         now=self.now_dt)],
                       {7: [_stream(70, entered_days_ago, now=self.now_dt)]},
                       now=self.now_dt)
        th.persist_status(res, home=self.tmp, now=self.now - status_age_s)

    def _flag_sets(self):
        import airuleset
        import cli_quals_cmd
        ow = {100: _w_row(100, self.now), 101: _w_row(101, self.now)}
        with mock.patch.dict(os.environ, {"HOME": self.tmp}), \
                mock.patch.object(airuleset, "_stream_self_login", lambda: "me"), \
                mock.patch.object(airuleset, "_issue_comment_ages",
                                  lambda n, *a, **k: _old_ages(self.now)), \
                mock.patch.object(airuleset, "resolve_authority",
                                  lambda cwd=None: "full"), \
                mock.patch.object(airuleset, "_watchdog_release_state_fetch",
                                  lambda cwd: None):
            return cli_quals_cmd._ops_wait_flag_sets(ow, "/r")

    def _net_stale(self):
        import airuleset
        ow = {100: _w_row(100, self.now), 101: _w_row(101, self.now)}
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            return airuleset._compute_net_stale_w(
                ow, ages_fn=lambda n: _old_ages(self.now), now=self.now)

    def test_ticket_inside_the_wait_is_neither_stale_nor_converge(self):
        self._persist(MONTALU, 10, ticket=100)
        stale, _rc, _gk, _up, _tw, _tc, converge, no_target, _dt = \
            self._flag_sets()
        self.assertNotIn(100, stale)
        self.assertNotIn(100, converge)
        self.assertNotIn(100, no_target)
        self.assertIn(101, stale | converge)          # an unrelated W member
        self.assertEqual(1, self._net_stale())

    def test_overdue_ticket_is_reported_again(self):
        self._persist(MONTALU, 23, ticket=100)
        stale, _rc, _gk, _up, _tw, _tc, converge, _nt, _dt = self._flag_sets()
        self.assertIn(100, stale | converge)
        self.assertEqual(2, self._net_stale())

    def test_stale_status_file_grants_no_exemption(self):
        self._persist(MONTALU, 10, ticket=100, status_age_s=7 * 3600)
        stale, _rc, _gk, _up, _tw, _tc, converge, _nt, _dt = self._flag_sets()
        self.assertIn(100, stale | converge)

    def test_miva_box_is_unchanged(self):
        self._persist(MIVA, 2, ticket=100)
        stale, _rc, _gk, _up, _tw, _tc, converge, _nt, _dt = self._flag_sets()
        self.assertIn(100, stale | converge)
        self.assertEqual(2, self._net_stale())

    def test_ops_wait_listing_names_the_wait(self):
        import io
        from contextlib import redirect_stdout
        import airuleset
        import cli_quals_cmd
        self._persist(MONTALU, 10, ticket=100)
        ow = {100: _w_row(100, self.now)}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.tmp}), \
                mock.patch.object(airuleset, "_stream_self_login", lambda: "me"), \
                mock.patch.object(airuleset, "_issue_comment_ages",
                                  lambda n, *a, **k: _old_ages(self.now)), \
                mock.patch.object(airuleset, "resolve_authority",
                                  lambda cwd=None: "full"), \
                mock.patch.object(airuleset, "_watchdog_release_state_fetch",
                                  lambda cwd: None), \
                redirect_stdout(buf):
            cli_quals_cmd._emit_ops_wait(ow, "/r", None, None)
        row = [ln for ln in buf.getvalue().splitlines()
               if ln.startswith("100\t")][0]
        self.assertIn("auto-close-wait", row)
        self.assertNotIn("stale!", row)


if __name__ == "__main__":
    unittest.main()
