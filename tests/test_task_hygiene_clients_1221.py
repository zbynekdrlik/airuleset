"""Issues 1221, 1239, 1262: class A flags only a comment a CLIENT is waiting on.

The fixtures carry shapes read from the live montalu board on 2.11.2026: partner
2 named "AI pomocník", owner partners 17128/17129, the sibling stream 20848, a
real `o_mail_redirect` mention body and the real guarded reaction row.
"""
import datetime
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import cli_handover_hygiene as hh  # noqa: E402
import cli_odoo_board as board  # noqa: E402
import cli_odoo_ro as ro  # noqa: E402
import cli_task_hygiene as th  # noqa: E402
from test_cli_task_hygiene import FakeOdoo  # noqa: E402

NOW = datetime.datetime(2026, 11, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)
VERIF, REAL = 2880, 2879
ZBYNEK_AI = [17244, "ZbynekAI"]
MAREK_AI = [20848, "MarekAI"]
OWNER = [17129, "Zbyněk Drlík"]
OWNER_2 = [17128, "Marek Drlík"]
PATRIK = [23, "Patrik Javorský"]
BOT = [2, "AI pomocník"]


def _cfg(url, **extra):
    cfg = {"instance_url": url, "api_key_env_file": "~/.secrets/x.env",
           "project_ids": [1],
           "stage_ids": {"verifikacia": VERIF, "realizacia": REAL,
                         "potrebuje_ujasnit": 3350, "hotovo": 2881},
           "stream_partner_ids": [17244], "own_author_names": ["ZbynekAI"],
           "client_confirm_days": 3}
    cfg.update(extra)
    return cfg


MONTALU = _cfg("https://erp.montalu.cloud")
OTHER = _cfg("https://erp.example.sk")


def _mention(pid, name):
    return ('<a href="/odoo/res.partner/%d" class="o_mail_redirect" data-oe-id="%d" '
            'data-oe-model="res.partner" target="_blank">@%s</a>' % (pid, pid, name))


def _m(mid, author, day, body="<p>text</p>", **extra):
    row = {"id": mid, "author_id": author, "date": "2026-10-%02d 09:00:00" % day,
           "reaction_ids": [], "body": body}
    row.update(extra)
    return row


def _task(tid=1, stage=(REAL, "Realizácia")):
    return {"id": tid, "name": "t", "stage_id": list(stage),
            "date_last_stage_update": "2026-10-01 09:00:00", "description": ""}


def _a(cfg, msgs, **kw):
    fake = FakeOdoo([_task()], {1: msgs}, **kw)
    return [it["task_id"] for it in th.compute_hygiene(fake.call, cfg, now=NOW)["A"]]


class Bots(unittest.TestCase):
    def test_montalu_partner_2_under_its_real_name_is_no_client(self):
        self.assertEqual([], _a(MONTALU, [_m(1, ZBYNEK_AI, 1), _m(2, BOT, 14)]))

    def test_montalu_default_profile_knows_the_renamed_bot(self):
        prof = board.auto_close_profile(MONTALU)
        self.assertIn(2, prof["bot_partner_ids"])
        self.assertIn("AI pomocník", prof["bot_author_names"])
        self.assertTrue(board.is_bot(BOT, prof))

    def test_renamed_bot_reminder_still_extends_the_auto_close_wait(self):
        task = _task(stage=(VERIF, "Verifikácia"))
        task["date_last_stage_update"] = "2026-09-20 09:00:00"
        prof = board.auto_close_profile(MONTALU)
        now = datetime.datetime(2026, 10, 12, 12, 0, tzinfo=datetime.timezone.utc)
        reminder = {"id": 5, "author_id": BOT, "date": "2026-10-11 04:00:00"}
        ac = board.classify(task, [reminder], prof, now, {17244})
        self.assertEqual("wait", ac["auto_close"])
        self.assertGreater(ac["until_ts"], reminder_ts(reminder) + 7 * 86400)

    def test_root_partner_is_no_client_on_a_board_without_a_profile(self):
        self.assertEqual([], _a(OTHER, [_m(1, ZBYNEK_AI, 1), _m(2, BOT, 14)]))

    def test_odoobot_name_is_no_client_on_a_board_without_a_profile(self):
        self.assertEqual([], _a(OTHER, [_m(2, [77, "OdooBot"], 14)]))

    def test_client_comment_below_a_bot_notice_is_still_flagged(self):
        self.assertEqual([1], _a(OTHER, [_m(1, PATRIK, 1), _m(2, BOT, 14)]))

    def test_a_stream_running_as_partner_2_stays_a_stream(self):
        cfg = _cfg("https://erp.example.sk", stream_partner_ids=[2])
        self.assertEqual([], _a(cfg, [_m(1, PATRIK, 1), _m(2, BOT, 14)]))


