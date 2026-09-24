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
_REAL_DEFAULT_CRIT = getattr(esc, "default_critical_pct", None)  # pre-pin


def _is_filing_argv(argv):
    toks = [str(x) for x in argv] if isinstance(argv, (list, tuple)) else []
    return bool(toks) and ("gk-request" in toks or (
        toks[0] == "gh" and toks[1:2] == ["issue"]))


@pytest.fixture(autouse=True)
def _no_real_gh_no_network(monkeypatch):
    """RECORD a real filing/network attempt and fail at TEARDOWN: raising
    inside the call would be swallowed by the filer's own `except` (review
    round 1 of #1136 — exactly how 8 real tickets got filed)."""
    real_run = subprocess.run
    leaked = []

    def _guarded_run(argv, *a, **kw):
        if _is_filing_argv(argv):
            leaked.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, "", "blocked by test")
        return real_run(argv, *a, **kw)

    def _no_network(*a, **kw):
        leaked.append(("urlopen", a))
        raise OSError("blocked by test")

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    monkeypatch.setattr(dg, "_default_box_class", lambda: None)  # hermetic
    # a direct filer call with no critical_pct reads THIS box's disk size
    # (85 % small / 90 % large): pin it; after_drain passes it explicitly
    monkeypatch.setattr(esc, "default_critical_pct", lambda: 90, raising=False)
    yield leaked
    assert not leaked, "reached a REAL filer / network: %r" % (leaked,)


class _Recorder:
    """A `run_fn` recorder: `gh issue create` prints the new issue URL, every
    other call succeeds with empty output (or the configured failures)."""

    def __init__(self, create_rc=0, edit_rc=0,
                 url="https://github.com/zbynekdrlik/odoo-erp/issues/7777",
                 raise_on=None, view_state="OPEN", view_rc=0):
        self.calls = []
        self.create_rc = create_rc
        self.edit_rc = edit_rc
        self.url = url
        self.raise_on = raise_on
        self.view_state = view_state
        self.view_rc = view_rc

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if self.raise_on and self.raise_on in argv:
            raise subprocess.TimeoutExpired(argv, 60)
        if "view" in argv:
            return types.SimpleNamespace(returncode=self.view_rc,
                                         stdout=self.view_state, stderr="")
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


