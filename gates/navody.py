"""gates.navody -- per-tenant client-guide (Návody) fact + live-link gate
(#1073, owner ruling 18.9.2026, montalu1: „návody buduj a udržiavaj a nech to
je airuleset pravidlo").

REPLACES the #1042 word-pattern intro-link block. The owner's point after the
dead `/odoo/knowledge` link incident (tasks 857/883, #1072): client guides are
a BUILT + MAINTAINED per-tenant deliverable, so the gate must verify a LIVE,
section-deep guide link -- never pattern-match the word „Návody" (which is what
forced a fabricated dead link into a client message).

Three consumers share this ONE module:
  (a) the #1042 Stop-hook block in hooks/stop-check-prose-violations.sh calls
      `python3 -m gates.navody` on a client-acceptance hand-off (the Stop
      payload on stdin) -- `run()` reads the per-tenant fact and blocks unless
      the message carries a LIVE guide link (or, for a NONE fact, the guide
      ticket reference); an UNKNOWN fact FAILS CLOSED with the fix named, so
      while the gate runs a fabricated or dead link cannot pass (the ONE
      fail-OPEN is an infra error — a python/curl crash — which mirrors every
      sibling gate's rc-only block convention);
  (b) `airuleset.py handoff`'s composer preflight calls `guide_maintenance()`
      -- a client-visible surface change in the RFR diff must ALSO touch
      `docs/<tenant>/navody-*.html` OR carry `Navody: n/a — <why>`;
  (c) tests call the pure functions directly with an injected `curl` /
      `read_text` seam.

The per-tenant FACT lives in `<repo>/.claude/streams/<stream>.md`:
    navody_url: https://<tenant-prod>/p/<token>/navody-<oblast>.html
  or, while the guide is still being built:
    navody_url: NONE — #<guide ticket>
  optionally overriding the client-visible surface allowlist:
    navody_surfaces: views/, kiosk/, static/src/, reports/

`tenant_guide(cwd, stream) -> (url|None, ticket|None)`:
    (url, None)   -- a live URL to verify;
    (None, "#N")  -- NONE fact, guide in progress, ticket ref;
    (None, None)  -- UNKNOWN (unreadable / missing / malformed fact) -> the gate
                     fails CLOSED.

STDLIB ONLY -- no third-party deps, and no `airuleset`/`watchdog`/`notify`
import at module load (a Stop gate stays cheap and self-contained). The current
stream is the box's UNSPOOFABLE uid account (`pwd.getpwuid(os.getuid())`, the
same source airuleset._current_user() uses), never `$USER`.
"""
import glob
import os
import re
import subprocess

from gates import read_payload, field_of, emit_block_stderr, allow

STREAMS_DIR = os.path.join(".claude", "streams")

# The conservative default client-visible surface allowlist (a substring match
# against each changed path). `static/src/**` collapses to the `static/src/`
# fragment. Overridable per stream via the `navody_surfaces:` fact line.
DEFAULT_SURFACES = ("views/", "templates/", "kiosk/", "static/src/")

_NAVODY_URL_RE = re.compile(r'^\s*navody_url\s*:\s*(.+?)\s*$',
                            re.IGNORECASE | re.MULTILINE)
_NAVODY_SURFACES_RE = re.compile(r'^\s*navody_surfaces\s*:\s*(.+?)\s*$',
                                 re.IGNORECASE | re.MULTILINE)
# "NONE — #7560" / "NONE - #7560" / "none #7560"
_NONE_TICKET_RE = re.compile(r'^NONE\b.*?#(\d+)', re.IGNORECASE)
# an https link token, bounded by whitespace / quotes / parens / backticks / >].
_HTTPS_RE = re.compile(r'https?://[^\s)>\]"\'`]+')
# a repo-static guide file basename: navody-<oblast>.html
_GUIDE_BASENAME_RE = re.compile(r'^navody-.+\.html$', re.IGNORECASE)
# a guide file path anywhere in a diff: docs/<tenant>/navody-<oblast>.html
_GUIDE_PATH_RE = re.compile(r'(?:^|/)docs/[^/]+/navody-[^/]*\.html$',
                            re.IGNORECASE)
