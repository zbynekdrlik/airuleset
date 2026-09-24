"""#1136 part A — watchdog Job 40's owner-actionable disk escalation goes to
the box's OWN infra lane, and fires on the #925 exhausted state too.

Owner ruling (issuecomment-5822220077): disk problems on gk belong to
gk-infra, never to the gk review/FLOW window. So on a box whose fleet
declaration (`cli_fleet.REMOTE_HOSTS[...]["windows"]`, #998) has a window with
role `infra` and a repo checkout, `file_severe_ticket` files into that repo
with label `infra`. Every other box keeps the airuleset repo through the
existing `gk-request` path. It files at >= 95 % (as before) AND when the drain
reported `drain_exhausted` at >= 90 % (#925's owner-actionable state), deduped
by the #896-899 durable state, never through Discord (#693/#850).

Every test injects a `run_fn` recorder. The autouse fixture below turns any
REAL `subprocess.run` of `gh` / `gk-request` into a test failure (the
#896-899 duplicate-ticket incident), and any network open into one too.
"""

import subprocess
import types
import urllib.request

import pytest

import cli_fleet
import watchdog.disk_guard as dg
import watchdog.disk_guard_escalation as esc


GK_WINDOWS = cli_fleet.box_windows("gatekeeper")


@pytest.fixture(autouse=True)
def _no_real_gh_no_network(monkeypatch):
    real_run = subprocess.run

    def _guarded_run(argv, *a, **kw):
        if isinstance(argv, (list, tuple)) and argv and (
                str(argv[0]) == "gh" or any(str(x) == "gk-request" for x in argv)):
            raise AssertionError(
                "TEST REACHED THE REAL subprocess.run WITH A FILING ARGV (%r) "
                "-- inject run_fn (the #896-899 duplicate-ticket incident)"
                % (argv,))
        return real_run(argv, *a, **kw)

    def _no_network(*a, **kw):
        raise AssertionError("#693/#850: the disk escalation must never "
                             "open a network connection (Discord) itself")

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)


class _Recorder:
    """A `run_fn` recorder: `gh issue create` prints the new issue URL, every
    other call succeeds with empty output (or the configured failures)."""

    def __init__(self, create_rc=0, edit_rc=0,
                 url="https://github.com/zbynekdrlik/odoo-erp/issues/7777"):
        self.calls = []
        self.create_rc = create_rc
        self.edit_rc = edit_rc
        self.url = url

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if "create" in argv:
            return types.SimpleNamespace(
                returncode=self.create_rc,
                stdout=self.url if self.create_rc == 0 else "",
                stderr="" if self.create_rc == 0 else "boom")
        if "edit" in argv:
            return types.SimpleNamespace(
                returncode=self.edit_rc, stdout="",
                stderr="" if self.edit_rc == 0 else "label denied")
        return types.SimpleNamespace(returncode=0, stdout="filed", stderr="")


def _exhausted(pct):
    return {"worst_pct": pct, "dim": "bytes", "drain_exhausted": True,
            "drain_skipped_rungs": [
                {"rung": "docker-image", "cls": "docker-image",
                 "path": "/var/lib/docker", "reason": "SKIP-CONTAINERS-RUNNING"}]}


# --------------------------------------------------------------------------- #
# target resolution — the fleet declaration decides, one identity lookup
# --------------------------------------------------------------------------- #
def test_gk_declaration_routes_to_odoo_erp_infra():
    assert esc.resolve_target(GK_WINDOWS) == ("zbynekdrlik/odoo-erp", "infra")


def test_box_without_infra_window_keeps_airuleset():
    assert esc.resolve_target([]) == ("zbynekdrlik/airuleset", None)
    # a stream box that declares a window, but no infra role (miva1/david*)
    stream = cli_fleet.box_windows("miva1")
    assert stream, "fixture: miva1 declares a window"
    assert esc.resolve_target(stream) == ("zbynekdrlik/airuleset", None)
    # an infra role with NO repo checkout has nowhere to file → fallback
    assert esc.resolve_target([{"name": "x", "cwd": "devel/x", "role": "infra"}]) == (
        "zbynekdrlik/airuleset", None)


def test_unsafe_declared_repo_falls_back_to_airuleset():
    """The declared repo is baked into a `-R` argv: a flag-shaped value
    must never reach gh (fail safe to the fallback, never drop the ticket)."""
    bad = [{"name": "i", "cwd": "devel/i", "role": "infra", "repo": "--evil"}]
    assert esc.resolve_target(bad) == ("zbynekdrlik/airuleset", None)


