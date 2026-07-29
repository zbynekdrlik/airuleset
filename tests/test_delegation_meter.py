"""The standing MAIN-vs-SUBAGENT cost meter (#130).

The measurement that motivated #130 was taken twice by hand because the
standing instrument could not take it: `burn.scan()` globs
`<projects>/*/*.jsonl`, which reaches only the top-level session transcript.
Claude Code writes subagent transcripts one level deeper, at
`<projects>/<project>/<sid>/subagents/agent-*.jsonl`, and that is also the
only place `isSidechain` ever appears — so `scan()`'s `main_vs_sidechain`
bucket reports a false 100%-MAIN split rather than an incomplete one
(measured on dev1: 100 top-level files / 2.2 GB reachable, 5,281 subagent
files / 2.4 GB invisible; #149 tracks reconciling `burn`'s own totals).

These tests lock the additive instrument: `burn.scan_split()` +
`airuleset.py delegation`. They also lock the two things the ticket is
emphatic about — every line is bucketed by ITS OWN timestamp (file mtime has
produced wrong answers here before), and the cost unit is a stated relative
weighting, never presented as a price.
"""
import argparse
import contextlib
import datetime
import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import burn


UTC = datetime.timezone.utc


def _usage_line(ts, model="claude-opus-5", i=0, cw=0, cr=0, o=0, sidechain=False,
                cwd="/home/newlevel/devel/demo"):
    return json.dumps({
        "timestamp": ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "isSidechain": sidechain,
        "cwd": cwd,
        "message": {"model": model, "usage": {
            "input_tokens": i, "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr, "output_tokens": o}},
    })


def _write_main(root, project, session, lines):
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / (session + ".jsonl")
    p.write_text("\n".join(lines) + "\n")
    return p


def _write_sub(root, project, session, agent, lines):
    d = Path(root) / project / session / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (agent + ".jsonl")
    p.write_text("\n".join(lines) + "\n")
    return p


class TestTheBlindnessThisFixes(unittest.TestCase):
    """The root cause, locked so it cannot silently come back."""

    def test_old_scan_glob_cannot_reach_subagent_transcripts(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [_usage_line(now, cr=1000)])
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_usage_line(now, cr=999000, sidechain=True)])
            old = burn.scan(root, days=1, now=now)
        # The pre-existing instrument sees the main file only, and its
        # main-vs-sidechain bucket therefore reports a pure-MAIN world.
        self.assertEqual(old["files_scanned"], 1)
        self.assertTrue(all(k.startswith("main|") for k in old["main_vs_sidechain"]),
                        old["main_vs_sidechain"])

    def test_scan_split_reaches_both_trees(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [_usage_line(now, cr=1000)])
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_usage_line(now, cr=999000, sidechain=True)])
            data = burn.scan_split(root, hours=24, now=now,
                                   repo_resolver=lambda cwd: None)
        row = data["projects"]["proj"]
        self.assertEqual(row["main"]["turns"], 1)
        self.assertEqual(row["sub"]["turns"], 1)
        self.assertEqual(row["main"]["cache_r"], 1000)
        self.assertEqual(row["sub"]["cache_r"], 999000)


class TestWindowIsByLineTimestamp(unittest.TestCase):
    def test_recent_file_with_out_of_window_lines_contributes_nothing(self):
        now = datetime.datetime.now(UTC)
        stale = now - datetime.timedelta(hours=40)
        with TemporaryDirectory() as root:
            p = _write_main(root, "proj", "s1", [_usage_line(stale, cr=500)])
            os.utime(p, None)  # fresh mtime, stale content
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["projects"], {})

    def test_file_older_than_the_window_is_skipped_without_opening(self):
        now = datetime.datetime.now(UTC)
        stale = now - datetime.timedelta(hours=40)
        with TemporaryDirectory() as root:
            p = _write_main(root, "proj", "s1", [_usage_line(stale, cr=500)])
            old = (stale).timestamp()
            os.utime(p, (old, old))
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["files_scanned"], 0)

    def test_only_the_in_window_lines_of_a_mixed_file_count(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [
                _usage_line(now - datetime.timedelta(hours=30), cr=7),
                _usage_line(now - datetime.timedelta(hours=1), cr=11),
            ])
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["projects"]["proj"]["main"]["turns"], 1)
        self.assertEqual(data["projects"]["proj"]["main"]["cache_r"], 11)


