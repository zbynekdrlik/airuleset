"""#1137 (issue 1233 folded in) — the slim `## ✅ Work Complete` report.

Owner (2026-10-26): "v princípe už ma zaujíma len čo bola úloha a ako sa
vyriešila". Only the report FORMAT shrinks; every verification gate behind it
stays. The new shape is four lines:

    ## ✅ Work Complete
    **Úloha:** <what was asked>
    **Riešenie:** <how it was solved>  🌐 <url if UI>
    ✅ Overené: plan N/N · review 0 🔴 0 🟡 0 🔵 · deep 0 🔴 0 🟡 0 🔵 ·
               arch <area> OK|REWORK fixed|REWORK #N · Výstup: <values> ·
               playbook: <lesson | none>
    <terminal marker>

`hooks/stop-check-prose-violations.sh` reads every gate from the ONE
`✅ Overené:` line (the LAST one, optional `- `/`> `/`**` prefix; a token it
carries is never re-read from the old forms), still accepts the pre-#1137
forms, owns the playbook proof (the separate `stop-check-playbook-review.sh`
is a no-op stub), keeps the hard "UI deploy with no 🌐 URL" check, and drops
the soft stderr-only checks plus the multi-env 🌐-count and regression-line
hard checks.

Hermetic: fresh session id per run (retry counter cleaned up) and a scratch
`HOME`, like `tests/test_prose_gate_output_line.py`.
"""

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

C3 = "0 \U0001f534 0 \U0001f7e1 0 \U0001f535"     # 0 🔴 0 🟡 0 🔵
GLOBE = "\U0001f310"

OVR_OK = ("✅ Overené: plan 4/4 · review %s · deep %s · arch dashboard OK · "
          "Výstup: pätička ukazuje „v1.4.2“ · playbook: none" % (C3, C3))


def slim(ovr=OVR_OK, uloha="**Úloha:** Dashboard má ukazovať nasadenú verziu.",
         riesenie=("**Riešenie:** Pätička číta verziu z buildu.  %s "
                   "https://dash.example.com/" % GLOBE),
         done="✅ DONE: verzia na dashboarde nasadená"):
    parts = ["## ✅ Work Complete", uloha, riesenie, ovr, done]
    return "\n".join(p for p in parts if p) + "\n"


# The pre-#1137 compact (#940) template — still accepted.
OLD = ("## ✅ Work Complete\n\n"
       "✅ /plan-check: 3/3 · /review: {c} · /requesting-code-review: {c}\n"
       "✅ Výstup: pätička dashboardu ukazuje „v1.4.2“\n"
       "🏛 Architektúra: dashboard — OK\n"
       "{g} https://dash.example.com/\n"
       "**Goal:** Dashboard má ukazovať verziu. — **What changed:** Pätička "
       "číta verziu z buildu.\n"
       "{pb}"
       "✅ DONE: verzia na dashboarde nasadená\n")


def old(playbook="📔 Playbook: nič nové\n", extra=""):
    return OLD.format(c=C3, g=GLOBE, pb=playbook + extra)


class _Hook(unittest.TestCase):

    def setUp(self):
        self._home = tempfile.TemporaryDirectory(prefix="airuleset-1137-home-")
        self.addCleanup(self._home.cleanup)
        self.env = {**os.environ, "HOME": self._home.name}

    def run_hook(self, msg):
        sid = "slim-%s" % uuid.uuid4().hex[:12]
        for pre in ("stop-block", "playbook-block"):
            p = Path("/tmp/airuleset-%s-%s" % (pre, sid))
            self.addCleanup(lambda p=p: p.unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, env=self.env, timeout=300)

    @staticmethod
    def blocked(r):
        return '"block"' in r.stdout

    def violations(self, r):
        if not self.blocked(r):
            return []
        reason = json.loads(r.stdout)["reason"].replace("\\n", "\n")
        return [ln.strip() for ln in reason.splitlines() if ln.startswith("- ")]

    def assert_clean(self, msg):
        r = self.run_hook(msg)
        self.assertFalse(self.blocked(r), "unexpected block: %r\nstderr=%s"
                         % (self.violations(r), r.stderr[-2000:]))
        return r

    def assert_only(self, msg, needle):
        r = self.run_hook(msg)
        v = self.violations(r)
        self.assertTrue(self.blocked(r), "expected a block naming %r" % needle)
        self.assertEqual(len(v), 1, "exactly one violation expected: %r" % v)
        self.assertIn(needle.lower(), v[0].lower(), v)
        return r


