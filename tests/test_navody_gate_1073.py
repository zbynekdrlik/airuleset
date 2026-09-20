"""#1073 — gates.navody: per-tenant client-guide (Návody) fact + live-link check
+ same-PR guide-maintenance preflight (owner ruling 18.9.2026, montalu1:
„návody buduj a udržiavaj a nech to je airuleset pravidlo").

Approach 1 (the decided design): the #1042 word-pattern intro-link block is
REPLACED by a per-tenant `navody_url` fact + a LIVE-link verification, and the
`airuleset.py handoff` composer preflight gains a same-PR guide-maintenance
gate. This suite locks:

1. FACT READER — `gates.navody.tenant_guide(cwd, stream) -> (url|None, ticket|None)`
   reads a `navody_url: <https://…>` / `navody_url: NONE — <#N>` line from
   `<repo>/.claude/streams/<stream>.md`; unreadable/missing/malformed → the
   distinct `(None, None)` unknown state.
2. STOP CHECK — `gates.navody.evaluate_stop(url, ticket, message, curl=…)`:
   a live 200 guide link passes, a dead link blocks, a `NONE — #N` fact requires
   `Návody: pripravujeme, #N` (not a link), an unknown fact FAILS CLOSED with the
   fix named. A fabricated link is impossible by construction.
3. PREFLIGHT — `gates.navody.guide_maintenance(changed_paths, body, surfaces=…)`:
   a client-visible surface change without a `docs/<tenant>/navody-*.html` change
   and without a `Navody: n/a — <why>` line REFUSES; either satisfies it; a
   non-surface change passes.
4. HOOK end-to-end — the #1042 block in `stop-check-prose-violations.sh` now
   calls the gate (unknown-fact / NONE-fact / bypass paths, no network).
5. SKILL text lock — the guide rule + the `ir.attachment` prohibition are in
   `skills/odoo-client-messaging/handover-compose.md`.

RED-before-GREEN: against the base tree `gates.navody` does not exist (import
error), the hook still word-checks, the preflight has no maintenance gate, and
the skill has no guide rule.
"""
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import uuid
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hook_state_cleanup import sweep_session_files  # noqa: E402

from gates import navody  # noqa: E402

HOOK = REPO / "hooks" / "stop-check-prose-violations.sh"
COMPOSE = REPO / "skills" / "odoo-client-messaging" / "handover-compose.md"


def _current_user():
    return pwd.getpwuid(os.getuid()).pw_name


# --------------------------------------------------------------------------- #
# 1. FACT READER
# --------------------------------------------------------------------------- #
class TestFactReader(unittest.TestCase):
    def _stream_dir(self, stream, body):
        d = tempfile.mkdtemp()
        sd = os.path.join(d, ".claude", "streams")
        os.makedirs(sd)
        with open(os.path.join(sd, "%s.md" % stream), "w", encoding="utf-8") as fh:
            fh.write(body)
        return d

    def test_url_fact(self):
        d = self._stream_dir("montalu1",
                             "navody_url: https://erp.montalu.cloud/p/tok/navody-money.html\n")
        url, ticket = navody.tenant_guide(d, "montalu1")
        self.assertEqual(url, "https://erp.montalu.cloud/p/tok/navody-money.html")
        self.assertIsNone(ticket)

    def test_none_plus_ticket_fact(self):
        d = self._stream_dir("montalu1", "navody_url: NONE — #7560\n")
        url, ticket = navody.tenant_guide(d, "montalu1")
        self.assertIsNone(url)
        self.assertEqual(ticket, "#7560")

    def test_none_ascii_dash(self):
        d = self._stream_dir("montalu1", "navody_url: NONE - #7560\n")
        url, ticket = navody.tenant_guide(d, "montalu1")
        self.assertEqual(ticket, "#7560")

    def test_missing_file_is_unknown(self):
        d = tempfile.mkdtemp()
        url, ticket = navody.tenant_guide(d, "montalu1")
        self.assertIsNone(url)
        self.assertIsNone(ticket)

    def test_missing_fact_line_is_unknown(self):
        d = self._stream_dir("montalu1", "# stream notes\nsome_other: value\n")
        url, ticket = navody.tenant_guide(d, "montalu1")
        self.assertIsNone(url)
        self.assertIsNone(ticket)

    def test_malformed_value_is_unknown(self):
        d = self._stream_dir("montalu1", "navody_url: maybe-later\n")
        url, ticket = navody.tenant_guide(d, "montalu1")
        self.assertIsNone(url)
        self.assertIsNone(ticket)

    def test_no_stream_is_unknown(self):
        url, ticket = navody.tenant_guide("/tmp", None)
        self.assertIsNone(url)
        self.assertIsNone(ticket)

    def test_surfaces_override_read(self):
        d = self._stream_dir("montalu1",
                             "navody_url: https://x/navody-a.html\n"
                             "navody_surfaces: views/, kiosk/, reports/\n")
        surf = navody.tenant_surfaces(d, "montalu1")
        self.assertIn("reports/", surf)

    def test_surfaces_default_when_absent(self):
        d = self._stream_dir("montalu1", "navody_url: https://x/navody-a.html\n")
        self.assertIsNone(navody.tenant_surfaces(d, "montalu1"))