class TestCostUnitWeighting(unittest.TestCase):
    def test_weights_are_the_stated_relative_units(self):
        self.assertEqual(burn.COST_UNIT_WEIGHTS,
                         {"in": 1.0, "cache_w": 1.25, "cache_r": 0.1, "out": 5.0})

    def test_cost_units_applies_the_weighting(self):
        row = {"in": 100, "cache_w": 200, "cache_r": 1000, "out": 10}
        self.assertAlmostEqual(burn.cost_units(row),
                               100 * 1.0 + 200 * 1.25 + 1000 * 0.1 + 10 * 5.0)

    def test_weighting_is_tier_neutral(self):
        """Volume, not model tier — tier drift is #133's separate scope."""
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "a", "s1", [_usage_line(now, model="claude-haiku-4-5", cr=1000)])
            _write_main(root, "b", "s1", [_usage_line(now, model="claude-fable-5", cr=1000)])
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["projects"]["a"]["main"]["units"],
                         data["projects"]["b"]["main"]["units"])

    def test_scan_reports_the_weighting_it_used(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["weights"], burn.COST_UNIT_WEIGHTS)


class TestMeanContextPerTurn(unittest.TestCase):
    def test_ctx_per_turn_is_all_input_context_over_turns(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [
                _usage_line(now, i=10, cw=90, cr=900, o=50),
                _usage_line(now, i=10, cw=90, cr=900, o=50),
            ])
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["projects"]["proj"]["main"]["ctx_per_turn"], 1000)

    def test_zero_turns_never_divides_by_zero(self):
        self.assertEqual(burn.ctx_per_turn({"turns": 0, "in": 0, "cache_w": 0,
                                            "cache_r": 0}), 0)


class TestSessionAndDispatchCounts(unittest.TestCase):
    def test_counts_distinct_transcripts_with_in_window_activity(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [_usage_line(now, cr=1)])
            _write_sub(root, "proj", "s1", "agent-a1", [_usage_line(now, cr=1, sidechain=True)])
            _write_sub(root, "proj", "s1", "agent-a2", [_usage_line(now, cr=1, sidechain=True)])
            _write_sub(root, "proj", "s1", "agent-a3", [_usage_line(now, cr=1, sidechain=True)])
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["projects"]["proj"]["main"]["sessions"], 1)
        self.assertEqual(data["projects"]["proj"]["sub"]["sessions"], 3)


class TestProjectSeparation(unittest.TestCase):
    def test_two_projects_do_not_bleed(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "alpha", "s1", [_usage_line(now, cr=100)])
            _write_sub(root, "beta", "s2", "agent-a1",
                       [_usage_line(now, cr=200, sidechain=True)])
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertEqual(data["projects"]["alpha"]["main"]["cache_r"], 100)
        self.assertEqual(data["projects"]["alpha"]["sub"]["turns"], 0)
        self.assertEqual(data["projects"]["beta"]["sub"]["cache_r"], 200)
        self.assertEqual(data["projects"]["beta"]["main"]["turns"], 0)


class TestRepoResolution(unittest.TestCase):
    def test_repo_comes_from_the_recorded_cwd_not_the_encoded_dirname(self):
        """`/` and a literal `-` both encode to `-` in the project dir name,
        so the directory name is an ambiguous source; the transcript's own
        `cwd` field is not."""
        now = datetime.datetime.now(UTC)
        seen = []

        def resolver(cwd):
            seen.append(cwd)
            return "zbynekdrlik/parovanie-produktov"

        with TemporaryDirectory() as root:
            _write_main(root, "-home-newlevel-devel-forestshop-parovanie-produktov",
                        "s1", [_usage_line(now, cr=1,
                                           cwd="/home/newlevel/devel/forestshop/parovanie-produktov")])
            data = burn.scan_split(root, hours=12, now=now, repo_resolver=resolver)
        self.assertEqual(seen, ["/home/newlevel/devel/forestshop/parovanie-produktov"])
        proj = data["projects"]["-home-newlevel-devel-forestshop-parovanie-produktov"]
        self.assertEqual(proj["cwd"], "/home/newlevel/devel/forestshop/parovanie-produktov")
        self.assertEqual(proj["repo"], "zbynekdrlik/parovanie-produktov")

    def test_unresolvable_repo_is_none_not_a_guess(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [_usage_line(now, cr=1)])
            data = burn.scan_split(root, hours=12, now=now,
                                   repo_resolver=lambda cwd: None)
        self.assertIsNone(data["projects"]["proj"]["repo"])

    def test_repo_of_cwd_parses_ssh_and_https_remotes(self):
        self.assertEqual(
            burn.repo_of_cwd("/x", _runner=lambda c: "https://github.com/zbynekdrlik/camera-box.git"),
            "zbynekdrlik/camera-box")
        self.assertEqual(
            burn.repo_of_cwd("/x", _runner=lambda c: "git@github.com:zbynekdrlik/airuleset.git"),
            "zbynekdrlik/airuleset")

    def test_repo_of_cwd_returns_none_when_git_fails(self):
        def boom(cmd):
            raise OSError("not a repo")
        self.assertIsNone(burn.repo_of_cwd("/x", _runner=boom))