# --------------------------------------------------------------------------- #
# review round 1 (#1136): label atomic with create, exception-safe, open-ticket
# dedupe, shared-stream scope, pytest belt, markdown-safe body
# --------------------------------------------------------------------------- #
def test_create_carries_the_infra_label_and_edit_verifies_it(tmp_path):
    """The label rides on create (no unlabelled window the review arrival
    rider could see) AND the separate edit verifies it (#221: create drops a
    label it cannot apply without failing)."""
    rec = _Recorder()
    dg.file_severe_ticket(_exhausted(91), str(tmp_path), 5000.0, [],
                          dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    create, edit = rec.calls
    assert create[create.index("--label") + 1] == "infra"
    assert edit[edit.index("--add-label") + 1] == "infra"


def test_label_edit_raising_still_marks_filed(tmp_path):
    """An exception from the label step (a gh timeout) must not escape before
    the dedupe mark: the issue exists, a re-file would duplicate it."""
    rec = _Recorder(raise_on="edit")
    logs = dg.file_severe_ticket(_exhausted(91), str(tmp_path), 5000.0, [],
                                 dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    assert any("SEVERE-TICKET-FAIL" in ln for ln in logs), logs
    rec2 = _Recorder()
    assert dg.file_severe_ticket(_exhausted(91), str(tmp_path), 5060.0, [],
                                 dry_run=False, run_fn=rec2,
                                 windows=GK_WINDOWS) == []
    assert rec2.calls == []


def _after_window(tmp_path, view_state, view_rc=0):
    """File once, then poll again past the 24h window with the stored issue
    in ``view_state``; returns (logs, recorder of the second poll)."""
    home = str(tmp_path)
    dg.file_severe_ticket(_exhausted(91), home, 5000.0, [], dry_run=False,
                          run_fn=_Recorder(), windows=GK_WINDOWS)
    rec = _Recorder(view_state=view_state, view_rc=view_rc)
    later = 5000.0 + dg.SEVERE_TICKET_REFILE_S + 60
    logs = dg.file_severe_ticket(_exhausted(91), home, later, [],
                                 dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    return logs, rec


def test_still_open_ticket_is_not_filed_again_after_the_window(tmp_path):
    """Deduped against the OPEN ticket (owner plan A), not only 24h."""
    logs, rec = _after_window(tmp_path, "OPEN")
    assert [c[:3] for c in rec.calls] == [["gh", "issue", "view"]]
    view = rec.calls[0]
    assert view[3] == "7777"
    assert view[view.index("-R") + 1] == "zbynekdrlik/odoo-erp"
    assert any("still open" in ln for ln in logs), logs


def test_closed_ticket_is_filed_again_after_the_window(tmp_path):
    logs, rec = _after_window(tmp_path, "CLOSED")
    assert [c[:3] for c in rec.calls][1:2] == [["gh", "issue", "create"]]


def test_unreadable_ticket_state_files_again(tmp_path):
    """An unknown state never suppresses the escalation (fail toward filing)."""
    logs, rec = _after_window(tmp_path, "", view_rc=1)
    assert ["gh", "issue", "create"] in [c[:3] for c in rec.calls]


def test_shared_stream_box_does_not_file_the_exhausted_band(tmp_path):
    """One account's drain sees only its own data, and N stream accounts
    would file N tickets: the 90-94 exhausted band stays off there."""
    rec = _Recorder()
    assert dg.file_severe_ticket(_exhausted(92), str(tmp_path), 5000.0, [],
                                 dry_run=False, run_fn=rec, windows=[],
                                 box_class="shared-stream") == []
    assert rec.calls == []
    dg.file_severe_ticket(_exhausted(96), str(tmp_path), 5000.0, [],
                          dry_run=False, run_fn=rec, windows=[],
                          box_class="shared-stream")
    assert rec.calls, ">= 95 still files on a shared-stream box"


def test_default_filer_is_refused_under_pytest(tmp_path, _no_real_gh_no_network):
    """Belt for the #896-899 / #1144-1151 class: a test that forgets to inject
    run_fn gets a logged refusal, never a real ticket, and no dedupe mark."""
    logs = dg.file_severe_ticket(_exhausted(92), str(tmp_path), 5000.0, [],
                                 dry_run=False, windows=[])
    assert _no_real_gh_no_network == []
    assert any("SEVERE-TICKET-FAIL" in ln and "pytest" in ln for ln in logs), logs
    rec = _Recorder()
    dg.file_severe_ticket(_exhausted(92), str(tmp_path), 5010.0, [],
                          dry_run=False, run_fn=rec, windows=[])
    assert rec.calls, "a refused default filing must not mark the ticket filed"


def test_body_is_markdown_safe(tmp_path):
    """Paths/reasons are local strings: no backtick may break out of the code
    span (where @mentions and #refs would render)."""
    rec = _Recorder()
    st = _exhausted(91)
    st["drain_skipped_rungs"] = [{"rung": "x", "path": "/a`@owner", "reason": "b`#1"}]
    dg.file_severe_ticket(st, str(tmp_path), 5000.0, [("/c`@x", 1)],
                          dry_run=False, run_fn=rec, windows=GK_WINDOWS)
    body = rec.calls[0][rec.calls[0].index("--body") + 1]
    assert "`/a'@owner`" in body and "`b'#1`" in body and "`/c'@x`" in body


# --------------------------------------------------------------------------- #
# coordinator ruling (#1136): the exhausted floor is the box's OWN effective
# drain-critical level (`effective_critical_pct`: 85 % on a <= 64 GB root like
# gk, 90 % above), never a fixed 90 % — gk exhausted at 86 % must file
# --------------------------------------------------------------------------- #
def _guard_poll(tmp_path, monkeypatch, blocks, free):
    """One real `run_disk_guard` poll on a gk-declared box whose only rung is
    skipped; returns the `gh issue create` argvs it sent."""
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
            f_blocks=blocks, f_bfree=free, f_bavail=free,
            f_frsize=4096, f_files=100000, f_ffree=50000),
        dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000,
        planners_fn=lambda _h, _n: [("docker-image", _skipping_planner)],
        severe_run_fn=rec, top_consumers_fn=lambda *a, **kw: [])
    return [c for c in rec.calls if c[:3] == ["gh", "issue", "create"]]


def test_gk_small_disk_exhausted_at_86_files_once(tmp_path, monkeypatch):
    """The #1136 state itself: 38 GB-class root at 86 %, drain exhausted."""
    creates = _guard_poll(tmp_path, monkeypatch, blocks=1000, free=140)
    assert len(creates) == 1, creates
    assert creates[0][creates[0].index("-R") + 1] == "zbynekdrlik/odoo-erp"
    title = creates[0][creates[0].index("--title") + 1]
    assert "86%" in title and "drain exhausted" in title
    # a second poll inside the dedupe window files nothing
    assert _guard_poll(tmp_path, monkeypatch, blocks=1000, free=140) == []


def test_large_disk_at_86_files_nothing(tmp_path, monkeypatch):
    """A > 64 GB root keeps the 90 % critical level: 86 % is not exhausted."""
    assert _guard_poll(tmp_path, monkeypatch, blocks=20_000_000,
                       free=2_800_000) == []


def test_exhausted_floor_is_the_passed_critical_level(tmp_path):
    rec = _Recorder()
    assert dg.file_severe_ticket(_exhausted(84), str(tmp_path), 5000.0, [],
                                 dry_run=False, run_fn=rec, windows=GK_WINDOWS,
                                 critical_pct=85) == []
    assert rec.calls == []
    dg.file_severe_ticket(_exhausted(86), str(tmp_path), 5000.0, [],
                          dry_run=False, run_fn=rec, windows=GK_WINDOWS,
                          critical_pct=85)
    assert rec.calls and rec.calls[0][:3] == ["gh", "issue", "create"]
    rec2 = _Recorder()
    assert dg.file_severe_ticket(_exhausted(89), str(tmp_path / "b"), 5000.0,
                                 [], dry_run=False, run_fn=rec2,
                                 windows=GK_WINDOWS, critical_pct=90) == []
    assert rec2.calls == []


def test_default_floor_is_effective_critical_pct(monkeypatch):
    """No critical_pct → the box's own `effective_critical_pct` (the helper the
    `drain_exhausted` bookkeeping uses), never a fixed number."""
    import watchdog.disk_guard_worktrees as dgw
    assert _REAL_DEFAULT_CRIT is not None, "esc.default_critical_pct missing"
    for pct in (85, 90):
        monkeypatch.setattr(dgw, "effective_critical_pct", lambda *a, _p=pct, **k: _p)
        assert _REAL_DEFAULT_CRIT() == pct
    assert not hasattr(esc, "EXHAUSTED_FILE_PCT"), "no fixed 90 % floor left"
