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

    def test_backup_is_universal_across_authorities(self):
        # #503 review 🟡-3/🟡-4: the durability backup is the UNIFORM layer for
        # EVERY worktree worker; reduced-authority (fork-no-merge / branch-merge)
        # get durability from it, NOT from an early push of their real (gated)
        # delivery branch. The backup bullet must say so.
        w = _bullet_window(self.text, "durability BACKUP of your branch to origin")
        # Lock the operative universality phrase (unique to this statement),
        # not just the presence of the authority NAMES (which recur in the
        # delivery clause, giving a partial-revert no teeth) -- #498/#500.
        self.assertIn("regardless of authority", w,
                      "the backup bullet must declare itself the durability "
                      "layer for EVERY worktree worker regardless of authority "
                      "(#503 review 🟡-3/🟡-4)")
        for tok in ("fork-no-merge", "branch-merge"):
            self.assertIn(tok, w,
                          "the backup bullet must name the reduced-authority "
                          "profiles it covers (missing %r) -- #503 review" % tok)

    def test_fork_no_merge_durability_is_the_wip_backup_not_a_gated_fork_push(self):
        # The fork-no-merge line must point durability at the universal WIP
        # backup and keep its fork-branch push as a CLEAN hand-off (not a
        # mid-work push its own gates would block) -- #503 review 🟡-2/🟡-3.
        w = _norm(self.text)
        idx = w.find("`fork-no-merge` = you push YOUR fork branch")
        self.assertNotEqual(idx, -1, "expected the fork-no-merge authority line")
        window = w[idx:idx + 700]
        self.assertIn("refs/autopilot-wip", window,
                      "fork-no-merge must get durability from the universal "
                      "refs/autopilot-wip backup (#503 review)")
        self.assertRegex(window, r"HAND-OFF|hand off|hand-off",
                         "fork-no-merge's fork-branch push is its clean "
                         "HAND-OFF, not an early mid-work durability push "
                         "(#503 review)")


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
        # Teeth: lock the literal recovery COMMAND (a single strong token) so a
        # partial revert of either the `fetch` or the ref namespace breaks it --
        # a bare `refs/autopilot-wip`/`fetch` window check has no teeth because
        # the backup ref is mentioned twice in the note (#498/#500 lesson).
        self.assertIn("git fetch origin 'refs/autopilot-wip/", window,
                      "the #332 recovery note must fall back to FETCHING the "
                      "origin backup ref (git fetch origin "
                      "'refs/autopilot-wip/<branch>:...') when the local branch "
                      "ref is also gone (#503)")


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


@unittest.skipUnless(shutil.which("ruff"), "ruff not installed")
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

    def test_a_mere_mention_of_the_ns_does_NOT_exempt_a_real_push(self):
        # #503 review 🟡-1/B🔵-5: the exemption must anchor to the push
        # DESTINATION refspec, not a bare substring anywhere in the command --
        # a normal push whose commit message / echo merely MENTIONS the
        # namespace must STILL be gated (a real lint error, waved through, is
        # the bug).
        for cmd in (
            "git commit -m 'doc refs/autopilot-wip/ backup' && git push origin main",
            "git push origin main # note: refs/autopilot-wip/ ns",
            'git push origin main && echo "backed up to refs/autopilot-wip/x"',
        ):
            r = _run_hook("pre-push-lint.sh", self.repo, cmd)
            self.assertEqual(r.returncode, 2, "must NOT exempt: %s\n%s"
                             % (cmd, r.stdout + r.stderr))


class TestPrePushTestCheckExemptsBackupRef(unittest.TestCase):
    """A mid-work backup can be a not-yet-tested intermediate; pre-push-test-
    check must not block a push to the backup ref. (Note #503 review B🔵-4:
    this fixture blocks at Gate 1 -- feature code changed, no test file yet --
    which fires BEFORE the Gate-2 RED->GREEN check; the exemption exits before
    ALL gates, so the teeth hold at whichever gate fires first.)"""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-503-tc-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "dev")
        (self.repo / "a.py").write_text("x = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        # feature code changed with NO test file -> Gate 1 (feature-needs-test)
        # blocks a normal push. Deliberately NO `Closes/Fixes #N` in the message,
        # to keep any close-trigger substring out of the repo entirely.
        (self.repo / "a.py").write_text("x = 2\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "fix: correct value")

    def test_normal_push_still_blocks_on_missing_test(self):
        r = _run_hook("pre-push-test-check.sh", self.repo, "git push origin dev")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_backup_ref_push_is_exempt(self):
        r = _run_hook("pre-push-test-check.sh", self.repo,
                      "git push origin HEAD:refs/autopilot-wip/worktree-agent-x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestBlockTestSkipsExemptsBackupRef(unittest.TestCase):
    """#503 review 🔵-2: block-test-skips.sh also gates `git push`; a backup
    snapshot that happens to contain a skip pattern mid-work must not be
    blocked (it triggers no CI), while a normal push adding one still blocks."""

    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="airuleset-503-skip-"))
        self.addCleanup(shutil.rmtree, root, True)
        bare = root / "rem.git"
        _git(root, "init", "-q", "--bare", str(bare))
        self.repo = root / "repo"
        _git(root, "clone", "-q", str(bare), str(self.repo))
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "test_x.py").write_text("def test_ok():\n    assert 1 == 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        # push init as origin/main so the added-skip diff has a base to resolve
        _git(self.repo, "push", "-q", "origin", "HEAD:main")
        _git(self.repo, "branch", "--set-upstream-to=origin/main")
        # add a banned skip pattern in a test file (NOT pushed)
        (self.repo / "test_x.py").write_text(
            "import unittest\n\n\n@unittest.skip('wip')\n"
            "def test_ok():\n    assert 1 == 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "wip skip")

    def test_normal_push_still_blocks_on_added_skip(self):
        r = _run_hook("block-test-skips.sh", self.repo, "git push origin main")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_backup_ref_push_is_exempt(self):
        r = _run_hook("block-test-skips.sh", self.repo,
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