class TestCostPerTicket(unittest.TestCase):
    def test_units_per_ticket_divides(self):
        self.assertAlmostEqual(burn.units_per_ticket(8_200_000, 2), 4_100_000)

    def test_zero_closed_tickets_is_none_not_infinity(self):
        """camera-box's real 12h result: 131.3M units, 0 tickets closed."""
        self.assertIsNone(burn.units_per_ticket(131_300_000, 0))

    def test_render_shows_spend_with_no_ticket_as_a_dash_not_zero(self):
        merged = {
            "by_project": {
                "dev1:camera-box": {
                    "host": "dev1", "project": "camera-box", "cwd": "/c",
                    "repo": "zbynekdrlik/camera-box",
                    "main": burn.finalize_split_row({"turns": 390, "in": 0, "cache_w": 0,
                                                     "cache_r": 100, "out": 0, "sessions": 1}),
                    "sub": burn.finalize_split_row({"turns": 3268, "in": 0, "cache_w": 0,
                                                    "cache_r": 900, "out": 0, "sessions": 40}),
                    "closed_tickets": 0,
                },
            },
            "weights": burn.COST_UNIT_WEIGHTS,
        }
        out = burn.render_split(merged, hours=12)
        self.assertIn("camera-box", out)
        self.assertIn("—", out)
        self.assertNotIn("0.0 units/ticket", out)


class TestRenderNeverCallsItAPrice(unittest.TestCase):
    def _merged(self):
        return {
            "by_project": {
                "dev1:proj": {
                    "host": "dev1", "project": "proj", "cwd": "/c", "repo": None,
                    "main": burn.finalize_split_row({"turns": 10, "in": 1, "cache_w": 2,
                                                     "cache_r": 3, "out": 4, "sessions": 1}),
                    "sub": burn.finalize_split_row({"turns": 20, "in": 1, "cache_w": 2,
                                                    "cache_r": 3, "out": 4, "sessions": 5}),
                    "closed_tickets": None,
                },
            },
            "weights": burn.COST_UNIT_WEIGHTS,
        }

    def test_states_the_weighting_used(self):
        out = burn.render_split(self._merged(), hours=12)
        self.assertIn("1.25", out)
        self.assertIn("relative units", out.lower())

    def test_does_not_print_a_dollar_figure(self):
        out = burn.render_split(self._merged(), hours=12)
        self.assertNotIn("$", out)

    def test_shows_main_and_sub_rows_with_their_share(self):
        out = burn.render_split(self._merged(), hours=12)
        self.assertIn("MAIN", out)
        self.assertIn("SUB", out)


