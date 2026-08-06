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
