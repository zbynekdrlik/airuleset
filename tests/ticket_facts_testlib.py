"""Shared fixtures for the #1141 slice-3 facts tests (fake GraphQL answers,
a throwaway-repo git helper). A `_testlib` sibling, not a cross-test import:
the push gate's `unittest discover` cannot import one test module from another.
"""

import json
import os
import subprocess


def _labels(*names):
    return [{"name": n} for n in names]


def _row(n, *names):
    return {"number": n, "title": "t%d" % n, "labels": _labels(*names)}


_NOW = 1790244000.0                       # 2026-09-24T10:00:00Z
_FRESH = "2026-09-24T09:30:00Z"           # a head commit 30 min old


def _graphql(*prs):
    """A GraphQL payload for open PRs: (number, title, body, rollup, closes)
    plus optional (draft, head committedDate)."""
    nodes = []
    for number, title, body, state, closes, *rest in prs:
        draft = rest[0] if rest else False
        date = rest[1] if len(rest) > 1 else _FRESH
        nodes.append({
            "number": number, "title": title, "body": body, "isDraft": draft,
            "closingIssuesReferences": {"nodes": [
                {"number": c, "repository": {"nameWithOwner": "o/r"}}
                if isinstance(c, int) else
                {"number": c[0], "repository": {"nameWithOwner": c[1]}}
                for c in closes]},
            "commits": {"nodes": [{"commit": {"committedDate": date,
                                              "statusCheckRollup": (
                {"state": state} if state else None)}}]}})
    return {"data": {"repository": {"pullRequests": {"nodes": nodes}}}}


def _reasons(numbers=(), reopened=()):
    """A stateReason GraphQL answer: iN aliases, REOPENED for `reopened`."""
    return {"data": {"repository": {
        "i%d" % n: {"stateReason": "REOPENED" if n in reopened else None}
        for n in numbers}}}


def _gh(pr_payload, numbers=(), reopened=()):
    """gh_fn answering the PR query and the stateReason query (ruling 1)."""
    def run(args):
        if any("stateReason" in a for a in args):
            return json.dumps(_reasons(numbers, reopened))
        return json.dumps(pr_payload)
    return run


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    return subprocess.run(["git", "-C", repo, *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()