class TestMergeAcrossHosts(unittest.TestCase):
    def _rep(self, host, project, main_turns, sub_turns):
        return {
            "host": host, "user": "u", "hours": 12,
            "weights": burn.COST_UNIT_WEIGHTS,
            "files_scanned": 1, "usage_lines": 1,
            "projects": {project: {
                "cwd": "/c", "repo": None,
                "main": burn.finalize_split_row({"turns": main_turns, "in": 0, "cache_w": 0,
                                                 "cache_r": 10, "out": 0, "sessions": 1}),
                "sub": burn.finalize_split_row({"turns": sub_turns, "in": 0, "cache_w": 0,
                                                "cache_r": 20, "out": 0, "sessions": 2}),
            }},
        }

    def test_same_project_name_on_two_boxes_does_not_collide(self):
        merged = burn.merge_splits([self._rep("dev1", "odoo-erp", 5, 50),
                                    self._rep("gatekeeper", "odoo-erp", 7, 70)])
        self.assertIn("dev1:odoo-erp", merged["by_project"])
        self.assertIn("gatekeeper:odoo-erp", merged["by_project"])
        self.assertEqual(merged["by_project"]["gatekeeper:odoo-erp"]["main"]["turns"], 7)

    def test_totals_sum_main_and_sub_separately(self):
        merged = burn.merge_splits([self._rep("dev1", "a", 5, 50),
                                    self._rep("dev2", "b", 7, 70)])
        self.assertEqual(merged["totals"]["main"]["turns"], 12)
        self.assertEqual(merged["totals"]["sub"]["turns"], 120)

    def test_totals_recompute_derived_fields_rather_than_summing_them(self):
        merged = burn.merge_splits([self._rep("dev1", "a", 5, 50),
                                    self._rep("dev2", "b", 5, 50)])
        # cache_r 10 per host, 2 hosts, 10 turns -> 2 ctx/turn, not 1+1 summed
        self.assertEqual(merged["totals"]["main"]["ctx_per_turn"], 2)

    def test_merge_tolerates_a_report_without_the_projects_key(self):
        merged = burn.merge_splits([{"host": "x"}, self._rep("dev1", "a", 1, 1)])
        self.assertEqual(merged["totals"]["main"]["turns"], 1)

    def test_absorbs_an_already_merged_report_from_a_remote_box(self):
        """A remote box is collected by running ITS OWN `delegation --json`,
        which prints the MERGED shape (`by_project`), not the raw
        `split_report` shape (`projects`). Live-caught: the coordinator
        silently dropped every remote box and reported a dev1-only total as a
        fleet total."""
        remote = burn.merge_splits([self._rep("gatekeeper", "odoo-erp", 733, 3504)])
        merged = burn.merge_splits([self._rep("dev1", "airuleset", 347, 3141),
                                    remote])
        self.assertIn("gatekeeper:odoo-erp", merged["by_project"])
        self.assertEqual(
            merged["by_project"]["gatekeeper:odoo-erp"]["main"]["turns"], 733)
        self.assertEqual(
            merged["by_project"]["gatekeeper:odoo-erp"]["sub"]["turns"], 3504)
        self.assertEqual(merged["totals"]["main"]["turns"], 347 + 733)
        self.assertEqual(merged["totals"]["sub"]["turns"], 3141 + 3504)

    def test_an_already_merged_report_keeps_its_own_host_not_the_collectors(self):
        remote = burn.merge_splits([self._rep("dev2", "codex-bridge", 192, 40)])
        merged = burn.merge_splits([remote])
        self.assertIn("dev2:codex-bridge", merged["by_project"])
        self.assertNotIn("?:codex-bridge", merged["by_project"])
        self.assertEqual(
            merged["by_project"]["dev2:codex-bridge"]["host"], "dev2")

    def test_an_already_merged_report_carries_its_repo_through(self):
        rep = self._rep("dev2", "codex-bridge", 1, 1)
        rep["projects"]["codex-bridge"]["repo"] = "zbynekdrlik/codex-bridge"
        merged = burn.merge_splits([burn.merge_splits([rep])])
        self.assertEqual(
            merged["by_project"]["dev2:codex-bridge"]["repo"],
            "zbynekdrlik/codex-bridge")


class TestSplitReport(unittest.TestCase):
    def test_tags_host_and_user(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [_usage_line(now, cr=1)])
            rep = burn.split_report(hours=12, root=root)
        self.assertEqual(rep["host"], os.uname().nodename)
        self.assertIn("user", rep)


