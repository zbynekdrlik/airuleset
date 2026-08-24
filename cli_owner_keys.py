"""Owner SSH public-key provisioning (#653) — self-contained leaf.

Owner directive (2026-08-24, spinbike session): "airuleset by mal byt
informovany ze tam musi byt moj windows laptop kluc aby som nemusel zadavat
heslo." The owner was locked out of the new key-only spinbike-vps from his
Windows laptop because nothing in provisioning ensures his CURRENT laptop key
lands on a new target — it reached subdev/gk only when someone seeded those
`authorized_keys` by hand.

`provision_owner_keys()` closes that gap: it appends the maintained
`OWNER_PUBKEYS` set into the account's `~/.ssh/authorized_keys`, idempotently,
keyed on the base64 key BLOB. It is folded into `cmd_install`, so the deploy
loop's SINGLE existing `git pull && install` connection provisions every
target (and the local box) with ZERO extra ssh rounds — deliberately NOT a
separate ssh-per-host provisioning round like `provision_subdev_soniox_key`,
because the 11 shared subdev accounts already saturate the box's global
unauthenticated-connection pool (the #358 MaxStartups lesson) and another
full round multiplies that pressure.

WHY a committed constant, not a local `owner-keys/*.pub` dir: owner PUBLIC
keys are not secret (safe to commit), and a local-only file is invisible to
git-deploy — the "silent provisioning gap = dead feature" lesson from the
gatekeeper Discord `.env`. A committed constant deploys WITH the code to
every target.

SAFETY / SECURITY — the #653 hard invariants, enforced structurally here:
  * PUBLIC keys only. `OWNER_PUBKEYS` is public material; no private key ever
    touches this module.
  * Idempotent APPEND keyed on the base64 blob (the substring the whole
    file is matched on), so a re-run — or a differently-commented existing
    copy of the same key — is a no-op.
  * NEVER truncates, NEVER removes ANY existing line. So the key the current
    ssh connection authenticated with is structurally safe, and a VPS root
    lockout is impossible (you can only ever ADD a key, never take one away).
  * The dead `newlevel@newlevel-baking-ai-nb` RSA is deliberately NOT in
    `OWNER_PUBKEYS`, so it is never propagated. ACTIVE removal of a stale key
    is out of scope — a removal path risks an unrecoverable lockout for a
    non-goal, and "not propagated" is fully satisfied by exclusion.
  * Root `/root/.ssh/authorized_keys` is best-effort only, gated on
    passwordless sudo (`sudo -n true` — never prompts), keys piped via stdin
    (never argv), and never fails the install.
"""
import os
import shlex
import shutil
import subprocess
import sys


# The maintained set of OWNER public keys (#653). PUBLIC material, safe to
# commit (the ticket: "The key set is public material, safe to keep in
# config"). Read from dev1's own authorized_keys, where the owner's interim
# hand-provisioning placed them:
#   * zbynek-windows — the owner's CURRENT laptop key, the canonical one this
#     ticket exists for (its absence on spinbike-vps caused the lockout).
#   * zbynek-github  — the GitHub-registered key, riding along per the ticket.
# The ancient `newlevel@newlevel-baking-ai-nb` RSA is intentionally ABSENT —
# the machine no longer exists, and stale keys for dead machines are exactly
# what must not spread.
OWNER_PUBKEYS = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDXysBDPzwyPUO"
    "+7hs4u0P/0Ef0kx4MEd+uenFPTjgnk zbynek-windows",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOU4rk5Y/gDjnXYdH02MEXQsAbWVQ8dUJMoun"
    "PvIl3ND zbynek-github",
)


def _key_blob(line):
    """The base64 key BLOB (field 2) of an authorized_keys line — the token
    the whole idempotency keys on (unique per key, unlike the free-text
    comment). Returns None for a malformed/blank line."""
    parts = (line or "").split()
    return parts[1] if len(parts) >= 2 else None


def _chmod_best_effort(path, mode):
    """chmod that never aborts provisioning — a target fs that refuses chmod
    (a mounted share, an unusual acl) must not break install. Logs the reason
    rather than swallowing it silently (script-failure-policy.md)."""
    try:
        os.chmod(path, mode)
    except OSError as e:
        print("  owner keys: chmod %o on %s failed (non-fatal): %s"
              % (mode, path, e), file=sys.stderr)