def test_own_windows_uses_the_box_windows_lookup(monkeypatch):
    """No second identity lookup: this box's windows come from
    `cli_fleet.box_windows(cli_concurrency._current_user())`."""
    import cli_concurrency
    monkeypatch.setattr(cli_concurrency, "_current_user", lambda: "gatekeeper")
    assert esc.own_windows() == GK_WINDOWS
    monkeypatch.setattr(cli_concurrency, "_current_user", lambda: "")
    assert esc.own_windows() == []


# --------------------------------------------------------------------------- #
# filing — gk goes to the infra lane, everyone else keeps gk-request
# --------------------------------------------------------------------------- #
def test_gk_files_into_odoo_erp_with_infra_label(tmp_path):
    rec = _Recorder()
    logs = dg.file_severe_ticket(_exhausted(91), str(tmp_path), 5000.0,
                                 [("/var/lib/docker", 4_300_000_000)],
                                 dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    assert any("SEVERE-TICKET" in ln for ln in logs)
    assert not any("FAIL" in ln for ln in logs), logs
    create, edit = rec.calls
    assert create[:3] == ["gh", "issue", "create"]
    assert create[create.index("-R") + 1] == "zbynekdrlik/odoo-erp"
    assert "--label" not in create, "#221: the label is never baked into create"
    assert edit[:4] == ["gh", "issue", "edit", "7777"]
    assert edit[edit.index("-R") + 1] == "zbynekdrlik/odoo-erp"
    assert edit[edit.index("--add-label") + 1] == "infra"
    assert not any("gk-request" in c for c in rec.calls), (
        "gk-request adds needs-gatekeeper → the review/FLOW window; the owner "
        "ruling keeps disk work out of it")
    assert not any("needs-gatekeeper" in c for c in rec.calls)


def test_gk_body_says_what_the_drain_could_not_free_and_whose_it_is(tmp_path):
    rec = _Recorder()
    dg.file_severe_ticket(_exhausted(91), str(tmp_path), 5000.0,
                          [("/var/lib/docker", 4_300_000_000)],
                          dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    create = rec.calls[0]
    title = create[create.index("--title") + 1]
    body = create[create.index("--body") + 1]
    assert "91%" in title
    assert "gk-infra" in body, "the body names the infra window that owns it"
    assert "/var/lib/docker" in body, "top consumers = what is left to free"
    assert "SKIP-CONTAINERS-RUNNING" in body, (
        "the rungs the drain skipped = what it could not free, and why")


def test_box_without_infra_window_keeps_gk_request_to_airuleset(tmp_path):
    rec = _Recorder()
    dg.file_severe_ticket(_exhausted(91), str(tmp_path), 5000.0, [],
                          dry_run=False, run_fn=rec, windows=[])
    (argv,) = rec.calls
    assert "gk-request" in argv
    assert argv[argv.index("--repo") + 1] == "zbynekdrlik/airuleset"


def test_label_failure_is_logged_and_still_dedupes(tmp_path):
    """The ticket exists once create succeeded: a denied label is logged
    loudly, and the dedupe still holds (a re-file would be a duplicate)."""
    rec = _Recorder(edit_rc=1)
    logs = dg.file_severe_ticket(_exhausted(92), str(tmp_path), 5000.0, [],
                                 dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    assert any("SEVERE-TICKET-FAIL" in ln and "infra" in ln for ln in logs), logs
    rec2 = _Recorder()
    assert dg.file_severe_ticket(_exhausted(92), str(tmp_path), 5060.0, [],
                                 dry_run=False, run_fn=rec2,
                                 windows=GK_WINDOWS) == []
    assert rec2.calls == []


def test_create_failure_does_not_mark_filed(tmp_path):
    """#895 F5: a failed create never suppresses the next poll's retry."""
    rec = _Recorder(create_rc=1)
    logs = dg.file_severe_ticket(_exhausted(92), str(tmp_path), 5000.0, [],
                                 dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    assert any("SEVERE-TICKET-FAIL" in ln for ln in logs), logs
    assert len(rec.calls) == 1, "no label edit without a created issue"
    rec2 = _Recorder()
    dg.file_severe_ticket(_exhausted(92), str(tmp_path), 5060.0, [],
                          dry_run=False, run_fn=rec2, windows=GK_WINDOWS)
    assert rec2.calls and rec2.calls[0][:3] == ["gh", "issue", "create"]


# --------------------------------------------------------------------------- #
# trigger — the #925 exhausted state at >= 90 % files, once
# --------------------------------------------------------------------------- #
def test_exhausted_at_90_files_once_and_dedupes(tmp_path):
    rec = _Recorder()
    logs1 = dg.file_severe_ticket(_exhausted(90), str(tmp_path), 5000.0, [],
                                  dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    assert logs1 and len(rec.calls) == 2
    logs2 = dg.file_severe_ticket(_exhausted(93), str(tmp_path), 5000.0 + 3600,
                                  [], dry_run=False, run_fn=rec,
                                  windows=GK_WINDOWS)
    assert logs2 == [], "a second call inside the dedupe window files nothing"
    assert len(rec.calls) == 2


def test_below_90_files_nothing_even_when_exhausted(tmp_path):
    rec = _Recorder()
    assert dg.file_severe_ticket(_exhausted(89), str(tmp_path), 5000.0, [],
                                 dry_run=False, run_fn=rec,
                                 windows=GK_WINDOWS) == []
    assert rec.calls == []


def test_90_to_94_without_exhausted_files_nothing(tmp_path):
    """The drain still has room: not owner-actionable, not a ticket."""
    rec = _Recorder()
    for flag in (False, None):
        st = {"worst_pct": 93, "dim": "bytes", "drain_exhausted": flag}
        assert dg.file_severe_ticket(st, str(tmp_path), 5000.0, [],
                                     dry_run=False, run_fn=rec,
                                     windows=GK_WINDOWS) == []
    assert rec.calls == []


def test_95_still_files_without_exhausted(tmp_path):
    rec = _Recorder()
    dg.file_severe_ticket({"worst_pct": 96, "dim": "bytes"}, str(tmp_path),
                          5000.0, [], dry_run=False, run_fn=rec,
                          windows=GK_WINDOWS)
    assert rec.calls and rec.calls[0][:3] == ["gh", "issue", "create"]


def test_after_drain_files_on_exhausted_below_95(tmp_path, monkeypatch):
    """End to end through `run_disk_guard`: a 91 % small disk whose only rung
    is skipped → `drain_exhausted` → the filer fires (the old 95 % gate in
    `disk_guard_post.after_drain` would have swallowed it)."""
    monkeypatch.setattr(esc, "own_windows", lambda: GK_WINDOWS)
    d = tmp_path / ".claude" / "disk-guard"
    d.mkdir(parents=True, exist_ok=True)
    (d / "last-drain").write_text("0")

    def _skipping_planner():
        return [{"cls": "docker-image", "path": "/var/lib/docker",
                 "bytes": 500_000_000, "kind": "skip",
                 "reason": "SKIP-CONTAINERS-RUNNING"}]

    rec = _Recorder()
    dg.run_disk_guard(
        now=1000.0, home=str(tmp_path), dry_run=False,
        statvfs_fn=lambda _m: types.SimpleNamespace(
            f_blocks=1000, f_bfree=90, f_bavail=90,
            f_frsize=4096, f_files=100000, f_ffree=50000),
        dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000,
        planners_fn=lambda _h, _n: [("docker-image", _skipping_planner)],
        severe_run_fn=rec, top_consumers_fn=lambda *a, **kw: [])
    creates = [c for c in rec.calls if c[:3] == ["gh", "issue", "create"]]
    assert len(creates) == 1, rec.calls
    assert creates[0][creates[0].index("-R") + 1] == "zbynekdrlik/odoo-erp"


# --------------------------------------------------------------------------- #
# no Discord path (#693/#850)
# --------------------------------------------------------------------------- #
def test_no_discord_path(tmp_path):
    """Every filing goes through the injected run_fn (the autouse fixture
    fails any real subprocess/network), and no argv is a notify call."""
    for windows in (GK_WINDOWS, []):
        rec = _Recorder()
        dg.file_severe_ticket(_exhausted(91), str(tmp_path / str(len(windows))),
                              5000.0, [], dry_run=False, run_fn=rec,
                              windows=windows)
        assert rec.calls
        for argv in rec.calls:
            joined = " ".join(argv)
            assert "notify" not in joined and "discord" not in joined.lower()


def test_escalation_module_never_imports_notify():
    import inspect
    src = inspect.getsource(esc)
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith(("import ", "from ")):
            assert "notify" not in s, s
