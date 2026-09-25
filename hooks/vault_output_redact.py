"""The PostToolUse redactor behind hooks/redact-vault-output.sh (#1153 part c).

Reads the PostToolUse payload on stdin. When a credential-store value
(`<store>/<NAME>.secret`, the `airuleset.py secret` channel) or — since slice
2 — the value of a regular file directly under the plain key-file root
(`~/.secrets/<name>`, loaded by cli_vault_keyfile.plain_root_values: no
symlinks, no subdirs, no `*.pub`, size-capped, 8+ bytes) appears in the
tool's output, prints `hookSpecificOutput.updatedToolOutput`: the SAME
structure with every string that carried a value rewritten through
`cli_vault._secret_redact` — the filter `secret exec` already applies to its
child's fd 1/2 (raw bytes, the stripped value, and the encoded/escaped
renderings a dump produces). Prints NOTHING when there is nothing to redact,
so Claude Code keeps the original output untouched.

Why this works for BUILT-IN tools (verified 2026-09-25 on Claude Code 2.1.281,
ticket comment 5828264462): `updatedToolOutput` "replaces the tool's output
with the provided value before it is sent to Claude", and the session jsonl
stores the replacement — the original never reaches the transcript. The value
"must match the tool's output shape" (a mismatching one is ignored for built-in
tools), which is why this rewrites strings IN PLACE and never adds, drops or
retypes a field.

Exit codes: 0 always on a decision (redacted or not). Anything it cannot do —
an unreadable payload, a crash — exits 1 with a one-line reason on stderr and
no stdout: Claude Code shows a non-blocking hook error and passes the ORIGINAL
output through. This is deliberately fail-OPEN: a PostToolUse hook runs after
the tool, so it has nothing to block, and exit 2 would feed stderr to Claude
as feedback. It never prints a value: stdout carries only redacted text, and
stderr carries only exception class names and counts.

Honest limits: OpenTelemetry/analytics capture the original output before any
hook runs (the docs' own warning); a key file in a SUBDIR of the plain root,
behind a symlink, or under another name elsewhere is not known (the READ guard
block-vault-store-read.sh is the first line there); a multi-line value is
matched LINE by line (16+ byte lines, anchored at the end or start of an
output line) and a PEM block whole, so a short line of it, or one embedded
mid-line, is not; a deliberately transformed value
(reversed, double-encoded, one character per line) is not recognised — the
same residual `secret exec` has.
"""

import json
import os
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# The needle rules (whole values through `_secret_redact`, 16+-byte LINES of
# a multi-line value matched end/start-anchored in one pass, PEM banners and
# `NAME=` halves never needles, a PEM block whole) live in cli_vault_keyfile
# since slice 2 of #1153 — `secret exec --file` filters with the SAME code.
from cli_vault_keyfile import (MARKER, needles_for,  # noqa: E402
                               plain_root_values, scrub_bytes)


def store_values():
    """Every stored value (bytes). Reads the files directly —
    `vault.read_value` would `ensure_dir()` (mkdir + chmod) on every call."""
    from filedrop import vault

    d = Path(vault.secrets_dir())
    out = []
    try:
        entries = list(d.iterdir())
    except OSError:
        return out
    cap = vault.MAX_SECRET_BYTES          # the store's own ceiling (review B 6)
    for p in entries:
        if p.suffix != ".secret" or not vault.NAME_RE.fullmatch(p.stem):
            continue
        try:
            fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            continue
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                continue
            value = fh.read(cap + 1)
        if not value or len(value) > cap:
            continue
        out.append(value)
    return out


def strings(obj):
    """Every string anywhere in `obj` (dict values and list items)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def scrub(obj, values, redact):
    """`obj` with every string rewritten; the structure is never changed."""
    if isinstance(obj, str):
        raw = obj.encode("utf-8", "surrogatepass")
        new = scrub_bytes(raw, values, redact)
        return obj if new == raw else new.decode("utf-8", "replace")
    if isinstance(obj, dict):
        return {k: scrub(v, values, redact) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, values, redact) for v in obj]
    return obj


def main():
    from cli_vault import _secret_redact

    try:
        payload = json.loads(sys.stdin.buffer.read())
    except ValueError as e:
        print("redact-vault-output: unreadable payload (%s) — output NOT "
              "checked" % e.__class__.__name__, file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or "tool_response" not in payload:
        return 0
    values = needles_for(store_values() + plain_root_values())
    if not values[0]:
        return 0
    response = payload["tool_response"]
    # Cheap pre-filter: every string of the response joined once, so the
    # common no-hit case costs one redaction pass instead of one per string.
    # NOT a json.dumps — that re-escapes the strings, and a value whose own
    # JSON rendering sits inside a string would then be escaped TWICE and
    # missed. Joined by NEWLINE so the line-anchored needles see each string
    # as its own row; a match spanning a joint is re-checked per string below.
    joined = b"\n".join(s.encode("utf-8", "surrogatepass") for s in strings(response))
    if scrub_bytes(joined, values, _secret_redact) == joined:
        return 0
    redacted = scrub(response, values, _secret_redact)
    if redacted == response:
        return 0
    hits = json.dumps(redacted).count(MARKER.decode()) - json.dumps(response).count(
        MARKER.decode())
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "updatedToolOutput": redacted,
        "additionalContext": (
            "redact-vault-output (#1153): %d occurrence(s) of a credential value "
            "(the store or a plain key file) were replaced with %s in this tool's "
            "output before it reached you. Do not try to recover the value; use "
            "`airuleset.py secret exec <NAME> -- <cmd>` (a stored value) or "
            "`secret exec --file <path> --env KEY -- <cmd>` (a plain key file)."
            % (hits, MARKER.decode())),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — fail open, loudly, value-free
        print("redact-vault-output: %s — output NOT checked"
              % exc.__class__.__name__, file=sys.stderr)
        sys.exit(1)
