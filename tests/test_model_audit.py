"""#871 — airuleset.py model-audit (read-only allowlist float check).

Locks (a) watchdog.transcripts.transcript_last_assistant_model reading the
newest-assistant `model` from a fixture transcript, and (b) the pure
cli_model_audit.audit_model_floats core flagging any model outside the exact-id
allowlist (airuleset.MODEL_TIERS), main + subagent, via injected fns.
"""
import json
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import airuleset  # noqa: E402
import cli_model_audit  # noqa: E402
from watchdog.transcripts import transcript_last_assistant_model  # noqa: E402


def _write_transcript(path, model, extra_after=None):
    """A minimal CC-shaped jsonl: a user turn, then an assistant turn carrying
    `message.model`. `extra_after` (a model id) appends a LATER assistant turn
    so we can prove the NEWEST wins (the float case)."""
    lines = [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant",
         "message": {"role": "assistant", "model": model,
                     "content": [{"type": "text", "text": "ok"}]}},
    ]
    if extra_after:
        lines.append({"type": "assistant",
                      "message": {"role": "assistant", "model": extra_after,
                                  "content": [{"type": "text", "text": "later"}]}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                    encoding="utf-8")


class TestLastAssistantModel(TestCase):
    def test_reads_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            _write_transcript(p, "claude-fable-5-1")
            self.assertEqual(transcript_last_assistant_model(p), "claude-fable-5-1")

    def test_newest_assistant_wins_the_float(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            # launched on 5.0, floated to 5.1 mid-session
            _write_transcript(p, "claude-fable-5-1", extra_after="claude-fable-5-1")
            self.assertEqual(transcript_last_assistant_model(p), "claude-fable-5-1")

    def test_missing_or_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.jsonl"
            p.write_text("", encoding="utf-8")
            self.assertEqual(transcript_last_assistant_model(p), "")

    def test_skips_a_trailing_synthetic_entry(self):
        # #871 adversarial review 🔴3b: a trailing synthetic/bookkeeping
        # assistant record (sentinel/empty text) carrying a placeholder
        # model must be SKIPPED — the walk continues back to the last REAL
        # served model, same as transcript_last_assistant_text's own
        # sentinel-skip semantics.
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            lines = [
                {"type": "user", "message": {"role": "user", "content": "hi"}},
                {"type": "assistant",
                 "message": {"role": "assistant", "model": "claude-fable-5-1",
                             "content": [{"type": "text", "text": "ok"}]}},
                # trailing synthetic entry: sentinel (empty) text, a
                # placeholder model id that is NOT a real served model.
                {"type": "assistant",
                 "message": {"role": "assistant", "model": "<synthetic>",
                             "content": [{"type": "text", "text": ""}]}},
            ]
            p.write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                        encoding="utf-8")
            self.assertEqual(transcript_last_assistant_model(p), "claude-fable-5-1")


class TestAuditModelFloats(TestCase):
    def _fake_find(self, model_by_cwd):
        def find(projects_dir, cwd):
            m = model_by_cwd.get(cwd)
            return ("/x/%s.jsonl" % cwd.strip("/").replace("/", "_"), 0.0) if m else None
        return find

    def test_flags_banned_main_and_sub(self):
        panes = [("%p1", "/a"), ("%p2", "/b")]
        # /a main is floated to 5.1 (BANNED); /a has a sub on opus-4-8 (BANNED,
        # superseded); /b main on the allowlisted sonnet (ok).
        model_of = {
            "/x/a.jsonl": "claude-fable-5-1",
            "/x/a.jsonl#sub0": "claude-opus-4-8",
            "/x/b.jsonl": "claude-sonnet-5",
        }
        find = self._fake_find({"/a": "claude-fable-5-1", "/b": "claude-sonnet-5"})
        read = lambda p: model_of.get(str(p), "")  # noqa: E731
        subs = lambda main: [str(main) + "#sub0"] if str(main).endswith("a.jsonl") else []  # noqa: E731

        recs = cli_model_audit.audit_model_floats(panes, "/proj", find, read,
                                                  subagent_iter=subs)
        by = {(r["cwd"], r["kind"]): r for r in recs}
        self.assertTrue(by[("/a", "main")]["banned"])
        self.assertEqual(by[("/a", "main")]["model"], "claude-fable-5-1")
        self.assertTrue(by[("/a", "sub")]["banned"])
        self.assertFalse(by[("/b", "main")]["banned"])

    def test_allowlisted_models_never_flagged(self):
        panes = [("%p", "/c")]
        find = self._fake_find({"/c": "claude-opus-4-6"})
        for allowed in airuleset.MODEL_TIERS.values():
            read = lambda p, a=allowed: a  # noqa: E731
            recs = cli_model_audit.audit_model_floats(panes, "/proj", find, read,
                                                      subagent_iter=lambda m: [])
            self.assertEqual(len(recs), 1)
            self.assertFalse(recs[0]["banned"], "%s must be ok" % allowed)

    def test_no_transcript_yields_no_record(self):
        panes = [("%p", "/none")]
        recs = cli_model_audit.audit_model_floats(
            panes, "/proj", lambda pd, cwd: None, lambda p: "x",
            subagent_iter=lambda m: [])
        self.assertEqual(recs, [])

    def test_dated_served_id_for_allowlisted_tier_not_flagged(self):
        # #871 adversarial review 🔴3a: Anthropic sometimes serves a DATED
        # snapshot id for an allowlisted tier (claude-haiku-4-5-20251001) —
        # the audit must not flag it BANNED.
        panes = [("%p", "/d")]
        find = self._fake_find({"/d": "claude-haiku-4-5-20251001"})
        read = lambda p: "claude-haiku-4-5-20251001"  # noqa: E731
        recs = cli_model_audit.audit_model_floats(panes, "/proj", find, read,
                                                  subagent_iter=lambda m: [])
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0]["banned"], recs[0])

    def test_dedupes_a_subagent_transcript_reported_more_than_once(self):
        # #871: two panes resolving to the SAME main transcript (or a
        # subagent_iter that yields the same path twice for any other
        # reason) must not double-report the same sub transcript.
        panes = [("%p1", "/a"), ("%p2", "/a")]
        model_of = {"/x/a.jsonl": "claude-sonnet-5",
                    "/x/a.jsonl#sub0": "claude-fable-5-1"}
        find = self._fake_find({"/a": "claude-sonnet-5"})
        read = lambda p: model_of.get(str(p), "")  # noqa: E731
        subs = lambda main: [str(main) + "#sub0"]  # noqa: E731

        recs = cli_model_audit.audit_model_floats(panes, "/proj", find, read,
                                                  subagent_iter=subs)
        sub_recs = [r for r in recs if r["kind"] == "sub"]
        self.assertEqual(len(sub_recs), 1, sub_recs)

    def test_fable_5_1_allowed_on_the_allowlist(self):
        # #894: claude-fable-5-1 is now the allowed Fable tier (revises #871).
        panes = [("%p", "/e")]
        find = self._fake_find({"/e": "claude-fable-5-1"})
        read = lambda p: "claude-fable-5-1"  # noqa: E731
        recs = cli_model_audit.audit_model_floats(panes, "/proj", find, read,
                                                  subagent_iter=lambda m: [])
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0]["banned"], recs[0])

    def test_fable_5_0_flagged_as_off_allowlist(self):
        # #894: claude-fable-5 (5.0) is retired from the lineup.
        panes = [("%p", "/e")]
        find = self._fake_find({"/e": "claude-fable-5"})
        read = lambda p: "claude-fable-5"  # noqa: E731
        recs = cli_model_audit.audit_model_floats(panes, "/proj", find, read,
                                                  subagent_iter=lambda m: [])
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["banned"], recs[0])


