"""pytest-only test isolation, applied to EVERY test in this suite —
including `unittest.TestCase` classes (pytest applies autouse fixtures
there too).

`_isolate_draft_rescue` (#271, adversarial-review MINOR finding):
`deliver_with_stash`/`_send_goal_verified` persist non-empty input-box
content to `watchdog.draft_rescue_dir()` (default `~/.claude/draft-rescue/`)
BEFORE any keystroke — unconditionally, on every real call, not only when a
test explicitly patches it. `airuleset.py`'s `cmd_push` already points the
WHOLE `python -m unittest discover -s tests` run (the actual push gate) at a
throwaway directory via `AIRULESET_DRAFT_RESCUE_DIR` — but `conftest.py` is
NOT read by `unittest discover` at all, only by `pytest`, so a developer
running `python -m pytest tests/test_X.py` standalone (a normal, sanctioned
dev-time workflow per this repo's own `.claude/rules/airuleset-internals.md`)
bypasses that env-var injection entirely. Measured on the real suite
(adversarial review, #271): 9 test files whose fixtures transitively reach
these two primitives via `run_once`/`bounce_backstop`/`gk_request_backstop`/
`deliver_discord_replies`/etc. produce 43 real writes into the developer's
ACTUAL `~/.claude/draft-rescue/` when run this way, with filenames and
content indistinguishable from a genuinely rescued draft for the full
14-day TTL. This autouse fixture closes that gap for every `pytest` run
without auditing (or trusting a per-class opt-in on) all ~19 affected files
individually — it costs nothing for a test that never reaches either
primitive, and it composes cleanly with any test's OWN more specific
`unittest.mock.patch.object(wd, "draft_rescue_dir", ...)` (whichever patch
is innermost simply wins for its own scope, exactly like any other nested
`mock.patch`)."""
import contextlib
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import watchdog as wd


@contextlib.contextmanager
def _per_run_tempdir(base=None):
    """#548 CORE: redirect the process's tempfile machinery into ONE throwaway
    per-run directory, then remove that directory WHOLESALE on exit.

    Root cause it fixes: the suite has ~459 raw `tempfile.mkdtemp()`/`mkstemp()`
    call sites (many without cleanup — the #385 lock-litter class), which leaked
    465k `/tmp/tmp*` dirs on dev1 (ext4 htree ENOSPC + inode pressure). Setting
    BOTH `tempfile.tempdir` (in-process — the module global `gettempdir()`
    returns, so every raw mkdtemp lands here even after `gettempdir()` cached)
    AND `$TMPDIR` (for any subprocess a test spawns) catches EVERY call site
    without editing a single one, and the wholesale `rmtree` on exit means the
    whole run's litter is gone.

    Composability: the function-scoped isolation fixtures below use their own
    `TemporaryDirectory()`, which simply nests inside this dir and cleans up as
    before; a test that patches `$TMPDIR`/`tempfile.tempdir` itself still wins
    for its own (inner) scope. The per-run dir is named `airuleset-pytest-run-*`
    so that a KILLED run's one leaked dir is itself reaped by the #548
    airuleset-* reaper (>3d) — the redirect narrows the leak to one self-healing
    directory instead of thousands.

    Exposed as a plain context manager (not only the fixture) so it is unit-
    testable directly — the fixture below is a thin session-scoped wrapper."""
    orig_tempdir = tempfile.tempdir
    orig_env = os.environ.get("TMPDIR")
    root = base if base is not None else tempfile.gettempdir()
    run_dir = Path(root) / ("airuleset-pytest-run-" + uuid.uuid4().hex)
    run_dir.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(run_dir)
    os.environ["TMPDIR"] = str(run_dir)
    try:
        yield run_dir
    finally:
        tempfile.tempdir = orig_tempdir
        if orig_env is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = orig_env
        shutil.rmtree(run_dir, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _redirect_tempdir_per_run():
    """#548 CORE fixture — wraps `_per_run_tempdir` at SESSION scope so it is
    established BEFORE any function-scoped fixture or test, and torn down (dir
    removed) LAST, after every function-scoped `TemporaryDirectory` nested
    inside it has already cleaned up. The `cmd_push` `unittest discover` gate
    never reads conftest.py, so it gets the identical redirect via its own
    `test_env["TMPDIR"]` (#385 dual-coverage)."""
    with _per_run_tempdir() as run_dir:
        yield run_dir


@pytest.fixture(autouse=True)
def _isolate_draft_rescue():
    with TemporaryDirectory() as d:
        rescue_dir = Path(d) / "draft-rescue"
        with mock.patch.object(wd, "draft_rescue_dir", return_value=rescue_dir):
            yield rescue_dir


@pytest.fixture(autouse=True)
def _isolate_gh_app_token_dir():
    """`airuleset._is_gh_app_token_box()` (#356) reads `GH_APP_TOKEN_DIR`
    (falling back to `~/.config/gh-app-tokens/`) with NO test-only bypass —
    it is a plain, always-live directory-presence check, same shape as
    `draft_rescue_dir()` above and for the SAME reason it needs isolating
    here: a `pytest`-direct run (not `cmd_push`'s own `unittest discover`,
    which airuleset.py never touches this variable for either) inherits
    the REAL process environment/`$HOME` unmodified. On a genuinely
    App-token-authenticated box (david2/david3/david4, odoo-erp#3281/#3282)
    that directory really exists — a fresh-context adversarial review of
    #356 reproduced it live (a fake `$HOME` with the real directory
    present) and found 3 of `TestSliceQualsRefusesAnUnresolvableIdentity`'s
    own PRE-EXISTING tests fail outright, since they assume `_slice_quals`
    still calls `_gh_login()` unconditionally.

    Point `GH_APP_TOKEN_DIR` at a path inside a throwaway directory that is
    deliberately NEVER created — `.is_dir()` on a genuinely missing path is
    exactly "not an App-token box", the correct default for every test that
    never mentions the mechanism. A test that DOES want to exercise the
    App-token branch (`tests/test_authority_slice_quals.py`'s own
    `TestSliceQualsHandlesAppTokenBoxes`) still wins with its own,
    innermost `mock.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": ...})` —
    identical composability to `_isolate_draft_rescue` above."""
    with TemporaryDirectory() as d:
        missing = Path(d) / "gh-app-tokens"    # never .mkdir()'d — the point
        with mock.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(missing)}):
            yield missing