def reminder_ts(msg):
    return board.parse_dt(msg["date"]).timestamp()


class OwnerSide(unittest.TestCase):
    def test_owner_partners_are_no_client_on_montalu(self):
        self.assertEqual([], _a(MONTALU, [_m(1, OWNER, 20)]))
        self.assertEqual([], _a(MONTALU, [_m(1, OWNER_2, 20)]))

    def test_a_sibling_stream_is_no_client_on_montalu(self):
        self.assertEqual([], _a(MONTALU, [_m(1, MAREK_AI, 20)]))

    def test_owner_ids_stay_on_their_board(self):
        self.assertEqual([1], _a(OTHER, [_m(1, OWNER, 20)]))

    def test_per_box_owner_partner_ids(self):
        cfg = _cfg("https://erp.example.sk", owner_partner_ids=[17129])
        self.assertEqual([], _a(cfg, [_m(1, OWNER, 20)]))

    def test_owner_comment_does_not_satisfy_class_b(self):
        fake = FakeOdoo([_task()], {1: [_m(1, OWNER, 20)]})
        res = th.compute_hygiene(fake.call, MONTALU, now=NOW)
        self.assertEqual([1], [it["task_id"] for it in res["B"]])

    def test_owner_comment_still_cancels_the_auto_close_wait(self):
        task = _task(stage=(VERIF, "Verifikácia"))
        task["date_last_stage_update"] = "2026-10-01 09:00:00"
        prof = board.auto_close_profile(MONTALU)
        ac = board.classify(task, [_m(1, OWNER, 20)], prof, NOW, {17244})
        self.assertIsNone(ac)


class Reactions(unittest.TestCase):
    ROW = {"message_id": 1, "content": "👷", "partner_id": 17244,
           "partner_name": "ZbynekAI", "guest_id": False, "guest_name": False}

    def test_stream_ack_is_read_through_the_real_signature(self):
        self.assertEqual([], _a(OTHER, [_m(1, PATRIK, 20)],
                                reactions={1: [self.ROW]}))

    def test_owner_ack_counts_on_montalu(self):
        row = dict(self.ROW, partner_id=17129, partner_name="Zbyněk Drlík")
        self.assertEqual([], _a(MONTALU, [_m(1, PATRIK, 20)], reactions={1: [row]}))

    def test_ack_matched_by_the_reactor_name(self):
        cfg = _cfg("https://erp.example.sk", stream_partner_ids=[],
                   own_author_names=["ZbynekAI"])
        self.assertEqual([], _a(cfg, [_m(1, PATRIK, 20)], reactions={1: [self.ROW]}))

    def test_client_reaction_is_no_ack(self):
        row = dict(self.ROW, partner_id=21, partner_name="Dominik Volek")
        self.assertEqual([1], _a(OTHER, [_m(1, PATRIK, 20)], reactions={1: [row]}))

    def test_a_403_on_reaction_ids_leaves_the_guarded_read_working(self):
        class Denied(FakeOdoo):
            def call(self, model, method, **body):
                if method == "search_read" and "reaction_ids" in body.get("fields", []):
                    raise ro.OdooError("Odoo mail.message/search_read HTTP 403: denied")
                return super().call(model, method, **body)

        msgs = {1: [_m(1, PATRIK, 20)], 2: [_m(2, PATRIK, 21)]}
        fake = Denied([_task(1), _task(2)], msgs, reactions={1: [self.ROW]})
        res = th.compute_hygiene(fake.call, OTHER, now=NOW)
        self.assertTrue(res["reactions_unavailable"])
        self.assertEqual([2], [it["task_id"] for it in res["A"]])
        self.assertNotIn("reaction_unknown", res["A"][0])

    def test_availability_probe_uses_the_real_signature(self):
        fake = FakeOdoo([], {})
        self.assertTrue(hh.guarded_reactions_ok(fake.call, [{"id": 1}]))


