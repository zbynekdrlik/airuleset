"""#1260 — a session Claude Code stopped with "Autocompact is thrashing" must never
get a watchdog `continue`, and the 402 KB odoo-erp gatekeeper lens files must not be
loaded into every turn of every reduced sub-dev stream.

Live incident (david3@subdev, 24./25.10.2026): CC's per-model 400k compact window
sat below the ~431k post-compact baseline, so every compact refilled the window and
CC stopped the turn with a synthetic `isApiErrorMessage` entry. Job 1 read that as a
resumable API stall and typed `continue` 31 times in one night.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_config  # noqa: E402

# The exact transcript entry CC 2.1.306 wrote on david3 (25.10.2026 04:43:20Z).
THRASH_TEXT = ("Autocompact is thrashing: the context refilled to the limit within 3 turns "
               "of the previous compact, 3 times in a row. A file being read or a tool "
               "output is likely too large for the context window. Try reading in smaller "
               "chunks, or use /clear to start fresh.")
THRASH_ENTRY = {"type": "assistant", "isApiErrorMessage": True,
                "apiError": "autocompact_thrashing", "error": "invalid_request",
                "message": {"role": "assistant", "model": "<synthetic>",
                            "content": [{"type": "text", "text": THRASH_TEXT}]}}

_IDLE_PANE = ("● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"
              "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")


class _FakeTmux:
    """list-panes / capture-pane from canned data; records every send-keys argv."""

    def __init__(self, panes):
        self.panes = panes
        self.sent = []

    def __call__(self, argv, timeout=8):
        if argv[:2] == ["tmux", "list-panes"]:
            return self.panes
        if argv[:2] == ["tmux", "display-message"]:
            return "0" if argv[-1] == "#{pane_in_mode}" else ""
        if argv[:2] == ["tmux", "capture-pane"]:
            return _IDLE_PANE
        if argv[:2] == ["tmux", "send-keys"]:
            self.sent.append(argv)
        return ""

    def continues_sent(self):
        return sum(1 for a in self.sent if "-l" in a and "continue" in a)


class ThrashClassifier(TestCase):
    def test_the_real_banner_is_a_thrash(self):
        import watchdog
        self.assertTrue(watchdog.is_autocompact_thrash(THRASH_TEXT))

    def test_ordinary_api_errors_are_not(self):
        import watchdog
        for text in ("API Error: 529 Overloaded",
                     "Claude usage limit reached — resets at 18:00",
                     "API Error: Connection refused", "", None):
            self.assertFalse(watchdog.is_autocompact_thrash(text), text)


class JobOneNeverContinuesAThrash(TestCase):
    """Job 1 drives the same `run_once` the timer runs; only tmux and Discord are faked."""

    _SID = "5e55abc0-51d0-4a5e-9f1e-00000000abcd"

    def setUp(self):
        import watchdog
        self.w = watchdog
        self.tmp = tempfile.mkdtemp()
        self.projects = Path(self.tmp) / "projects"
        self.projects.mkdir()
        self.state = str(Path(self.tmp) / "state.json")
        self.pings = []
        orig_cache = self.w.usage._USAGE_CACHE_PATH
        self.w.usage._USAGE_CACHE_PATH = str(Path(self.tmp) / "usage-cache.json")
        self.addCleanup(setattr, self.w.usage, "_USAGE_CACHE_PATH", orig_cache)

        def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None,
                                  logs=None, user_authored=False, nudge=None, state=None):
            run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
            run(["tmux", "send-keys", "-t", pid, "Enter"])
            return True
        p = m.patch.object(self.w, "send_verified", _typing_send_verified)
        p.start()
        self.addCleanup(p.stop)

    def _send(self, body, owner=None, dedup_key=None, dry_run=False):
        self.pings.append((body, dedup_key))
        return "sent"

    def _transcript(self, cwd, entries, age_s, now):
        d = self.projects / self.w.encode_project_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / (self._SID + ".jsonl")
        p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        os.utime(p, (now - age_s, now - age_s))
        return p

    def _kw(self, fake):
        return dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                    state_path=self.state, grace=300, interval=300, max_nudges=3)

    def test_thrash_stop_gets_no_continue_on_any_sweep(self):
        now = 1_000_000
        cwd = "/devel/thrashing"
        self._transcript(cwd, [THRASH_ENTRY], 600, now)
        fake = _FakeTmux("%5\tclaude\t" + cwd + "\n")
        logs = self.w.run_once(now=now, **self._kw(fake))
        for step in (300, 600, 1800, 7200):
            logs += self.w.run_once(now=now + step, **self._kw(fake))
        self.assertEqual(fake.continues_sent(), 0, "a thrash stop must never get `continue`")
        self.assertTrue(any("autocompact-thrash" in ln for ln in logs), logs)
        self.assertLessEqual(len(self.pings), 1, "at most one machine-channel line")

    def test_a_529_still_gets_its_continue(self):
        """Control: the ordinary API-stall recovery is unchanged."""
        now = 1_000_000
        cwd = "/devel/overloaded"
        err = {"type": "assistant", "isApiErrorMessage": True,
               "message": {"role": "assistant",
                           "content": [{"type": "text", "text": "API Error: 529 Overloaded"}]}}
        self._transcript(cwd, [err], 600, now)
        fake = _FakeTmux("%5\tclaude\t" + cwd + "\n")
        self.w.run_once(now=now, **self._kw(fake))
        self.assertEqual(fake.continues_sent(), 1)


class LensFilesStayOutOfStreamContext(TestCase):
    PATTERN = "**/.claude/rules/gk-review-lenses*.md"

    def _apply(self, user, settings):
        with m.patch.object(airuleset, "_current_user", return_value=user):
            return cli_config.apply_managed_settings_defaults(settings)

    def test_a_reduced_stream_excludes_the_lens_files(self):
        for user in ("david3", "montalu1", "miva1"):
            out = self._apply(user, {})
            self.assertIn(self.PATTERN, out.get("claudeMdExcludes", []), user)

    def test_existing_entries_are_kept_and_nothing_duplicates(self):
        out = self._apply("montalu2", {"claudeMdExcludes": ["/home/x/own/CLAUDE.md"]})
        out = self._apply("montalu2", out)
        self.assertEqual(out["claudeMdExcludes"].count(self.PATTERN), 1)
        self.assertIn("/home/x/own/CLAUDE.md", out["claudeMdExcludes"])

    def test_the_gatekeeper_keeps_its_review_checklist_loaded(self):
        out = self._apply("gatekeeper", {"claudeMdExcludes": [self.PATTERN, "/keep/me"]})
        self.assertNotIn(self.PATTERN, out.get("claudeMdExcludes", []))
        self.assertIn("/keep/me", out.get("claudeMdExcludes", []))

    def test_a_malformed_list_self_heals(self):
        out = self._apply("david1", {"claudeMdExcludes": "not-a-list"})
        self.assertEqual(out["claudeMdExcludes"], [self.PATTERN])


class PerModelCompactWindowIsStripped(TestCase):
    """CC's autoCompactOffer writes the window per model; the top-level strip missed it."""

    def _apply(self, settings):
        with m.patch.object(airuleset, "_current_user", return_value="gatekeeper"):
            return cli_config.apply_managed_settings_defaults(settings)

    def test_the_offer_written_window_is_removed(self):
        out = self._apply({"modelSettings": {"claude-opus-5-5": {"autoCompactWindow": 400000}}})
        self.assertNotIn("autoCompactWindow",
                         out.get("modelSettings", {}).get("claude-opus-5-5", {}))

    def test_other_per_model_keys_survive(self):
        out = self._apply({"modelSettings": {"claude-opus-5-5": {
            "autoCompactWindow": 400000, "keepMe": 1}}})
        self.assertEqual(out["modelSettings"]["claude-opus-5-5"], {"keepMe": 1})


class SelfReviewReadsEveryLensPart(TestCase):
    """With the lenses out of the always-on context, the stream's self-review must
    load the index AND every prevencia companion explicitly."""

    def test_the_worker_brief_names_the_prevencia_companions(self):
        brief = (Path(__file__).resolve().parent.parent / "agents" /
                 "autopilot-worker.md").read_text()
        self.assertIn("gk-review-lenses-prevencia", brief)
