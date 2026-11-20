"""Cap cargo's parallel build jobs per account (#1384) — a stdlib-only leaf.

cargo's default ``build.jobs`` is the core count: on dev1 (16 cores) one
workspace build started up to 16 rustc at 1-2 GB each, swap filled, and
earlyoom killed other projects' running work. ``install`` (as each account)
writes ONE marker line under ``[build]`` in the account's cargo config:

    jobs = 4  # airuleset-managed (#1384): delete this comment to keep your own value

WHY the account's ``~/.cargo/config.toml`` (not ``CARGO_BUILD_JOBS`` in the
toolchain env file): cargo reads the file itself on every invocation, so a
non-login, non-interactive shell (a Claude Bash call, a systemd unit, a CI
runner) honours it, and a running session gets it without a restart; an env
var would also override a repo's own ``jobs``. cargo gives a deeper config
(the repo's ``.cargo/config.toml``) precedence, and this module never reads
or writes a repo's file.

The edit is a key-level merge validated by re-parsing (``tomllib``): the new
document must equal the old one with only ``build.jobs`` set, else nothing is
written. A ``jobs`` without the marker is the account's own value and is kept.
Never raises: every failure is a ``WARN`` status line (install stays non-fatal).
"""
import copy
import os
import re
import shutil
import tomllib
from pathlib import Path

CARGO_JOBS_CAP = 4
MARKER = "airuleset-managed (#1384)"
# The shared toolchain's cargo (`cli_project_toolchain.RUST_BIN`); project
# accounts reach cargo only through it, and `install` runs without their
# login PATH, so `which` alone would miss it.
SHARED_RUST_BIN = "/opt/rust/cargo/bin"
_HEADER = "# Managed by airuleset (#1384): cap cargo's parallel rustc jobs.\n"
_MANAGED_RE = re.compile(r"(?m)^[ \t]*jobs[ \t]*=[^\n#]*#[ \t]*" + re.escape(MARKER) + r"[^\n]*$")
_BUILD_HEADER_RE = re.compile(r"(?m)^[ \t]*\[[ \t]*build[ \t]*\][ \t]*(?:#[^\n]*)?$")


def policy_jobs(cpu_count=None):
    """``min(CARGO_JOBS_CAP, cores)``, at least 1 (never above the default)."""
    cores = os.cpu_count() if cpu_count is None else cpu_count
    return max(1, min(CARGO_JOBS_CAP, cores or 1))


def managed_line(jobs):
    return "jobs = %d  # %s: delete this comment to keep your own value" % (jobs, MARKER)


def _refuse(why):
    return None, "refused: " + why


def plan_edit(text, jobs):
    """``(new_text, status)``; ``new_text`` None means write nothing. ``text``
    None = no file yet. Status: created / set / unchanged / kept ... / refused: ..."""
    line = managed_line(jobs)
    if text is None:
        return _HEADER + "[build]\n" + line + "\n", "created"
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return _refuse("not valid TOML (%s)" % e)
    build = doc.get("build")
    if build is not None and not isinstance(build, dict):
        return _refuse("`build` is not a table")
    managed = _MANAGED_RE.search(text)
    if build and "jobs" in build:
        if not managed:
            return None, "kept the account's own jobs = %s" % build["jobs"]
        if build["jobs"] == jobs:
            return None, "unchanged"
        indent = text[managed.start():].split("jobs", 1)[0]
        new = text[:managed.start()] + indent + line + text[managed.end():]
    elif managed:
        return _refuse("the managed jobs line is outside [build]")
    else:
        header = _BUILD_HEADER_RE.search(text)
        if header:
            new = text[:header.end()] + "\n" + line + text[header.end():]
        elif build is not None:
            return _refuse("`build` is an inline table or dotted keys")
        else:
            sep = "" if text.endswith("\n") or not text else "\n"
            new = text + sep + ("\n" if text.strip() else "") + "[build]\n" + line + "\n"
    want = copy.deepcopy(doc)
    want.setdefault("build", {})["jobs"] = jobs
    try:
        got = tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        return _refuse("the edit would not parse")
    if got != want:
        return _refuse("the edit would change more than build.jobs")
    return new, "set"


def config_path(home):
    """The file cargo reads in ``$CARGO_HOME`` (``~/.cargo``): the legacy
    extension-less ``config`` wins when it exists, else ``config.toml``."""
    legacy = Path(home) / ".cargo" / "config"
    return legacy if legacy.exists() else Path(home) / ".cargo" / "config.toml"


def cargo_present(home, which=None, shared_bin=SHARED_RUST_BIN):
    which = which or shutil.which
    return bool(os.access(Path(home) / ".cargo" / "bin" / "cargo", os.X_OK)
                or os.path.exists(os.path.join(shared_bin, "cargo")) or which("cargo"))


def _write_atomic(path, text):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".airuleset-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def ensure_cargo_jobs(home=None, cpu_count=None, which=None, shared_bin=SHARED_RUST_BIN):
    """Apply the cap for one account; returns the status line, never raises."""
    try:
        home = Path(home) if home is not None else Path.home()
        if not cargo_present(home, which, shared_bin):
            return "skipped - no cargo for this account"
        path = config_path(home)
        if path.is_symlink():
            return "WARN %s is a symlink, left untouched" % path
        text = path.read_text(encoding="utf-8") if path.exists() else None
        jobs = policy_jobs(cpu_count)
        new, status = plan_edit(text, jobs)
        if new is None:
            return "%s (%s)" % (status, path) if not status.startswith("refused") \
                else "WARN %s: %s, left untouched" % (path, status)
        _write_atomic(path, new)
        return "%s jobs = %d in %s" % (status, jobs, path)
    except Exception as e:  # noqa: BLE001 - install must not fail here; the WARN line reports it
        return "WARN cargo jobs cap failed: %s" % e


def install_step(home=None):
    """``cmd_install`` step 3b-quinque-bis-6: print the status, never fail the
    install. Inert under pytest with the real home (the #1196 rule)."""
    if home is None and "PYTEST_CURRENT_TEST" in os.environ:
        print("  cargo jobs cap: skipped under pytest (real home)")
        return
    print("  cargo jobs cap (#1384): %s" % ensure_cargo_jobs(home=home))
