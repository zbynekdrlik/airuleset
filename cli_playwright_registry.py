"""cli_playwright_registry -- which browser builds a live playwright install needs (#1175).

The per-user cache `~/.cache/ms-playwright` is SHARED by every project on the
account. playwright keeps its own registry there: each `.links/<hash>` file holds
the path of a playwright-core package, and that package's `browsers.json` lists
the browser revisions it needs. airuleset's cleanup (cli_playwright_mcp
`_iter_chromium_pair_builds`) must never remove a build that registry still
needs: on dev1 (2026-09-28) every push removed fohmixer's chromium-1208 and
iemmixer's chromium-1243 although both were registered.

`needed_build_dirs(cache)` returns the set of build DIR names needed by the
live links (for example `chromium-1208`, `chromium_headless_shell-1208`), or
None when the registry cannot be read reliably. The caller treats None as
"everything is needed" and removes nothing (fail-safe). A link whose package
dir no longer exists is stale and protects nothing, which is playwright's own
rule too. Stdlib only.
"""

import json
from pathlib import Path


def needed_build_dirs(cache):
    """Set of `<browser_dir>-<revision>` names the live links need, or None."""
    links = Path(cache) / ".links"
    if not links.exists():
        return set()
    needed = set()
    try:
        entries = sorted(links.iterdir())
    except OSError:
        return None
    for link in entries:
        try:
            target = Path(link.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            return None
        if not target.is_dir():
            continue                     # stale link: its package is gone
        try:
            data = json.loads((target / "browsers.json").read_text(encoding="utf-8"))
            browsers = data["browsers"]
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
            return None
        for b in browsers if isinstance(browsers, list) else ():
            name, rev = (b or {}).get("name"), (b or {}).get("revision")
            if isinstance(name, str) and isinstance(rev, str) and rev.isdigit():
                needed.add("%s-%s" % (name.replace("-", "_"), rev))
    return needed