class TestDelegationCommand(unittest.TestCase):
    def test_registered_in_subcommands(self):
        self.assertIn("delegation", airuleset.SUBCOMMANDS)
        self.assertTrue(callable(airuleset.SUBCOMMANDS["delegation"]))

    def test_remote_cmd_uses_identity_when_present(self):
        remote = {"name": "gatekeeper", "host": "1.2.3.4", "user": "gatekeeper",
                  "repo_path": "~/devel/airuleset",
                  "identity": "~/.secrets/gatekeeper_access_ed25519"}
        cmd = airuleset._delegation_remote_cmd(remote, hours=12)
        self.assertIn("-i", cmd)
        self.assertNotIn("sshpass", cmd)
        self.assertIn("gatekeeper@1.2.3.4", cmd)
        self.assertIn("airuleset.py delegation --json --hours 12", " ".join(cmd))

    def test_remote_cmd_uses_sshpass_without_identity(self):
        remote = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                  "repo_path": "~/devel/airuleset"}
        cmd = airuleset._delegation_remote_cmd(remote, hours=6)
        self.assertIn("sshpass", cmd)
        self.assertIn("newlevel@5.6.7.8", cmd)
        self.assertIn("airuleset.py delegation --json --hours 6", " ".join(cmd))

    def test_remote_cmd_matches_the_existing_burn_ssh_shape(self):
        """Reuse the sanctioned mechanism — never invent a new ssh shape."""
        remote = {"name": "gatekeeper", "host": "1.2.3.4", "user": "gatekeeper",
                  "repo_path": "~/devel/airuleset",
                  "identity": "~/.secrets/gatekeeper_access_ed25519"}
        a = airuleset._delegation_remote_cmd(remote, hours=12)
        b = airuleset._burn_remote_cmd(remote, days=7)
        self.assertEqual(a[:-1], b[:-1])

    def test_remote_command_is_read_only(self):
        remote = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                  "repo_path": "~/devel/airuleset"}
        joined = " ".join(airuleset._delegation_remote_cmd(remote, hours=12))
        for danger in ("rm ", "scp", "rsync", ">", "install", "push"):
            self.assertNotIn(danger, joined.split("airuleset.py")[-1])

    def test_unknown_host_name_exits_nonzero(self):
        with self.assertRaises(SystemExit) as ctx:
            airuleset.cmd_delegation(argparse.Namespace(
                hours=12, json=True, host="not-a-real-box", tickets=False))
        self.assertNotEqual(ctx.exception.code, 0)

    def test_json_flag_prints_valid_json(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [_usage_line(now, cr=5)])
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_usage_line(now, cr=50, sidechain=True)])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_delegation(argparse.Namespace(
                    hours=12, json=True, host=None, tickets=False, root=root))
        data = json.loads(buf.getvalue())
        self.assertIn("by_project", data)
        self.assertEqual(data["weights"], burn.COST_UNIT_WEIGHTS)

    def test_human_output_by_default(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_main(root, "proj", "s1", [_usage_line(now, cr=5)])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_delegation(argparse.Namespace(
                    hours=12, json=False, host=None, tickets=False, root=root))
        out = buf.getvalue()
        self.assertIn("MAIN", out)
        self.assertIn("relative units", out.lower())


class TestClosedTicketCounts(unittest.TestCase):
    def test_counts_only_issues_closed_inside_the_window(self):
        start = datetime.datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
        end = datetime.datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        payload = json.dumps([
            {"number": 1, "closedAt": "2026-07-28T01:00:00Z"},
            {"number": 2, "closedAt": "2026-07-28T11:00:00Z"},
            {"number": 3, "closedAt": "2026-07-27T23:00:00Z"},
            {"number": 4, "closedAt": None},
        ])
        n = airuleset._closed_ticket_count("o/r", start, end, _runner=lambda c: payload)
        self.assertEqual(n, 2)

    def test_gh_failure_is_none_not_zero(self):
        """An unavailable gh must not fabricate a zero-ticket denominator."""
        def boom(cmd):
            raise OSError("gh missing")
        start = datetime.datetime(2026, 7, 28, tzinfo=UTC)
        self.assertIsNone(airuleset._closed_ticket_count(
            "o/r", start, start, _runner=boom))


class TestDocumentedWhereASessionWillFindIt(unittest.TestCase):
    """Item 3 of #130: the measure -> change one thing -> re-measure ->
    keep/revert cycle has to live somewhere a future session actually
    reads, not in a ticket comment."""

    RULE = Path(__file__).resolve().parent.parent / ".claude" / "rules" / "airuleset-internals.md"

    def test_the_working_cycle_is_documented_in_the_path_scoped_rule(self):
        text = self.RULE.read_text()
        self.assertIn("delegation", text)
        for phrase in ("re-measure", "keep or revert"):
            self.assertIn(phrase, text.lower())

    def test_the_rule_is_path_scoped_to_the_burn_module(self):
        text = self.RULE.read_text()
        head = text.split("---")[1] if text.startswith("---") else ""
        self.assertIn("burn", head)


if __name__ == "__main__":
    unittest.main()