@pytest.fixture(autouse=True)
def _isolate_session_status_dir():
    """#486 G1: the session heartbeat producer (`watchdog/session_status.py`),
    invoked by the notify-discord-pending / notify-compact-subagent-boundary /
    session-start-fetch hooks, writes `~/.claude/session-status/<sid>.json` via
    `AIRULESET_SESSION_STATUS_DIR` (falling back to the real `~/.claude`). A
    `pytest`-direct run that exercises any of those hooks would otherwise
    scatter heartbeat files into the developer's REAL home (measured: 49 files
    from the hook-behaviour subset alone) — same class as the draft-rescue /
    gh-app-token / autopilot-lock leaks isolated above. Point the dir at a
    throwaway location; a test that wants a specific one still wins with its own
    innermost `monkeypatch.setenv`/`base_dir=` (identical composability)."""
    with TemporaryDirectory() as d:
        target = Path(d) / "session-status"
        with mock.patch.dict(os.environ,
                             {"AIRULESET_SESSION_STATUS_DIR": str(target)}):
            yield target


@pytest.fixture(autouse=True)
def _isolate_content_dedup_store():
    """#687: `notify.content_dedup_claim` (the ✅ cross-session dedup) writes its
    claim markers to a SHARED sticky store — by default `tempfile.gettempdir()/
    airuleset-notify-content-dedup` (deliberately NOT under $HOME, since it must
    coalesce across separate unix accounts). Its KEY is deliberately SHARED for
    an identical payload, so a per-run dir does NOT isolate it: two tests that
    drive the ✅ send-hook with the SAME owner+project+text within the window
    would coalesce cross-test (the exact failure in test_notify_delivery_log's
    ✅ tests). Point it at a fresh PER-TEST dir so every test's ✅ path is a
    clean slate; a test that needs a specific store still wins with its own
    innermost `store_dir=`/`AIRULESET_CONTENT_DEDUP_DIR`. `mock.patch.dict` (a
    real os.environ entry) so a SUBPROCESS the test spawns (the shell send-hook
    → `notify --content-dedup-claim`) inherits it. The push-gate `unittest
    discover` (which never reads this conftest) gets a per-run floor in
    `cmd_push`; colliding TestCase files ALSO set it per-test in their own setUp
    (dual-runner, the #385 pattern)."""
    with TemporaryDirectory() as d:
        target = Path(d) / "content-dedup"
        with mock.patch.dict(os.environ,
                             {"AIRULESET_CONTENT_DEDUP_DIR": str(target)}):
            yield target