class TestSubagentRecencyWindow(TestCase):
    """#871 RED: a live pane's `subagents/` dir accumulates EVERY
    subagent ever dispatched in that session's whole lifetime (weeks) --
    `_subagent_transcripts` must consider only those written within
    `cli_model_audit.MODEL_AUDIT_SUBAGENT_RECENCY_S` seconds of now. A
    long-dead historical subagent (banned model, from before the ban even
    existed) is not a live FLOAT risk and must not be reported."""

    def _session_dir(self, tmpdir):
        import os as _os
        main_path = _os.path.join(tmpdir, "sess.jsonl")
        subdir = _os.path.join(tmpdir, "sess", "subagents")
        os_makedirs = _os.makedirs
        os_makedirs(subdir, exist_ok=True)
        return main_path, subdir

    def test_old_subagent_excluded_only_recent_reported(self):
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as d:
            main_path, subdir = self._session_dir(d)
            _write_transcript(Path(main_path), "claude-sonnet-5")
            recent = os.path.join(subdir, "recent.jsonl")
            old = os.path.join(subdir, "old.jsonl")
            _write_transcript(Path(recent), "claude-fable-5-1")  # BANNED
            _write_transcript(Path(old), "claude-fable-5-1")     # BANNED, stale
            now = time.time()
            os.utime(recent, (now, now))
            three_days_ago = now - 3 * 86400
            os.utime(old, (three_days_ago, three_days_ago))

            panes = [("%p", "/proj")]
            find = lambda pd, cwd: (main_path, 0.0)  # noqa: E731
            recs = cli_model_audit.audit_model_floats(
                panes, "/proj", find, transcript_last_assistant_model)
            sub_recs = [r for r in recs if r["kind"] == "sub"]
            self.assertEqual(len(sub_recs), 1, sub_recs)
            self.assertEqual(sub_recs[0]["transcript"], recent)
            self.assertTrue(sub_recs[0]["banned"])

    def test_subagent_transcripts_helper_filters_by_mtime(self):
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as d:
            main_path, subdir = self._session_dir(d)
            recent = os.path.join(subdir, "recent.jsonl")
            old = os.path.join(subdir, "old.jsonl")
            Path(recent).write_text("{}\n", encoding="utf-8")
            Path(old).write_text("{}\n", encoding="utf-8")
            now = time.time()
            os.utime(recent, (now, now))
            os.utime(old, (now - 3 * 86400, now - 3 * 86400))

            out = cli_model_audit._subagent_transcripts(main_path, now=now)
            self.assertEqual(out, [recent])


