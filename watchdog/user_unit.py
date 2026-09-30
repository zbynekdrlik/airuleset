"""Launch detached work as a transient `systemd-run --user` unit — the ONE
launcher for the watchdog (#1067 slice 1c quals refresher, #1176 detached
checkout fast-forward).

Why a transient unit: the watchdog runs as `api-watchdog.service`
(`Type=oneshot` + `KillMode=control-group`), so a plain `Popen` child — even
with `start_new_session=True` — stays in the SAME cgroup and systemd kills it
the moment the oneshot exits (`setsid` does NOT escape a cgroup). A transient
`--user` unit runs in its OWN cgroup and outlives the sweep; `--collect`
garbage-collects it once it finishes, and a fixed `--unit` name makes a
concurrent same-name start fail atomically ("already exists") — the
single-flight backstop every caller relies on.

This module only LAUNCHES and reports how it went; each caller decides its own
fallback (the refresher: a logged Popen; the fast-forward: the in-unit merge).
"""
import os
import subprocess

# A `systemd-run --user` transient unit inherits the USER MANAGER's environment,
# NOT the caller's — so a child's env is NOT forwarded unless asked for. The
# default set is the #1067 refresher's (it shells `gh`/`git` by BARE NAME and
# needs, and ONLY needs, these — least privilege):
#   PATH           resolve `gh` (incl. the ~/.local/bin app-token shim, #888) + git
#   HOME           ~/.claude cache dir + ~/.config/gh
#   XDG_CONFIG_HOME gh's config dir when relocated off ~/.config
#   LANG, LC_ALL   gh/git output encoding (avoids the #1108 UnicodeEncode class)
#   GITHUB_TOKEN   gh honours it for auth
#   GH_* prefix    gh's OWN namespace (GH_TOKEN / GH_CONFIG_DIR / GH_HOST / …) —
#                  all legitimately gh's; forwarding the whole namespace keeps the
#                  child's gh behaving identically to the watchdog's.
# The broad `GITHUB_` prefix and XDG_RUNTIME_DIR were REMOVED (review: narrow to
# need — the unit gets XDG_RUNTIME_DIR from its manager, and CI `GITHUB_*` vars
# are not the derivation's business). A caller whose child needs less passes
# its own narrower `env_keys` / `env_prefixes`.
DEFAULT_ENV_KEYS = ("PATH", "HOME", "XDG_CONFIG_HOME", "LANG", "LC_ALL",
                    "GITHUB_TOKEN")
DEFAULT_ENV_PREFIXES = ("GH_",)
CLIENT_TIMEOUT_S = 10   # the systemd-run CLIENT call; the unit itself is detached
TIMEOUT_WHY = "systemd-run error: TimeoutExpired"   # outcome UNKNOWN, not "not started"


def setenv_args(source, keys=DEFAULT_ENV_KEYS, prefixes=DEFAULT_ENV_PREFIXES):
    """`--setenv=NAME` args (NAME ONLY — no `=VALUE`) telling systemd-run to
    IMPORT each var's value from its OWN client environment into the unit
    (systemd 255: "When = and VALUE are omitted, the value of the variable with
    the same name in the program environment will be used").

    Emitting NAME-only keeps credential VALUES (GH_TOKEN / GITHUB_TOKEN / any
    GH_* secret) OUT of the systemd-run ARGV — on a shared-stream box (subdev,
    ~15 accounts) `/proc/<pid>/cmdline` / `ps aux` is readable by every other
    account, and a `--setenv=NAME=VALUE` would ALSO persist the value in the
    transient unit's properties (review: cross-account credential leak). The
    values ride the systemd-run client's `env=` dict instead (in-process memory,
    never argv). `source` is the watchdog process env; only vars PRESENT in it are
    forwarded, so every emitted NAME resolves in the client env."""
    prefixes = tuple(prefixes or ())
    return ["--setenv=%s" % k for k in sorted(source)
            if k in keys or (prefixes and k.startswith(prefixes))]


def _client_env():
    """The systemd-run CLIENT env: the shared user-bus env (#826), or None when
    that helper is unavailable (the client then inherits os.environ — a live
    user bus inside a --user service, so NOT a forced fall-back)."""
    try:
        from cli_filedrop_watchdog import _xdg_runtime_env
        return _xdg_runtime_env()
    except Exception:  # noqa: BLE001 — the helper is optional; see docstring
        return None


def launch(unit, workdir, child_argv, runtime_max_s, run_fn=None,
           env_keys=DEFAULT_ENV_KEYS, env_prefixes=DEFAULT_ENV_PREFIXES):
    """Start `child_argv` as the transient `--user` unit `unit` (cwd `workdir`,
    killed by systemd after `runtime_max_s`). Returns `(True, "started")`,
    `(True, "exists")` when a unit of that name is already running (the atomic
    single-flight backstop), or `(False, <why>)` — `systemd-run absent`,
    `systemd-run error: <Exc>`, `systemd-run rc=<n>` — when the caller must
    fall back. `run_fn` is the test seam (defaults to `subprocess.run`)."""
    run = run_fn or subprocess.run
    env = _client_env()
    src = env if env is not None else os.environ
    argv = ["systemd-run", "--user", "--collect", "--quiet",
            "--unit", unit,
            "--working-directory", workdir,
            "--property", "RuntimeMaxSec=%d" % runtime_max_s,
            *setenv_args(src, env_keys, env_prefixes),
            "--", *child_argv]
    try:
        # a short client-call ceiling: systemd-run returns in ms once the unit is
        # started; CLIENT_TIMEOUT_S bounds a bus hang without adding real latency
        # to the sweep. `env=src` is the SAME dict the `--setenv=NAME` list was
        # derived from, so every imported NAME always resolves in the client env.
        r = run(argv, capture_output=True, text=True, timeout=CLIENT_TIMEOUT_S,
                env=src)
    except FileNotFoundError:
        return False, "systemd-run absent"
    except subprocess.TimeoutExpired:
        # the bus may still have accepted the unit: a caller whose fallback
        # would RACE the unit (the #1176 merge) must treat this as unknown
        return False, TIMEOUT_WHY
    except Exception as e:  # noqa: BLE001 — any spawn error: the caller falls back
        return False, "systemd-run error: %s" % type(e).__name__
    if r.returncode == 0:
        return True, "started"
    if "exists" in (r.stderr or "").lower():
        return True, "exists"
    return False, "systemd-run rc=%d" % r.returncode
