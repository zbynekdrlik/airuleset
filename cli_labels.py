"""#1053 — idempotent label-ensure for the gk state-machine labels.

The footer's `gk`/`I` state machine (see `cli_quals.py`) keys on the labels
`gk-processing` (gatekeeper is processing a hand-off) and `verify-on-copy`
(deployed by the gatekeeper, returned to the sub-dev to verify on a fresh PROD
copy). Those labels must EXIST on every stream repo before gk can apply them —
`gh issue edit --add-label <missing>` errors rather than creating a label.

`airuleset.py labels --ensure --repo <owner/name>` creates them idempotently
(check-then-create, NEVER `gh label create --force`, which overwrites an existing
label's hand-curated colour/description on every call — the #191 M1 incident).
The GATEKEEPER runs this once after deploy (a full-authority box that owns the
repo); a reduced-authority stream must NOT run it against a foreign repo.

Stdlib-only; the gh runner is injectable so the idempotency is testable with a
fake gh (no network).
"""
import subprocess


# name -> (color hex, description). Colours picked to sit alongside the repo's
# existing hand-off palette without colliding: gk-processing = amber (in
# progress), verify-on-copy = green (deployed, awaiting stream verification).
LABELS_1053 = {
    "gk-processing": (
        "fbca04",
        "Gatekeeper is reviewing / merging / deploying this hand-off "
        "(airuleset#1053) — counts in the sub-dev footer's gk",
    ),
    "verify-on-copy": (
        "0e8a16",
        "Deployed by the gatekeeper — sub-dev must verify on its own fresh "
        "PROD copy (REFRESH-DEV-BOX-FROM-PROD) then post Verified-on-copy: "
        "(airuleset#1053)",
    ),
}


def _default_gh_run(argv):
    """Run a `gh` argv, returning the CompletedProcess. Kept tiny + injectable
    so `ensure_labels` can be driven by a fake gh in tests (no network)."""
    try:
        import airuleset
        env = airuleset._gh_env()
    except Exception:
        env = None
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=30, env=env)


def _repo_flags(repo):
    return ["-R", repo] if repo else []


def label_exists(name, repo, gh_run):
    """True iff a label named EXACTLY `name` already exists on `repo`. A gh
    error reads as 'unknown' → return None so the caller can fail LOUD rather
    than blindly create (or silently skip) on a broken read."""
    r = gh_run(["gh", "label", "list", "--search", name,
                "--json", "name", "-q", ".[].name"] + _repo_flags(repo))
    if getattr(r, "returncode", 1) != 0:
        return None
    return name in (r.stdout or "").splitlines()


def ensure_labels(repo, gh_run=None, labels=None):
    """Idempotently ensure each label in `labels` (default LABELS_1053) exists
    on `repo`. Check-then-create, NEVER --force. Returns a dict
    {name: "exists" | "created" | "failed: <reason>"}; a second call once the
    labels are present creates nothing (all "exists")."""
    gh_run = gh_run or _default_gh_run
    labels = labels or LABELS_1053
    result = {}
    for name, (color, desc) in labels.items():
        have = label_exists(name, repo, gh_run)
        if have is None:
            result[name] = "failed: could not read existing labels"
            continue
        if have:
            result[name] = "exists"
            continue
        r = gh_run(["gh", "label", "create", name, "--color", color,
                    "--description", desc] + _repo_flags(repo))
        if getattr(r, "returncode", 1) == 0:
            result[name] = "created"
        else:
            reason = (getattr(r, "stderr", "") or "").strip() or "gh error"
            result[name] = "failed: %s" % reason
    return result


def cmd_labels(args):
    """`airuleset.py labels --ensure [--repo <owner/name>]` — ensure the #1053
    gk state-machine labels (gk-processing / verify-on-copy) exist on a repo.

    The gatekeeper runs this ONCE after deploy on a full-authority box that owns
    the repo. A reduced-authority stream must NOT run it against a foreign repo
    (it would 403, and creating labels on a repo you do not own is out of the
    stream's authority)."""
    if not getattr(args, "ensure", False):
        print("labels: nothing to do (pass --ensure)")
        return 0
    repo = getattr(args, "repo", None)
    if not repo:
        r = _default_gh_run(["gh", "repo", "view", "--json", "nameWithOwner",
                             "-q", ".nameWithOwner"])
        repo = (getattr(r, "stdout", "") or "").strip()
        if not repo:
            print("labels: --repo <owner/name> required (could not resolve "
                  "the current repo)")
            return 1
    result = ensure_labels(repo)
    failed = False
    for name in sorted(result):
        status = result[name]
        print("labels: %s %-14s -> %s" % (repo, name, status))
        if status.startswith("failed"):
            failed = True
    return 1 if failed else 0
