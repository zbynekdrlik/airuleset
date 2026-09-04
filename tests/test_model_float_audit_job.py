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
        newest_sub = lambda main: str(main) + ".sub"  # noqa: E731
        state = {}
        out = model_float_audit_job(0.0, state, panes, "/proj", read, find,
                                    newest_sub, due_fn=lambda *a, **k: True)
        self.assertEqual(len(out), 2, out)
        self.assertTrue(any("claude-fable-5-1" in ln and "main" in ln for ln in out))
        self.assertTrue(any("claude-opus-4-8" in ln and "sub" in ln for ln in out))
        self.assertIn("model_audit_last_ts", state)

    def test_allowlisted_models_silent(self):
        panes = [("%p", "/ok")]
        find = _find({"/ok": 1})
        models = {"/t/ok.jsonl": "claude-fable-5",
                  "/t/ok.jsonl.sub": "claude-sonnet-5"}
        read = lambda p: models.get(str(p), "")  # noqa: E731
        out = model_float_audit_job(0.0, {}, panes, "/proj", read, find,
                                    lambda m: str(m) + ".sub",
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
                                    lambda m: None, due_fn=lambda *a, **k: True)
        self.assertEqual(out, [])

    def test_hourly_gate_skips_when_not_due(self):
        panes = [("%p", "/repo")]
        find = _find({"/repo": 1})
        read = lambda p: "claude-fable-5-1"  # noqa: E731
        # due_fn False -> returns [] without touching panes/state marker
        state = {}
        out = model_float_audit_job(0.0, state, panes, "/proj", read, find,
                                    lambda m: None, due_fn=lambda *a, **k: False)
        self.assertEqual(out, [])
        self.assertNotIn("model_audit_last_ts", state)

    def test_never_pings_owner(self):
        # the function signature must not carry a send_fn / notify path — a
        # machine-channel-only job (the #850 class). Its module imports no notify.
        import inspect

        import watchdog.model_audit_job as mod
        self.assertNotIn("send_fn", inspect.signature(model_float_audit_job).parameters)
        self.assertNotIn("import notify", Path(mod.__file__).read_text())


if __name__ == "__main__":
    main()
