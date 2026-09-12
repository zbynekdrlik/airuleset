"""#992 req 4 / #993 item 5 — the coordinator's design/review READ of an infra
unit's files is allowed by ONE documented allowlist rule in
hooks/block-main-implementation.sh (`_is_infra_review_read`), replacing the
per-read one-shot bypass marker.

A Fable MAIN doing design/review of an infra unit must be able to read the
unit's files (many at once, or a recursive grep over an infra dir) without arming
a marker each time — that IS the coordinator's job (#992). The exception is
scoped to INFRA paths (hooks/, .github/, modules/, skills/, agents/, watchdog/,
scripts/, tests/, airuleset.py, cli_*.py, …) and to READ-ONLY commands only;
a bulk read whose paths are not all infra stays blocked, and the exception never
touches a non-Fable main. (`git diff/show` are not gated by this hook at all —
it only gates bulk-read command names — so no git handling is needed here.)
"""

import json
import os
import subprocess
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "block-main-implementation.sh"


def _transcript(model="claude-fable-5-1"):
    return json.dumps({"type": "assistant", "message": {
        "role": "assistant", "model": model,
        "content": [{"type": "text", "text": "x"}]}}) + "\n"


def _run(command, model="claude-fable-5-1"):
    sid = "t-infra-" + uuid.uuid4().hex[:8]
    with TemporaryDirectory() as d:
        tp = str(Path(d) / "s.jsonl")
        Path(tp).write_text(_transcript(model))
        payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command},
                   "transcript_path": tp}
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              env=dict(os.environ), capture_output=True, text=True)


class TestInfraReviewReadAllowed(TestCase):
    def test_cat_three_infra_files_is_allowed(self):
        # 3 files exceeds the narrow-readonly 2-file cap → blocked without the
        # infra exception.
        r = _run("cat hooks/a.sh hooks/b.sh hooks/c.sh")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cat_many_infra_areas_is_allowed(self):
        r = _run("cat modules/core/x.md skills/y/SKILL.md agents/z.md watchdog/w.py")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_recursive_grep_over_an_infra_dir_is_allowed(self):
        # grep -r is blocked by narrow-readonly and is_blocked_segment; the
        # infra exception allows it when the search path is an infra dir.
        r = _run("grep -rn WDRAIN hooks/")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cat_top_level_airuleset_infra_py_is_allowed(self):
        r = _run("cat airuleset.py cli_quals_cmd.py design_gate.py")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestNonInfraStillBlocked(TestCase):
    def test_cat_three_non_infra_files_is_blocked(self):
        r = _run("cat src/one.py src/two.py src/three.py")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_mixed_infra_and_non_infra_paths_is_blocked(self):
        # 3 files (past the narrow-readonly cap), not ALL infra → not exempt.
        r = _run("cat hooks/a.sh hooks/b.sh src/app.py")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_recursive_grep_over_a_non_infra_dir_is_blocked(self):
        r = _run("grep -rn foo src/")
        self.assertEqual(r.returncode, 2, r.stdout)

    # --- #993-review 🔴/🟡: writes disguised as infra reads must stay blocked ---
    def test_sed_in_place_ni_flag_is_blocked(self):
        r = _run("sed -ni 's/a/b/p' hooks/x.sh")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_sed_long_in_place_is_blocked(self):
        r = _run("sed --in-place 's/a/b/' hooks/x.sh")
        self.assertEqual(r.returncode, 2, r.stdout)

    # NB: `sed -n 's/…/w file' hooks/x.sh` is a PRE-EXISTING `_is_narrow_readonly_953`
    # (#953) write hole — base c63617d8 also allows it (rc 0), so it is NOT a
    # regression of this diff (my `_is_infra_review_read` rejects it as defence
    # in depth). Recorded as a followup_candidate for the narrow-readonly gate.

    def test_cat_infra_with_amp_redirect_to_non_infra_is_blocked(self):
        r = _run("cat hooks/a.sh hooks/b.sh hooks/c.sh &> cli_out.py")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_path_traversal_out_of_infra_is_blocked(self):
        r = _run("cat hooks/../../../../etc/passwd hooks/a.sh hooks/b.sh")
        self.assertEqual(r.returncode, 2, r.stdout)


class TestInfraReadEdgeCasesAllowed(TestCase):
    def test_sed_n_print_range_over_infra_is_allowed(self):
        r = _run("sed -n '10,40p' hooks/big-hook.sh")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dotfile_infra_dirs_are_recognised(self):
        # `.claude`/`.github` must match (the old lstrip('./') ate the dot)
        r = _run("cat .claude/rules/a.md .claude/rules/b.md .github/workflows/c.yml")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestNonFableMainUntouched(TestCase):
    def test_opus_main_is_not_gated_at_all(self):
        r = _run("cat src/one.py src/two.py src/three.py", model="claude-opus-4-8")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    main()