# --------------------------------------------------------------------------- #
# 2. STOP CHECK
# --------------------------------------------------------------------------- #
def _curl_ok(url):
    return 200


def _curl_dead(url):
    return 404


class TestStopEvaluate(unittest.TestCase):
    URL = "https://erp.montalu.cloud/p/tok/navody-money.html"

    def test_unknown_fact_blocks_with_fix(self):
        v, reason = navody.evaluate_stop(None, None, "…nejaká akceptačná správa…")
        self.assertEqual(v, "block")
        self.assertIn("navody_url", reason)

    def test_none_fact_requires_ticket_line(self):
        msg = "Odovzdávam klientovi. Návody: pripravujeme, #7560. ZbynekAI 1"
        v, _ = navody.evaluate_stop(None, "#7560", msg)
        self.assertEqual(v, "allow")

    def test_none_fact_ascii_variant_passes(self):
        msg = "Odovzdávam. Navody: pripravujeme, #7560."
        v, _ = navody.evaluate_stop(None, "#7560", msg)
        self.assertEqual(v, "allow")

    def test_none_fact_without_ticket_line_blocks(self):
        # a message with a link but NOT the pripravujeme line -> block
        msg = "Odovzdávam klientovi, pozri https://erp.montalu.cloud/p/tok/navody-money.html"
        v, reason = navody.evaluate_stop(None, "#7560", msg)
        self.assertEqual(v, "block")
        self.assertIn("#7560", reason)

    def test_url_fact_live_link_passes(self):
        msg = "Správa odkazuje na návod: %s#kiosk" % self.URL
        v, _ = navody.evaluate_stop(self.URL, None, msg, curl=_curl_ok)
        self.assertEqual(v, "allow")

    def test_url_fact_dead_link_blocks(self):
        msg = "Správa odkazuje na návod: %s#kiosk" % self.URL
        v, reason = navody.evaluate_stop(self.URL, None, msg, curl=_curl_dead)
        self.assertEqual(v, "block")

    def test_url_fact_no_guide_link_blocks(self):
        # the message links a record but NOT the tenant guide
        msg = "Pozri https://erp.montalu.cloud/odoo/project/4/tasks/503"
        v, reason = navody.evaluate_stop(self.URL, None, msg, curl=_curl_ok)
        self.assertEqual(v, "block")

    def test_url_fact_prefix_not_substring(self):
        # a sibling token that merely shares a prefix must NOT count as the guide
        other = "https://erp.montalu.cloud/p/tokEVIL/navody-money.html"
        base = "https://erp.montalu.cloud/p/tok"
        msg = "Pozri %s" % other
        v, _ = navody.evaluate_stop(base, None, msg, curl=_curl_ok)
        self.assertEqual(v, "block")

    def test_repo_static_missing_file_blocks(self):
        d = tempfile.mkdtemp()  # no docs/ tree
        msg = "Správa odkazuje na návod: %s" % self.URL
        v, reason = navody.evaluate_stop(self.URL, None, msg,
                                         curl=_curl_ok, repo_root=d)
        self.assertEqual(v, "block")

    def test_repo_static_present_file_passes(self):
        d = tempfile.mkdtemp()
        docs = os.path.join(d, "docs", "montalu1")
        os.makedirs(docs)
        with open(os.path.join(docs, "navody-money.html"), "w") as fh:
            fh.write("<html></html>")
        msg = "Správa odkazuje na návod: %s" % self.URL
        v, _ = navody.evaluate_stop(self.URL, None, msg,
                                    curl=_curl_ok, repo_root=d)
        self.assertEqual(v, "allow")


# --------------------------------------------------------------------------- #
# 3. PREFLIGHT — same-PR guide-maintenance gate
# --------------------------------------------------------------------------- #
class TestPreflightMaintenance(unittest.TestCase):
    def test_surface_change_without_guide_or_na_refuses(self):
        ok, reason = navody.guide_maintenance(
            ["addons/montalu/views/attendance_views.xml", "addons/montalu/models/x.py"],
            body="READY-FOR-REVIEW: done.")
        self.assertFalse(ok)
        self.assertIn("navody", reason.lower())

    def test_surface_change_with_guide_file_passes(self):
        ok, _ = navody.guide_maintenance(
            ["addons/montalu/views/attendance_views.xml",
             "docs/montalu1/navody-dochadzka.html"],
            body="READY-FOR-REVIEW: done.")
        self.assertTrue(ok)

    def test_surface_change_with_navody_na_passes(self):
        ok, _ = navody.guide_maintenance(
            ["addons/montalu/kiosk/kiosk.xml"],
            body="READY-FOR-REVIEW: done.\nNavody: n/a — backend-only render fix")
        self.assertTrue(ok)

    def test_navody_na_needs_a_reason(self):
        ok, _ = navody.guide_maintenance(
            ["addons/montalu/kiosk/kiosk.xml"],
            body="READY-FOR-REVIEW: done.\nNavody: n/a")
        self.assertFalse(ok)

    def test_non_surface_change_passes(self):
        ok, _ = navody.guide_maintenance(
            ["addons/montalu/models/x.py", "airuleset.py"],
            body="READY-FOR-REVIEW: done.")
        self.assertTrue(ok)

    def test_static_src_surface_detected(self):
        ok, reason = navody.guide_maintenance(
            ["addons/montalu/static/src/js/widget.js"],
            body="RFR")
        self.assertFalse(ok)

    def test_surfaces_override_adds_surface(self):
        ok, _ = navody.guide_maintenance(
            ["addons/montalu/reports/report.xml"],
            body="RFR", surfaces=["views/", "reports/"])
        self.assertFalse(ok)

    def test_empty_diff_passes(self):
        ok, _ = navody.guide_maintenance([], body="RFR")
        self.assertTrue(ok)