class TestAuditTolerantPredicate(TestCase):
    """#871 adversarial review 🔴3a: airuleset.is_banned_model_for_audit
    tolerates a served dated-snapshot id for an allowlisted tier -- the
    dispatch-surface airuleset.is_banned_model stays exact and unaffected."""

    def test_dated_haiku_allowed_by_audit_predicate(self):
        self.assertFalse(
            airuleset.is_banned_model_for_audit("claude-haiku-4-5-20251001"))

    def test_dated_haiku_still_banned_by_exact_dispatch_predicate(self):
        # the DISPATCH-surface predicate is unaffected -- still exact.
        self.assertTrue(
            airuleset.is_banned_model("claude-haiku-4-5-20251001"))

    def test_fable_5_1_still_banned_by_audit_predicate(self):
        self.assertTrue(airuleset.is_banned_model_for_audit("claude-fable-5-1"))

    def test_bare_alias_still_banned_by_audit_predicate(self):
        self.assertTrue(airuleset.is_banned_model_for_audit("fable"))

    def test_every_allowlisted_id_clears_audit_predicate(self):
        for ok in airuleset.MODEL_TIERS.values():
            self.assertFalse(airuleset.is_banned_model_for_audit(ok), ok)

    def test_empty_value_not_banned_by_audit_predicate(self):
        self.assertFalse(airuleset.is_banned_model_for_audit(""))


if __name__ == "__main__":
    main()