class ColleagueAddressed(unittest.TestCase):
    KUKNI = "<span>%s kukni to prosím ťa</span>" % _mention(21, "Dominik Volek")

    def test_the_live_client_to_client_comment_is_not_a(self):
        self.assertEqual([], _a(MONTALU, [_m(1, ZBYNEK_AI, 1),
                                          _m(2, PATRIK, 20, self.KUKNI)]))

    def test_several_colleagues(self):
        body = "<p>%s %s kuk</p>" % (_mention(21, "Dominik Volek"), _mention(20, "Pavol"))
        self.assertEqual([], _a(MONTALU, [_m(2, PATRIK, 20, body)]))

    def test_a_mention_of_the_stream_is_a(self):
        body = "<p>%s neviem</p>" % _mention(17244, "ZbynekAI")
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, body)]))

    def test_a_mention_of_a_sibling_stream_is_a(self):
        body = "<p>%s co je bod A11?</p>" % _mention(20848, "MarekAI")
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, body)]))

    def test_a_mention_of_the_stream_by_name_is_a(self):
        body = "<p>%s neviem</p>" % _mention(555, "ZbynekAI")
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, body)]))

    def test_colleague_plus_stream_is_a(self):
        body = "<p>%s %s kuk</p>" % (_mention(21, "Dominik Volek"),
                                     _mention(20848, "MarekAI"))
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, body)]))

    def test_a_mention_inside_a_sentence_is_a(self):
        body = "<p>aj grafiky dostaneme od %s</p>" % _mention(21, "Dominik Volek")
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, body)]))

    def test_anchors_only_in_a_quoted_reply_are_a(self):
        body = ('<div data-oe-version="2.0">Máme odpojene money ?&nbsp;</div>'
                '<div class="o_mail_reply_container" data-o-mail-quote="1">%s</div>'
                % _mention(21, "Dominik Volek"))
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, body)]))

    def test_recipients_without_a_mention_are_a(self):
        body = "<p>Funguje, ale problém že nejde rozdeliť</p>"
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, body, partner_ids=[21])]))

    def test_older_client_comment_below_a_colleague_mention_is_found(self):
        msgs = [_m(1, PATRIK, 19, "<p>nejde to</p>"), _m(2, PATRIK, 20, self.KUKNI)]
        fake = FakeOdoo([_task()], {1: msgs})
        res = th.compute_hygiene(fake.call, MONTALU, now=NOW)
        self.assertEqual("2026-10-19 09:00:00", res["A"][0]["date"])

    def test_non_string_body_is_a(self):
        self.assertEqual([1], _a(MONTALU, [_m(2, PATRIK, 20, False)]))

    def test_the_batched_read_asks_for_the_body(self):
        seen = {}

        class Spy(FakeOdoo):
            def call(self, model, method, **body):
                if model == "mail.message" and method == "search_read":
                    seen.setdefault("fields", body.get("fields"))
                return super().call(model, method, **body)

        fake = Spy([_task()], {1: [_m(1, PATRIK, 20)]})
        th.compute_hygiene(fake.call, MONTALU, now=NOW)
        self.assertIn("body", seen["fields"])

    def test_opening_scan_stays_linear(self):
        started = time.monotonic()
        view = board.ClientView(MONTALU, board.auto_close_profile(MONTALU), {"ZbynekAI"}, {17244})
        self.assertFalse(view.colleague_addressed({"body": "<span>" * 40000}))
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