# --------------------------------------------------------------------------- #
# 4. HOOK end-to-end (through stop-check-prose-violations.sh, no network)
# --------------------------------------------------------------------------- #
ACCEPT_NO_LINK = (
    "**Otázka — projekt odoo-erp (montalu, dochádzka):** Funkcia dochádzka je "
    "hotová a nasadená na PROD. Chcem ju odovzdať klientovi akceptačnou správou "
    "do vlákna „Dochádzka 1\" — "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288 "
    "a označiť Odoo task https://erp.montalu.cloud/odoo/project/4/tasks/503 ako "
    "needs-acceptance.\n"
    "> Ahoj Pavol, dochádzka je nasadená na vašom systéme.\n>\n> ZbynekAI 1\n"
    "❓ NEEDS YOU: schváliš akceptačnú správu klientovi?"
)


class TestHookEndToEnd(unittest.TestCase):
    def _run(self, msg, cwd):
        sid = "navody1073-%s" % uuid.uuid4().hex[:10]
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": cwd})
        p = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, timeout=300)
        sweep_session_files(sid)
        return p

    def _blocked(self, p):
        return '"decision"' in p.stdout and '"block"' in p.stdout

    def _stream_cwd(self, fact_line):
        d = tempfile.mkdtemp()
        sd = os.path.join(d, ".claude", "streams")
        os.makedirs(sd)
        with open(os.path.join(sd, "%s.md" % _current_user()), "w",
                  encoding="utf-8") as fh:
            fh.write(fact_line + "\n")
        return d

    def test_unknown_fact_acceptance_is_blocked(self):
        # a client-acceptance hand-off with no per-tenant guide fact -> fail
        # closed (the #1073 core: a fabricated link is impossible by construction)
        d = tempfile.mkdtemp()  # no stream file -> unknown
        self.assertTrue(self._blocked(self._run(ACCEPT_NO_LINK, d)))

    def test_none_fact_with_ticket_line_is_allowed(self):
        d = self._stream_cwd("navody_url: NONE — #7560")
        msg = ACCEPT_NO_LINK.replace(
            "> Ahoj Pavol, dochádzka je nasadená na vašom systéme.",
            "> Ahoj Pavol, dochádzka je nasadená. Návody: pripravujeme, #7560.")
        self.assertFalse(self._blocked(self._run(msg, d)))

    def test_none_fact_without_ticket_line_is_blocked(self):
        d = self._stream_cwd("navody_url: NONE — #7560")
        self.assertTrue(self._blocked(self._run(ACCEPT_NO_LINK, d)))

    def test_bypass_is_allowed(self):
        d = tempfile.mkdtemp()
        msg = ACCEPT_NO_LINK + "\n# airuleset:intro-link-ok API-only feature"
        self.assertFalse(self._blocked(self._run(msg, d)))

    def test_non_acceptance_question_not_blocked(self):
        d = tempfile.mkdtemp()
        msg = ("**Otázka — projekt camera-box:** Mám dve možnosti pre HDMI.\n"
               "- (1) DRM master (odporúčam)\n- (2) v4l2\n"
               "❓ NEEDS YOU: ktorú cestu?")
        self.assertFalse(self._blocked(self._run(msg, d)))


# --------------------------------------------------------------------------- #
# 5. SKILL text lock
# --------------------------------------------------------------------------- #
class TestSkillText(unittest.TestCase):
    def setUp(self):
        self.raw = COMPOSE.read_text(encoding="utf-8")
        self.low = re.sub(r"\s+", " ", self.raw.lower())

    def test_guide_repo_path_present(self):
        self.assertIn("docs/<tenant>/navody-", self.raw)

    def test_ir_attachment_prohibition_present(self):
        self.assertIn("ir.attachment", self.low)
        self.assertIn("csp", self.low)

    def test_no_guide_yet_ticket_rule_present(self):
        # no guide yet -> guide ticket reference, never a fabricated link
        self.assertIn("navody_url", self.raw.lower())

    def test_live_section_deeplink_rule_present(self):
        self.assertIn("deep-link", self.low)

    def test_body_under_injector_soft_cap(self):
        # the injector's per-BODY measure: MAX_BODY(24000) - 300 = 23700
        self.assertLessEqual(len(self.raw.strip()), 23700)


if __name__ == "__main__":
    unittest.main()