def _append_missing_keys(path, keys):
    """Idempotently APPEND every key in `keys` to the authorized_keys file at
    `path`, matched on its blob being a substring of the current file text
    (the exact same test `grep -qF <blob>` makes in the root shell path, so a
    forced-command/option-prefixed existing copy is still recognised). Creates
    `path` (and its parent) with `700`/`600` perms.

    APPEND-ONLY by construction: the existing file is opened `"a"`, never
    `"w"`; no line is ever rewritten or removed. Returns a list of
    `(key_line, "added" | "present")` for logging. Never called for root
    (that path cannot be written without sudo) — root uses the shell script
    below."""
    path = os.path.expanduser(path)
    ssh_dir = os.path.dirname(path)
    if ssh_dir:
        os.makedirs(ssh_dir, exist_ok=True)
        _chmod_best_effort(ssh_dir, 0o700)

    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            existing = fh.read()

    seen = existing  # blob-substring match against existing AND batch-added
    results = []
    to_add = []
    for key in keys:
        blob = _key_blob(key)
        if not blob:
            continue
        if blob in seen:
            results.append((key, "present"))
        else:
            to_add.append(key.rstrip("\n"))
            seen += "\n" + key
            results.append((key, "added"))

    if to_add:
        # a file that lacks a trailing newline must not glue an owner key onto
        # its last line.
        prefix = "\n" if (existing and not existing.endswith("\n")) else ""
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(prefix + "".join(k + "\n" for k in to_add))

    _chmod_best_effort(path, 0o600)
    return results


def _authorized_keys_append_script(ssh_dir):
    """A POSIX-sh script that idempotently APPENDS keys (read from stdin, one
    per line) into `<ssh_dir>/authorized_keys`, keyed on the base64 blob.
    APPEND-ONLY: it uses only `mkdir -p` / `chmod` / `touch` / `grep` / `>>`
    — never a truncating `>`, never `rm`, never `sed -i`/`tee`/`truncate`, so
    it can never remove an existing key (root-lockout-proof). Used for the
    root path via `sudo -n sh -c <script>`; the blob is field 2 via
    `set -- $key` (no awk dependency)."""
    d = shlex.quote(ssh_dir)
    return (
        "set -e; umask 077; "
        "d=" + d + "; mkdir -p \"$d\"; chmod 700 \"$d\"; "
        "f=\"$d/authorized_keys\"; touch \"$f\"; chmod 600 \"$f\"; "
        "while IFS= read -r key; do "
        "[ -n \"$key\" ] || continue; "
        "set -- $key; blob=$2; "
        "[ -n \"$blob\" ] || continue; "
        "grep -qF -- \"$blob\" \"$f\" || printf '%s\\n' \"$key\" >> \"$f\"; "
        "done"
    )


def _provision_root_keys(keys, run, root_ssh_dir="/root/.ssh"):
    """Best-effort root provisioning. Returns a short status string; NEVER
    raises (the caller must never break install over root). Gated on
    passwordless sudo so it never prompts on a no-sudo account (every subdev
    stream account) — a `sudo -n true` probe fails instantly there and the
    root append is skipped. When already running AS root, the current-user
    path has already covered `~/.ssh` (== /root/.ssh), so this is a no-op."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return "self-root (current-user path covers /root)"
    if not shutil.which("sudo"):
        return "no-sudo"
    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as e:  # noqa: BLE001 — best-effort, never break install
        return "sudo-probe-error: %r" % e
    if getattr(probe, "returncode", 1) != 0:
        return "no-passwordless-sudo"

    script = _authorized_keys_append_script(root_ssh_dir)
    stdin = "".join(k.rstrip("\n") + "\n" for k in keys if _key_blob(k))
    try:
        r = run(["sudo", "-n", "sh", "-c", script], input=stdin,
                capture_output=True, text=True, timeout=15)
    except Exception as e:  # noqa: BLE001
        return "root-provision-error: %r" % e
    if getattr(r, "returncode", 1) != 0:
        return "root-provision-failed rc=%d" % r.returncode
    return "root-provisioned"


def provision_owner_keys(keys=None, run=None, user_ssh_dir=None,
                         provision_root=True, root_ssh_dir="/root/.ssh"):
    """Append the OWNER public keys to THIS account's authorized_keys (and,
    best-effort, root's). Called by `cmd_install`, so it runs on every managed
    target through the deploy loop's existing connection AND on the local box —
    no extra ssh. Idempotent + append-only (see module docstring). Prints a
    one-line summary; returns `{"user": [(key, action)...], "root": <status>}`.

    `run` defaults to `subprocess.run` (injectable for tests); `user_ssh_dir`
    defaults to `~/.ssh` (a concrete path a test can point at a tmp dir);
    `keys` defaults to `OWNER_PUBKEYS`."""
    run = run or subprocess.run
    keys = list(keys if keys is not None else OWNER_PUBKEYS)
    if user_ssh_dir is None:
        user_ssh_dir = os.path.expanduser("~/.ssh")

    user_results = _append_missing_keys(
        os.path.join(user_ssh_dir, "authorized_keys"), keys)
    n_added = sum(1 for _k, a in user_results if a == "added")

    root_status = (_provision_root_keys(keys, run, root_ssh_dir)
                   if provision_root else "skipped")

    print("  owner keys: %d key(s) — %d appended, %d already present "
          "(this account); root: %s"
          % (len(keys), n_added, len(user_results) - n_added, root_status))
    return {"user": user_results, "root": root_status}
