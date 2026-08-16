"""#503 -- a worktree worker's FINISHED, committed work can be lost with its
worktree.

Root cause (traced against current HEAD): in `isolation: "worktree"` mode the
worker builds ALL its work as LOCAL commits and the contract says
"NEVER `push`" (`agents/autopilot-worker.md`); the supervisor merges from the
LOCAL branch ref via the shared `.git`, so origin is never written until
integration. The local branch ref survives `git worktree remove`, but NOT a
`.git` loss / branch-ref deletion / box re-clone (the ticket's case 2), and the
account-cap death that kills workers lands squarely in that window (the worker
dies at its final test-run / push step). Nothing puts the finished work
anywhere durable in that window.

Fix (convention, not new machinery): the worktree worker pushes a durability
BACKUP of its branch to origin after the FIRST commit and after every later
commit, to a dedicated CI-NEUTRAL ref namespace `refs/autopilot-wip/<branch>`.
A push to any ref OUTSIDE `refs/heads/*` and `refs/tags/*` triggers NO GitHub
Actions workflow in any repo (verified: GitHub docs + community #48858), so the
backup burns ZERO CI with no per-repo reasoning. The backup is never the merge
source (the supervisor still merges from the LOCAL branch ref) -- it is read
only on recovery. The supervisor deletes the backup ref after integrating the
branch; recovery fetches it when the local ref is also gone.

This file locks BOTH surfaces:
  * the prose convention across `agents/autopilot-worker.md` and
    `skills/autopilot/SKILL.md` -- #498/#500 statement/window teeth, each
    mutation-verified by hand (see the review comment on #503);
  * the FUNCTIONAL exemption of the three pre-push CI-protection hooks for a
    `refs/autopilot-wip/*` destination -- a real repo in a state that WOULD
    block a normal push, proving the WIP push is exempt while the normal push
    still blocks (this is the teeth: revert the exemption and the WIP-push
    assertions fail).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_MD = ROOT / "agents" / "autopilot-worker.md"
SKILL_MD = ROOT / "skills" / "autopilot" / "SKILL.md"
HOOKS = ROOT / "hooks"

# The dedicated CI-neutral backup ref namespace this whole ticket introduces.
BACKUP_NS = "refs/autopilot-wip/"


def _norm(text):
    """Collapse markdown wrapping so a phrase spanning a physical line-wrap
    still matches -- the established prose-lock technique (#307/#317/#500)."""
    return " ".join(text.split())


def _bullet_window(text, start_anchor):
    """The normalized window from `start_anchor` to the NEXT top-level
    markdown bullet (`\\n- `), i.e. exactly the operative bullet. `start_anchor`
    MUST be unique to that bullet (never a nearby why-prose line), per #498."""
    idx = text.index(start_anchor)
    nxt = text.find("\n- ", idx + len(start_anchor))
    if nxt == -1:
        nxt = idx + 1600
    return _norm(text[idx:nxt])


# --------------------------------------------------------------------------- #
# Prose convention -- worker
# --------------------------------------------------------------------------- #
class TestWorkerContractMandatesTheDurabilityBackup(unittest.TestCase):

    def setUp(self):
        self.text = WORKER_MD.read_text(encoding="utf-8")

    def test_the_literal_backup_push_command_is_documented(self):
        # The exact command a worker runs -- a strong single-token lock.
        self.assertIn("git push origin HEAD:refs/autopilot-wip/", self.text,
                      "the worker prompt must document the literal durability "
                      "backup push command (#503)")

    def test_the_backup_bullet_carries_all_load_bearing_tokens(self):
        # Bounded to the operative bullet, anchored on a phrase unique to it.
        w = _bullet_window(self.text, "durability BACKUP of your branch to origin")
        for tok in ("FIRST commit", "refs/heads", "refs/tags", "#503"):
            self.assertIn(tok, w,
                          "the durability-backup bullet is missing %r -- it "
                          "must state after the FIRST commit + the "
                          "refs/heads/refs/tags CI-neutral reasoning (#503)"
                          % tok)
        # CI-neutrality must be asserted, not merely implied.
        self.assertRegex(
            w, r"no GitHub Actions|NO GitHub Actions|ZERO CI|no CI|zero CI",
            "the backup bullet must state that the ref namespace triggers no "
            "GitHub Actions / burns zero CI (#503)")

    def test_backup_is_declared_not_the_merge_source(self):
        w = _bullet_window(self.text, "durability BACKUP of your branch to origin")
        self.assertRegex(
            w, r"LOCAL branch ref|local branch ref|read only on recovery|"
               r"recovery",
            "the backup bullet must state it is NOT the merge source (the "
            "supervisor still merges from the LOCAL branch ref; the backup is "
            "read only on recovery) -- #503")

    def test_never_push_invariant_carves_out_the_backup(self):
        # The old blanket "NEVER `push`" must be scoped to the INTEGRATION
        # flow, or a worker reads the new backup instruction as forbidden.
        self.assertIn("INTEGRATION flow", self.text,
                      "the 'NEVER push' invariant must be scoped to the "
                      "INTEGRATION flow so the durability backup is not read "
                      "as banned (#503)")

    def test_evidence_block_demands_the_exact_pushed_ref(self):
        # Case 1: the worktree DIRECTORY name and the branch name can differ,
        # so 'my branch' is not enough -- the exact pushed ref is required.
        # Lock this on the worktree-mode variant's branch: field.
        self.assertRegex(
            _norm(self.text),
            r"EXACT (?:branch/ref|ref|pushed ref)|exact (?:pushed )?ref name",
            "the evidence block must demand the EXACT pushed ref name, never "
            "'my branch' (#503 case 1)")

    def test_worktree_stop_point_notes_the_backup_is_the_one_allowed_push(self):
        marker = "Worktree-mode STOP POINT"
        self.assertIn(marker, self.text)
        idx = self.text.index(marker)
        window = _norm(self.text[idx:idx + 1600])
        self.assertIn("refs/autopilot-wip", window,
                      "the worktree-mode STOP POINT must carve out the "
                      "durability backup as the ONE push a worker does make "
                      "(#503)")

    def test_fork_no_merge_pushes_its_branch_early(self):
        # Case 1 was literally a fork-no-merge worker that pushed at the END.
        # Its fork branch IS the durable + hand-off copy, so it must push
        # after the FIRST commit, not at the end.
        w = _norm(self.text)
        # The fork-no-merge early-push guidance must exist and cite #503.
        m = w.find("fork branch")
        self.assertNotEqual(m, -1)
        self.assertRegex(
            w,
            r"fork branch[^.]*FIRST commit|after the FIRST commit[^.]*fork|"
            r"push (?:your )?fork branch (?:right )?after (?:your )?FIRST",
            "fork-no-merge must push its fork branch after the FIRST commit "
            "(not at the end) -- #503")


# --------------------------------------------------------------------------- #
# Prose convention -- supervisor (skill)
# --------------------------------------------------------------------------- #
class TestSupervisorCleansUpAndRecoversTheBackup(unittest.TestCase):

    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")

    def test_supervisor_deletes_the_backup_ref_after_integration(self):
        # The remote-litter cleanup: the exact delete command, near #503.
        self.assertIn("git push origin --delete refs/autopilot-wip/", self.text,
                      "the supervisor must delete the durability backup ref "
                      "after integrating the branch (#503)")

    def test_recovery_note_falls_back_to_the_origin_backup(self):
        # The #332 dead-worker branch-finding gains a fallback: if the LOCAL
        # branch ref is gone too, the commits still exist on origin at the
        # backup ref -- fetch it. Anchor on a phrase UNIQUE to that recovery
        # note (per #498) -- NOT a bare "#332", which also appears in the
        # unrelated no-fixed-cap section far above.
        anchor = "dead worker's branch is NOT self-discovering"
        self.assertIn(anchor, self.text,
                      "expected the #332 dead-worker recovery note")
        idx = self.text.index(anchor)
        window = _norm(self.text[idx:idx + 2600])
        self.assertIn("refs/autopilot-wip", window,
                      "the #332 recovery note must fall back to fetching the "
                      "origin backup ref when the local branch ref is also "
                      "gone (#503)")
        self.assertRegex(window, r"fetch",
                         "the recovery fallback must FETCH the backup ref "
                         "(#503)")


# --------------------------------------------------------------------------- #
# Functional -- pre-push hooks exempt the refs/autopilot-wip/* destination
# --------------------------------------------------------------------------- #
def _git(repo, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, env=env)


def _run_hook(hook, repo, command):
    payload = {"tool_input": {"command": command}}
    return subprocess.run(["bash", str(HOOKS / hook)], input=json.dumps(payload),
                          cwd=str(repo), capture_output=True, text=True,
                          env=dict(os.environ), timeout=30)


class TestPrePushLintExemptsBackupRef(unittest.TestCase):
    """A mid-work backup snapshot is legitimately lint-dirty; the CI-protection
    lint gate must not block a push to the backup ref (it triggers no CI)."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-503-lint-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (self.repo / "app.py").write_text("VALUE = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        # a genuine lint error (unused import) in the pushed change
        (self.repo / "app.py").write_text("import os\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "wip")

    def test_normal_push_still_blocks_on_lint(self):
        r = _run_hook("pre-push-lint.sh", self.repo, "git push origin main")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_backup_ref_push_is_exempt(self):
        r = _run_hook("pre-push-lint.sh", self.repo,
                      "git push origin HEAD:refs/autopilot-wip/worktree-agent-x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestPrePushTestCheckExemptsBackupRef(unittest.TestCase):
    """A mid-work backup can be a RED-before-GREEN intermediate; the
    RED->GREEN order gate must not block a push to the backup ref."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-503-tc-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "dev")
        (self.repo / "a.py").write_text("x = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        # a bug-fix commit with NO preceding test commit -> Gate 2 blocks
        (self.repo / "a.py").write_text("x = 2\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "fix: correct value\n\nCloses #1")

    def test_normal_push_still_blocks_on_red_before_green(self):
        r = _run_hook("pre-push-test-check.sh", self.repo, "git push origin dev")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_backup_ref_push_is_exempt(self):
        r = _run_hook("pre-push-test-check.sh", self.repo,
                      "git push origin HEAD:refs/autopilot-wip/worktree-agent-x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestPrePushBaseSyncExemptsBackupRef(unittest.TestCase):
    """A backup push to a fresh branch won't conflict in practice, but the
    exemption also skips the fetch+merge-tree overhead on every backup. Prove
    the exemption with a genuinely CONFLICTING state (teeth): the normal push
    blocks, the backup push is exempt."""

    def _base_repo(self):
        root = Path(tempfile.mkdtemp(prefix="airuleset-503-bs-"))
        self.addCleanup(shutil.rmtree, root, True)
        bare = root / "rem.git"
        _git(root, "init", "-q", "--bare", str(bare))
        repo = root / "repo"
        _git(root, "clone", "-q", str(bare), str(repo))
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        (repo / "shared").write_text("line1\nline2\nline3\n")
        _git(repo, "add", "shared")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "push", "-q", "origin", "main")
        _git(repo, "checkout", "-q", "-b", "dev")
        _git(repo, "push", "-q", "origin", "dev")
        _git(repo, "remote", "set-head", "origin", "-a")
        return repo

    def _edit_line2(self, repo, branch, text):
        _git(repo, "checkout", "-q", branch)
        (repo / "shared").write_text(f"line1\n{text}\nline3\n")
        _git(repo, "commit", "-qam", f"{branch} edit")

    def _conflicting(self):
        repo = self._base_repo()
        self._edit_line2(repo, "main", "MAIN-EDIT")
        _git(repo, "push", "-q", "origin", "main")
        self._edit_line2(repo, "dev", "DEV-EDIT")
        return repo

    def test_normal_push_still_blocks_on_conflict(self):
        repo = self._conflicting()
        r = _run_hook("pre-push-base-sync.sh", repo, "git push origin dev")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_backup_ref_push_is_exempt(self):
        repo = self._conflicting()
        r = _run_hook("pre-push-base-sync.sh", repo,
                      "git push origin HEAD:refs/autopilot-wip/dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_supervisor_delete_of_the_backup_ref_is_allowed(self):
        # The supervisor's cleanup `git push origin --delete refs/autopilot-wip/x`
        # must not be gated either (a deletion triggers no CI).
        repo = self._conflicting()
        r = _run_hook("pre-push-base-sync.sh", repo,
                      "git push origin --delete refs/autopilot-wip/dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
