"""#1185 ROZHODNUTÉ (main, 29.9.2026, issuecomment-5894541407) — the close gate
must see the 29.9 sweep shape.

The lane's review proved the 29.9 closure sweep tickets (odoo-erp#6885,
#6830, #6577, #7371) were NOT thread-bound, so the thread-only gate never
engaged. The decision:
  1. BINDING widens: a ticket is acceptance-bound when it is thread-bound (as
     before) OR carries an Odoo task link (`/odoo/project/<pid>/tasks/<tid>`)
     OR has the `needs-acceptance` label (the hook now fetches `labels`).
  2. OWNER-RULING exit: an `Acceptance-cited:` line counts when the SAME line
     carries a `msg <id>` OR a GitHub `issuecomment-<digits>` reference (an
     owner ROZHODNUTÉ, the odoo-erp#3171 shape). A stage-only citation still
     blocks.
  3. `agents/autopilot-worker.md` states the new evidence shapes.

SECOND ROZHODNUTÉ (issuecomment-5894862095) refines it: binding = thread-bound
OR `needs-acceptance` (a task link alone no longer binds — internal tickets
mention tasks); a `--reason "not planned"` close is never checked; a recorded
call counts when the line names the recording (`meeting <meet-code>`); an owner
`issuecomment-<id>` is verified ONLINE by the hook (author must be the owner,
fetch failure or any other author blocks).

The fixtures below are modelled on the real sweep tickets (task link and/or
`needs-acceptance` + a stage-only prose citation); client data is omitted.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discuss_close_guard as g  # noqa: E402

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

MODULE = ROOT / "discuss_close_guard.py"
HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"
GATE_LIB = ROOT / "hooks" / "lib-discuss-close-gate.sh"


def _gate_src():
    """The hook source plus the Discuss/acceptance gate lib it sources (when
    split out), so a source lock follows the code wherever it lives."""
    parts = [HOOK.read_text(encoding="utf-8")]
    if GATE_LIB.exists():
        parts.append(GATE_LIB.read_text(encoding="utf-8"))
    return "\n".join(parts)
AGENT = ROOT / "agents" / "autopilot-worker.md"
MSGDIR = ROOT / "skills" / "odoo-client-messaging"
STAGES = MSGDIR / "client-board-stages.md"
COMPOSE = MSGDIR / "handover-compose.md"

TASK = "https://erp.montalu.cloud/odoo/project/1/tasks/855"
STAGE_CITE = "Acceptance-cited: Odoo úloha 855 je v Hotovo na tabuli klienta"
OWNER_URL = ("https://github.com/zbynekdrlik/odoo-erp/issues/3171"
             "#issuecomment-5874238532")


def _issue(body="", comments=(), labels=()):
    return json.dumps({
        "body": body,
        "comments": [{"body": c} for c in comments],
        "labels": [{"name": n} for n in labels],
    })


# The 29.9 sweep shapes. The six real ones carried `needs-acceptance` at close
# (odoo-erp#6518/#6577/#6821/#6830/#6885/#6943) and must block; odoo-erp#7173
# never had the label and #7371 lost it on 21.9., so under the second decision
# (a task link alone does not bind) those two are NOT bound.
SWEEP = {
    "odoo-erp#6885": _issue(body=f"Klientska úloha: {TASK}",
                            comments=[STAGE_CITE],
                            labels=("tenant:montalu", "needs-acceptance", "ops-wait")),
    "odoo-erp#6830": _issue(body=f"Bug. Úloha {TASK}",
                            comments=["Uzavreté (pokyn ownera 29.9.): úloha je v Hotovo.",
                                      STAGE_CITE],
                            labels=("bug", "needs-acceptance")),
    "odoo-erp#6577": _issue(body="Požiadavka klienta.",
                            comments=["Acceptance-cited: presunuté do Hotovo 12.9."],
                            labels=("needs-acceptance",)),
    "odoo-erp#6518": _issue(body=f"Úloha {TASK}", comments=[],
                            labels=("needs-acceptance",)),
    "odoo-erp#6821": _issue(body=f"Úloha {TASK}",
                            comments=["Acceptance-cited: task 855 v Hotovo"],
                            labels=("needs-acceptance", "ops-wait")),
    "odoo-erp#6943": _issue(body=f"Úloha {TASK}",
                            comments=["Acceptance-cited: Hotovo (stream 12.9.)"],
                            labels=("needs-acceptance",)),
}
LINK_ONLY = {
    "odoo-erp#7371": _issue(body=f"Cenník skla. {TASK}",
                            comments=["Acceptance-cited: task 855 v Hotovo"],
                            labels=("tenant:montalu", "ops-wait")),
    "odoo-erp#7173": _issue(body=f"Úloha {TASK}", comments=[],
                            labels=("enhancement",)),
}


class TestWidenedBinding(TestCase):
    def test_sweep_shapes_block(self):
        for name, payload in SWEEP.items():
            with self.subTest(ticket=name):
                self.assertIn(g.evaluate_close(payload),
                              ("acceptance-cited-without-msg",
                               "thread-bound-no-closing-note"))

    def test_task_link_alone_does_not_bind(self):
        for name, payload in LINK_ONLY.items():
            with self.subTest(ticket=name):
                self.assertIsNone(g.evaluate_close(payload))

    def test_task_link_without_label_is_not_checked(self):
        self.assertIsNone(g.evaluate_close(_issue(body=f"Úloha {TASK}")))

    def test_needs_acceptance_label_alone_binds(self):
        self.assertEqual(
            g.evaluate_close(_issue(body="plain", labels=("needs-acceptance",))),
            "thread-bound-no-closing-note")

    def test_is_acceptance_bound_reasons(self):
        self.assertFalse(g.is_acceptance_bound({"body": f"x {TASK}"}))
        self.assertTrue(g.is_acceptance_bound(
            {"body": "x", "labels": [{"name": "needs-acceptance"}]}))
        self.assertTrue(g.is_acceptance_bound({"body": "Discuss-thread: 257"}))
        self.assertFalse(g.is_acceptance_bound(
            {"body": "plain", "labels": [{"name": "bug"}]}))

    def test_odoo_links_do_not_bind(self):
        for body in (f"Úloha {TASK}", "https://erp.montalu.cloud/odoo/project/1",
                     "https://erp.montalu.cloud/odoo/sale.order/12",
                     "/odoo/project/1/tasks/ (no id)"):
            with self.subTest(body=body):
                self.assertIsNone(g.evaluate_close(_issue(body=body)))

    def test_malformed_labels_never_crash_and_fall_back_to_text(self):
        for labels in ("needs-acceptance", [None, 3, {"x": 1}], {"name": "x"}):
            with self.subTest(labels=labels):
                payload = json.dumps({"body": "plain", "comments": [],
                                      "labels": labels})
                self.assertIsNone(g.evaluate_close(payload))

    def test_unbound_ticket_stays_allowed(self):
        self.assertIsNone(g.evaluate_close(
            _issue(body="plain", comments=[STAGE_CITE], labels=("bug",))))


class TestEvidenceShapes(TestCase):
    def test_msg_citation_passes_on_a_label_bound_ticket(self):
        self.assertIsNone(g.evaluate_close(_issue(
            body=f"Úloha {TASK}", labels=("needs-acceptance",),
            comments=["Acceptance-cited: msg 1742799 task 855"])))

    def test_owner_ruling_issuecomment_needs_online_check(self):
        for line, ref in (
                (f"Acceptance-cited: owner ROZHODNUTÉ 29.9. {OWNER_URL}",
                 "zbynekdrlik/odoo-erp:5874238532"),
                ("Acceptance-cited: owner ruling issuecomment-5874238532",
                 ":5874238532"),
                ("Acceptance-cited: ROZHODNUTÉ (#issuecomment-5874238532)",
                 ":5874238532")):
            with self.subTest(line=line):
                payload = _issue(body=f"Úloha {TASK}", labels=("needs-acceptance",),
                                 comments=[line])
                self.assertEqual(g.evaluate_close(payload), "owner-check")
                self.assertEqual(g.owner_refs(payload), [ref])

    def test_msg_evidence_needs_no_online_check(self):
        payload = _issue(body="x", labels=("needs-acceptance",),
                         comments=["Acceptance-cited: owner issuecomment-1",
                                   "Acceptance-cited: msg 1742799"])
        self.assertIsNone(g.evaluate_close(payload))

    def test_meeting_citation_with_recording_id_passes(self):
        for line in ("Acceptance-cited: call výroba 29.9.2026, meeting zrc-vxqy-bqe [06:36–07:42]",
                     "Acceptance-cited: meeting `mdq-bvtq-aku` 12:30 Patrik: „sedí“",
                     "Acceptance-cited: Meeting: mdq-bvtq-aku"):
            with self.subTest(line=line):
                self.assertIsNone(g.evaluate_close(_issue(
                    body="x", labels=("needs-acceptance",), comments=[line])))

    def test_meeting_citation_without_recording_id_blocks(self):
        for line in ("Acceptance-cited: call výroba 29.9.2026 — Patrik: „plne funkčné“",
                     "Acceptance-cited: meeting 29.9.2026 s výrobou",
                     "Acceptance-cited: meeting <recording id>",
                     "Acceptance-cited: nahrávka zrc-vxqy-bqe (without the meeting word)"):
            with self.subTest(line=line):
                self.assertEqual(g.evaluate_close(_issue(
                    body="x", labels=("needs-acceptance",), comments=[line])),
                    "acceptance-cited-without-msg")

    def test_not_planned_needs_no_guard_change(self):
        # the not-planned exemption lives in the segmenter/hook (close reason is
        # a property of the close COMMAND, not of the ticket text)
        self.assertEqual(g.evaluate_close(SWEEP["odoo-erp#6885"]),
                         "acceptance-cited-without-msg")

    def test_owner_ruling_without_a_reference_still_blocks(self):
        for line in ("Acceptance-cited: owner ruling 2026-09-02 (webterm), "
                     "bez klientskej správy",
                     "Acceptance-cited: owner ROZHODNUTÉ issuecomment-",
                     "Acceptance-cited: issuecomment-<id>",
                     "Acceptance-cited: 5874238532 (CEO odpoveď)"):
            with self.subTest(line=line):
                self.assertEqual(g.evaluate_close(_issue(
                    body=f"Úloha {TASK}", labels=("needs-acceptance",),
                    comments=[line])),
                    "acceptance-cited-without-msg")

    def test_legacy_and_defer_unchanged_on_a_widened_binding(self):
        for line in ("Discuss-closed: msg 1731999", "Discuss-closed: tacit",
                     "Discuss-defer: siblings #1 still open",
                     "Acceptance-defer: siblings #1 still open"):
            with self.subTest(line=line):
                self.assertIsNone(g.evaluate_close(_issue(
                    body=f"Úloha {TASK}", labels=("needs-acceptance",),
                    comments=[STAGE_CITE, line])))


class TestCliAndHook(TestCase):
    def _cli(self, payload):
        return subprocess.run([sys.executable, str(MODULE)], input=payload,
                              capture_output=True, text=True).stdout.strip()

    def test_cli_sees_the_sweep_shape(self):
        self.assertEqual(self._cli(SWEEP["odoo-erp#6885"]), "BLOCK-CITED")
        self.assertEqual(self._cli(_issue(body="x", labels=("needs-acceptance",))), "BLOCK")
        self.assertEqual(
            self._cli(_issue(body="x", labels=("needs-acceptance",),
                             comments=[f"Acceptance-cited: owner {OWNER_URL}"])),
            "OWNER-CHECK zbynekdrlik zbynekdrlik/odoo-erp:5874238532")

    def test_owner_login_matches_the_maintainer_constant(self):
        import airuleset
        self.assertEqual(g.OWNER_LOGIN, airuleset.MAINTAINER_GH_LOGIN)

    def test_hook_fetches_labels(self):
        src = _gate_src()
        self.assertEqual(src.count("--json body,comments,labels"), 2)
        self.assertNotIn("--json body,comments ", src)

    def _hook(self, payload, cmd=None, gh_login=None, gh_fail=False):
        """Drive the REAL hook. A fake `gh` on PATH answers the owner-comment
        lookup: prints ``gh_login`` or exits 1 (``gh_fail``); it records every
        call so a test can assert the lookup happened (or did not)."""
        fd = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                         encoding="utf-8")
        fd.write(payload)
        fd.close()
        cwd = tempfile.mkdtemp()
        (Path(cwd) / "CLAUDE.md").write_text(
            "# p\n<!-- airuleset:authority=full -->\n")
        bindir = tempfile.mkdtemp()
        self.gh_log = Path(bindir) / "calls.log"
        fake = Path(bindir) / "gh"
        answer = ("  echo 'HTTP 404: Not Found' >&2; exit 1 ;;\n" if gh_fail
                  else f"  echo '{gh_login or ''}'; exit 0 ;;\n")
        fake.write_text(
            "#!/bin/bash\n"
            f'echo "$*" >> "{self.gh_log}"\n'
            'case "$*" in *issues/comments/*)\n'
            + answer
            + "esac\nexit 1\n")
        fake.chmod(0o755)
        env = hermetic_hook_env(self)
        env["AIRULESET_DISCUSS_CLOSE_FIXTURE"] = fd.name
        env["PATH"] = bindir + ":" + env.get("PATH", "")
        cmd = cmd or "gh issue close 6885 -R zbynekdrlik/odoo-erp --comment done"
        return subprocess.run(["bash", str(HOOK)],
                              input=json.dumps({"tool_input": {"command": cmd}}),
                              capture_output=True, text=True, cwd=cwd, env=env)

    def _calls(self):
        return self.gh_log.read_text() if self.gh_log.exists() else ""

    def _owner_issue(self):
        return _issue(body="x", labels=("needs-acceptance",),
                      comments=[f"Acceptance-cited: owner ROZHODNUTÉ {OWNER_URL}"])

    def test_hook_blocks_the_sweep_shapes(self):
        for name, payload in SWEEP.items():
            with self.subTest(ticket=name):
                r = self._hook(payload)
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_hook_cited_block_names_the_three_exits(self):
        r = self._hook(SWEEP["odoo-erp#6885"])
        self.assertEqual(r.returncode, 2, r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("issuecomment-<id>", err)
        self.assertIn("owner ROZHODNUTÉ", err)
        self.assertIn('"Acceptance-cited: meeting <recording id> [mm:ss]', err)

    def test_hook_plain_block_names_the_binding(self):
        r = self._hook(_issue(body="x", labels=("needs-acceptance",)))
        self.assertEqual(r.returncode, 2, r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("or, #1185, the needs-acceptance label", err)
        self.assertNotIn("/odoo/project/<pid>/tasks/<tid>", err)

    def test_hook_task_link_only_is_not_checked(self):
        r = self._hook(LINK_ONLY["odoo-erp#7371"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hook_not_planned_close_is_never_checked(self):
        for reason in ('--reason "not planned"', "-r 'not planned'",
                       '--reason="not planned"', "--reason not_planned"):
            with self.subTest(reason=reason):
                r = self._hook(SWEEP["odoo-erp#6885"],
                               cmd=f"gh issue close 6885 -R zbynekdrlik/odoo-erp {reason}")
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_hook_completed_reason_is_still_checked(self):
        r = self._hook(SWEEP["odoo-erp#6885"],
                       cmd="gh issue close 6885 -R zbynekdrlik/odoo-erp --reason completed")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_hook_not_planned_exempts_only_its_own_segment(self):
        cmd = ('gh issue close 6885 -R zbynekdrlik/odoo-erp --reason "not planned" && '
               "gh issue close 6830 -R zbynekdrlik/odoo-erp")
        r = self._hook(SWEEP["odoo-erp#6885"], cmd=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_hook_owner_comment_passes(self):
        r = self._hook(self._owner_issue(), gh_login="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("repos/zbynekdrlik/odoo-erp/issues/comments/5874238532",
                      self._calls())

    def test_hook_bare_issuecomment_uses_the_close_repo(self):
        r = self._hook(_issue(body="x", labels=("needs-acceptance",),
                              comments=["Acceptance-cited: owner issuecomment-5874238532"]),
                       gh_login="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("repos/zbynekdrlik/odoo-erp/issues/comments/5874238532",
                      self._calls())

    def test_hook_foreign_author_blocks_with_reason(self):
        r = self._hook(self._owner_issue(), gh_login="odoo-erp-stream-tokens[bot]")
        self.assertEqual(r.returncode, 2, r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("authored by odoo-erp-stream-tokens[bot], not zbynekdrlik", err)

    def test_hook_fetch_error_blocks_with_reason(self):
        r = self._hook(self._owner_issue(), gh_fail=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("could not be fetched", err)
        self.assertIn("airuleset:discuss-close-ok", err)

    def test_hook_bypass_skips_the_lookup(self):
        r = self._hook(self._owner_issue(), gh_fail=True,
                       cmd="gh issue close 6885 -R zbynekdrlik/odoo-erp "
                           "# airuleset:discuss-close-ok owner ruled on the phone")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hook_msg_evidence_skips_the_online_lookup(self):
        r = self._hook(_issue(body="x", labels=("needs-acceptance",),
                              comments=["Acceptance-cited: msg 1742799"]),
                       gh_fail=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("issues/comments", self._calls())

    def test_hook_uses_a_bounded_timeout(self):
        src = _gate_src()
        self.assertRegex(src, r'timeout \d+ gh api "repos/')


class TestDoctrine(TestCase):
    def test_agent_prompt_names_the_evidence_shapes(self):
        text = " ".join(AGENT.read_text(encoding="utf-8").split())
        self.assertIn(
            "`Acceptance-cited:` with the ACCEPTANCE evidence on that same line — "
            "`msg <id>` (the client's message/reaction, an owner/client stage "
            "move's tracking message, or the auto-close note), `meeting <recording "
            "id>` (a recorded call), or an owner ROZHODNUTÉ `issuecomment-<id>` "
            "(the hook verifies its author online); a stage a stream set is never "
            "acceptance (#1185)", text)
        self.assertIn("the `needs-acceptance` label also binds the ticket (a task "
                      "link alone does not); a `--reason \"not planned\"` close is "
                      "never checked", text)

    def test_stages_rule6_names_the_owner_exit(self):
        text = " ".join(STAGES.read_text(encoding="utf-8").split())
        self.assertIn("the close gate rejects an `Acceptance-cited:` with no `msg "
                      "<id>`, `meeting <recording id>` or owner `issuecomment-<id>` "
                      "(#1185)", text)

    def test_compose_close_line_names_the_owner_exit(self):
        text = " ".join(COMPOSE.read_text(encoding="utf-8").split())
        self.assertIn("`Acceptance-cited:` bez `msg <id>`/`meeting <id>`/"
                      "`issuecomment-<id>` BLOKUJE", text)


if __name__ == "__main__":
    main()
