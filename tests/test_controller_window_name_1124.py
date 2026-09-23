"""#1124 (window part) — the controller's tmux window is named `ar`, like
every other single-session box, and its `session-created` hook no longer
collides with the #660 owner audit.

Before this ticket:
  * the window-name block (#554/#592/#593) rendered only for
    `is_single_session_box_user` (subdev streams + gk). The controller's
    `airuleset` account was deliberately kept out of that predicate by #985,
    because it attaches the OWNER's `zbynek` session, so the window stayed
    `claude` with automatic-rename on;
  * `cli_aliases.short_target_alias("airuleset", "airuleset")` returned
    `airulese` (an 8-char truncation), not the webterm tab id `ar`;
  * BOTH managed `session-created` writers (the window-name hook and the #660
    owner audit) emitted an UNINDEXED `set-hook -g session-created`. An
    unindexed set CLEARS the whole hook array (live-verified on a private `-L`
    tmux 3.7b socket), so on any box carrying both, the later writer silently
    deleted the other.

The fix (ROZHODNUTÉ, issuecomment-5795729398, option 1):
  * a dedicated `is_window_name_eligible(user)` = `is_single_session_box_user`
    OR the #985 controller gate (the SAME gate `_is_ssh_attach_eligible` uses);
    ONLY the window-name applier switches to it. `_owner_session_default` and
    the #660 audit keep `is_single_session_box_user` unchanged;
  * ONE hook-index registry in `cli_tmux_provisioning`: each managed
    `session-created` writer owns a fixed index, used by the conf line, the
    live-apply AND the `-gu` revert (which unsets ONLY its own index);
  * the `ar` alias comes from the single `cli_aliases` source.

Dependency-injected fake `run` + tmp conf/home; the box class is mocked through
the `cli_bashrc_appliers.default_box_class` seam. No real tmux here (the
real-socket proof lives in test_controller_window_name_tmux_1124.py).
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli_aliases
import cli_bashrc_appliers as bash_appliers
import cli_tmux_provisioning as tmuxprov


class _Rec:
    """Records argv. `list-windows` answers with ONE window so the live
    rename path is exercised; everything else succeeds with no output."""

    def __init__(self, windows="@1\t/home/x\n"):
        self.calls = []
        self._windows = windows

    def __call__(self, argv):
        self.calls.append(list(argv))
        out = self._windows if "list-windows" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    def hook_calls(self):
        return [c for c in self.calls
                if "set-hook" in c
                and any(a.startswith("session-created") for a in c)]


def _box(cls):
    return mock.patch("cli_bashrc_appliers.default_box_class",
                      return_value=cls)


def _no_marker():
    # hermetic: never read this box's own #1060 model-backend marker file
    return mock.patch("cli_tmux_provisioning._hook_marker", return_value=None)


def _tmp_conf(content="# existing content\n"):
    d = Path(tempfile.mkdtemp())
    p = d / ".tmux.conf"
    p.write_text(content)
    return p, d


class TestControllerAlias(unittest.TestCase):
    def test_controller_box_alias_is_ar(self):
        self.assertEqual(
            cli_aliases.short_target_alias("airuleset", "airuleset"), "ar")

    def test_controller_alias_is_keyed_on_the_owner_account_only(self):
        # `claudy` is a SECOND unix account on the same controller box; its
        # own tab is `claudy`, so the box-keyed `ar` must not swallow it.
        self.assertEqual(
            cli_aliases.short_target_alias("claudy", "airuleset"), "claudy")

    def test_other_aliases_unchanged(self):
        self.assertEqual(cli_aliases.short_target_alias(None, "ar"), "ar")
        self.assertEqual(cli_aliases.short_target_alias("newlevel", "dev1"),
                         "dev1")
        self.assertEqual(cli_aliases.short_target_alias("newlevel", "dev2"),
                         "dev2")
        self.assertEqual(
            cli_aliases.short_target_alias("gatekeeper", "gatekeeper-cx23"),
            "gk")


class TestWindowNameEligibility(unittest.TestCase):
    def test_controller_user_on_controller_box_is_eligible(self):
        with _box("controller"):
            self.assertTrue(bash_appliers.is_window_name_eligible("airuleset"))

    def test_controller_user_off_the_controller_is_not_eligible(self):
        for cls in ("workstation", "shared-stream", None):
            with _box(cls):
                self.assertFalse(
                    bash_appliers.is_window_name_eligible("airuleset"), cls)

    def test_owner_newlevel_is_never_eligible(self):
        # #593 lock: dev1/dev2 are multi-project boxes.
        for cls in ("controller", "workstation", None):
            with _box(cls):
                self.assertFalse(
                    bash_appliers.is_window_name_eligible("newlevel"), cls)

    def test_single_session_users_stay_eligible(self):
        with _box("shared-stream"):
            self.assertTrue(bash_appliers.is_window_name_eligible("montalu2"))
            self.assertTrue(
                bash_appliers.is_window_name_eligible("gatekeeper"))
            self.assertFalse(
                bash_appliers.is_window_name_eligible("dominika"))

    def test_sibling_consumers_unchanged_for_the_controller_user(self):
        # #985 lock: only the window-name applier widens. The owner-session
        # default stays the owner group, never `airuleset`.
        from cli_webterm import OWNER_GROUP
        with _box("controller"):
            self.assertFalse(
                bash_appliers.is_single_session_box_user("airuleset"))
            self.assertEqual(
                bash_appliers._owner_session_default("airuleset"), OWNER_GROUP)


class TestControllerWindowBlock(unittest.TestCase):
    def test_controller_renders_ar_with_automatic_rename_off(self):
        p, _ = _tmp_conf()
        rec = _Rec()
        with _box("controller"), _no_marker():
            changed = tmuxprov.apply_stream_tmux_window_name(
                p, user="airuleset", host="airuleset", run=rec)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn(tmuxprov.STREAM_TMUX_WINDOW_MARK_START, text)
        self.assertIn("set-option -gw automatic-rename off", text)
        idx = tmuxprov.SESSION_CREATED_HOOK_INDEX["window-name"]
        self.assertIn('set-hook -g session-created[%d] "rename-window ar"'
                      % idx, text)
        # live-apply: same index, and the existing window renamed to `ar`
        self.assertIn(["tmux", "set-hook", "-g", "session-created[%d]" % idx,
                       "rename-window ar"], rec.calls)
        self.assertIn(["tmux", "rename-window", "-t", "@1", "ar"], rec.calls)

    def test_controller_user_on_a_workstation_gets_no_block(self):
        p, _ = _tmp_conf()
        with _box("workstation"), _no_marker():
            changed = tmuxprov.apply_stream_tmux_window_name(
                p, user="airuleset", host="dev1", run=_Rec())
        self.assertFalse(changed)
        self.assertNotIn(tmuxprov.STREAM_TMUX_WINDOW_MARK_START, p.read_text())

    def test_dev1_dev2_newlevel_render_nothing_on_any_box_class(self):
        # #593 regression lock, also under a mocked controller class.
        for cls in ("controller", "workstation"):
            for box in ("dev1", "dev2"):
                p, _ = _tmp_conf()
                with _box(cls), _no_marker():
                    changed = tmuxprov.apply_stream_tmux_window_name(
                        p, user="newlevel", host=box, run=_Rec())
                self.assertFalse(changed, (cls, box))
                text = p.read_text()
                self.assertNotIn(tmuxprov.STREAM_TMUX_WINDOW_MARK_START, text)
                self.assertNotIn("automatic-rename off", text)
                self.assertNotIn("session-created", text)

    def test_owner_box_revert_unsets_only_its_own_index(self):
        rec = _Rec(windows="")
        p, _ = _tmp_conf()
        with _box("workstation"), _no_marker():
            tmuxprov.apply_stream_tmux_window_name(
                p, user="newlevel", host="dev1", run=rec)
        idx = tmuxprov.SESSION_CREATED_HOOK_INDEX["window-name"]
        self.assertIn(["tmux", "set-hook", "-gu", "session-created[%d]" % idx],
                      rec.calls)
        self.assertNotIn(["tmux", "set-hook", "-gu", "session-created"],
                         rec.calls)


class TestOwnerAuditIndex(unittest.TestCase):
    def _apply_audit(self, user, cls):
        p, home = _tmp_conf()
        rec = _Rec()
        with _box(cls):
            tmuxprov.apply_owner_session_created_audit(
                p, user=user, run=rec, home=home)
        return p.read_text(), rec

    def test_audit_renders_and_live_applies_at_its_own_index(self):
        idx = tmuxprov.SESSION_CREATED_HOOK_INDEX["owner-audit"]
        for user, cls in (("newlevel", "workstation"),
                          ("airuleset", "controller")):
            text, rec = self._apply_audit(user, cls)
            self.assertIn(tmuxprov.OWNER_AUDIT_MARK_START, text, user)
            self.assertIn("set-hook -g session-created[%d] '" % idx, text,
                          user)
            live = rec.hook_calls()
            self.assertEqual(len(live), 1, user)
            self.assertEqual(live[0][:4],
                             ["tmux", "set-hook", "-g",
                              "session-created[%d]" % idx], user)

    def test_audit_still_stripped_on_single_session_boxes(self):
        for user in ("gatekeeper", "montalu2"):
            text, rec = self._apply_audit(user, "shared-stream")
            self.assertNotIn(tmuxprov.OWNER_AUDIT_MARK_START, text, user)
            self.assertEqual(rec.hook_calls(), [], user)


class TestNoUnindexedSessionCreatedWriter(unittest.TestCase):
    # `set-hook` with any flag cluster, then `session-created` NOT followed by
    # an index bracket == the array-clearing shape this ticket bans.
    _UNINDEXED = re.compile(r"set-hook\s+-\w+\s+session-created(?!\[)")

    def test_registry_gives_each_writer_a_distinct_index(self):
        reg = tmuxprov.SESSION_CREATED_HOOK_INDEX
        self.assertEqual(set(reg), {"window-name", "owner-audit"})
        self.assertEqual(len(set(reg.values())), len(reg))

    def test_no_rendered_conf_or_live_argv_is_unindexed(self):
        boxes = (("airuleset", "airuleset", "controller"),
                 ("newlevel", "dev1", "workstation"),
                 ("newlevel", "dev2", "workstation"),
                 ("gatekeeper", "gatekeeper-cx23", "workstation"),
                 ("montalu2", "subdev", "shared-stream"))
        for user, host, cls in boxes:
            p, home = _tmp_conf("")
            rec = _Rec()
            with _box(cls), _no_marker():
                # the install order: window-name first, then the audit
                tmuxprov.apply_stream_tmux_window_name(
                    p, user=user, host=host, run=rec)
                tmuxprov.apply_owner_session_created_audit(
                    p, user=user, run=rec, home=home)
            text = p.read_text()
            conf_lines = [ln for ln in text.splitlines()
                          if not ln.lstrip().startswith("#")]
            for ln in conf_lines:
                self.assertIsNone(self._UNINDEXED.search(ln), (user, ln))
            for argv in rec.hook_calls():
                name = argv[3]
                self.assertRegex(name, r"^session-created\[\d+\]$",
                                 (user, argv))

    def test_controller_conf_carries_both_writers_at_distinct_indexes(self):
        p, home = _tmp_conf("")
        with _box("controller"), _no_marker():
            tmuxprov.apply_stream_tmux_window_name(
                p, user="airuleset", host="airuleset", run=_Rec())
            tmuxprov.apply_owner_session_created_audit(
                p, user="airuleset", run=_Rec(), home=home)
        text = p.read_text()
        reg = tmuxprov.SESSION_CREATED_HOOK_INDEX
        self.assertIn("session-created[%d] \"rename-window ar\""
                      % reg["window-name"], text)
        self.assertIn("session-created[%d] 'run-shell -b" % reg["owner-audit"],
                      text)


if __name__ == "__main__":
    unittest.main()
