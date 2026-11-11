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
(d) JOB 49 (class H + the reaction-403 degrade) — removed with the montalu
    board machinery (#1306); its tests went with it.
(e) DOCTRINE-AUDIT — a report-only `(memory regex, superseding rule)` table
    flags the montalu `no assignee` memory against #1166, never edits it.

Fixtures only — never a live Odoo, never a real pane.
"""
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

INJECT = REPO / "hooks" / "inject-situational-rule.sh"
PROSE = REPO / "hooks" / "stop-check-prose-violations.sh"
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

    def test_silent_on_unrelated_comment(self):
        # (review 2: ANY poster call now loads the rule — a Discuss post is a
        # possible handover too, so that case moved to TestTriggerReview2)
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


def _entry_tool(cmd, name="Bash", tid=None, error=False):
    tid = tid or ("t" + uuid.uuid4().hex[:8])
    inp = {"command": cmd} if name == "Bash" else cmd
    res = {"type": "tool_result", "tool_use_id": tid, "content": "ok"}
    if error:
        res.update(is_error=True, content="Exit code 2")
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}},
        {"type": "user", "message": {"role": "user", "content": [res]}},
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
        cmd = ('gh issue comment 8606 --body "Presunuté do Verifikácia.\n'
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


# --------------------------------------------------------------------------- #
# Review-1 regressions (fresh-context adversarial review, 29.9.2026) — every case
# below was a reproduced false block / slip-through / hidden failure.
# --------------------------------------------------------------------------- #
QUESTION_BODY = "<p>Skúsili ste 20 mm? Stačí napísať číslo.</p>"
ACC = 'gh issue comment 8606 --body "Acceptance-thread: %s"'


class TestStopGateReview1(unittest.TestCase):
    def test_question_with_both_loose_words_is_not_a_handover(self):
        self.assertEqual(_verdict(_turn(_post(1192, QUESTION_BODY))), "allow")

    def test_no_stage_move_post_is_not_a_handover(self):
        self.assertEqual(_verdict(_turn(_post(1192, HANDOVER_BODY,
                                              extra=" --no-stage-move"))), "allow")

    def test_heredoc_reply_allowed_heredoc_handover_blocked(self):
        cmd = ("python3 scripts/odoo_post.py --model project.task --res-id 1192 "
               "--body \"$(cat <<'EOF'\n%s\nEOF\n)\"")
        self.assertEqual(_verdict(_turn(cmd % REPLY_BODY)), "allow")
        self.assertEqual(_verdict(_turn(cmd % HANDOVER_BODY)), "block")

    def test_also_tasks_are_settled_by_the_handover_run(self):
        cmds = (_post(981, HANDOVER_BODY, extra=" --handover --also-tasks 979 980"),
                ACC % (TASK_URL % 979))
        self.assertEqual(_verdict(_turn(*cmds)), "allow")

    def test_chained_post_and_comment_are_judged_per_segment(self):
        post = _post(1192, HANDOVER_BODY)
        self.assertEqual(_verdict(_turn(post + " && gh issue comment 7 --body posted")),
                         "block")
        hand = _post(1192, HANDOVER_BODY, extra=" --handover")
        self.assertEqual(_verdict(_turn(hand + " && " + ACC % (TASK_URL % 1192))), "allow")

    def test_wrapped_invocations_are_posts(self):
        body = json.dumps(HANDOVER_BODY, ensure_ascii=False)
        for cmd in ("timeout 60 python3 scripts/odoo_post.py --model project.task "
                    "--res-id 1192 --body %s" % body,
                    "python3 -m scripts.odoo_post --model project.task --res-id 1192 "
                    "--body %s" % body,
                    "bash -c %s" % json.dumps(
                        "python3 scripts/odoo_post.py --model project.task --res-id 1192 "
                        "--body '%s'" % HANDOVER_BODY, ensure_ascii=False)):
            with self.subTest(cmd=cmd[:40]):
                self.assertEqual(_verdict(_turn(cmd)), "block")

    def test_other_comment_shapes_are_acceptance_comments(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "acc.md")
        Path(f).write_text("Acceptance-thread: %s\n" % (TASK_URL % 1192), encoding="utf-8")
        for cmd in ("gh issue close 8606 --comment 'Acceptance-thread: %s'" % (TASK_URL % 1192),
                    "gh api repos/o/r/issues/8606/comments -F body=@%s" % f,
                    "gh api repos/o/r/issues/8606/comments -f body='Acceptance-thread: %s'"
                    % (TASK_URL % 1192)):
            with self.subTest(cmd=cmd[:30]):
                self.assertEqual(_verdict(_turn(cmd)), "block")

    def test_a_stage_id_read_or_grep_is_not_a_move(self):
        read = ("python3 - <<'EOF'\nprint(m.execute_kw(db, uid, k, 'project.task', "
                "'read', [[1192]], {'fields': ['stage_id']}))\nEOF")
        for ev in (read, "grep -rn stage_id notes/1192.md"):
            with self.subTest(ev=ev[:20]):
                self.assertEqual(_verdict(_turn(ev, ACC % (TASK_URL % 1192))), "block")

    def test_a_heredoc_write_to_verification_is_a_move(self):
        write = ("python3 - <<'EOF'\nm.execute_kw(db, uid, k, 'project.task', 'write', "
                 "[[1192], {'stage_id': VERIF}])\nEOF")
        self.assertEqual(_verdict(_turn(write, ACC % (TASK_URL % 1192))), "allow")

    def test_a_failed_or_dry_handover_run_settles_nothing(self):
        entries = [_entry_user("owner: pošli")]
        entries.extend(_entry_tool(_post(1192, HANDOVER_BODY, extra=" --handover"), error=True))
        entries.extend(_entry_tool(ACC % (TASK_URL % 1192)))
        self.assertEqual(_verdict(_transcript(entries)), "block")
        dry = _post(1192, HANDOVER_BODY, extra=" --handover --dry-run")
        self.assertEqual(_verdict(_turn(dry, ACC % (TASK_URL % 1192))), "block")

    def test_a_failed_post_owes_nothing(self):
        entries = [_entry_user("owner: pošli")]
        entries.extend(_entry_tool(_post(1192, HANDOVER_BODY), error=True))
        self.assertEqual(_verdict(_transcript(entries)), "allow")

    def test_discuss_handover_naming_tasks_owes_their_move(self):
        body = HANDOVER_BODY + "<p>Úlohy: %s</p>" % (TASK_URL % 644)
        post = _post(783, body, model="discuss.channel")
        self.assertEqual(_verdict(_turn(post)), "block")
        move = "python3 scripts/odoo-task-sync.py move --task 644 --stage Verifikácia"
        self.assertEqual(_verdict(_turn(post, move)), "allow")

    def test_mcp_write_with_configured_verification_id_is_a_move(self):
        from gates import handover
        entries = [_entry_user("owner: pošli")]
        entries.extend(_entry_tool({"model": "project.task", "method": "write",
                                    "ids": [644], "values": {"stage_id": 2880}},
                                   name="mcp__odoo__call"))
        entries.extend(_entry_tool(ACC % (TASK_URL % 644)))
        tp = _transcript(entries)
        self.assertEqual(handover.evaluate(tp, "ok", "/tmp", verif_id=2880)[0], "allow")
        self.assertEqual(handover.evaluate(tp, "ok", "/tmp", verif_id=2879)[0], "block")

    def test_bypass_must_be_its_own_line_with_a_reason(self):
        t = _turn(_post(1192, HANDOVER_BODY))
        self.assertEqual(_verdict(t, msg="Escape je `airuleset:handover-ok <reason>`.\n✅ DONE"),
                         "block")
        self.assertEqual(_verdict(t, msg="airuleset:handover-ok x\n✅ DONE"), "block")


class TestInstallStepReportsSupersededMemory(unittest.TestCase):
    def test_install_step_line(self):
        import airuleset
        home = tempfile.mkdtemp()
        d = os.path.join(home, ".clau" + "de", "projects",
                         "-home-montalu4-devel-odoo-odoo-erp", "memory")
        os.makedirs(d)
        Path(d, "b.md").write_text("Board tasks: no assignee.\n", encoding="utf-8")
        lines = airuleset._run_doctrine_audit_step(home=home, repo_dir=None)
        self.assertTrue(any("#1179" in ln and "contradict" in ln for ln in lines), lines)


# --------------------------------------------------------------------------- #
# Review-2 regressions (second fresh-context adversarial review, 29.9.2026).
# --------------------------------------------------------------------------- #
POST_OK = "OK: mail.message 5551 posted and verified clean."


def _calls(*items):
    """[_entry_user] + per item (cmd | (cmd, error, result) | (name, dict))."""
    entries = [_entry_user("owner: schvaľujem")]
    for it in items:
        if isinstance(it, str):
            entries.extend(_entry_tool(it))
        elif isinstance(it[1], dict):
            entries.extend(_entry_tool(it[1], name=it[0]))
        else:
            cmd, err, res = it
            e = _entry_tool(cmd, error=err)
            e[1]["message"]["content"][0]["content"] = res
            entries.extend(e)
    return _transcript(entries)


def _note(tmp, body=HANDOVER_BODY):
    Path(tmp, "note.html").write_text(body, encoding="utf-8")
    return os.path.join(tmp, "note.html")


class TestStopGateReview2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.note = _note(self.tmp)

    def v(self, tp, msg="✅ DONE: ok", verif_id=None):
        from gates import handover
        return handover.evaluate(tp, msg, self.tmp, verif_id=verif_id)[0]

    def test_a_succeeded_post_in_a_failed_call_still_owes(self):
        post = _post(1192, HANDOVER_BODY)
        move = "python3 scripts/odoo-task-sync.py move 1192 Verifikácia"
        self.assertEqual(self.v(_calls((post + " && " + move, True, POST_OK + "\nboom"))),
                         "block")
        self.assertEqual(self.v(_calls((post + "; gh issue view 99999", True, POST_OK))),
                         "block")
        acc = ACC % (TASK_URL % 644) + " && gh issue edit 8606 --add-label nope"
        self.assertEqual(self.v(_calls((acc, True, "https://github.com/o/r/issues/8606"
                                        "#issuecomment-1\nlabel not found"))), "block")

    def test_body_shapes_the_resolver_now_reads(self):
        base = "python3 scripts/odoo_post.py --model project.task --res-id 1192 "
        shapes = (
            "BODY=$(cat %s); %s--body \"$BODY\"" % (self.note, base),
            "BODY=\"$(cat %s)\"\n%s--body \"$BODY\"" % (self.note, base),
            "%s--body - < %s" % (base, self.note),
            "cd %s && %s--body \"$(cat note.html)\"" % (self.tmp, base),
            "/usr/bin/env python3 scripts/odoo_post.py --model project.task --res-id 1192 "
            "--body \"$(cat %s)\"" % self.note,
            "python3 - <<'EOF'\nfrom odoo_post import post\npost('project.task', 1192, "
            "open('%s').read(), signature='ZbynekAI 1')\nEOF" % self.note,
            "python3 scripts/odoo-task-sync.py post-message 1192 \"$(cat %s)\"" % self.note,
        )
        for cmd in shapes:
            with self.subTest(cmd=cmd[:50]):
                self.assertEqual(self.v(_calls(cmd)), "block")

    def test_python_api_with_a_handover_plan_settles(self):
        cmd = ("python3 - <<'EOF'\nfrom odoo_post import post, plan_handover\n"
               "plan = plan_handover(1192)\npost('project.task', 1192, open('%s').read(), "
               "handover_mode=plan)\nEOF" % self.note)
        self.assertEqual(self.v(_calls(cmd)), "allow")

    def test_a_body_file_deleted_later_in_the_turn_is_still_read(self):
        f = os.path.join(self.tmp, "gone.html")
        tp = _calls(("Write", {"file_path": f, "content": HANDOVER_BODY}),
                    "python3 scripts/odoo_post.py --model project.task --res-id 1192 "
                    "--body \"$(cat %s)\"" % f)
        self.assertEqual(self.v(tp), "block")

    def test_mentions_never_settle_a_debt(self):
        post = _post(1192, HANDOVER_BODY)
        for ev in ('echo "TODO: move 1192 to Verifikácia"',
                   "ls # move 1192 to Verifikácia",
                   "python3 -c \"print('move 1192 verif')\"",
                   "grep -n write notes_verif_1192.txt",
                   "python3 scripts/odoo-task-sync.py move-stage 1100 Verifikácia # 1192",
                   "python3 scripts/odoo-task-sync.py move-stage 1192 Realizácia # verif",
                   "python3 scripts/odoo-task-sync.py move-stage 1192 Verifikácia --help"):
            with self.subTest(ev=ev[:40]):
                self.assertEqual(self.v(_calls(post, ev)), "block")
        for tool in (("TodoWrite", {"todos": [{"content": "move 1192 to Verifikácia"}]}),
                     ("Agent", {"prompt": "write stage_id Verifikácia on task 1192"})):
            with self.subTest(tool=tool[0]):
                self.assertEqual(self.v(_calls(post, tool)), "block")

    def test_real_writes_settle(self):
        post = _post(1192, HANDOVER_BODY)
        upd = ("python3 - <<'EOF'\nclient.update('project.task', 1192, stage_id=2880)\nEOF")
        self.assertEqual(self.v(_calls(post, upd), verif_id=2880), "allow")
        mcp = ("mcp__odoo__update_record", {"model": "project.task", "id": 1192,
                                            "values": {"stage_id": 2880}})
        self.assertEqual(self.v(_calls(post, mcp), verif_id=2880), "allow")
        self.assertEqual(self.v(_calls(post, mcp), verif_id=5), "block")

    def test_acceptance_thread_must_be_a_line_and_not_the_airuleset_repo(self):
        lane = ('gh issue comment 1179 --body "LANE-RETURN: the gate blocks an '
                'Acceptance-thread: naming %s"' % (TASK_URL % 1192))
        self.assertEqual(self.v(_calls(lane)), "allow")
        own = ('gh issue comment 1179 -R zbynekdrlik/airuleset --body '
               '"Acceptance-thread: %s"' % (TASK_URL % 1192))
        self.assertEqual(self.v(_calls(own)), "allow")
        foreign = ('gh -R zbynekdrlik/odoo-erp issue comment 8606 --body '
                   '"Acceptance-thread: %s"' % (TASK_URL % 1192))
        self.assertEqual(self.v(_calls(foreign)), "block")

    def test_a_task_notification_does_not_reset_the_turn(self):
        entries = [_entry_user("owner: pošli")]
        entries.extend(_entry_tool(_post(1192, HANDOVER_BODY)))
        entries.append(_entry_user("<task-notification>\n<task-id>x</task-id>done"))
        self.assertEqual(self.v(_transcript(entries)), "block")

    def test_bypass_in_a_code_fence_does_not_count(self):
        tp = _calls(_post(1192, HANDOVER_BODY))
        msg = "```\nairuleset:handover-ok moved by hand earlier\n```\n✅ DONE"
        self.assertEqual(self.v(tp, msg=msg), "block")

    def test_bypass_is_audited(self):
        from unittest import mock

        from gates import audit, handover
        home = tempfile.mkdtemp()
        tp = _calls(_post(1192, HANDOVER_BODY))
        payload = json.dumps({"transcript_path": tp, "cwd": self.tmp,
                              "last_assistant_message": "airuleset:handover-ok moved "
                              "1192 in the previous turn\n✅ DONE"})
        with mock.patch.dict(os.environ, {"HOME": home}):
            handover.run(payload)
            log = audit.audit_log_path(handover.AUDIT_LOG)
            self.assertIn("airuleset:handover-ok moved 1192", Path(log).read_text())
        self.assertIn((handover.AUDIT_LOG, ()), audit.CLI_BYPASS_LOGS)


class TestStopGateReview2b(unittest.TestCase):
    """The review-2 probe corpus re-run after the fix: four shapes still slipped."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.note = _note(self.tmp)

    def test_remaining_body_shapes(self):
        from gates import handover
        base = "python3 scripts/odoo_post.py --model project.task --res-id 1192 "
        for cmd in ("cat %s | %s--body -" % (self.note, base),
                    "%s--body-file %s" % (base, self.note),
                    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['python3', "
                    "'scripts/odoo_post.py', '--model', 'project.task', '--res-id', '1192', "
                    "'--body', open('%s').read()])\nEOF" % self.note,
                    base + "--body 'Čo skúsiť: export. Stačí len 👍'"):
            with self.subTest(cmd=cmd[:40]):
                self.assertEqual(handover.evaluate(_calls(cmd), "ok", self.tmp)[0], "block")

    def test_shape_and_argv_parsing_stay_linear(self):
        from gates import handover
        t0 = time.monotonic()
        handover.is_handover_body("Čo skúsiť " + "stačí a" * 50000)
        handover._py_argv_calls("odoo_post.py' '--model', " * 20000)
        self.assertLess(time.monotonic() - t0, 3.0)


class TestTriggerReview2(unittest.TestCase):
    def test_every_posting_shape_loads_the_rule(self):
        tmp = tempfile.mkdtemp()
        for cmd in ("python3 scripts/odoo_post.py --model discuss.channel --res-id 31 "
                    "--body \"$(cat x.html)\"",
                    "python3 - <<'EOF'\nfrom odoo_post import post\nEOF",
                    "python3 scripts/odoo-task-sync.py post-message 1192 x",
                    "gh pr comment 5 --body 'Acceptance-thread: x'",
                    "gh api repos/o/r/issues/5/comments -f body='Acceptance-thread: x'"):
            with self.subTest(cmd=cmd[:40]):
                self.assertTrue(_inject("Bash", {"command": cmd}, tmp))


class TestSupersededReview2(unittest.TestCase):
    def test_already_correct_memories_are_not_flagged(self):
        for text in ("montalu: assignee = the addressee, never no assignee\n",
                     "do not leave montalu tasks with no assignee\n",
                     "montalu bez assignee NIKDY — #1166\n",
                     "no user_ids on miva boards (montalu: addressee)\n"):
            with self.subTest(text=text):
                home = tempfile.mkdtemp()
                d = os.path.join(home, ".clau" + "de", "projects", "-home-x-devel", "memory")
                os.makedirs(d)
                Path(d, "m.md").write_text(text, encoding="utf-8")
                self.assertEqual(da.scan_superseded_memory(home), [])


if __name__ == "__main__":
    unittest.main()
