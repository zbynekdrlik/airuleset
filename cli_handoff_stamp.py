"""cli_handoff_stamp.py -- the `Verified-at-UTC:` post-time stamp for the
`handoff --body-file` pass-through (#1344). Stdlib only.

The compose path stamps `Verified-at-UTC:` itself; the pass-through posted a
stream-authored body verbatim (#1044), so a missing or copied-forward value
reached the odoo-erp RFR gate (REQUIRED_FIELDS MISSING, #5869 MALFORMED,
#6377 batch 3 INCONSISTENT). This leaf rewrites ONLY that one field's value;
fences, the single `HEAD:` line and every other byte pass through untouched.

The gate parses field labels on EVERY line (fences included, first match
wins), so a `Verified-at-UTC:` label inside a fence is refused rather than
left stale or rewritten as evidence; a value carried on a continuation line is
refused too (rewriting the label line alone would leave a malformed value).
"""
import datetime
import os
import re
from typing import Optional

# The gate's FIELD_PATTERNS shape for one label (bullet / bold / colon inside
# or outside the bold): group 1 = the prefix up to the value, group 2 = value.
_VA_LINE_RE = re.compile(
    r"^([ \t]*[-*]?[ \t]*\**Verified-at-UTC\**[ \t]*:[ \t]*\**[ \t]*)(.*)$",
    re.IGNORECASE)
_HEAD_LINE_RE = re.compile(r"^([ \t]*[-*]?[ \t]*)\**HEAD\**[ \t]*:", re.IGNORECASE)
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
_BLOCK = "handoff BLOCK: --body-file "


def utc_now() -> str:
    """The stamp value: whole-second UTC, the gate's timestamp shape."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def stamp_verified_at(body: str, now_utc: str) -> tuple[str, Optional[str]]:
    """``(stamped_body, error)``: every unfenced `Verified-at-UTC:` field line
    gets ``now_utc`` as its value; with none, one is inserted before the first
    unfenced `HEAD:` line (a label line already stops continuation scans, so no
    other field's parse changes). On error the body is returned unchanged."""
    lines = body.splitlines(keepends=True)
    out, fence, stamped, head_at = [], None, False, None
    for line in lines:
        text = line.rstrip("\r\n")
        eol = line[len(text):]
        fm = _FENCE_RE.match(text)
        if fm:
            fence = None if fence == fm.group(1) else (fence or fm.group(1))
            out.append(line)
            continue
        m = _VA_LINE_RE.match(text)
        if m and fence:
            return body, (_BLOCK + "body has a Verified-at-UTC: line inside a "
                          "fenced block; the gate reads it as the field. "
                          "Remove or reword it (the composer stamps the field)")
        if m:
            if not m.group(2).strip():
                return body, (_BLOCK + "Verified-at-UTC: must carry its value "
                              "on the same line (the composer stamps it)")
            out.append(m.group(1) + now_utc + eol)
            stamped = True
            continue
        if head_at is None and not fence and _HEAD_LINE_RE.match(text):
            head_at = len(out)
        out.append(line)
    if not stamped:
        if head_at is None:
            return body, _BLOCK + "body has no HEAD: line to stamp beside"
        prefix = _HEAD_LINE_RE.match(out[head_at]).group(1)
        out.insert(head_at, "%sVerified-at-UTC: %s\n" % (prefix, now_utc))
    return "".join(out), None


def stamp_body_file(body: str, gate_dir: str, owner_repo: str, issue
                    ) -> tuple[str, Optional[str], str]:
    """``(stamped_body, path_or_None, now_utc)``: stamp and write the body the
    composer will hash + post (the compose path's own ``body-<repo>-<N>.md``),
    leaving the stream's source file untouched. On error the BLOCK reason is
    printed and ``path`` is None, so the caller refuses before any receipt."""
    now = utc_now()
    os.makedirs(gate_dir, exist_ok=True)  # the caller's receipt lands here too
    stamped, err = stamp_verified_at(body, now)
    if err:
        print(err)
        return body, None, now
    path = os.path.join(gate_dir, "body-%s-%s.md" % (owner_repo, issue))
    try:
        with open(path, "w") as f:
            f.write(stamped)
    except OSError as e:
        print(_BLOCK + "cannot write the stamped body: %s" % e)
        return body, None, now
    return stamped, path, now