class SlimReportPasses(_Hook):

    def test_four_line_report_passes(self):
        self.assert_clean(slim())

    def test_rework_fixed_and_rework_ticket_accepted(self):
        for arch in ("arch dashboard REWORK fixed", "arch hooks REWORK #1240",
                     "arch hooks REWORK #1240 (prepis parsera)"):
            with self.subTest(arch=arch):
                self.assert_clean(slim(ovr=OVR_OK.replace("arch dashboard OK", arch)))

    def test_vystup_na_with_reason_accepted_without_url(self):
        ovr = OVR_OK.replace("Výstup: pätička ukazuje „v1.4.2“",
                             "Výstup: n/a — interná zmena hooku")
        # a non-UI change (no dashboard deploy words: the restored 🌐 check, R2)
        self.assert_clean(slim(ovr=ovr, uloha="**Úloha:** Hook má čítať jeden riadok.",
                               riesenie="**Riešenie:** Hook číta jeden riadok.",
                               done="✅ DONE: hook opravený"))

    def test_slash_tokens_accepted(self):
        self.assert_clean(slim(ovr=OVR_OK.replace("review", "/review")
                               .replace("deep", "/deep")))

    def test_line_prefixes_accepted(self):
        for ovr in ("- " + OVR_OK, "> " + OVR_OK,
                    OVR_OK.replace("✅ Overené:", "**✅ Overené:**")):
            with self.subTest(ovr=ovr[:16]):
                self.assert_clean(slim(ovr=ovr))

    def test_quoted_template_above_is_not_read(self):
        tmpl = ("Šablóna z hooku:\n    ✅ Overené: plan N/N · review %s · "
                "deep %s · arch <oblasť> OK · Výstup: <hodnoty> · "
                "playbook: <lekcia>\n" % (C3, C3))
        self.assert_clean(tmpl + slim())

    def test_playbook_lesson_accepted(self):
        self.assert_clean(slim(ovr=OVR_OK.replace(
            "playbook: none", "playbook: internals-hooks.md — Overené parser")))


