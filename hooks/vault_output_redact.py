"""The PostToolUse redactor behind hooks/redact-vault-output.sh (#1153 part c).

Reads the PostToolUse payload on stdin. When a credential-store value
(`<store>/<NAME>.secret`, the `airuleset.py secret` channel) appears in the
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
hook runs (the docs' own warning); only STORE values are known here — a plain
`~/.secrets/*` key file is guarded at READ time by block-vault-store-read.sh
instead; a multi-line value is also matched LINE by line (16+ byte lines,
anchored at the end or start of an output line), so a short line of it, or
one embedded mid-line, is not; a deliberately transformed value
(reversed, double-encoded, one character per line) is not recognised — the
same residual `secret exec` has.
"""

import json
import os
import re
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MARKER = b"<<REDACTED>>"
# A LINE of a multi-line value (a PEM key body) is its own needle: Grep's
# `path:N:` prefixes, `cat -n`, a Read's `N→` gutter, or an Edit patch's
# `+<line>` entries print the value one line at a time, and a whole-value
# match then finds nothing (review B finding 3). Such tools put a PREFIX in
# front of the line, so a line needle is matched at the END (or the start) of
# an output line by a set lookup — one pass, never a replace per needle
# (review C finding 2: a 64 KB value of 16-byte lines cost 19.7 s and would
# time the hook out, i.e. fail open). Not needles (review C finding 3): a PEM
# banner, and the NAME half of a `NAME=value` line — only its value counts.
MIN_LINE_BYTES = 16
MAX_LINE_LENGTHS = 64
PEM_BANNER_RE = re.compile(rb"^-----(?:BEGIN|END) [A-Z0-9 ]+-----$")
ENV_LINE_RE = re.compile(rb"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=(.*)$")


def store_values():
    """(values, lines): every stored value (bytes) and every 16+-byte line of a
    multi-line one, each longest first. Reads the files directly —
    `vault.read_value` would `ensure_dir()` (mkdir + chmod) on every call."""
    from filedrop import vault

    d = Path(vault.secrets_dir())
    out, lines = [], []
    try:
        entries = list(d.iterdir())
    except OSError:
        return out, lines
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
        if b"\n" in value.strip():
            lines.extend(_line_needles(value))
    return (sorted(set(out), key=len, reverse=True), set(lines))


def _line_needles(value):
    for ln in value.splitlines():
        ln = ln.strip()
        env = ENV_LINE_RE.match(ln)
        if env:
            ln = env.group(1).strip().strip(b"\"'")
        if len(ln) >= MIN_LINE_BYTES and not PEM_BANNER_RE.match(ln):
            yield ln


def _scrub_bytes(blob, needles, redact):
    """Whole values through the full `secret exec` filter (every rendering);
    single lines of a multi-line value by an end/start-anchored set lookup per
    output line (see MIN_LINE_BYTES)."""
    values, lines = needles
    for v in values:
        blob = redact(blob, v, MARKER)
    if not lines:
        return blob
    lengths = sorted({len(n) for n in lines}, reverse=True)[:MAX_LINE_LENGTHS]
    out = []
    for row in blob.split(b"\n"):
        body = row.rstrip(b"\r \t")
        for n in lengths:
            if len(body) < n:
                continue
            if body[-n:] in lines:
                row = body[:-n] + MARKER + row[len(body):]
                break
            if body[:n] in lines:
                row = MARKER + row[n:]
                break
        out.append(row)
    return b"\n".join(out)


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
        new = _scrub_bytes(raw, values, redact)
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
    values = store_values()
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
    if _scrub_bytes(joined, values, _secret_redact) == joined:
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
            "redact-vault-output (#1153): %d occurrence(s) of a stored credential "
            "value were replaced with %s in this tool's output before it reached "
            "you. Do not try to recover the value; use `airuleset.py secret exec "
            "<NAME> -- <cmd>` to use it." % (hits, MARKER.decode())),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — fail open, loudly, value-free
        print("redact-vault-output: %s — output NOT checked"
              % exc.__class__.__name__, file=sys.stderr)
        sys.exit(1)
