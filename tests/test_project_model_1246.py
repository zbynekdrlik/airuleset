"""#1246 — an owner-declared per-PROJECT main model (pz-server@dev1 runs Opus 4.6)
must pass every #1203 model surface, and watchdog Job 54 must not crash.

The live incident (14.10.2026, dev1): the owner typed `/model claude-opus-4-6`
in the pz-server session and the #1203 PreToolUse gate blocked every tool call
(`block model claude-opus-4-6 …-devel-pz-server…`). Probing it showed Job 54
dying on every sweep: `find_marker(tpath, managed)` passed the model string as
`max_bytes` -> `TypeError`. The unit tests injected `lambda t, m=None`, so the
production seam was never run; the Job 54 tests below drive `run_job` itself
with the REAL `model_fallback.find_marker`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import airuleset  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_lineup  # noqa: E402
import statusbar  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402
from watchdog import idle_pane, model_restore as mr  # noqa: E402
from watchdog.send_outcome import SendOutcome  # noqa: E402

MANAGED = "claude-opus-5-5[1m]"
PZ = "claude-opus-4-6"
PZ_KEY = "-home-newlevel-devel-pz-server"
NOW = 1_800_000_000
HOOK = REPO / "hooks" / "block-model-fallback.sh"


def _assistant(model, content=None, uuid="a"):
    return {"type": "assistant", "isSidechain": False, "uuid": uuid,
            "timestamp": "2026-10-14T20:09:44Z",
            "message": {"model": model, "role": "assistant",
                        "content": content or [{"type": "text", "text": "ok"}]}}


def _marker(frm=PZ, to="claude-sonnet-5-5"):
    return _assistant(to, [{"type": "fallback", "from": {"model": frm},
                            "to": {"model": to}}], uuid="m1")


def _model_cmd(args):
    return {"type": "user", "isSidechain": False,
            "message": {"role": "user", "content":
                        "<command-name>/model</command-name>\n<command-message>model"
                        "</command-message>\n<command-args>%s</command-args>" % args}}


def _transcript(testcase, key, entries):
    """A transcript under a Claude Code project dir named `key`."""
    root = tempfile.mkdtemp(prefix="pm1246-")
    testcase.addCleanup(shutil.rmtree, root, True)
    d = os.path.join(root, key)
    os.makedirs(d)
    p = os.path.join(d, "sid1.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return p


class Resolver(unittest.TestCase):
    def test_pz_server_declares_opus_4_6(self):
        self.assertEqual(model_lineup.PROJECT_MAIN_MODELS.get("pz-server"), PZ)

    def test_pz_server_transcript_resolves_to_its_model_and_the_managed_one(self):
        t = "/home/newlevel/.claude/projects/%s/3b6d.jsonl" % PZ_KEY
        self.assertEqual(model_lineup.main_models_for_transcript(t), (PZ, (PZ, MANAGED)))

    def test_another_project_resolves_to_the_managed_model_only(self):
        t = "/home/newlevel/.claude/projects/-home-newlevel-devel-restreamer/x.jsonl"
        self.assertEqual(model_lineup.main_models_for_transcript(t), (MANAGED, (MANAGED,)))

    def test_a_longer_name_or_a_non_devel_parent_never_matches(self):
        for key in ("-home-newlevel-devel-foo-pz-server", "-home-x-notdevel-pz-server",
                    PZ_KEY + "-sub", ""):
            self.assertEqual(model_lineup.main_models(model_lineup.project_of_key(key)),
                             (MANAGED, (MANAGED,)), key)

    def test_a_launch_dir_encodes_like_claude_code(self):
        self.assertEqual(model_lineup.dir_key("/home/newlevel/devel/pz-server"), PZ_KEY)
        self.assertEqual(model_lineup.dir_key("/home/u/devel/web_app.ai"),
                         "-home-u-devel-web-app-ai")

    def test_a_path_object_resolves_like_its_string(self):
        # review A: watchdog.newest_transcript returns a pathlib.Path
        t = Path("/home/newlevel/.claude/projects/%s/3b6d.jsonl" % PZ_KEY)
        self.assertEqual(model_lineup.main_models_for_transcript(t), (PZ, (PZ, MANAGED)))

    def test_garbage_never_raises(self):
        for v in (None, 3, [], "x"):
            self.assertEqual(model_lineup.main_models_for_transcript(v), (MANAGED, (MANAGED,)))


class Verdict(unittest.TestCase):
    def _v(self, entries, key=PZ_KEY):
        p = _transcript(self, key, entries)
        target, allowed = model_lineup.main_models_for_transcript(p)
        return mf.verdict(mf.tail_state(p), target, allowed=allowed)

    def test_pz_server_on_its_model_is_on_lineup(self):
        self.assertIsNone(self._v([_assistant("claude-opus-5-5"), _model_cmd(PZ), _assistant(PZ)]))

    def test_pz_server_may_switch_back_to_the_managed_model(self):
        self.assertIsNone(self._v([_assistant(PZ), _assistant("claude-opus-5-5")]))
        self.assertIsNone(self._v([_assistant(PZ), _model_cmd(MANAGED)]))

    def test_a_third_model_in_pz_server_is_off_lineup_and_restores_to_its_model(self):
        v = self._v([_assistant("claude-sonnet-5-5")])
        self.assertEqual((v["kind"], v["managed"]), ("model", PZ))
        self.assertEqual(v["managed_short"], "opus4.6")


class GateDecide(unittest.TestCase):
    def setUp(self):
        from gates import modelfallback
        self.g = modelfallback

    def _decide(self, p):
        return self.g.decide(json.dumps({"tool_name": "Bash", "transcript_path": p}), env={})

    def test_pz_server_on_opus_4_6_is_allowed(self):
        p = _transcript(self, PZ_KEY, [_model_cmd(PZ), _assistant(PZ)])
        self.assertEqual(self._decide(p), (None, None, ""))

    def test_pz_server_on_the_managed_model_is_allowed(self):
        p = _transcript(self, PZ_KEY, [_assistant("claude-opus-5-5")])
        self.assertEqual(self._decide(p), (None, None, ""))

    def test_pz_server_on_a_third_model_is_blocked_naming_opus_4_6(self):
        p = _transcript(self, PZ_KEY, [_assistant("claude-sonnet-5-5")])
        msg, kind, _ = self._decide(p)
        self.assertEqual(kind, "block")
        self.assertIn("`%s`" % PZ, msg)
        self.assertIn("opus4.6", msg)

    def test_another_project_on_opus_4_6_is_still_blocked(self):
        p = _transcript(self, "-home-newlevel-devel-restreamer", [_assistant(PZ)])
        msg, kind, _ = self._decide(p)
        self.assertEqual(kind, "block")
        self.assertIn(MANAGED, msg)

    def test_a_proven_fallback_in_pz_server_still_blocks(self):
        p = _transcript(self, PZ_KEY, [_assistant(PZ), _marker()])
        msg, kind, _ = self._decide(p)
        self.assertEqual(kind, "block")
        self.assertIn("safeguard fallback", msg)

    def test_hook_end_to_end_allows_pz_server_on_opus_4_6(self):
        p = _transcript(self, PZ_KEY, [_assistant(PZ)])
        env = hermetic_hook_env(self)
        env.pop("AIRULESET_ROLE", None)
        env.pop("AIRULESET_MODEL_GUARD", None)
        r = subprocess.run(["bash", str(HOOK)], text=True, capture_output=True, env=env,
                           input=json.dumps({"tool_name": "Read", "transcript_path": p}),
                           timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))


class Footer(unittest.TestCase):
    GREEN = "\033[38;5;40mopus4.6\033[0m"

    def test_pz_server_on_opus_4_6_is_green(self):
        p = _transcript(self, PZ_KEY, [_assistant(PZ)])
        seg = statusbar.model_segment({"model": {"id": MANAGED}, "transcript_path": p},
                                      managed_model=MANAGED)
        self.assertEqual(seg, self.GREEN)

    def test_pz_server_cwd_without_a_transcript_is_green(self):
        for payload in ({"model": {"id": PZ},
                         "workspace": {"current_dir": "/home/newlevel/devel/pz-server"}},
                        {"model": {"id": PZ}, "cwd": "/home/newlevel/devel/pz-server"}):
            self.assertEqual(statusbar.model_segment(payload, managed_model=MANAGED),
                             self.GREEN, payload)

    def test_another_project_on_opus_4_6_stays_red(self):
        p = _transcript(self, "-home-newlevel-devel-restreamer", [_assistant(PZ)])
        seg = statusbar.model_segment({"model": {"id": PZ}, "transcript_path": p},
                                      managed_model=MANAGED)
        self.assertEqual(seg, "\033[38;5;196mopus4.6 FALLBACK\033[0m")


class Job54RunJob(unittest.TestCase):
    """`run_job` itself (the production wiring), with the REAL find_marker."""

    def _run(self, tpath, state=None):
        typed = []

        def deliver(pid, tp, text):
            typed.append(text)
            return SendOutcome("submitted")

        state = {} if state is None else state
        with unittest.mock.patch.object(idle_pane, "production_deps",
                                        lambda *a, **k: ({}, deliver, None)), \
                unittest.mock.patch.object(idle_pane, "pane_ready",
                                           lambda pid, cwd, kind, **d: (True, "", "sid1", tpath)), \
                unittest.mock.patch.object(mr, "newest_transcript",
                                           lambda pd, cwd, ex=None: tpath), \
                unittest.mock.patch.object(mr, "pane_is_implementer",
                                           lambda pid, run=None: False):
            logs = mr.run_job(NOW, state, [("%1", "/home/newlevel/devel/x")], run=None,
                              sleep_fn=lambda s: None, projects_dir="/nonexistent",
                              budget_left=lambda: 300)
        return logs, typed, state

    def test_an_off_lineup_pane_is_restored_without_a_type_error(self):
        p = _transcript(self, "-home-newlevel-devel-restreamer", [_assistant("claude-opus-4-8")])
        logs, typed, _ = self._run(p)
        self.assertEqual(typed, ["/model " + MANAGED], logs)

    def test_pz_server_on_opus_4_6_is_left_alone(self):
        p = _transcript(self, PZ_KEY, [_model_cmd(PZ), _assistant(PZ)])
        logs, typed, state = self._run(p)
        self.assertEqual(typed, [], logs)
        self.assertFalse(any("OFF-LINEUP" in ln or "FALLBACK" in ln for ln in logs), logs)
        self.assertEqual(state.get("model_restore"), {})

    def test_pz_server_on_a_third_model_is_restored_to_opus_4_6(self):
        p = _transcript(self, PZ_KEY, [_assistant("claude-sonnet-5-5")])
        logs, typed, state = self._run(p)
        self.assertEqual(typed, ["/model " + PZ], logs)
        with open(p, "a", encoding="utf-8") as fh:          # the switch, then a reply
            fh.write(json.dumps(_model_cmd(PZ)) + "\n" + json.dumps(_assistant(PZ)) + "\n")
        logs, _typed, state = self._run(p, state)
        self.assertTrue(state["model_restore"]["sid1"].get("restored_at"), logs)

    def test_the_wired_deep_scan_reads_a_real_transcript(self):
        p = _transcript(self, "-home-newlevel-devel-restreamer",
                        [_assistant("claude-opus-5-5"), _marker("claude-opus-5-5", "claude-opus-4-8"),
                         _assistant("claude-opus-4-8")])
        seen = {}

        def fake_restore_job(now, state, panes, **kw):
            seen.update(kw)
            return []

        with unittest.mock.patch.object(mr, "restore_job", fake_restore_job), \
                unittest.mock.patch.object(idle_pane, "production_deps",
                                           lambda *a, **k: ({}, None, None)):
            mr.run_job(NOW, {}, [], run=None, sleep_fn=None, projects_dir="/x")
        marker = seen["find_marker"](p, MANAGED)
        self.assertEqual((marker["from"], marker["to"]), ("claude-opus-5-5", "claude-opus-4-8"))


class Launcher(unittest.TestCase):
    def _run(self, project, fn="claude", conversation=False):
        home = tempfile.mkdtemp(prefix="pm1246-home-")
        self.addCleanup(shutil.rmtree, home, True)
        cwd = Path(home) / "devel" / project
        cwd.mkdir(parents=True)
        if conversation:
            enc = str(cwd).replace("/", "-").replace(".", "-").replace("_", "-")
            d = Path(home) / ".claude" / "projects" / enc
            d.mkdir(parents=True)
            (d / "s.jsonl").write_text("{}")
        bashrc = Path(home) / ".bashrc"
        cdir = Path(home) / ".claude"
        cdir.mkdir(exist_ok=True)
        airuleset.apply_ultracode_launcher(bashrc, cdir / "airuleset-claude-launch.sh",
                                           cdir / "airuleset-claude-history.py",
                                           cdir / "airuleset-claude-history-popup.sh")
        stub = Path(home) / "bin"
        stub.mkdir()
        (stub / "claude").write_text('#!/bin/bash\necho "ARGS:$*"\n')
        (stub / "claude").chmod(0o755)
        env = {**os.environ, "HOME": home, "PATH": "%s:%s" % (stub, os.environ["PATH"])}
        r = subprocess.run(["bash", "-c", "source '%s'; cd '%s'; %s" % (bashrc, cwd, fn)],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertIn("ARGS:", r.stdout, r.stderr)
        return r.stdout

    def test_pz_server_launches_on_opus_4_6(self):
        self.assertIn("--model %s " % PZ, self._run("pz-server"))

    def test_pz_server_resume_keeps_opus_4_6(self):
        out = self._run("pz-server", conversation=True)
        self.assertIn(" -c", out)
        self.assertIn("--model %s " % PZ, out)

    def test_pz_server_new_mode_too(self):
        self.assertIn("--model %s " % PZ, self._run("pz-server", fn="claude-new"))

    def test_another_project_keeps_the_managed_model(self):
        out = self._run("restreamer")
        self.assertIn("--model %s " % MANAGED, out)
        self.assertNotIn(PZ, out)

    def test_plain_mode_passes_no_model(self):
        self.assertNotIn("--model", self._run("pz-server", fn="claude-plain"))


class DesignGate(unittest.TestCase):
    def _check(self, slug, stamp):
        from gates import designdispatch
        return designdispatch.check_issue(5, slug, "/x", fetch=lambda s, n, c: [stamp])

    def test_pz_server_accepts_its_own_main_model(self):
        self.assertEqual(self._check("zbynekdrlik/pz-server", "Design-by: main %s" % PZ),
                         (True, "ok"))

    def test_another_repo_refuses_opus_4_6(self):
        ok, _why = self._check("zbynekdrlik/restreamer", "Design-by: main %s" % PZ)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