class SlimReportGates(_Hook):

    def test_missing_vystup_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            " · Výstup: pätička ukazuje „v1.4.2“", "")), "Výstup")

    def test_value_free_vystup_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "pätička ukazuje „v1.4.2“", "funguje")), "value-free")

    def test_na_next_to_inline_url_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "pätička ukazuje „v1.4.2“", "n/a — interná zmena")), "n/a")

    def test_dishonest_review_counter_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "review %s" % C3, "review 1 \U0001f534 0 \U0001f7e1 0 \U0001f535")),
            "review")

    def test_dishonest_deep_counter_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "deep %s" % C3, "deep 0 \U0001f534 2 \U0001f7e1 0 \U0001f535")),
            "deep")

    def test_dishonest_review_cannot_borrow_deep_zeros(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "review %s" % C3, "/review 1 \U0001f534 0 \U0001f7e1 0 \U0001f535")),
            "review")

    def test_review_without_blue_counter_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "review %s" % C3, "review 0 \U0001f534 0 \U0001f7e1")), "review")

    def test_unequal_plan_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace("plan 4/4", "plan 2/5")), "plan")

    def test_arch_not_ok_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "arch dashboard OK", "arch hooks NOT OK")), "arch")

    def test_vystup_value_ends_at_next_separator(self):
        self.assert_only(slim(ovr=OVR_OK.replace(
            "pätička ukazuje „v1.4.2“", "funguje · CI 12/12 green")),
            "value-free")

    def test_last_overene_line_wins(self):
        bad = OVR_OK.replace("review %s" % C3,
                             "review 1 \U0001f534 0 \U0001f7e1 0 \U0001f535")
        self.assert_only(slim(ovr=OVR_OK + "\n" + bad), "review")

    def test_missing_plan_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace("plan 4/4 · ", "")), "plan")

    def test_missing_arch_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(" · arch dashboard OK", "")), "arch")

    def test_missing_playbook_blocks(self):
        self.assert_only(slim(ovr=OVR_OK.replace(" · playbook: none", "")), "playbook")

    def test_missing_uloha_blocks(self):
        self.assert_only(slim(uloha=""), "Úloha")

    def test_missing_riesenie_blocks(self):
        self.assert_only(slim(riesenie="%s https://dash.example.com/" % GLOBE),
                         "Riešenie")

    def test_localhost_inline_url_still_blocks(self):
        r = self.run_hook(slim(riesenie="**Riešenie:** Náhľad beží.  %s "
                                         "http://localhost:3000/" % GLOBE))
        self.assertTrue(any("localhost" in v for v in self.violations(r)),
                        self.violations(r))

    def test_bare_odoo_thread_id_still_blocks(self):
        r = self.run_hook(slim(riesenie="**Riešenie:** Odpoveď je v Discuss "
                                         "vlákne 288."))
        self.assertTrue(any("discuss.channel" in v for v in self.violations(r)),
                        self.violations(r))

    def test_missing_heading_shows_slim_template(self):
        msg = ("PR je zmergnutý, ready to merge.\n"
               "https://github.com/o/r/pull/7\n\n✅ DONE: hotovo")
        r = self.run_hook(msg)
        self.assertTrue(self.blocked(r))
        self.assertIn("✅ Overené:", r.stderr)
        self.assertNotIn("slovnormal-mcp PR #9", r.stderr)


class OldFormsStillAccepted(_Hook):

    def test_old_compact_template_passes(self):
        self.assert_clean(old())

    def test_old_lines_with_uloha_riesenie_pass(self):
        msg = old().replace("**Goal:** Dashboard má ukazovať verziu. — "
                            "**What changed:**", "**Úloha:** Dashboard má "
                            "ukazovať verziu.\n**Riešenie:**")
        self.assert_clean(msg)

    def test_overene_line_with_goal_what_changed_passes(self):
        self.assert_clean(slim(uloha="**Goal:** Dashboard má ukazovať verziu.",
                               riesenie="**What changed:** Pätička číta verziu.  "
                                        "%s https://dash.example.com/" % GLOBE))

    def test_plain_uloha_riesenie_in_question_not_a_report(self):
        msg = ("Úloha: zistiť prečo padá CI.\nRiešenie: dve možnosti.\n"
               "https://github.com/o/r/pull/7\n\n**Otázka — projekt x (y):** "
               "PR #7 (oprava CI) má dve cesty.\n- a (odporúčam)\n- b\n"
               "❓ NEEDS YOU: ktorú cestu?\n")
        r = self.run_hook(msg)
        self.assertFalse(any("Work Complete" in v or "Missing" in v
                             for v in self.violations(r)), self.violations(r))

    def test_old_template_without_playbook_blocks_here(self):
        # the playbook proof moved INTO this hook (same heading-only scope)
        self.assert_only(old(playbook=""), "playbook")


