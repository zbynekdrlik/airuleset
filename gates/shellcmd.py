"""gates.shellcmd -- ONE quote-aware shell-command splitter + stripper for the
whole gate family (#1020).

Before this module the SAME two primitives existed in four hand-written copies:

  * ``design_gate._strip_quoted`` (whose docstring literally read "same order as
    block-sensitive-staging.sh's bypass parse"),
  * ``block-sensitive-staging.sh``'s bypass quote-strip (``re.sub`` single then
    double), and
  * ``block-ungated-issue-filing.sh``'s ``split_top_level`` quote-aware splitter,
  * ``block-main-implementation.sh``'s ``NORM_CMD`` + ``case`` arming glob (#1017 --
    a compound command slipped through because that normalize never SPLIT the
    command).

``strip_quoted`` and ``split_top_level`` here are now the source of truth for
THREE of those four: design_gate's ``_strip_quoted`` (re-exported from
gates.design.gitctx), block-sensitive-staging's bypass parse (via gates.secrets),
and block-main-implementation's #1017 arming check. The FOURTH -- block-ungated-
issue-filing.sh's own ``split_top_level`` -- still carries its byte-identical
inline copy, because the full filing-hook migration was deliberately deferred to
its own reviewed lane (#1020); it adopts this module when that lands. They are
pure/offline (no filesystem, no subprocess), so they unit-test without a
subprocess. Behaviour is byte-for-byte the union of the originals -- the originals
agreed (single-quoted spans stripped first, then double; split on &&/||/;/&/|/
newline, quote- and backslash-aware), so there is no divergence to reconcile here
(the one push-scope divergence lives in gates.pushscope, not here).
"""
import re

_QUOTED_SPAN_SQ_RE = re.compile(r"'[^']*'")
_QUOTED_SPAN_DQ_RE = re.compile(r'"[^"]*"')


def strip_quoted(text):
    """Remove single- and double-quoted spans from ``text`` so a token that
    lives only INSIDE a quoted argument (a ``git merge`` mentioned in a ``-m``
    message, a ``# airuleset:secret-ok`` inside a commit-message body) is not
    mistaken for a real command / real shell comment. Single quotes first, then
    double -- the exact order design_gate._strip_quoted and
    block-sensitive-staging.sh's bypass parse both used. Never raises."""
    return _QUOTED_SPAN_DQ_RE.sub("", _QUOTED_SPAN_SQ_RE.sub("", text or ""))


def split_top_level(text):
    """Split ``text`` on &&/||/;/&/|/newline, but QUOTE-AWARE -- a ``;``/``|``
    sitting inside a real quoted argument (an issue TITLE, a commit message; real
    corpus example: camera-box #827's title literally contains one) must never be
    treated as a command separator. A backslash escapes the next character.

    Verbatim from block-ungated-issue-filing.sh's own ``split_top_level`` (which
    itself deliberately reused block-gh-invalid-json-flag.sh's #85 segmentation
    shape). Returns the list of segments (raw, with their quotes/whitespace
    intact); a trailing empty segment after a terminal separator is kept, exactly
    as the original produced it."""
    text = text or ""
    segs, buf, i, n, quote = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 2
            continue
        if text[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|", "\n"):
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs
