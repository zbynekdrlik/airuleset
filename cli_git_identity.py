"""cli_git_identity.py — public repos commit under the owner's GitHub noreply
identity by default (#1196).

Origin: a clone with no local ``user.name``/``user.email`` commits under the
box's GLOBAL identity. On a dev workstation that was a personal one (another
person's name and a student email), and 22 public fohmixer commits went out
under it (fohmixer#3). Owner, 30.9.2026: keep the old history as it is, use
noreply from now on, never ask again — "netreba z toho robit vedu".

What this does, and nothing more:
  * resolves the gh user's noreply identity ONCE (``gh api user`` →
    ``<id>+<login>@users.noreply.github.com``, name = the account name or the
    login) and caches it in ``~/.claude/git-identity.json``;
  * for each checkout whose ``origin`` is a PUBLIC GitHub repo, sets the LOCAL
    ``user.name``/``user.email`` when absent or different;
  * never writes global config, never touches a private (or unknown-
    visibility) repo, never rewrites history;
  * rate-aware: visibility is looked up only for a checkout that NEEDS a
    change, and cached per repo for a day — an already-correct box costs zero
    gh calls;
  * every gh failure is LOUD (a stderr WARNING) and never fatal.

Every git/gh call goes through ``cli_onboard_exec._exec``/``_gh``: local for
``install``, ssh-wrapped for a remote ``onboard-project --host``, and an
injectable runner in tests.
"""

import json
import os
import re
import sys
import time

import cli_onboard_exec as _x

NOREPLY_DOMAIN = "users.noreply.github.com"
CACHE_FILE = "git-identity.json"
VISIBILITY_TTL_S = 24 * 3600
IDENTITY_TTL_S = 7 * 24 * 3600      # a box's gh login can change accounts
_VISIBILITIES = ("PUBLIC", "PRIVATE", "INTERNAL")
# https://github.com/o/r(.git), git@github.com:o/r(.git),
# ssh://git@github.com(:22)/o/r — github.com must start the host (never
# notgithub.com) and end it (never github.company.example).
_GITHUB_ORIGIN = re.compile(
    r"(?:^|[@/])github\.com(?::\d+)?[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
# Claude Code's own plugin/marketplace clones — not ours to configure.
_SKIP_MARKER = os.sep + os.path.join(".claude", "plugins") + os.sep


class IdentityError(RuntimeError):
    """A gh/git failure for ONE checkout — reported, never fatal."""


class IdentityUnavailable(IdentityError):
    """``gh api user`` failed — every checkout would fail the same way."""


# --------------------------------------------------------------------------- #
# Cache — one small JSON file under ~/.claude.
# --------------------------------------------------------------------------- #
def cache_path(home):
    return os.path.join(str(home), ".claude", CACHE_FILE)


def load_cache(home):
    try:
        with open(cache_path(home), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(home, data):
    path = cache_path(home)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        print("  Git identity: WARNING cache not saved (%s)" % e, file=sys.stderr)


# --------------------------------------------------------------------------- #
# Lookups.
# --------------------------------------------------------------------------- #
def identity_from_user(user):
    """(name, email) from a ``gh api user`` object; ValueError when malformed."""
    uid, login = user.get("id"), user.get("login")
    if not isinstance(uid, int) or not isinstance(login, str) or not login:
        raise ValueError("no id/login")
    return (user.get("name") or login,
            "%d+%s@%s" % (uid, login, NOREPLY_DOMAIN))


def noreply_identity(cache, run=None, now=None):
    """The cached noreply identity, else ``gh api user`` once (and cache it
    for IDENTITY_TTL_S)."""
    now = time.time() if now is None else now
    got = cache.get("identity") or {}
    fresh = isinstance(got.get("ts"), (int, float)) and now - got["ts"] < IDENTITY_TTL_S
    if fresh and got.get("name") and got.get("email"):
        return got["name"], got["email"]
    r = _x._gh(["api", "user"], run=run)
    if r.returncode != 0:
        raise IdentityUnavailable("gh api user failed: %s"
                                  % (r.stderr or "").strip()[:200])
    try:
        name, email = identity_from_user(json.loads(r.stdout))
    except (ValueError, AttributeError, TypeError):
        raise IdentityUnavailable("gh api user: unexpected answer") from None
    cache["identity"] = {"name": name, "email": email, "ts": now}
    return name, email


def origin_slug(path, host=None, run=None):
    """``owner/name`` when ``origin`` points at GitHub, else None."""
    r = _x._git(path, ["config", "--get", "remote.origin.url"], host=host, run=run)
    if r.returncode != 0:
        return None
    mt = _GITHUB_ORIGIN.search((r.stdout or "").strip())
    return "%s/%s" % mt.groups() if mt else None


def visibility(slug, cache, run=None, now=None):
    """PUBLIC / PRIVATE / INTERNAL, cached per repo for VISIBILITY_TTL_S."""
    now = time.time() if now is None else now
    seen = cache.setdefault("visibility", {})
    hit = seen.get(slug)
    if isinstance(hit, list) and len(hit) == 2 and now - hit[1] < VISIBILITY_TTL_S:
        return hit[0]
    r = _x._gh(["repo", "view", slug, "--json", "visibility", "-q",
                ".visibility"], run=run)
    vis = (r.stdout or "").strip().upper()
    if r.returncode != 0 or vis not in _VISIBILITIES:
        raise IdentityError("gh repo view %s failed: %s"
                            % (slug, ((r.stderr or "").strip() or vis)[:200]))
    seen[slug] = [vis, now]
    return vis


def _local(path, key, host, run):
    r = _x._git(path, ["config", "--local", "--get", key], host=host, run=run)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


# --------------------------------------------------------------------------- #
# One checkout.
# --------------------------------------------------------------------------- #
def ensure(path, cache, host=None, run=None, dry_run=False, now=None):
    """Bring ONE checkout to the noreply identity if it is a public GitHub repo.

    Returns (status, detail); status is ``not-ours`` / ``not-github`` / ``ok``
    / ``private`` / ``would-set`` / ``set``. Raises IdentityError on a gh/git failure."""
    if _SKIP_MARKER in os.path.join(str(path), ""):
        return "not-ours", "Claude Code plugin clone — not ours"
    slug = origin_slug(path, host=host, run=run)
    if not slug:
        return "not-github", "no GitHub origin"
    name, email = noreply_identity(cache, run=run, now=now)
    if (_local(path, "user.email", host, run) == email
            and _local(path, "user.name", host, run) == name):
        return "ok", "%s already %s" % (slug, email)
    vis = visibility(slug, cache, run=run, now=now)
    if vis != "PUBLIC":
        return "private", "%s is %s — untouched" % (slug, vis.lower())
    detail = "%s → %s <%s>" % (slug, name, email)
    if dry_run:
        return "would-set", detail
    for key, val in (("user.name", name), ("user.email", email)):
        r = _x._git(path, ["config", "--local", key, val], host=host, run=run)
        if r.returncode != 0:
            raise IdentityError("git config %s failed in %s: %s"
                                % (key, path, (r.stderr or "").strip()[:200]))
    return "set", detail


# --------------------------------------------------------------------------- #
# Entry points: install (every checkout on the box) and onboard (one project).
# --------------------------------------------------------------------------- #
def apply(roots, home, run=None, now=None):
    """Run ``ensure`` over every checkout root; one cache load/save."""
    cache = load_cache(home)
    counts, changed, errors = {}, [], []
    for root in roots:
        try:
            status, detail = ensure(root, cache, run=run, now=now)
        except IdentityUnavailable as e:
            errors.append(str(e))
            break
        except IdentityError as e:
            errors.append("%s: %s" % (root, e))
            continue
        counts[status] = counts.get(status, 0) + 1
        if status == "set":
            changed.append(detail)
    save_cache(home, cache)
    return {"counts": counts, "set": changed, "errors": errors}


def summary(result):
    c = result["counts"]
    return ("%d set, %d already noreply, %d private, %d not GitHub, %d errors"
            % (c.get("set", 0), c.get("ok", 0), c.get("private", 0),
               c.get("not-github", 0), len(result["errors"])))


def _app_shim_box():
    import cli_gh_rate
    return cli_gh_rate.is_app_shim_box()


def install_step(home, roots, run=None, app_shim=None):
    """``cmd_install`` hook: ONE summary line, a stderr WARNING per failure.
    Never raises. With the real runner under a test run it touches nothing
    (the #1190 ``PYTEST_CURRENT_TEST`` guard shape). On an app-token-shim
    stream box (issue 888) ``gh api user`` can never answer, so the step is a
    quiet one-line skip there instead of a WARNING on every install."""
    if run is None and os.environ.get("PYTEST_CURRENT_TEST"):
        print("  Git identity: skipped under test (no real checkout touched)")
        return None
    try:
        if (app_shim or _app_shim_box)():
            print("  Git identity: skipped — app-token gh box has no user identity")
            return None
        result = apply(roots() if callable(roots) else roots, home, run=run)
    except Exception as e:  # noqa: BLE001 — install must never break on this
        print("  Git identity: FAILED (%r) — install continues" % (e,),
              file=sys.stderr)
        return None
    print("  Git identity (public repos → noreply): %s" % summary(result))
    for line in result["set"]:
        print("    set %s" % line)
    for err in result["errors"]:
        print("  Git identity: WARNING %s" % err, file=sys.stderr)
    return result


def onboard_step(path, host=None, run=None, dry_run=False, home=None):
    """``onboard-project`` step dict (``cli_onboard._step`` shape). With no
    explicit ``home`` (the real one) it is inert under a test run."""
    if home is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return {"step": "git_identity", "status": "skipped",
                "detail": "skipped under test (real home not touched)"}
    home = home or os.path.expanduser("~")
    cache = load_cache(home)
    before = json.dumps(cache, sort_keys=True)
    try:
        status, detail = ensure(path, cache, host=host, run=run, dry_run=dry_run)
    except IdentityError as e:
        print("onboard-project: git identity WARNING %s" % e, file=sys.stderr)
        return {"step": "git_identity", "status": "skipped", "detail": str(e)}
    if not dry_run and json.dumps(cache, sort_keys=True) != before:
        save_cache(home, cache)
    mapped = {"set": "applied", "would-set": "would-apply"}.get(status, "satisfied")
    return {"step": "git_identity", "status": mapped, "detail": detail}
