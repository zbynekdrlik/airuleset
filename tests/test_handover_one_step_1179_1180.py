"""#1179 + #1180 — a client handover is ONE enforced step, and its rules load
where the hand-off actually happens (owner, montalu1 28.9. + montalu4 29.9.).

Locks the main's Approach 1 (a)-(e):

(a) TRIGGER — `client-board-stages.md` also injects on a Bash `odoo_post.py …
    --approval-ref` post and on a `gh issue comment` whose body carries
    `Acceptance-thread:` (inline, heredoc, or a Write-authored body file); the
    stage-move Write binding stays.
(b) RULE TEXT — rule 3 names the poster's handover mode (odoo-erp#8606) as the
    ONLY way to post a handover on a project.task, inside the #1102 budget.
(c) STOP GATE — `gates.handover` (dispatched from the #1018 block of
    stop-check-prose-violations.sh) blocks a turn that posted a handover-shaped
    note on a project.task via `odoo_post.py` WITHOUT `--handover`, and a turn
    whose `Acceptance-thread:` comment names a board task with no `--handover`
    run / verification-stage move of it; a non-handover task post, a Discuss
    post, an earlier turn, a `--handover` run and a cited bypass all pass.
(d) JOB 49 — a 403 on the `reaction_ids` read degrades (A/C still run, one
    `reactions unavailable (403)` log per day) and never hides another
    failure; class H flags a stream handover on a task that is NOT in the
    verification stage 10 min later → nudge + Stop-gate line.
(e) DOCTRINE-AUDIT — a report-only `(memory regex, superseding rule)` table
    flags the montalu `no assignee` memory against #1166, never edits it.

Fixtures only — never a live Odoo, never a real pane.
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hook_state_cleanup import hermetic_hook_env, sweep_session_files  # noqa: E402

import cli_doctrine_audit as da  # noqa: E402
import cli_odoo_ro as ro  # noqa: E402
import cli_task_hygiene as th  # noqa: E402
from watchdog import task_hygiene as job  # noqa: E402

INJECT = REPO / "hooks" / "inject-situational-rule.sh"
PROSE = REPO / "hooks" / "stop-check-prose-violations.sh"
UNTRACKED = REPO / "hooks" / "stop-check-untracked-work.sh"
STAGES = REPO / "skills" / "odoo-client-messaging" / "client-board-stages.md"

TASK_URL = "https://erp.montalu.cloud/odoo/project/5/tasks/%s"
HANDOVER_BODY = ("<p>Čo: nový export faktúr.</p><p>Kde: Účtovníctvo ▸ Export — "
                 "https://erp.montalu.cloud/odoo/action-123</p><p>Čo skúsiť: "
                 "exportujte september.</p><p>stačí 👍</p>")
REPLY_BODY = "<p>Dobrý deň, áno — faktúru z augusta treba stornovať a vystaviť znova.</p>"


# --------------------------------------------------------------------------- #
# (a) trigger
# --------------------------------------------------------------------------- #
def _inject(tool, tool_input, tmp):
    sid = "h1179-" + uuid.uuid4().hex[:12]
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
               "session_id": sid, "tool_input": tool_input}
    env = dict(os.environ, TMPDIR=tmp)
    r = subprocess.run(["bash", str(INJECT)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    if not r.stdout.strip():
        return False
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    return 'file="skills/odoo-client-messaging/client-board-stages.md"' in ctx


class TestTrigger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h1179-inj-")

    def test_fires_on_odoo_post_with_approval_ref(self):
        self.assertTrue(_inject("Bash", {"command": (
            "cd ~/devel/odoo/odoo-erp && python3 scripts/odoo_post.py --model "
            "project.task --res-id 1192 --approval-ref \"owner 29.9. msg 4378\" "
            "--body \"$(cat /tmp/h.html)\"")}, self.tmp))

    def test_fires_on_line_continued_odoo_post(self):
        self.assertTrue(_inject("Bash", {"command": (
            "python3 scripts/odoo_post.py --model discuss.channel --res-id 783 \\\n"
            "  --approval-ref \"owner msg 12\" --body \"$(cat /tmp/h.html)\"")},
            self.tmp))

    def test_fires_on_acceptance_thread_inline_comment(self):
        self.assertTrue(_inject("Bash", {"command": (
            "gh issue comment 8606 --body \"Acceptance-thread: %s\""
            % (TASK_URL % 1192))}, self.tmp))

    def test_fires_on_acceptance_thread_heredoc_comment(self):
        self.assertTrue(_inject("Bash", {"command": (
            "gh issue comment 8606 -F - <<'EOF'\nOdoslané.\nAcceptance-thread: "
            "%s\nEOF" % (TASK_URL % 1192))}, self.tmp))

    def test_fires_on_acceptance_thread_body_file_write(self):
        self.assertTrue(_inject("Write", {
            "file_path": "/tmp/ack-8606.md",
            "content": "Acceptance-thread: %s\n" % (TASK_URL % 1192)}, self.tmp))

    def test_stage_move_binding_kept(self):
        self.assertTrue(_inject("Write", {
            "file_path": "/x/task_sync.py",
            "content": "env['project.task'].browse(1).write({'stage_id': 7})"},
            self.tmp))

    def test_silent_on_unrelated_post_and_comment(self):
        self.assertFalse(_inject("Bash", {"command": (
            "python3 scripts/odoo_post.py --model discuss.channel --res-id 31 "
            "--body \"$(cat /tmp/x.html)\"")}, self.tmp))
        self.assertFalse(_inject("Bash", {"command": (
            "gh issue comment 5 --body \"LANE-RETURN: branch x head abc\"")},
            self.tmp))


# --------------------------------------------------------------------------- #
# (b) rule text
# --------------------------------------------------------------------------- #
class TestRuleText(unittest.TestCase):
    def test_rule3_names_the_one_step_handover_mode(self):
        text = STAGES.read_text(encoding="utf-8")
        self.assertIn("odoo_post.py --handover", text)
        self.assertIn("odoo-erp#8606", text)
        self.assertIn("Acceptance-thread:", text)


# --------------------------------------------------------------------------- #
# (c) Stop gate
# --------------------------------------------------------------------------- #
def _entry_user(text, meta=False):
    e = {"type": "user", "message": {"role": "user", "content": text}}
    if meta:
        e["isMeta"] = True
    return e


def _entry_tool(cmd, name="Bash", tid=None):
    tid = tid or ("t" + uuid.uuid4().hex[:8])
    inp = {"command": cmd} if name == "Bash" else cmd
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}},
    ]


def _transcript(entries):
    fd, path = tempfile.mkstemp(prefix="h1180-", suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def _turn(*cmds, prior=()):
    entries = [_entry_user("predchádzajúca úloha")]
    for c in prior:
        entries.extend(_entry_tool(c))
    entries.append(_entry_user("owner: schvaľujem, pošli to"))
    for c in cmds:
        entries.extend(_entry_tool(c))
    return _transcript(entries)


def _post(res_id, body, model="project.task", extra=""):
    return ("cd ~/devel/odoo/odoo-erp && python3 scripts/odoo_post.py --model %s "
            "--res-id %s --approval-ref \"owner 29.9. msg 1\"%s --body %s"
            % (model, res_id, extra, json.dumps(body, ensure_ascii=False)))


def _verdict(tpath, msg="Odoslané.\n\n✅ DONE: správa odoslaná", cwd=None):
    from gates import handover
    return handover.evaluate(tpath, msg, cwd or tempfile.gettempdir())[0]


class TestStopGate(unittest.TestCase):
    def test_blocks_task_handover_post_without_handover_mode(self):
        self.assertEqual(_verdict(_turn(_post(1192, HANDOVER_BODY))), "block")

    def test_allows_task_handover_post_with_handover_mode(self):
        self.assertEqual(_verdict(_turn(_post(1192, HANDOVER_BODY,
                                              extra=" --handover"))), "allow")

    def test_allows_reply_to_client_question_on_task(self):
        self.assertEqual(_verdict(_turn(_post(1192, REPLY_BODY))), "allow")

    def test_allows_discuss_free_conversation(self):
        self.assertEqual(_verdict(_turn(_post(783, HANDOVER_BODY,
                                              model="discuss.channel"))), "allow")

    def test_blocks_handover_body_read_from_file(self):
        d = tempfile.mkdtemp()
        body = os.path.join(d, "h.html")
        Path(body).write_text(HANDOVER_BODY, encoding="utf-8")
        cmd = ("python3 scripts/odoo_post.py --model project.task --res-id 1192 "
               "--approval-ref \"x\" --body \"$(cat %s)\"" % body)
        self.assertEqual(_verdict(_turn(cmd)), "block")

    def test_blocks_acceptance_thread_naming_unmoved_task(self):
        cmd = 'gh issue comment 8606 --body "Acceptance-thread: %s"' % (TASK_URL % 644)
        self.assertEqual(_verdict(_turn(cmd)), "block")

    def test_blocks_acceptance_thread_from_body_file(self):
        d = tempfile.mkdtemp()
        body = os.path.join(d, "ack.md")
        Path(body).write_text("Odoslané do Discuss.\nAcceptance-thread: %s\n"
                              % (TASK_URL % 923), encoding="utf-8")
        self.assertEqual(_verdict(_turn("gh issue comment 8606 -F %s" % body)),
                         "block")

    def test_acceptance_thread_satisfied_by_stage_move(self):
        cmds = ("python3 scripts/odoo-task-sync.py move --task 644 --stage Verifikácia",
                'gh issue comment 8606 --body "Acceptance-thread: %s"' % (TASK_URL % 644))
        self.assertEqual(_verdict(_turn(*cmds)), "allow")

    def test_acceptance_thread_satisfied_by_handover_run(self):
        cmds = (_post(644, HANDOVER_BODY, extra=" --handover"),
                'gh issue comment 8606 --body "Acceptance-thread: %s"' % (TASK_URL % 644))
        self.assertEqual(_verdict(_turn(*cmds)), "allow")

    def test_move_of_other_task_does_not_satisfy(self):
        cmds = ("python3 scripts/odoo-task-sync.py move --task 111 --stage Verifikácia",
                'gh issue comment 8606 --body "Acceptance-thread: %s"' % (TASK_URL % 644))
        self.assertEqual(_verdict(_turn(*cmds)), "block")

    def test_body_text_is_not_a_stage_move(self):
        # the comment itself names the task AND the word Verifikácia — that is a
        # MENTION, never evidence the task moved
        cmd = ('gh issue comment 8606 --body "Presunuté do Verifikácia. '
               'Acceptance-thread: %s"' % (TASK_URL % 644))
        self.assertEqual(_verdict(_turn(cmd)), "block")

    def test_acceptance_thread_on_discuss_only_passes(self):
        cmd = ('gh issue comment 8606 --body "Acceptance-thread: https://erp.montalu.'
               'cloud/odoo/discuss?active_id=discuss.channel_783"')
        self.assertEqual(_verdict(_turn(cmd)), "allow")

    def test_earlier_turn_is_not_this_turn(self):
        t = _turn("git status", prior=(_post(1192, HANDOVER_BODY),))
        self.assertEqual(_verdict(t), "allow")

    def test_stop_hook_feedback_does_not_reset_the_turn(self):
        entries = [_entry_user("owner: pošli to")]
        entries.extend(_entry_tool(_post(1192, HANDOVER_BODY)))
        entries.append(_entry_user("Stop hook feedback:\nfix it", meta=True))
        self.assertEqual(_verdict(_transcript(entries)), "block")

    def test_mentions_of_the_poster_are_not_a_post(self):
        self.assertEqual(_verdict(_turn("grep -n handover scripts/odoo_post.py",
                                        "python3 scripts/odoo_post.py --help")),
                         "allow")

    def test_cited_bypass_allows(self):
        msg = ("Úlohu 1192 som presunul ručne do Verifikácie + assignee.\n"
               "airuleset:handover-ok 1192 moved + assignee set by hand\n\n✅ DONE: ok")
        self.assertEqual(_verdict(_turn(_post(1192, HANDOVER_BODY)), msg=msg),
                         "allow")

    def test_missing_transcript_fails_open(self):
        self.assertEqual(_verdict("/nonexistent/t.jsonl"), "allow")

    def test_prose_hook_wires_the_gate(self):
        tpath = _turn(_post(1192, HANDOVER_BODY))
        sid = "h1180-" + uuid.uuid4().hex[:10]
        payload = json.dumps({"session_id": sid, "transcript_path": tpath,
                              "cwd": tempfile.gettempdir(),
                              "last_assistant_message": "Odoslané.\n\n✅ DONE: správa odoslaná"})
        p = subprocess.run(["bash", str(PROSE)], input=payload, capture_output=True,
                           text=True, timeout=300, env=hermetic_hook_env(self))
        sweep_session_files(sid)
        self.assertIn('"block"', p.stdout)
        self.assertIn("#1180", p.stdout)


# --------------------------------------------------------------------------- #
# (d) Job 49 — reaction 403 degrade + class H
# --------------------------------------------------------------------------- #
NOW = datetime.datetime(2026, 9, 29, 12, 0, 0, tzinfo=datetime.timezone.utc)
CFG = {"instance_url": "https://erp.montalu.cloud", "project_ids": [5],
       "stage_ids": {"verifikacia": 2880, "realizacia": 2879,
                     "potrebuje_ujasnit": 3350, "hotovo": 2881},
       "stream_partner_ids": [17244], "own_author_names": ["ZbynekAI"],
       "client_confirm_days": 3}
STREAM = [17244, "ZbynekAI"]
CLIENT = [999, "Patrik Javorský"]


def _dt(minutes_ago):
    return (NOW - datetime.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


class Fake:
    """In-memory Odoo for the `call` seam. `deny_reactions` = a 403 whenever the
    batched read asks for `reaction_ids`; `deny_all` = a 403 on every message
    read; `status` overrides the status of the reaction-field denial."""

    def __init__(self, tasks, msgs, deny_reactions=False, deny_all=False,
                 status=403):
        self.tasks, self.msgs = tasks, msgs
        self.deny_reactions, self.deny_all, self.status = deny_reactions, deny_all, status

    def call(self, model, method, **body):
        if model == "project.task":
            return [dict(t) for t in self.tasks]
        if method == "message_reactions_guarded":
            raise ro.OdooError("Odoo mail.message/message_reactions_guarded HTTP 403: denied")
        fields = body.get("fields", [])
        if self.deny_all or (self.deny_reactions and "reaction_ids" in fields):
            raise ro.OdooError("Odoo mail.message/search_read HTTP %s: access denied"
                               % self.status)
        rids, terms = set(), []
        for cond in body.get("domain", []):
            if cond[0] == "res_id" and cond[1] == "in":
                rids = set(cond[2])
            if cond[0] == "body" and cond[1] == "ilike":
                terms.append(cond[2].lower())
        rows = [dict(m) for m in self.msgs if m["res_id"] in rids
                and all(t in (m.get("body") or "").lower() for t in terms)]
        rows.sort(key=lambda m: m["date"], reverse=True)
        rows.sort(key=lambda m: m["res_id"])
        return [{k: v for k, v in m.items() if k in fields or k == "res_id"}
                for m in rows]


def _task(tid, stage, moved_min_ago=600):
    names = {2880: "Verifikácia", 2879: "Realizácia", 3350: "Potrebuje ujasniť"}
    return {"id": tid, "name": "T%s" % tid, "stage_id": [stage, names[stage]],
            "date_last_stage_update": _dt(moved_min_ago)}


def _m(mid, rid, author, minutes_ago, body="<p>ok</p>"):
    return {"id": mid, "res_id": rid, "author_id": author, "date": _dt(minutes_ago),
            "body": body, "reaction_ids": [], "message_type": "comment"}


class TestReaction403(unittest.TestCase):
    def test_degrades_and_still_reports_a_and_c(self):
        fake = Fake([_task(1, 2879), _task(2, 2880, moved_min_ago=6 * 1440)],
                    [_m(10, 1, CLIENT, 60), _m(20, 2, STREAM, 5 * 1440, HANDOVER_BODY)],
                    deny_reactions=True)
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertEqual([x["task_id"] for x in r["A"]], [1])
        self.assertEqual([x["task_id"] for x in r["C"]], [2])
        self.assertTrue(r["reactions_unavailable"])

    def test_healthy_read_reports_reactions_available(self):
        fake = Fake([_task(1, 2879)], [_m(10, 1, CLIENT, 60)])
        self.assertFalse(th.compute_hygiene(fake.call, CFG, now=NOW)["reactions_unavailable"])

    def test_a_403_that_is_not_the_reaction_field_still_fails(self):
        fake = Fake([_task(1, 2879)], [_m(10, 1, CLIENT, 60)], deny_all=True)
        with self.assertRaises(ro.OdooError):
            th.compute_hygiene(fake.call, CFG, now=NOW)

    def test_a_non_403_failure_is_not_degraded(self):
        fake = Fake([_task(1, 2879)], [_m(10, 1, CLIENT, 60)],
                    deny_reactions=True, status=500)
        with self.assertRaises(ro.OdooError):
            th.compute_hygiene(fake.call, CFG, now=NOW)


class TestClassH(unittest.TestCase):
    def _h(self, tasks, msgs):
        return [x["task_id"] for x in
                th.compute_hygiene(Fake(tasks, msgs).call, CFG, now=NOW)["H"]]

    def test_handover_on_unmoved_task_after_10_min_is_h(self):
        self.assertEqual(self._h([_task(1192, 2879)],
                                 [_m(1, 1192, STREAM, 11, HANDOVER_BODY)]), [1192])

    def test_within_grace_is_not_h(self):
        self.assertEqual(self._h([_task(1192, 2879)],
                                 [_m(1, 1192, STREAM, 5, HANDOVER_BODY)]), [])

    def test_task_in_verification_is_not_h(self):
        self.assertEqual(self._h([_task(1192, 2880)],
                                 [_m(1, 1192, STREAM, 60, HANDOVER_BODY)]), [])

    def test_stage_move_after_handover_is_not_h(self):
        self.assertEqual(self._h([_task(1192, 2879, moved_min_ago=30)],
                                 [_m(1, 1192, STREAM, 60, HANDOVER_BODY)]), [])

    def test_client_or_non_handover_message_is_not_h(self):
        self.assertEqual(self._h([_task(1, 2879), _task(2, 3350)],
                                 [_m(1, 1, CLIENT, 60, HANDOVER_BODY),
                                  _m(2, 2, STREAM, 60, "<p>Stačí odpovedať áno/nie.</p>")]),
                         [])

    def test_h_persists_and_nudges(self):
        fake = Fake([_task(1192, 2879)], [_m(1, 1192, STREAM, 11, HANDOVER_BODY)])
        r = th.compute_hygiene(fake.call, CFG, now=NOW)
        self.assertIn("H=1", r["summary"])
        self.assertIn("#1192", th.compose_nudge(r, CFG))
        home = tempfile.mkdtemp()
        st = th.persist_status(r, home=home)
        self.assertEqual(st["h"], 1)
        self.assertTrue(st["h_items"][0].startswith("#1192"))


class _JobDeps:
    def __init__(self, result):
        self.result, self.delivered = result, []

    def run(self, state, now):
        return job.task_hygiene_job(
            now, state, [("%1", "/home/m/devel/odoo")], "/proj", cfg=CFG,
            compute=lambda c: self.result, persist=lambda r: None,
            deliver=lambda pid, tp, text: self.delivered.append(text) or True,
            gate_ok=lambda *a: True, mark_sent=lambda *a: None,
            find_transcript=lambda pd, cwd: ("/t/s.jsonl",),
            capture=lambda pid: "❯ ", in_mode=lambda pid: False,
            at_idle=lambda c: True, recent_human=lambda *a: False)


def _res(h=0, r403=False):
    return {"A": [], "B": [], "C": [],
            "H": [{"task_id": 1192 + i, "task_name": "t", "stage": "Realizácia"}
                  for i in range(h)],
            "summary": "task-hygiene: A=0 B=0 C=0" + (" H=%d" % h if h else ""),
            "reactions_unavailable": r403}


class TestJob49(unittest.TestCase):
    def test_reaction_403_logged_once_per_day(self):
        deps, state = _JobDeps(_res(r403=True)), {}
        day1 = datetime.datetime(2026, 9, 29, 8, tzinfo=datetime.timezone.utc).timestamp()
        first = deps.run(state, day1)
        second = deps.run(state, day1 + 7200)
        third = deps.run(state, day1 + 86400)
        tag = "reactions unavailable (403)"
        self.assertEqual(sum(tag in ln for ln in first), 1)
        self.assertEqual(sum(tag in ln for ln in second), 0)
        self.assertEqual(sum(tag in ln for ln in third), 1)

    def test_h_alone_nudges(self):
        deps = _JobDeps(_res(h=1))
        logs = deps.run({}, 1_000_000.0)
        self.assertEqual(len(deps.delivered), 1)
        self.assertTrue(any("H=1" in ln for ln in logs))


class TestUntrackedStopLine(unittest.TestCase):
    def test_h_blocks_with_its_line(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".claude", "task-hygiene"))
        with open(os.path.join(home, ".claude", "task-hygiene", "status.json"), "w") as fh:
            json.dump({"ts": time.time(), "a": 0, "b": 0, "c": 0, "b_verif": 0,
                       "h": 1, "h_items": ["#1192 Export faktúr"]}, fh)
        sid = "h1180u-" + uuid.uuid4().hex[:10]
        env = dict(os.environ, HOME=home)
        p = subprocess.run(["bash", str(UNTRACKED)], input=json.dumps(
            {"last_assistant_message": "hotovo\n✅ DONE: x", "session_id": sid}),
            capture_output=True, text=True, env=env, timeout=60)
        sweep_session_files(sid)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("#1192", p.stderr)
        self.assertIn("Verifik", p.stderr)


# --------------------------------------------------------------------------- #
# (e) doctrine-audit — superseded memory, report-only
# --------------------------------------------------------------------------- #
class TestSupersededMemory(unittest.TestCase):
    def _home(self, files):
        home = tempfile.mkdtemp()
        for proj, name, text in files:
            d = os.path.join(home, ".claude", "projects", proj, "memory")
            os.makedirs(d, exist_ok=True)
            Path(d, name).write_text(text, encoding="utf-8")
        return home

    def test_flags_montalu_no_assignee_memory(self):
        home = self._home([("-home-montalu4-devel-odoo-odoo-erp",
                            "project_board-tasks-no-assignee.md",
                            "---\nname: board tasks\n---\nBoard tasks: no assignee "
                            "(owner 9.7.) — nikdy nenastavuj user_ids.\n")])
        found = da.scan_superseded_memory(home)
        self.assertEqual(len(found), 1)
        self.assertIn("#1166", found[0]["superseded_by"])
        self.assertIn("client-board-stages.md", found[0]["superseded_by"])

    def test_miva_no_assignee_is_current_doctrine(self):
        home = self._home([("-home-miva1-devel-odoo-odoo-erp", "board.md",
                            "miva client task carries no assignee (user_ids empty).\n")])
        self.assertEqual(da.scan_superseded_memory(home), [])

    def test_report_only_never_edits(self):
        text = "Board tasks: no assignee.\n"
        home = self._home([("-home-montalu1-devel-odoo-odoo-erp", "x.md", text)])
        da.audit(home, fix=True)
        da.scan_superseded_memory(home)
        path = Path(home, ".claude", "projects", "-home-montalu1-devel-odoo-odoo-erp",
                    "memory", "x.md")
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_cli_prints_the_superseded_section(self):
        home = self._home([("-home-montalu4-devel-odoo-odoo-erp", "b.md",
                            "Board tasks: no assignee.\n")])
        env = dict(os.environ, HOME=home)
        p = subprocess.run([sys.executable, str(REPO / "airuleset.py"), "doctrine-audit"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("superseded", p.stdout.lower())
        self.assertIn("#1166", p.stdout)


if __name__ == "__main__":
    unittest.main()