class RemovedChecksAreGone(_Hook):

    def test_bug_fix_report_needs_no_regression_line(self):
        self.assert_clean(old(extra="Fixes #42 (pád dashboardu) — bug fix.\n"))

    def test_multi_env_deploy_with_one_url_passes(self):
        self.assert_clean(old(extra="✅ Deploy: deployed to dev and prod, "
                                    "dashboard verified.\n"))

    def test_ui_deploy_without_url_blocks(self):
        # review R2 (owner #595): the single-env "UI deploy with no 🌐 URL"
        # hard check is restored; only the multi-env >=2 🌐 check stays gone.
        self.assert_only(old(extra="✅ Deploy: dashboard deployed successfully.\n")
                         .replace("%s https://dash.example.com/\n" % GLOBE, ""),
                         "🌐")

    def test_slim_ui_deploy_without_url_blocks(self):
        self.assert_only(slim(riesenie="**Riešenie:** Dashboard má novú stránku, "
                                       "nasadené na prod."), "🌐")

    def _no_stderr(self, msg, phrase):
        r = self.run_hook(msg)
        self.assertNotIn(phrase, r.stderr)

    def test_no_bare_ref_soft_warning(self):
        self._no_stderr("Pozrel som issue #42, nič netreba.\n\n✅ DONE: skontrolované",
                        "Bare issue/PR number")

    def test_no_trailing_question_soft_warning(self):
        self._no_stderr(old().replace("✅ DONE: verzia na dashboarde nasadená\n",
                                      "Sedí to?\n"), "ends with '?'")

    def test_no_follow_up_soft_warning(self):
        self._no_stderr(old(extra="Follow-up filed: #77\n"),
                        "You filed a follow-up issue")

    def test_no_blue_finding_soft_warning(self):
        self._no_stderr(old(extra="🔵 deferred to the next PR\n"),
                        "skipping or deferring")

    def test_no_phase_gated_soft_warning(self):
        self._no_stderr("Phase 2 remains gated on Phase 1 merge.\n\n✅ DONE: fáza 1",
                        "Phase N remains gated")

    def test_no_missing_pr_url_soft_warning(self):
        self._no_stderr("PR is ready to merge, all checks green.\n\n✅ DONE: PR hotový",
                        "without providing the PR URL")

    def test_no_backend_url_soft_warning(self):
        self._no_stderr(old(extra="✅ Deploy: deployed successfully.\n"
                                  "%s http://10.0.0.5:8000/api/\n" % GLOBE),
                        "backend/API URL")

    def test_no_goal_order_soft_warning(self):
        msg = old().replace("**Goal:** Dashboard má ukazovať verziu. — "
                            "**What changed:** Pätička číta verziu z buildu.\n", "")
        msg = msg.replace("## ✅ Work Complete\n\n", "## ✅ Work Complete\n\n"
                          "**Goal:** Dashboard má ukazovať verziu. — **What "
                          "changed:** Pätička číta verziu z buildu.\n")
        self._no_stderr(msg, "appears BEFORE the audit lines")


class PlaybookHookRetired(unittest.TestCase):

    def test_playbook_hook_is_a_silent_noop_stub(self):
        # review R4: a session started before 0.1.558 still calls this Stop
        # hook path; a missing file is exit 127 on every Stop.
        stub = ROOT / "hooks" / "stop-check-playbook-review.sh"
        self.assertTrue(stub.exists())
        msg = json.dumps({"last_assistant_message": "## ✅ Work Complete\nx",
                          "session_id": "stub-%s" % uuid.uuid4().hex[:8]})
        home = tempfile.TemporaryDirectory(prefix="airuleset-1137-stub-")
        self.addCleanup(home.cleanup)
        r = subprocess.run(["bash", str(stub)], input=msg, capture_output=True,
                           text=True, timeout=10,
                           env={**os.environ, "HOME": home.name})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        text = stub.read_text()
        self.assertIn("set -euo pipefail", text)
        self.assertIn("retired by #1137", text)
        self.assertNotIn("stop-check-playbook-review",
                         (ROOT / "settings" / "hooks.json").read_text())


if __name__ == "__main__":
    unittest.main()
