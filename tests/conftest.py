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
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import watchdog as wd


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
    App-token branch (`tests/test_authority_profiles.py`'s own
    `TestSliceQualsHandlesAppTokenBoxes`) still wins with its own,
    innermost `mock.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": ...})` —
    identical composability to `_isolate_draft_rescue` above."""
    with TemporaryDirectory() as d:
        missing = Path(d) / "gh-app-tokens"    # never .mkdir()'d — the point
        with mock.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(missing)}):
            yield missing


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
