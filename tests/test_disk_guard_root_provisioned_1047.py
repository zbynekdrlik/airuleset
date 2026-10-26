"""#1047: the disk-guard distinguishes 'root guard NEVER provisioned on this box'
from 'root timer dead', and `status` surfaces the provisioned state.

The `ROOT-REPORT-STALE … the root timer may be dead (see #841)` alarm is emitted
by `watchdog/disk_guard_root.py::_warn_once_per_day` via `maybe_record_root_finding`,
called from the CRITICAL escalate branch of `watchdog/disk_guard.py::run_disk_guard`.
On a box where the root guard was NEVER provisioned (no `cli_disk_guard_root.
ROOT_TIMER_PATH` — the owner workstations, #841), that alarm is a FALSE
'may be dead'. Fix (Option A, in-lane): gate the recorder CALL on
`_root_guard_provisioned()` — not provisioned → an HONEST once/day 'not
provisioned' line and SKIP the call (no dead-timer text, no stale-warn marker);
provisioned → the dead-timer path byte-identical.

All I/O is a temp HOME or an injected `exists_fn` — no real /run report, no real
/etc unit, no ping.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from watchdog import disk_guard as dg          # noqa: E402
from watchdog import disk_guard_root as r      # noqa: E402


def _statvfs(used_pct):
    class S:
        f_blocks = 1000
        f_bfree = int(1000 * (100 - used_pct) / 100)
        f_bavail = int(1000 * (100 - used_pct) / 100)
        f_files = 1000
        f_ffree = 900
    return lambda _p: S()


_CRIT_KW = dict(dev_fn=lambda p: 1, geteuid_fn=lambda: 1000, mounts=("/",),
                planners_fn=lambda _h, _n: [("noop", lambda: [])],
                top_consumers_fn=lambda *a, **k: [])


# --------------------------------------------------------------------------- #
# Point 1a — the provisioned predicate (timer-unit presence)
# --------------------------------------------------------------------------- #
def test_predicate_absent_timer_is_false():
    assert dg._root_guard_provisioned(
        timer_path="/nope/airuleset-disk-guard-root.timer",
        exists_fn=lambda p: False) is False


def test_predicate_present_timer_is_true():
    assert dg._root_guard_provisioned(
        timer_path="/etc/systemd/system/airuleset-disk-guard-root.timer",
        exists_fn=lambda p: True) is True


def test_predicate_defaults_to_root_timer_path_constant():
    seen = {}

    def _exists(p):
        seen["path"] = p
        return True

    assert dg._root_guard_provisioned(exists_fn=_exists) is True
    from cli_disk_guard_root import ROOT_TIMER_PATH
    assert seen["path"] == ROOT_TIMER_PATH


def test_predicate_never_raises_on_broken_exists_fn():
    def _boom(_p):
        raise OSError("nope")
    assert dg._root_guard_provisioned(timer_path="/x", exists_fn=_boom) is False


def test_fallback_timer_path_is_drift_locked_to_the_real_constant():
    # review 🟡: the lazy-import fallback literal MUST equal the real constant,
    # else a box whose `cli_disk_guard_root` import fails probes a stale path and
    # falsely reports 'not provisioned'.
    from cli_disk_guard_root import ROOT_TIMER_PATH
    assert dg._ROOT_TIMER_PATH_FALLBACK == ROOT_TIMER_PATH


def test_predicate_uses_fallback_when_import_fails(monkeypatch):
    # Force the lazy import to raise; the predicate must probe the FALLBACK path
    # (drift-locked above) and honour exists_fn's verdict on it.
    import builtins
    seen = {}
    real_import = builtins.__import__

    def _no_cli(name, *a, **k):
        if name == "cli_disk_guard_root":
            raise ImportError("forced")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_cli)

    def _exists(p):
        seen["path"] = p
        return True

    assert dg._root_guard_provisioned(exists_fn=_exists) is True
    assert seen["path"] == dg._ROOT_TIMER_PATH_FALLBACK


# --------------------------------------------------------------------------- #
# Point 1b — the once/day 'not provisioned' warn helper
# --------------------------------------------------------------------------- #
def test_not_provisioned_warn_logs_the_exact_line_once():
    with tempfile.TemporaryDirectory() as td:
        now = 1_000_000
        l1 = dg._warn_not_provisioned_once_per_day(td, now)
        assert len(l1) == 1
        line = l1[0]
        assert "not provisioned on this box" in line
        assert "ROOT_TIMER_PATH" in line
        assert "root-level survey skipped" in line
        assert "may be dead" not in line
        # same day → deduped (once per day)
        assert dg._warn_not_provisioned_once_per_day(td, now + 60) == []


def test_not_provisioned_warn_uses_distinct_marker():
    with tempfile.TemporaryDirectory() as td:
        dg._warn_not_provisioned_once_per_day(td, 1_000_000)
        gd = dg._guard_dir(td)
        assert (gd / "root-not-provisioned-warn").exists()
        # must NOT collide with the dead-timer stale-warn marker
        assert not (gd / r.STALE_WARN_MARKER_NAME).exists()


def test_not_provisioned_warn_writes_to_disk_guard_log():
    with tempfile.TemporaryDirectory() as td:
        dg._warn_not_provisioned_once_per_day(td, 1_000_000)
        log = Path(dg._log_path(td))
        assert log.exists() and "not provisioned on this box" in log.read_text()


def test_not_provisioned_warn_refires_next_day():
    with tempfile.TemporaryDirectory() as td:
        now = 1_000_000
        assert len(dg._warn_not_provisioned_once_per_day(td, now)) == 1
        assert len(dg._warn_not_provisioned_once_per_day(td, now + 86400 + 100)) == 1


# --------------------------------------------------------------------------- #
# Point 1c — the wiring at the CRITICAL escalate branch
# --------------------------------------------------------------------------- #
def test_critical_not_provisioned_skips_recorder_and_warns(monkeypatch):
    calls = []
    monkeypatch.setattr(dg, "_root_guard_provisioned", lambda: False, raising=False)
    monkeypatch.setattr(r, "maybe_record_root_finding",
                        lambda *a, **k: calls.append((a, k)) or ["X ROOT-REPORT-STALE may be dead"])
    with tempfile.TemporaryDirectory() as td:
        logs = dg.run_disk_guard(now=1_000_000, home=td, dry_run=False,
                                 statvfs_fn=_statvfs(92), **_CRIT_KW)
    assert calls == [], "recorder must NOT run when root guard is not provisioned"
    assert any("not provisioned on this box" in ln for ln in logs)
    assert not any("ROOT-REPORT-STALE" in ln for ln in logs)


def test_critical_not_provisioned_line_once_per_day_across_polls(monkeypatch):
    monkeypatch.setattr(dg, "_root_guard_provisioned", lambda: False, raising=False)
    monkeypatch.setattr(r, "maybe_record_root_finding", lambda *a, **k: [])
    with tempfile.TemporaryDirectory() as td:
        l1 = dg.run_disk_guard(now=1_000_000, home=td, dry_run=False,
                               statvfs_fn=_statvfs(92), **_CRIT_KW)
        l2 = dg.run_disk_guard(now=1_000_060, home=td, dry_run=False,
                               statvfs_fn=_statvfs(92), **_CRIT_KW)
    assert sum("not provisioned on this box" in ln for ln in l1) == 1
    assert sum("not provisioned on this box" in ln for ln in l2) == 0


def test_critical_provisioned_calls_recorder_byte_identically(monkeypatch):
    calls = []
    monkeypatch.setattr(dg, "_root_guard_provisioned", lambda: True, raising=False)
    monkeypatch.setattr(r, "maybe_record_root_finding",
                        lambda *a, **k: calls.append((a, k)) or ["... ROOT-REPORT-STALE ... may be dead"])
    with tempfile.TemporaryDirectory() as td:
        logs = dg.run_disk_guard(now=1_000_000, home=td, dry_run=False,
                                 statvfs_fn=_statvfs(92), **_CRIT_KW)
    assert len(calls) == 1, "recorder MUST run byte-identically when provisioned"
    a, k = calls[0]
    assert a[1] == td and a[2] == 1_000_000 and k.get("dry_run") is False
    assert not any("not provisioned on this box" in ln for ln in logs)
    assert any("ROOT-REPORT-STALE" in ln for ln in logs)


def test_critical_provisioned_recorder_error_is_caught_and_logged(monkeypatch):
    # review nit: force the recorder to RAISE — the escalate branch must catch it
    # and log the preserved error line, never propagate (the guard is best-effort).
    monkeypatch.setattr(dg, "_root_guard_provisioned", lambda: True, raising=False)

    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(r, "maybe_record_root_finding", _boom)
    with tempfile.TemporaryDirectory() as td:
        logs = dg.run_disk_guard(now=1_000_000, home=td, dry_run=False,
                                 statvfs_fn=_statvfs(92), **_CRIT_KW)
    assert any("root-finding error" in ln and "kaboom" in ln for ln in logs)


def test_notice_level_neither_warns_nor_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(dg, "_root_guard_provisioned", lambda: False, raising=False)
    monkeypatch.setattr(r, "maybe_record_root_finding",
                        lambda *a, **k: calls.append(a) or [])
    with tempfile.TemporaryDirectory() as td:
        logs = dg.run_disk_guard(now=1_000_000, home=td, dry_run=True,
                                 statvfs_fn=_statvfs(78), dev_fn=lambda p: 1,
                                 geteuid_fn=lambda: 1000, mounts=("/",))
    assert calls == []
    assert not any("not provisioned on this box" in ln for ln in logs)


# --------------------------------------------------------------------------- #
# Point 2 — the `status` row (rendered in the watchdog leaf, printed by cmd_status)
# --------------------------------------------------------------------------- #
def test_status_row_provisioned():
    assert dg.root_guard_status_row(provisioned_fn=lambda: True) == "root disk-guard: provisioned"


def test_status_row_not_provisioned():
    assert dg.root_guard_status_row(provisioned_fn=lambda: False) == "root disk-guard: not provisioned"


def test_status_row_defaults_to_the_predicate():
    # default provisioned_fn is `_root_guard_provisioned`; in this env the timer
    # unit is absent, so the row reads 'not provisioned' (never raises).
    row = dg.root_guard_status_row()
    assert row in ("root disk-guard: provisioned", "root disk-guard: not provisioned")


# --------------------------------------------------------------------------- #
# Point 3 — the per-box report-only `root_guard_provisioned` fact in the
# conformance Job 34 report (watchdog/conformance.py), from the SAME predicate.
# --------------------------------------------------------------------------- #
from watchdog import conformance as conf          # noqa: E402


def _conf_fake_git(head="aaaa1111", origin="aaaa1111"):
    def g(args, cwd, timeout=None):
        sub = args[0]
        if sub == "fetch":
            return (0, "")
        if sub == "rev-parse":
            return (0, (head if args[-1] == "HEAD" else origin) + "\n")
        if sub == "merge-base":
            return (0, "")
        if sub == "status":
            return (0, "")
        return (0, "")
    return g


def _run_conf(state, tmp, provisioned_fn, dry_run=False):
    cmd = str(Path(tmp) / "CLAUDE.md")
    Path(cmd).write_text("managed\n")
    base = str(Path(tmp) / conf.CONFORMANCE_BASELINE_NAME)
    Path(base).write_text('{"claude_md_md5": %r, "head_sha": "aaaa1111"}'
                          % conf._md5_file(cmd))
    return conf.run_conformance_check(
        1_000_000, state, dry_run=dry_run, repo_root=tmp,
        claude_md_path=cmd, baseline_path=base,
        git_run=_conf_fake_git(), timer_check=lambda unit=None: "active",
        is_target_check=lambda: True, symlink_scan=lambda: [],
        doctrine_scan=lambda: {"high": 0, "medium": 0},
        persist=lambda: None, root_guard_provisioned_fn=provisioned_fn)


def test_conformance_check_records_provisioned_true():
    with tempfile.TemporaryDirectory() as td:
        state = {}
        _run_conf(state, td, provisioned_fn=lambda: True)
        assert state.get("root_guard_provisioned") is True


def test_conformance_check_records_provisioned_false():
    with tempfile.TemporaryDirectory() as td:
        state = {}
        _run_conf(state, td, provisioned_fn=lambda: False)
        assert state.get("root_guard_provisioned") is False


def test_conformance_check_uses_the_injected_predicate():
    called = {"n": 0}

    def _fn():
        called["n"] += 1
        return True

    with tempfile.TemporaryDirectory() as td:
        _run_conf({}, td, provisioned_fn=_fn)
    assert called["n"] == 1, "the conformance job must call the provisioned predicate exactly once"


def test_conformance_check_dry_run_does_not_persist_the_fact():
    with tempfile.TemporaryDirectory() as td:
        state = {}
        _run_conf(state, td, provisioned_fn=lambda: True, dry_run=True)
        assert "root_guard_provisioned" not in state


def test_conformance_check_default_predicate_is_the_disk_guard_one():
    # No injected fn -> defaults to disk_guard._root_guard_provisioned (import it,
    # never a second timer check). HERMETIC (review 🟡): assert the recorded fact
    # EQUALS the disk_guard predicate's own verdict on THIS box, never a hardcoded
    # False (a box that HAS provisioned the root guard is a legitimate fleet state).
    with tempfile.TemporaryDirectory() as td:
        state = {}
        conf.run_conformance_check(
            1_000_000, state, dry_run=False, repo_root=td,
            claude_md_path=str(Path(td) / "C.md"),
            baseline_path=str(Path(td) / conf.CONFORMANCE_BASELINE_NAME),
            git_run=_conf_fake_git(), timer_check=lambda unit=None: "active",
            is_target_check=lambda: True, symlink_scan=lambda: [],
            doctrine_scan=lambda: {"high": 0, "medium": 0}, persist=lambda: None)
        assert state.get("root_guard_provisioned") is dg._root_guard_provisioned()


def test_conformance_status_row_shows_provisioned():
    row = conf.conformance_status_row(
        {"conformance_last_check": 1, "root_guard_provisioned": True})
    assert "root disk-guard: provisioned" in row


def test_conformance_status_row_shows_not_provisioned():
    row = conf.conformance_status_row(
        {"conformance_last_check": 1, "root_guard_provisioned": False})
    assert "root disk-guard: not provisioned" in row


def test_conformance_status_row_absent_field_has_no_root_suffix():
    row = conf.conformance_status_row({"conformance_last_check": 1})
    assert "root disk-guard" not in row


def test_conformance_status_row_suffix_on_drift_branch_and_not_a_drift_dim():
    # review 🟡: the suffix must ride the DRIFT branch too, with a DISTINCT
    # delimiter so it is never mistaken for a drift dimension.
    row = conf.conformance_status_row(
        {"conformance": {"timer": {"detail": "dead"}},
         "conformance_last_check": 1, "root_guard_provisioned": False})
    assert "DRIFT" in row and "timer: dead" in row
    # a DISTINCT ` · ` delimiter, never the `; ` the drift dims use (review 🔵)
    assert " · root disk-guard: not provisioned" in row
    assert "; root disk-guard:" not in row
    # the report-only fact must NOT render as a drift dimension (no `key: drift`)
    assert "root_guard_provisioned:" not in row


def test_conformance_status_row_suffix_on_not_yet_checked_branch():
    row = conf.conformance_status_row({"root_guard_provisioned": True})
    assert "not yet checked" in row
    assert "root disk-guard: provisioned" in row
