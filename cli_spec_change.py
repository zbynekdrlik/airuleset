"""cli_spec_change -- `airuleset.py spec-change` (#1106 spec anchoring).

The ONE path a spec deviation takes once the owner has decided it: post a
`Spec-change:` comment on the spec ticket AND edit the named section in the spec
body, so the spec ticket stays the durable truth (never a silently-forked
implementation). Pattern donor: cli_design_record (compose-and-post CLI with an
injectable runner seam).

    airuleset.py spec-change --spec N --section x --body-file <new-section.md> [--repo owner/name]
"""
import re
import subprocess

import gates.spec as _gspec


def edit_section(spec_body, section, new_text):
    """Return `spec_body` with the `## §<section>` section's BODY replaced by
    `new_text` (the header line is preserved; siblings are untouched). Raises
    KeyError when no such section header exists.

    Robust against the #1106-review corruption class (R1#1/R2#5): the section
    boundary is the next ATX header (via `gates.spec.iter_headers`, which SKIPS
    ``` code fences) whose level is <= the target section's level -- so a
    `#`-comment inside a fenced code block, or a `### Sub`-header inside the
    section, never truncates the replacement. A §-carrying header is PREFERRED
    for a numeric section so `## 2 things` can never shadow `## §2 Real`."""
    sec = (section or "").lstrip("§").strip()
    body = spec_body or ""
    headers = list(_gspec.iter_headers(body))
    # Prefer a §-carrying header; fall back to a bare-number header only if no
    # §-header matches (design template is `## §N`, but tolerate `## N`).
    with_sec = re.compile(r'^#{1,6}[ \t]+§[ \t]*' + re.escape(sec) + r'\b')
    any_sec = re.compile(r'^#{1,6}[ \t]+§?[ \t]*' + re.escape(sec) + r'\b')
    target = None
    for pat in (with_sec, any_sec):
        for idx, (hstart, hend, level) in enumerate(headers):
            if pat.match(body[hstart:hend]):
                target = (idx, hend, level)
                break
        if target is not None:
            break
    if target is None:
        raise KeyError("section §%s not found in spec body" % sec)
    idx, hend, level = target
    end = len(body)
    for hstart2, _hend2, level2 in headers[idx + 1:]:
        if level2 <= level:
            end = hstart2
            break
    new_section = "\n" + (new_text or "").rstrip() + "\n\n"
    return body[:hend] + new_section + body[end:]


def _default_runner(argv, body=None):
    """(rc, stdout, stderr) for a `gh` invocation; `body` (when given) is fed on
    STDIN. Uses airuleset._gh_env() for the fleet's per-command token
    resolution (a stream box has no GH_TOKEN in its shell env)."""
    env = None
    try:
        import airuleset
        env = airuleset._gh_env()
    except Exception:
        env = None
    try:
        r = subprocess.run(argv, input=body, capture_output=True, text=True,
                           timeout=30, env=env)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)


def cmd_spec_change(args, runner=None):
    spec_n = getattr(args, "spec", None)
    section = getattr(args, "section", None)
    repo = getattr(args, "repo", None)
    body_file = getattr(args, "body_file", None)
    if not spec_n or not section or not body_file:
        print("spec-change: --spec, --section and --body-file are required")
        return 1
    try:
        with open(body_file, encoding="utf-8") as fh:
            new_text = fh.read()
    except OSError as e:
        print("spec-change BLOCK: cannot read --body-file: %s" % e)
        return 1
    if not new_text.strip():
        print("spec-change BLOCK: --body-file is empty")
        return 1
    run = runner or _default_runner
    R = ["-R", repo] if repo else []

    # 1. Fetch the spec ticket body.
    rc, out, err = run(["gh", "issue", "view", str(spec_n), "--json", "body",
                        "-q", ".body"] + R)
    if rc != 0:
        print("spec-change FAILED: cannot read spec #%s body: %s"
              % (spec_n, err or out or "rc=%d" % rc))
        return 1
    spec_body = out

    # 2. Edit the named section.
    try:
        new_body = edit_section(spec_body, section, new_text)
    except KeyError:
        print("spec-change BLOCK: section §%s not found in spec #%s -- the spec "
              "body must carry a `## §%s ...` header" % (section, spec_n, section))
        return 1

    # 3. Write the edited body back.
    rc2, out2, err2 = run(["gh", "issue", "edit", str(spec_n),
                           "--body-file", "-"] + R, new_body)
    if rc2 != 0:
        print("spec-change FAILED: cannot edit spec #%s body: %s"
              % (spec_n, err2 or out2 or "rc=%d" % rc2))
        return 1

    # 4. Post the durable Spec-change: comment (the audit record).
    sec = (section or "").lstrip("§").strip()
    comment = ("Spec-change: §%s — the spec section was updated (owner-decided "
               "deviation).\n\nNew §%s:\n\n%s\n" % (sec, sec, new_text.rstrip()))
    rc3, out3, err3 = run(["gh", "issue", "comment", str(spec_n),
                           "--body-file", "-"] + R, comment)
    if rc3 != 0:
        print("spec-change FAILED: spec #%s body edited but the Spec-change: "
              "comment could not be posted: %s"
              % (spec_n, err3 or out3 or "rc=%d" % rc3))
        return 1
    print("spec-change: spec #%s §%s updated + Spec-change: comment posted (%s)"
          % (spec_n, sec, out3 or "ok"))
    return 0
