### Investigate Existing Solutions Before Claiming Custom Work

**Before recommending custom development, you MUST investigate what existing libraries, modules, frameworks, OCA repos, or off-the-shelf solutions already provide. Read source code, manifests, READMEs, and recent commits — do not estimate scope from assumption.**

#### The rule

When the user asks "do we need to develop X?" or you are about to propose a custom layer / module / utility / wrapper:

1. **Identify the candidate existing solutions.** Library docs, OCA repos, npm/pypi packages, Odoo Enterprise/Community modules, framework built-ins, the project's own existing code.
2. **Read their actual source.** Manifest files, model definitions, view XML, README. `curl -s raw.githubusercontent.com/...` or `Read` the file. Do not rely on memory or general knowledge of "what library X usually does".
3. **Map each piece of the proposed custom layer to either: (a) provided by existing solution, (b) configuration/data only, or (c) genuinely custom code.**
4. **Report the honest scope.** Lines of code, file count, what is config vs. development. Distinguish "10 lines of Python override" from "build a new feature".

Only after this investigation may you recommend custom development — and the recommendation must reference the specific gaps you found in existing solutions, not a general impression.

#### Why this matters

Over-stating custom scope leads to:

- Reinventing maintained, tested, community-supported code that already works.
- Locking the user into permanent maintenance burden for code that didn't need to exist.
- Wrong build-vs-buy decisions because the "buy" side was never accurately measured.
- Wasted budget on quoted custom work that real existing solutions already cover.

Under-stating custom scope is also a failure — but the more common failure mode is over-stating, because you reach for "we'll write it" without checking what's already on the shelf.

#### Anti-patterns (all rewordings apply)

- "We'll need to build a custom signature widget" — without checking if the framework already has one.
- "This requires a custom mobile UI" — without testing the responsive default first.
- "We need a custom PDF report template" — without reading the existing report template.
- "Permissions need a custom layer" — without checking the module's shipped security groups.
- "We'll need email templates" — without recognising those are configuration data, not code.
- "Custom wizard for X" — without checking the upstream module's wizard list.
- "It's not in OCA / not in the framework" — when you haven't actually read the manifest or run a search.
- Estimating "~500 lines of custom code" without having opened a single existing-solution file.

If you catch yourself listing items in a "custom layer" without having read the existing solution's source for each item — STOP. Investigate first. Then revise.

#### What investigation looks like in practice

For an Odoo / OCA scenario:

```bash
# Read the upstream manifest to see what's shipped
curl -s "https://raw.githubusercontent.com/OCA/<repo>/<branch>/<module>/__manifest__.py"

# List the upstream wizards / views / reports
curl -s "https://api.github.com/repos/OCA/<repo>/contents/<module>?ref=<branch>"

# Read specific files that map to your proposed custom items
curl -s "https://raw.githubusercontent.com/OCA/<repo>/<branch>/<module>/wizard/foo.py"
```

For an npm / pypi scenario: read the package's `index.d.ts` / type stubs, read the README on github (not just npmjs.com), read recent issues for known gaps.

For a framework scenario: read the framework source for the area you're touching. Don't trust general knowledge.

#### When existing solution is partial

If the existing solution covers 80% of the need: state precisely which 20% is missing, why it can't be configured, and whether the gap is fixable upstream (contribute back) or genuinely needs a project-local layer.

#### The rule applies to all forms of "custom"

- "Custom module" / "custom addon" / "custom layer"
- "Build our own" / "we'll wrap it" / "thin shim on top"
- "Need to develop" / "needs custom dev" / "few hundred lines"
- Any rewording that proposes writing project-local code in place of an existing solution

The bar is the same: investigate, then propose. Not the other way around.