@pytest.fixture(autouse=True)
def _isolate_goal_roster():
    """#804: `watchdog.roster` (the durable expected-armed register) writes to
    `~/.claude/goal-roster.json` by default. A goal_lane_sweep integration test
    that upserts an armed pane / logs a DEAD-SESSION would otherwise write the
    developer's REAL home. Point it at a fresh per-test file via the same
    `AIRULESET_GOAL_ROSTER_PATH` env seam the module reads; `mock.patch.dict` (a
    real os.environ entry) so any subprocess inherits it. The push-gate
    `unittest discover` gets its own floor in `cmd_push`."""
    with TemporaryDirectory() as d:
        # #804 mode-5: force the resurrect ACTION flag deterministically OFF for
        # every goal test (so the RED mode-5 test never keystrokes because a dev
        # box happened to have the opt-in flag set); a test that needs it ON
        # overrides with its own nested patch.dict.
        with mock.patch.dict(os.environ,
                             {"AIRULESET_GOAL_ROSTER_PATH":
                              str(Path(d) / "goal-roster.json"),
                              "AIRULESET_RESURRECT_ACTION": "",
                              # #858 re-review: cadence job owns its state file;
                              # isolate it so a test never writes the developer's
                              # real ~/.claude/mdreview-cadence.json.
                              "AIRULESET_MDREVIEW_STATE_PATH":
                              str(Path(d) / "mdreview-cadence.json")}):
            yield


@pytest.fixture(autouse=True)
def _ignore_owner_kill_switch(monkeypatch):
    """#400: the owner kill-switch flag files under the REAL ~/.claude are
    production state; a direct pytest run on a box with them set must not
    have watchdog compact/goal jobs read as disabled."""
    monkeypatch.setenv("AIRULESET_TEST_IGNORE_DISABLE", "1")


@pytest.fixture(autouse=True)
def _isolate_autopilot_lock_dir(monkeypatch):
    """(#385) `airuleset._autopilot_lock_path()` reads
    `AIRULESET_AUTOPILOT_LOCK_DIR` (falling back to the real system tempdir)
    with no other test-only bypass. `tests/test_autopilot_lock.py`'s own
    `TestAcquireRelease`/`TestDirectoryShapedLockPath` classes spawn a REAL
    `autopilot-lock` CLI SUBPROCESS on every test — never an in-process call
    — against a fresh `tempfile.mkdtemp()` repo path that is never reused, so
    without this the lock (plus its `.mutex` sibling, plus any symlink or
    directory-shaped artifact the directory-shaped tests create) lands in the
    REAL system `/tmp` on every single test run, forever (measured live:
    thousands of leaked artifacts accumulated over weeks before this
    existed). `monkeypatch.setenv` (not `mock.patch.dict`) is required here,
    not merely conventional — the value must be a REAL `os.environ` entry so
    a SUBPROCESS spawned via `subprocess.run(..., env=dict(os.environ))`
    (exactly what `test_autopilot_lock.py`'s own `run()` helper does) sees
    it; `cmd_push`'s own pre-push `unittest discover` subprocess (which this
    `conftest.py` is NOT read by — pytest-only) gets the identical env var
    injected directly in `cmd_push` itself, mirroring `_isolate_draft_rescue`
    above."""
    with TemporaryDirectory() as d:
        lock_dir = Path(d) / "autopilot-lock"
        monkeypatch.setenv("AIRULESET_AUTOPILOT_LOCK_DIR", str(lock_dir))
        yield lock_dir
