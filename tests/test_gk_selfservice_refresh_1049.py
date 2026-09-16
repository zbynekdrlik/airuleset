"""Behaviour + unit tests for the #1049 self-service refresh-newer gate.

Hardens hooks/block-gk-request-without-selfservice.sh (#516) so a gk ACTION
request that reads as a self-serviceable PROD READ can no longer PASS on a
merely-PRESENT `Self-service-checked:` line: the line must now cite a
`refresh <run-id|comment-id> at <ISO-UTC>` NEWER than the newest event
timestamp in the request. The owner's three-day escalation (odoo-erp #1967,
"Preco ti to musim nonstop opakovat, ze mas kopiu produ?!") is the origin.

The gate FAILS OPEN: it only engages for a request positively classified as a
self-serviceable pure read (a read-verb + PROD, NOT a live intervention, NOT a
whitelisted gk-only surface the refresh rsync excludes). Everything else — the
whole existing #516 suite — stays exactly as it was.

Authority is simulated the #839-unspoofable way: a `<!-- airuleset:authority=
fork-no-merge -->` marker in the hook's cwd (resolve_authority honors it first),
exactly like test_gk_selfservice_gate.py.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "block-gk-request-without-selfservice.sh"

sys.path.insert(0, str(REPO_ROOT))
import airuleset  # noqa: E402


def run(cmd, user="david2", cwd=None, home=None):
    """Invoke the hook with a PreToolUse(Bash) stdin payload from a reduced
    sub-dev stream cwd. Returns the CompletedProcess (exit 0 = allowed, 2 =
    blocked). `home` lets a test read the Prevencia log it writes."""
    payload = json.dumps({"tool_input": {"command": cmd},
                          "session_id": "test-selfservice-refresh"})
    env = dict(os.environ)
    env["HOME"] = home or tempfile.mkdtemp(prefix="airuleset-ssr-home-")
    profile = airuleset.AUTHORITY_BY_USER.get(user) if user else None
    run_cwd = cwd
    if profile is not None:
        if run_cwd is None:
            run_cwd = tempfile.mkdtemp(prefix="airuleset-ssr-cwd-")
        Path(run_cwd, "CLAUDE.md").write_text(
            "<!-- airuleset:authority=%s -->\n" % profile, encoding="utf-8")
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        env=env, cwd=run_cwd or str(REPO_ROOT),
    )


def _read_log(home):
    p = Path(home, ".claude", "selfservice-gate.log")
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# --------------------------------------------------------------------------- #
# The new teeth — a self-serviceable PROD read must cite a fresh refresh.
# --------------------------------------------------------------------------- #
class ProdReadRefreshGate(TestCase):
    def test_prod_read_line_but_no_refresh_blocks(self):
        # RED against the current hook (returns 0 on line-present).
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read device_log on PROD for the 2026-09-14 10:00 '
                'event. Self-service-checked: the copy predates the event so I '
                'need gk to read it for me."')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("REFRESH-DEV-BOX-FROM-PROD", r.stderr)

    def test_prod_read_refresh_older_than_event_blocks(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "list config_parameter on PROD for the 2026-09-14 '
                '10:00 incident. Self-service-checked: refresh run-123 at '
                '2026-09-13T08:00:00Z from a fresh copy; nothing live needed."')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_prod_read_refresh_newer_than_event_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read config_parameter on PROD for the 2026-09-14 '
                '10:00 incident. Self-service-checked: refresh run-456 at '
                '2026-09-16T09:00:00Z from a fresh copy; already read it, '
                'this is the residual."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_prod_read_refresh_cited_no_event_passes(self):
        # a refresh citation is present and no event timestamp is mentioned:
        # nothing to predate -> PASS.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read the session count on PROD. Self-service-checked: '
                'refresh run-77 at 2026-09-16T09:00:00Z from a fresh copy."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_prod_read_no_line_still_blocks_existing(self):
        # existing #516 behaviour preserved: a PROD read with no line at all.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read device_log on PROD for the 2026-09-14 event"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Self-service-checked", r.stderr)


# --------------------------------------------------------------------------- #
# Whitelist — the genuinely gk-only surfaces the refresh rsync EXCLUDES pass on
# their own words (no refresh citation required).
# --------------------------------------------------------------------------- #
class WhitelistGkOnlySurface(TestCase):
    def test_session_store_surface_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read /var/lib/odoo/sessions/ on PROD for the '
                '2026-09-14 10:00 event. Self-service-checked: the session '
                'store is excluded from the refresh rsync; live gk read needed."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_container_logs_surface_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "grep the odoo container logs on PROD for the '
                '2026-09-14 10:00 error. Self-service-checked: container logs '
                'are not in the pg_dump/filestore refresh copy."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_secrets_surface_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read ~/.secrets on the PROD host for the 2026-09-14 '
                'config. Self-service-checked: root-only file, excluded from the '
                'refresh copy."')
        self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# Fail-open — a genuine LIVE intervention that merely mentions a read is not a
# self-serviceable pure read.
# --------------------------------------------------------------------------- #
class LiveInterventionFailOpen(TestCase):
    def test_restart_request_mentioning_read_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read the stuck count then restart the outgoing '
                'queue on PROD for the 2026-09-14 10:00 stall. '
                'Self-service-checked: read 40 stuck from the fresh copy; the '
                'live intervention I need is a restart of the sender."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_install_request_mentioning_read_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "check jq on PROD then install it. '
                'Self-service-checked: verified on the fresh copy jq is missing; '
                'the live intervention is installing jq into RUNTIME_DEPS."')
        self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# Timestamp parser robustness — a version number is NOT a date/time.
# --------------------------------------------------------------------------- #
class TimestampParserRobustness(TestCase):
    def test_version_number_not_read_as_event(self):
        # the only real event is 2026-09-14 10:00; 0.1.319/0.1.320/v1.2.3 must
        # not be parsed as newer timestamps that would make the refresh predate.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read config_parameter on PROD after the 2026-09-14 '
                '10:00 change (bump 0.1.319 -> 0.1.320, tag v1.2.3). '
                'Self-service-checked: refresh run-9 at 2026-09-16T09:00:00Z '
                'from a fresh copy."')
        self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# Prevencia log — a block records stream + reason-code + ticket.
# --------------------------------------------------------------------------- #
class PrevenciaLog(TestCase):
    def test_block_writes_reason_stream_ticket(self):
        home = tempfile.mkdtemp(prefix="airuleset-ssr-log-")
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 42 '
                '--comment "read device_log on PROD for the 2026-09-14 10:00 '
                'event. Self-service-checked: the copy predates the event."',
                home=home)
        self.assertEqual(r.returncode, 2, r.stderr)
        log = _read_log(home)
        self.assertIn("verdict=BLOCK", log)
        self.assertIn("reason=missing-refresh", log)
        self.assertIn("ticket=42", log)
        self.assertIn("stream=", log)


# --------------------------------------------------------------------------- #
# Bypass still honoured.
# --------------------------------------------------------------------------- #
class BypassStillWorks(TestCase):
    def test_bypass_marker_on_prod_read(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read device_log on PROD for the 2026-09-14 event. '
                'Self-service-checked: gk-only path."  '
                '# airuleset:selfservice-ok genuine gk-only surface')
        self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# Pure-function unit tests over the classifier (precise teeth, no subprocess).
# --------------------------------------------------------------------------- #
class ClassifierUnit(TestCase):
    def setUp(self):
        from gates import selfservice
        self.ss = selfservice

    def test_is_prod_read_positive(self):
        self.assertTrue(self.ss.is_self_serviceable_prod_read(
            "read device_log on PROD for the 2026-09-14 event"))

    def test_is_prod_read_negative_no_prod(self):
        self.assertFalse(self.ss.is_self_serviceable_prod_read(
            "read the local logs for the failing test"))

    def test_is_prod_read_negative_live_intervention(self):
        self.assertFalse(self.ss.is_self_serviceable_prod_read(
            "read the stuck count then restart the queue on PROD"))

    def test_is_prod_read_negative_whitelisted_surface(self):
        self.assertFalse(self.ss.is_self_serviceable_prod_read(
            "read /var/lib/odoo/sessions/ on PROD"))

    def test_refresh_citation_extraction(self):
        dt = self.ss.refresh_timestamp(
            "Self-service-checked: refresh run-456 at 2026-09-16T09:00:00Z ok")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 9)
        self.assertEqual(dt.day, 16)

    def test_no_refresh_citation(self):
        self.assertIsNone(self.ss.refresh_timestamp(
            "Self-service-checked: the copy predates the event"))

    def test_newest_event_ignores_version_and_refresh(self):
        text = ("read on PROD after 2026-09-14 10:00 (bump 0.1.319 -> 0.1.320) "
                "Self-service-checked: refresh run-9 at 2026-09-16T09:00:00Z")
        ev = self.ss.newest_event_timestamp(text)
        self.assertIsNotNone(ev)
        self.assertEqual((ev.year, ev.month, ev.day), (2026, 9, 14))

    def test_newest_event_bare_hhmm_today(self):
        ev = self.ss.newest_event_timestamp("read on PROD at 14:30 today")
        self.assertIsNotNone(ev)
        today = datetime.now(timezone.utc).date()
        self.assertEqual(ev.date(), today)
        self.assertEqual((ev.hour, ev.minute), (14, 30))

    def test_refresh_newer_true(self):
        older = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
        newer = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)
        self.assertTrue(self.ss.refresh_is_newer(newer, older))
        self.assertFalse(self.ss.refresh_is_newer(older, newer))

    def test_tz_normalisation_no_false_predate(self):
        # event 10:00+02:00 == 08:00Z; refresh 08:30Z is genuinely newer and
        # must not read as predating due to naive tz stripping.
        ev = self.ss.newest_event_timestamp("incident on PROD 2026-09-16 10:00+02:00")
        rf = self.ss.refresh_timestamp(
            "Self-service-checked: refresh r1 at 2026-09-16T08:30:00Z")
        self.assertTrue(self.ss.refresh_is_newer(rf, ev))


# --------------------------------------------------------------------------- #
# Audit reader — per-stream recurrence counting over the Prevencia log.
# --------------------------------------------------------------------------- #
class AuditReader(TestCase):
    def _log(self, lines):
        d = tempfile.mkdtemp(prefix="airuleset-ssr-audit-")
        p = Path(d, "selfservice-gate.log")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_counts_blocks_per_stream_and_reason(self):
        from gates import audit
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        lines = [
            "%sT08:00:00+02:00  verdict=BLOCK  kind=gk-request  "
            "reason=missing-refresh  stream=david1  ticket=42  session=s1" % today,
            "%sT09:00:00+02:00  verdict=BLOCK  kind=gk-request  "
            "reason=copy-predates-event  stream=david1  ticket=43  session=s2" % today,
            "%sT09:30:00+02:00  verdict=PASS  kind=gk-request  "
            "reason=refresh-newer-than-event  stream=david1  ticket=44  session=s3" % today,
            "%sT10:00:00+02:00  verdict=BLOCK  kind=gk-request  "
            "reason=missing-refresh  stream=montalu1  ticket=7  session=s4" % today,
        ]
        res = audit.count_selfservice_blocks(path=str(self._log(lines)),
                                             window_days=7)
        self.assertEqual(res["total"], 3)  # PASS line excluded
        self.assertEqual(res["per_stream"]["david1"], 2)
        self.assertEqual(res["per_stream"]["montalu1"], 1)
        self.assertEqual(res["per_reason"]["missing-refresh"], 2)
        self.assertEqual(res["per_reason"]["copy-predates-event"], 1)

    def test_missing_log_is_zero(self):
        from gates import audit
        res = audit.count_selfservice_blocks(
            path="/nonexistent/selfservice-gate.log")
        self.assertEqual(res["total"], 0)

    def test_window_excludes_old_lines(self):
        from gates import audit
        old = (datetime.now().astimezone() - timedelta(days=30)).strftime("%Y-%m-%d")
        lines = ["%sT08:00:00+02:00  verdict=BLOCK  kind=gk-request  "
                 "reason=missing-refresh  stream=david1  ticket=1  session=s" % old]
        res = audit.count_selfservice_blocks(path=str(self._log(lines)),
                                             window_days=7)
        self.assertEqual(res["total"], 0)


# --------------------------------------------------------------------------- #
# Whitelist constant lock — the ONE constant is the four gk-only surface
# families the refresh rsync excludes (prod-ro-clone-accounts.md).
# --------------------------------------------------------------------------- #
class WhitelistConstantLock(TestCase):
    def test_gk_only_surfaces_are_the_four_families(self):
        from gates import selfservice
        labels = [lbl for lbl, _p in selfservice.GK_ONLY_SURFACES]
        self.assertEqual(
            set(labels),
            {"session-store", "container-logs", "root-secrets", "runtime-state"},
            "the gk-only surface whitelist must stay exactly the four families "
            "prod-ro-clone-accounts.md says the refresh excludes")

    def test_each_family_matches_its_surface(self):
        from gates import selfservice
        for probe in ("/var/lib/odoo/sessions/", "odoo container logs",
                      "~/.secrets", "nginx runtime state"):
            self.assertTrue(selfservice.references_gk_only_surface(probe), probe)


# --------------------------------------------------------------------------- #
# Doctrine lock — the #1049 line lives in the DEEP companion (the always-on
# module has no byte headroom).
# --------------------------------------------------------------------------- #
class DoctrineLock(TestCase):
    def test_deep_carries_refresh_doctrine(self):
        deep = (REPO_ROOT / "skills" / "autonomous-verification-deep"
                / "DEEP.md").read_text(encoding="utf-8")
        self.assertIn(
            "A copy older than the event is a reason to REFRESH, never a reason "
            "to escalate.", deep)

    def test_always_on_module_untouched_for_byte_headroom(self):
        # the doctrine must NOT be in the always-on module (2 B headroom).
        mod = (REPO_ROOT / "modules" / "core"
               / "autonomous-verification.md").read_text(encoding="utf-8")
        self.assertNotIn("reason to REFRESH, never a reason to escalate", mod)


# --------------------------------------------------------------------------- #
# Audit CLI — the --selfservice-blocks view over the Prevencia log.
# --------------------------------------------------------------------------- #
class AuditCLI(TestCase):
    def test_selfservice_blocks_flag_runs_without_repo(self):
        # #1049-review-2 MINOR-4: the box-local view needs NO --repo.
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts"
                                 / "audit_bounce_rule_updates.py"),
             "--selfservice-blocks"],
            capture_output=True, text=True,
            env={**os.environ, "HOME": tempfile.mkdtemp(prefix="airuleset-ssr-cli-")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stream\tselfservice_blocks", r.stdout)

    def test_rounds_still_requires_repo(self):
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts"
                                 / "audit_bounce_rule_updates.py"), "--rounds"],
            capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--repo", r.stderr)


# --------------------------------------------------------------------------- #
# Adversarial-review-1 fixes (#1049 review): the fail-open promise must hold for
# a broad set of live-intervention verbs, and a bare HH:MM in ordinary prose
# must NOT be read as a today-event.
# --------------------------------------------------------------------------- #
class ReviewFix_LiveInterventionBreadth(TestCase):
    def test_reload_intervention_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read the config on PROD then reload nginx. '
                'Self-service-checked: read the state from the fresh copy; the '
                'live intervention I need is a reload of nginx."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_stop_queue_intervention_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "list the stuck jobs on PROD then stop the outgoing '
                'queue. Self-service-checked: read 40 stuck from the fresh copy; '
                'the live intervention I need is stopping the queue."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_delete_intervention_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read the orphan rows on PROD then delete them. '
                'Self-service-checked: found 12 orphans in the fresh copy; the '
                'live intervention I need is deleting them on PROD."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unit_intervention_verbs_are_not_pure_reads(self):
        from gates import selfservice
        for verb in ("reload nginx", "stop the queue", "delete the rows",
                     "enable the cron", "disable the job", "clear the cache",
                     "truncate the table", "rebuild the index"):
            self.assertFalse(
                selfservice.is_self_serviceable_prod_read(
                    "read the state on PROD then " + verb), verb)


class ReviewFix_BareTimeNeedsTodayCue(TestCase):
    def test_cron_schedule_time_is_not_an_event(self):
        # "runs daily at 15:00" is a schedule, not an event -> the fresh refresh
        # (older than 15:00 today) must still PASS.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read config_parameter on PROD. The report job runs '
                'daily at 15:00. Self-service-checked: refresh run-9 at '
                '2026-09-15T09:00:00Z from a fresh copy; already read it."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unit_bare_time_without_today_is_ignored(self):
        from gates import selfservice
        self.assertIsNone(selfservice.newest_event_timestamp(
            "read on PROD, the job runs daily at 15:00"))
        self.assertIsNone(selfservice.newest_event_timestamp(
            "as of 11:00 the count was 40 on PROD"))

    def test_unit_bare_time_with_today_still_counts(self):
        from gates import selfservice
        ev = selfservice.newest_event_timestamp("the outage at 14:30 today on PROD")
        self.assertIsNotNone(ev)
        self.assertEqual((ev.hour, ev.minute), (14, 30))


class ReviewFix_SlovakSurfaceAndOdooTighten(TestCase):
    def test_slovak_container_logs_surface_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "grep logy odoo kontajnera na PRODe pre 2026-09-14 '
                'chybu. Self-service-checked: container logy nie su v refresh '
                'kopii, treba zivy gk read."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bare_odoo_logs_is_not_auto_exempt(self):
        # tightened teeth: "the odoo logs" (DB ir.logging, self-serviceable) is
        # NOT a gk-only surface, so it still needs a refresh.
        from gates import selfservice
        self.assertFalse(selfservice.references_gk_only_surface(
            "read the odoo logs on PROD"))
        # but a genuine CONTAINER-logs request stays exempt
        self.assertTrue(selfservice.references_gk_only_surface(
            "grep the odoo container logs on PROD"))


# --------------------------------------------------------------------------- #
# Adversarial-review-2 fixes.
# --------------------------------------------------------------------------- #
class ReviewFix2_WhitelistScopedToRequest(TestCase):
    def test_naming_surface_in_rationale_does_not_exempt(self):
        # MAJOR-1: "unrelated to the session store" in the rationale must NOT
        # exempt a pure config_parameter read.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read config_parameter on PROD for the 2026-09-14 '
                '10:00 event. Self-service-checked: this is unrelated to the '
                'session store; the copy predates the event."')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("REFRESH-DEV-BOX-FROM-PROD", r.stderr)

    def test_naming_secrets_in_rationale_does_not_exempt(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read device_log on PROD for the 2026-09-14 10:00 '
                'event. Self-service-checked: not the ~/.secrets file; copy '
                'predates event."')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_surface_in_request_still_exempts(self):
        # the genuine gk-only read (surface in the REQUEST) still passes.
        from gates import selfservice
        self.assertFalse(selfservice.is_self_serviceable_prod_read(
            "read /var/lib/odoo/sessions/ on PROD",
            "read /var/lib/odoo/sessions/ on PROD. Self-service-checked: gk-only."))
        # the rationale-only mention does NOT exempt.
        self.assertTrue(selfservice.is_self_serviceable_prod_read(
            "read config_parameter on PROD",
            "read config_parameter on PROD. Self-service-checked: not the "
            "session store, copy predates event."))


class ReviewFix2_RefreshCitationParser(TestCase):
    def test_natural_refresh_sentence_not_a_citation(self):
        # MAJOR-2: "cache refresh completed at <ISO>" (id has no digit) must NOT
        # be read as the refresh copy timestamp; the r1 citation (2020) wins.
        from gates import selfservice
        body = ("read state on PROD. Self-service-checked: refresh r1 at "
                "2020-01-01T00:00:00Z. The prod cache refresh completed at "
                "2026-09-25T10:00:00Z, read state after.")
        rf = selfservice.refresh_timestamp(body)
        self.assertIsNotNone(rf)
        self.assertEqual(rf.year, 2020)
        ev = selfservice.newest_event_timestamp(body)
        self.assertIsNotNone(ev)
        self.assertEqual((ev.year, ev.month, ev.day), (2026, 9, 25))
        self.assertFalse(selfservice.refresh_is_newer(rf, ev))

    def test_date_only_refresh_citation_accepted(self):
        # MINOR-2: a date-only citation is a valid fresh-copy reference.
        from gates import selfservice
        dt = selfservice.refresh_timestamp(
            "Self-service-checked: refresh run-9 at 2026-09-16 from a fresh copy")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 9, 16))

    def test_date_only_refresh_passes_when_newer_than_event(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read config_parameter on PROD for the 2026-09-14 '
                '10:00 incident. Self-service-checked: refresh run-9 at '
                '2026-09-16 from a fresh copy; already read it."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_two_digit_tz_offset_parsed(self):
        # MINOR-3: an hours-only offset (+02) must not be dropped.
        from gates import selfservice
        ev = selfservice.newest_event_timestamp(
            "incident on PROD 2026-09-16 10:00+02")
        self.assertIsNotNone(ev)
        self.assertEqual((ev.hour, ev.minute), (8, 0))  # 10:00+02 == 08:00Z


class ReviewFix2_NginxErrorLogAndDrop(TestCase):
    def test_nginx_error_log_surface_passes(self):
        # MINOR-1: a word between the surface and "log" (nginx ERROR log) must
        # still be recognised as the gk-only container-logs surface.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "grep the nginx error log on PROD for the 2026-09-14 '
                'event. Self-service-checked: that file is excluded from the '
                'refresh rsync, live gk read needed."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_drop_decrease_sense_no_longer_exempts(self):
        # MAJOR-4: "sends drop sharply" is a diagnostic read, not an
        # intervention — it must NOT fail-open a pure read.
        from gates import selfservice
        self.assertTrue(selfservice.is_self_serviceable_prod_read(
            "read the outgoing mail queue on PROD; sends drop sharply after the "
            "2026-09-14 event"))
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "read the outgoing mail queue on PROD; sends drop '
                'sharply after the 2026-09-14 10:00 event. Self-service-checked: '
                'the copy predates the event."')
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    main()
