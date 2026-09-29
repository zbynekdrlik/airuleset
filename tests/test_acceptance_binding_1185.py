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


# The four 29.9 sweep shapes: task link + needs-acceptance (6885/6830/6577),
# task link without the label (7371), each with a stage-only citation.
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
    "odoo-erp#7371": _issue(body=f"Cenník skla. {TASK}",
                            comments=["Acceptance-cited: task 855 v Hotovo"],
                            labels=("tenant:montalu", "ops-wait")),
}


class TestWidenedBinding(TestCase):
    def test_sweep_shapes_block(self):
        for name, payload in SWEEP.items():
            with self.subTest(ticket=name):
                self.assertEqual(g.evaluate_close(payload),
                                 "acceptance-cited-without-msg")

    def test_task_link_without_any_disposition_blocks(self):
        self.assertEqual(g.evaluate_close(_issue(body=f"Úloha {TASK}")),
                         "thread-bound-no-closing-note")

    def test_needs_acceptance_label_alone_binds(self):
        self.assertEqual(
            g.evaluate_close(_issue(body="plain", labels=("needs-acceptance",))),
            "thread-bound-no-closing-note")

    def test_is_acceptance_bound_reasons(self):
        self.assertTrue(g.is_acceptance_bound({"body": f"x {TASK}"}))
        self.assertTrue(g.is_acceptance_bound(
            {"body": "x", "labels": [{"name": "needs-acceptance"}]}))
        self.assertTrue(g.is_acceptance_bound({"body": "Discuss-thread: 257"}))
        self.assertFalse(g.is_acceptance_bound(
            {"body": "plain", "labels": [{"name": "bug"}]}))

    def test_other_odoo_links_do_not_bind(self):
        for body in ("https://erp.montalu.cloud/odoo/project/1",
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
    def test_msg_citation_passes_on_a_task_bound_ticket(self):
        self.assertIsNone(g.evaluate_close(_issue(
            body=f"Úloha {TASK}", labels=("needs-acceptance",),
            comments=["Acceptance-cited: msg 1742799 task 855"])))

    def test_owner_ruling_issuecomment_passes(self):
        for line in (f"Acceptance-cited: owner ROZHODNUTÉ 29.9. {OWNER_URL}",
                     "Acceptance-cited: owner ruling issuecomment-5874238532",
                     "Acceptance-cited: ROZHODNUTÉ (#issuecomment-5874238532)"):
            with self.subTest(line=line):
                self.assertIsNone(g.evaluate_close(_issue(
                    body=f"Úloha {TASK}", labels=("needs-acceptance",),
                    comments=[line])))

    def test_owner_ruling_without_a_reference_still_blocks(self):
        for line in ("Acceptance-cited: owner ruling 2026-09-02 (webterm), "
                     "bez klientskej správy",
                     "Acceptance-cited: owner ROZHODNUTÉ issuecomment-",
                     "Acceptance-cited: issuecomment-<id>",
                     "Acceptance-cited: 5874238532 (CEO odpoveď)"):
            with self.subTest(line=line):
                self.assertEqual(g.evaluate_close(_issue(
                    body=f"Úloha {TASK}", comments=[line])),
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
        self.assertEqual(self._cli(_issue(body=f"Úloha {TASK}")), "BLOCK")

    def test_hook_fetches_labels(self):
        src = HOOK.read_text(encoding="utf-8")
        self.assertEqual(src.count("--json body,comments,labels"), 2)
        self.assertNotIn("--json body,comments ", src)

    def _hook(self, payload):
        fd = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                         encoding="utf-8")
        fd.write(payload)
        fd.close()
        cwd = tempfile.mkdtemp()
        (Path(cwd) / "CLAUDE.md").write_text(
            "# p\n<!-- airuleset:authority=full -->\n")
        env = hermetic_hook_env(self)
        env["AIRULESET_DISCUSS_CLOSE_FIXTURE"] = fd.name
        cmd = "gh issue close 6885 -R zbynekdrlik/odoo-erp --comment done"
        return subprocess.run(["bash", str(HOOK)],
                              input=json.dumps({"tool_input": {"command": cmd}}),
                              capture_output=True, text=True, cwd=cwd, env=env)

    def test_hook_blocks_the_sweep_shape_and_names_the_owner_exit(self):
        r = self._hook(SWEEP["odoo-erp#6885"])
        self.assertEqual(r.returncode, 2, r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("issuecomment-<id>", err)
        self.assertIn("owner ROZHODNUTÉ", err)

    def test_hook_plain_block_names_the_widened_binding(self):
        r = self._hook(_issue(body=f"Úloha {TASK}"))
        self.assertEqual(r.returncode, 2, r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("an Odoo task link (/odoo/project/<pid>/tasks/<tid>)", err)
        self.assertIn("the needs-acceptance label", err)

    def test_hook_allows_the_owner_ruling_citation(self):
        r = self._hook(_issue(body=f"Úloha {TASK}", labels=("needs-acceptance",),
                              comments=[f"Acceptance-cited: owner ROZHODNUTÉ {OWNER_URL}"]))
        self.assertEqual(r.returncode, 0, r.stderr)


class TestDoctrine(TestCase):
    def test_agent_prompt_names_the_evidence_shapes(self):
        text = " ".join(AGENT.read_text(encoding="utf-8").split())
        self.assertIn(
            "`Acceptance-cited:` with the ACCEPTANCE evidence on that same line — "
            "`msg <id>` (the client's message/reaction, an owner/client stage "
            "move's tracking message, or the auto-close note) or an owner "
            "ROZHODNUTÉ `issuecomment-<id>`; a stage a stream set is never "
            "acceptance (#1185)", text)
        self.assertIn("an Odoo task link or the `needs-acceptance` label also "
                      "binds the ticket", text)

    def test_stages_rule6_names_the_owner_exit(self):
        text = " ".join(STAGES.read_text(encoding="utf-8").split())
        self.assertIn("the close gate rejects an `Acceptance-cited:` with neither "
                      "`msg <id>` nor an owner-ruling `issuecomment-<id>` (#1185)",
                      text)

    def test_compose_close_line_names_the_owner_exit(self):
        text = " ".join(COMPOSE.read_text(encoding="utf-8").split())
        self.assertIn("`Acceptance-cited:` bez `msg <id>`/`issuecomment-<id>` "
                      "BLOKUJE", text)


if __name__ == "__main__":
    main()