# `Navody: n/a` with a bounded separator (a REASON on the same line is required
# and checked from the captured remainder, never a trailing `.*\S` that
# backtracks polynomially — the repo's #577/#1010 ReDoS discipline). `[ \t]`
# (not `\s`) so a newline never straddles the match; `.` here is line-bounded
# (no DOTALL). Bounded reps around the optional `/` remove the adjacent-star
# ambiguity the #1073 review measured.
_NAVODY_NA_RE = re.compile(
    r'(?im)^[ \t]*N[aá]vody[ \t]{0,4}:[ \t]{0,4}n[ \t]{0,2}/?[ \t]{0,2}a\b(?P<rest>.*)$')


# --------------------------------------------------------------------------- #
# Fact reader
# --------------------------------------------------------------------------- #
def _current_stream():
    """This box's stream identity = its UNSPOOFABLE uid account name (the same
    source `airuleset._current_user()` keys authority on). None on a uid with no
    passwd entry (exotic) -- resolves to the UNKNOWN fact state, never a wrong
    tenant."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return None


def _repo_root_for(cwd):
    """The repo root for `cwd` = the directory that CONTAINS a `.claude/streams`
    tree, walking up (bounded). None when none is found. No `git` subprocess (a
    worktree/submodule keeps `.git` as a file; the streams tree is the anchor we
    actually need)."""
    d = os.path.abspath(cwd or os.getcwd())
    for _ in range(16):
        if os.path.isdir(os.path.join(d, STREAMS_DIR)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _read_stream_file(cwd, stream, *, read_text=None):
    """The text of `<repo>/.claude/streams/<stream>.md`, or None (unreadable /
    missing / no stream). `read_text(path) -> str|None` is injected in tests."""
    if not stream:
        return None
    root = _repo_root_for(cwd) or os.path.abspath(cwd or os.getcwd())
    path = os.path.join(root, STREAMS_DIR, "%s.md" % stream)
    if read_text is not None:
        return read_text(path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def tenant_guide(cwd, stream, *, read_text=None):
    """`(url|None, ticket|None)` for the tenant's client guide.

    (url, None)  -- `navody_url: <https…>`;
    (None, "#N") -- `navody_url: NONE — #N`;
    (None, None) -- UNKNOWN (file/line missing or the value is neither a URL
                    nor a NONE+ticket) -> the gate fails CLOSED."""
    text = _read_stream_file(cwd, stream, read_text=read_text)
    if text is None:
        return (None, None)
    m = _NAVODY_URL_RE.search(text)
    if not m:
        return (None, None)
    val = (m.group(1) or "").strip()
    if val.lower().startswith(("http://", "https://")):
        return (val, None)
    nm = _NONE_TICKET_RE.match(val)
    if nm:
        return (None, "#" + nm.group(1))
    return (None, None)


def tenant_surfaces(cwd, stream, *, read_text=None):
    """The per-stream `navody_surfaces:` override as a list, or None when the
    line is absent (the caller then uses DEFAULT_SURFACES). Comma/space
    separated."""
    text = _read_stream_file(cwd, stream, read_text=read_text)
    if text is None:
        return None
    m = _NAVODY_SURFACES_RE.search(text)
    if not m:
        return None
    parts = re.split(r'[,\s]+', (m.group(1) or "").strip())
    surf = [p for p in parts if p]
    return surf or None


# --------------------------------------------------------------------------- #
# Live-link check
# --------------------------------------------------------------------------- #
def _curl_head(url):
    """HTTP status for `url` via a READ-ONLY `curl -sI` (HEAD), or None on any
    error. Short timeout so a hung host never wedges the Stop hook. This is the
    only network touch, and it is a HEAD -- never a body fetch, never a write."""
    try:
        r = subprocess.run(
            ["curl", "-sI", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "6", url],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    code = (r.stdout or "").strip()
    try:
        return int(code[:3])
    except (ValueError, IndexError):
        return None


def _guide_links(message, url):
    """Every https link in `message` that points to the tenant's guide -- a
    prefix match of the fact URL's base (fragment-insensitive), boundary-safe so
    a sibling token sharing only a prefix (`…/tok` vs `…/tokEVIL`) never counts."""
    base = (url or "").split("#", 1)[0].rstrip("/")
    if not base:
        return []
    out = []
    for m in _HTTPS_RE.finditer(message or ""):
        link = m.group(0).rstrip('.,;')
        lnofrag = link.split("#", 1)[0].rstrip("/")
        if lnofrag == base or lnofrag.startswith(base + "/"):
            out.append(link)
    return out


def _repo_static_ok(link, repo_root):
    """For a repo-static guide URL (basename `navody-*.html`), the design also
    requires the file to exist in the repo (a live 200 alone is not enough).
    Returns (ok, reason_or_None). Non-guide-basename URLs and an unresolvable
    repo_root pass (the live 200 governs alone)."""
    base = link.split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if not _GUIDE_BASENAME_RE.match(base):
        return True, None
    if not repo_root:
        return True, None
    # glob.escape the basename: it is derived from a message URL, so a literal
    # `*`/`?`/`[` in it must not become a glob metacharacter (#1073 review nit).
    matches = glob.glob(os.path.join(repo_root, "docs", "**", glob.escape(base)),
                        recursive=True)
    if matches:
        return True, None
    return False, (
        "BLOCKED (#1073): odkaz na klientský návod je ŽIVÝ, ale statický súbor "
        "návodu `%s` NIE JE v repozitári. Doplň `docs/<tenant>/%s` (statické "
        "self-contained HTML so screenshotmi z PROD kópie tenanta) do rovnakého "
        "PR." % (base, base))


# --------------------------------------------------------------------------- #
# Stop-check evaluation
# --------------------------------------------------------------------------- #
_FIX_UNKNOWN = (
    "BLOCKED (#1073): odovzdávaš klientovi / označuješ needs-acceptance, ale "
    "stav klientského návodu (Návody) pre tento stream je NEZNÁMY — chýba fakt "
    "`navody_url:` v `.claude/streams/<stream>.md`. Doplň buď "
    "`navody_url: <https://…/navody-<oblast>.html>` (živý deep-link na sekciu "
    "návodu), alebo `navody_url: NONE — #<ticket návodu>` (návod sa pripravuje). "
    "Bez tohto faktu sa akceptačná odovzdávka nedá overiť — fabrikovaný odkaz je "
    "z princípu nemožný (owner 18.9.2026). API-only funkcia bez produktovej "
    "stránky: `# airuleset:intro-link-ok <dôvod>`.")


def _fix_none(ticket):
    return (
        "BLOCKED (#1073): pre tento stream ešte NEEXISTUJE klientský návod "
        "(`navody_url: NONE — %s`). Akceptačná správa NESMIE odkazovať na "
        "(neexistujúci) návod — namiesto odkazu uveď v hand-offe riadok "
        "`Návody: pripravujeme, %s`. (owner 18.9.2026: „návody buduj a "
        "udržiavaj\")." % (ticket, ticket))


def _fix_missing_link(url):
    return (
        "BLOCKED (#1073): akceptačná odovzdávka nenesie odkaz na sekciu "
        "klientského návodu. Pridaj deep-link na konkrétnu sekciu návodu "
        "(začína na `%s`); akceptačné vlákno na návod ODKAZUJE, neopisuje "
        "kroky." % url)


def _fix_dead_link(url):
    # Fails CLOSED for BOTH a real non-200 AND a curl-infra error (curl missing,
    # DNS/timeout) — `_curl_head` returns None for the latter, so the wording is
    # honest about the unknowable case rather than asserting "dead" (#1073
    # review): the link could NOT be verified LIVE.
    return (
        "BLOCKED (#1073): odkaz na klientský návod sa nepodarilo overiť ako ŽIVÝ "
        "(`curl -sI` != 200, alebo chyba overenia — curl/DNS/timeout). Over a "
        "naprav deep-link na sekciu návodu (`%s`) — odovzdávka smie niesť len "
        "ŽIVÝ odkaz na návod, nikdy fabrikovaný ani neoverený." % url)


def evaluate_stop(url, ticket, message, *, curl=None, repo_root=None):
    """('allow'|'block', reason) for a client-acceptance hand-off.

    - UNKNOWN fact (url is None and ticket is None) -> block, fail CLOSED;
    - NONE fact (ticket set) -> require `Návody: pripravujeme, #N` in the
      message, else block;
    - URL fact -> require a guide deep-link that is LIVE (`curl` 200); for a
      repo-static `navody-*.html` URL the file must also exist in the repo."""
    if url is None and ticket is None:
        return "block", _FIX_UNKNOWN

    if ticket is not None:
        tnum = re.escape(ticket.lstrip("#"))
        # Bounded reps around the optional comma (no adjacent-unbounded-star
        # ambiguity → linear, per #577/#1010 ReDoS discipline); `[ \t]` never
        # spans a newline.
        pat = re.compile(
            r'N[aá]vody[ \t]{0,4}:[ \t]{0,4}pripravujeme[ \t]{0,4},?[ \t]{0,4}#%s\b'
            % tnum, re.IGNORECASE)
        if pat.search(message or ""):
            return "allow", "guide-in-progress ticket referenced"
        return "block", _fix_none(ticket)

    # URL fact.
    links = _guide_links(message, url)
    if not links:
        return "block", _fix_missing_link(url)
    curl = curl or _curl_head
    static_reason = None
    live_found = False
    for link in links:
        if curl(link) == 200:
            live_found = True
            ok, why = _repo_static_ok(link, repo_root)
            if ok:
                return "allow", "live guide link (200)"
            static_reason = why
    if live_found and static_reason:
        return "block", static_reason
    return "block", _fix_dead_link(url)


# --------------------------------------------------------------------------- #
# Same-PR guide-maintenance preflight (airuleset.py handoff composer)
# --------------------------------------------------------------------------- #
def _matches_surface(path, surfaces):
    # Segment-boundary match, never a raw substring: `views/` must NOT match
    # `reviews/`/`previews/`/`interviews/` (#1073 review). A trailing `*`/`**`
    # glob tail on a surface is stripped to its directory fragment.
    p = "/" + (path or "").replace("\\", "/").lstrip("/")
    for s in surfaces:
        seg = (s or "").replace("\\", "/").rstrip("*")
        if not seg:
            continue
        seg = "/" + seg.lstrip("/")
        if seg in p:
            return True
    return False


def _is_guide_file(path):
    return bool(_GUIDE_PATH_RE.search((path or "").replace("\\", "/")))


def _has_navody_na(body):
    # `Navody: n/a — <why>` (a reason after n/a is REQUIRED; a bare `n/a` does
    # not escape). Linear — the reason is read from the captured line remainder,
    # never a backtracking `.*\S`.
    for m in _NAVODY_NA_RE.finditer(body or ""):
        if (m.group("rest") or "").strip():
            return True
    return False


def guide_maintenance(changed_paths, body, *, surfaces=None):
    """(ok, reason_or_None) for the RFR diff.

    When the diff touches a client-visible surface, it must ALSO touch a
    `docs/<tenant>/navody-*.html` guide file OR the RFR body must carry
    `Navody: n/a — <why>`; otherwise refuse, naming the surface to document.
    A non-surface diff passes."""
    surfaces = surfaces or list(DEFAULT_SURFACES)
    paths = changed_paths or []
    touched = [p for p in paths if _matches_surface(p, surfaces)]
    if not touched:
        return True, None
    if any(_is_guide_file(p) for p in paths):
        return True, None
    if _has_navody_na(body):
        return True, None
    return False, (
        "handoff BLOCK: RFR mení klientsky viditeľnú plochu (%s) bez zmeny "
        "návodu (#1073). V rovnakom PR uprav sekciu návodu "
        "`docs/<tenant>/navody-*.html` pre túto obrazovku, alebo pridaj do RFR "
        "riadok `Navody: n/a — <prečo>`. (owner 18.9.2026: „návody buduj a "
        "udržiavaj\")" % ", ".join(touched[:5]))


# --------------------------------------------------------------------------- #
# Stop-hook I/O shell
# --------------------------------------------------------------------------- #
def run(payload, *, curl=None, read_text=None):
    """Resolve inputs, decide, emit. Invoked by the #1042 hook block ONLY on a
    client-acceptance hand-off, so a decision here already assumes the hand-off
    shape. A block emits the fix to stderr + exits 2; otherwise falls through."""
    msg = field_of(payload, "last_assistant_message", "")
    cwd = field_of(payload, "cwd", "") or os.getcwd()
    stream = _current_stream()
    url, ticket = tenant_guide(cwd, stream, read_text=read_text)
    repo_root = _repo_root_for(cwd)
    verdict, reason = evaluate_stop(url, ticket, msg, curl=curl,
                                    repo_root=repo_root)
    if verdict == "block":
        emit_block_stderr(reason)


def main():
    run(read_payload())
    allow()


if __name__ == "__main__":
    main()
