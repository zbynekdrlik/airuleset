"""Shared fixtures for the #1333 house-stack tests: REAL git checkouts.

Since review round 3 (ROZHODNUTÉ I) ``cli_stack_scope`` asks git itself
(``git rev-parse --show-toplevel --git-common-dir``), so a fake ``.git`` dir
no longer counts as a repo. Every git call runs with the user's and the
system's git config switched off, so the box's own config never leaks in.
"""

import os
import subprocess
from pathlib import Path

_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, env=_ENV).stdout


def git_repo(root, url=None, commit=False, branch="main"):
    """`git init` at `root` (created), with an `origin` remote when `url` is
    given and one empty commit when `commit` is true."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", branch)
    if url:
        git(root, "remote", "add", "origin", url)
    if commit:
        git(root, "commit", "-q", "--allow-empty", "-m", "init")
    return root


def commit_all(root, message="change"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)


def add_origin(root, set_head=True, branch="main"):
    """A bare clone of `root` as its `origin` (fetched), with `origin/HEAD`
    pointing at `main` unless `set_head` is false."""
    root = Path(root)
    bare = root.parent / (root.name + "-origin.git")
    subprocess.run(["git", "clone", "-q", "--bare", str(root), str(bare)], check=True,
                   capture_output=True, env=_ENV)
    git(root, "remote", "add", "origin", str(bare))
    git(root, "fetch", "-q", "origin")
    if set_head:
        git(root, "remote", "set-head", "origin", branch)
    return bare


def add_submodule(superproject, sub_repo, name):
    """`sub_repo` (a repo with a commit) checked out as submodule `name`."""
    git(superproject, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        str(sub_repo), name)
    return Path(superproject) / name


def git_worktree(main, path, branch="lane"):
    """A linked worktree of `main` (which needs a commit) at `path`."""
    git(main, "worktree", "add", "-q", "-b", branch, str(path))
    return Path(path)
