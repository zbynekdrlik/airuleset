"""#871 — watchdog Job 41 model_float_audit_job (machine-channel only).

Journals a `model-float …` line per live pane/subagent on a model OUTSIDE the
exact-id allowlist (airuleset.MODEL_TIERS); hourly-gated; NEVER pings the owner
(no send_fn param exists). Dependency-injected for a network/tmux-free test.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from watchdog.model_audit_job import model_float_audit_job  # noqa: E402


def _find(model_by_cwd):
    def find(projects_dir, cwd):
        return ("/t/%s.jsonl" % cwd.strip("/"), 0.0) if cwd in model_by_cwd else None
    return find


class TestModelFloatAuditJob(TestCase):
    def test_flags_floated_main_and_sub(self):
        panes = [("%p", "/repo")]
        find = _find({"/repo": 1})
        models = {"/t/repo.jsonl": "claude-fable-5-1",       # main floated to 5.1
                  "/t/repo.jsonl.sub": "claude-opus-4-8"}    # sub on superseded opus
        read = lambda p: models.get(str(p), "")  # noqa: E731
        subs = lambda main, now: [str(main) + ".sub"]  # noqa: E731
        state = {}
        out = model_float_audit_job(0.0, state, panes, "/proj", read, find,
                                    subs, due_fn=lambda *a, **k: True)
        self.assertEqual(len(out), 2, out)
        self.assertTrue(any("claude-fable-5-1" in ln and "main" in ln for ln in out))
        self.assertTrue(any("claude-opus-4-8" in ln and "sub" in ln for ln in out))
        self.assertIn("model_audit_last_ts", state)

    def test_allowlisted_models_silent(self):
        panes = [("%p", "/ok")]
        find = _find({"/ok": 1})
        models = {"/t/ok.jsonl": "claude-fable-5-1",
                  "/t/ok.jsonl.sub": "claude-sonnet-5"}
        read = lambda p: models.get(str(p), "")  # noqa: E731
        out = model_float_audit_job(0.0, {}, panes, "/proj", read, find,
                                    lambda main, now: [str(main) + ".sub"],
                                    due_fn=lambda *a, **k: True)
        self.assertEqual(out, [])

    def test_dated_served_id_for_allowlisted_tier_not_flagged(self):
        # #871 adversarial review 🔴3a: same audit-tolerance requirement as
        # cli_model_audit.py -- Job 41 must not journal a false model-float
        # violation for a served dated-snapshot id of an allowlisted tier.
        panes = [("%p", "/d")]
        find = _find({"/d": 1})
        read = lambda p: "claude-haiku-4-5-20251001"  # noqa: E731
        out = model_float_audit_job(0.0, {}, panes, "/proj", read, find,
                                    lambda main, now: [], due_fn=lambda *a, **k: True)
        self.assertEqual(out, [])

    def test_hourly_gate_skips_when_not_due(self):
        panes = [("%p", "/repo")]
        find = _find({"/repo": 1})
        read = lambda p: "claude-fable-5-1"  # noqa: E731
        # due_fn False -> returns [] without touching panes/state marker
        state = {}
        out = model_float_audit_job(0.0, state, panes, "/proj", read, find,
                                    lambda main, now: [], due_fn=lambda *a, **k: False)
        self.assertEqual(out, [])
        self.assertNotIn("model_audit_last_ts", state)

    def test_never_pings_owner(self):
        # the function signature must not carry a send_fn / notify path — a
        # machine-channel-only job (the #850 class). Its module imports no notify.
        import inspect

        import watchdog.model_audit_job as mod
        self.assertNotIn("send_fn", inspect.signature(model_float_audit_job).parameters)
        self.assertNotIn("import notify", Path(mod.__file__).read_text())

    def test_dedupes_a_subagent_transcript_reported_more_than_once(self):
        # #871: two panes resolving to the same main transcript must not
        # double-journal the same subagent transcript.
        panes = [("%p1", "/a"), ("%p2", "/a")]
        find = _find({"/a": 1})
        models = {"/t/a.jsonl": "claude-sonnet-5",
                  "/t/a.jsonl#sub0": "claude-fable-5-1"}
        read = lambda p: models.get(str(p), "")  # noqa: E731
        subs = lambda main, now: [str(main) + "#sub0"]  # noqa: E731
        out = model_float_audit_job(0.0, {}, panes, "/proj", read, find,
                                    subs, due_fn=lambda *a, **k: True)
        sub_lines = [ln for ln in out if " sub " in ln]
        self.assertEqual(len(sub_lines), 1, out)

    def test_only_recent_subagent_journaled_via_the_real_recency_helper(self):
        # #871 integration: wiring `subagent_transcripts` to
        # cli_model_audit._subagent_transcripts (the real production seam,
        # watchdog/__init__.py) means a long-dead (3-day-old) subagent
        # transcript is never journaled, only a fresh one.
        import os
        import tempfile
        import time

        import cli_model_audit
        from watchdog.transcripts import transcript_last_assistant_model

        with tempfile.TemporaryDirectory() as d:
            main_path = os.path.join(d, "sess.jsonl")
            subdir = os.path.join(d, "sess", "subagents")
            os.makedirs(subdir, exist_ok=True)

            def _write(p, model):
                Path(p).write_text(
                    '{"type":"assistant","message":{"role":"assistant",'
                    '"model":"%s","content":[{"type":"text","text":"ok"}]}}\n'
                    % model, encoding="utf-8")

            _write(main_path, "claude-sonnet-5")
            recent = os.path.join(subdir, "recent.jsonl")
            old = os.path.join(subdir, "old.jsonl")
            _write(recent, "claude-fable-5-1")
            _write(old, "claude-fable-5-1")
            now = time.time()
            os.utime(recent, (now, now))
            os.utime(old, (now - 3 * 86400, now - 3 * 86400))

            panes = [("%p", "/proj")]
            find = lambda pd, cwd: (main_path, 0.0)  # noqa: E731
            out = model_float_audit_job(
                now, {}, panes, "/proj", transcript_last_assistant_model,
                find, cli_model_audit._subagent_transcripts,
                due_fn=lambda *a, **k: True)
            # both recent.jsonl and old.jsonl carry the SAME banned model, so
            # a working recency filter is what collapses this to exactly ONE
            # sub line (the stale 3-day-old transcript excluded) rather than
            # two (one per distinct path -- dedupe alone cannot explain a
            # single line here, since the two paths differ).
            sub_lines = [ln for ln in out if " sub " in ln]
            self.assertEqual(len(sub_lines), 1, out)


if __name__ == "__main__":
    main()
