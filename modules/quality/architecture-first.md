### Architecture First

Before writing code, think about the design:

- **Follow existing patterns** in the codebase. Consistency is more important than cleverness.
- **No patchwork.** If the current architecture is wrong for the task, fix the architecture — do not stack workarounds on a broken foundation. A patch that "works for now" becomes permanent technical debt that makes every future change harder. When you find yourself writing a workaround, STOP and ask: "Is the underlying design correct?" If not, propose a redesign to the user before patching.
- **Critical self-review:** Be skeptical of your own conclusions. Before assuming something "doesn't work":
  1. Search documentation and GitHub issues for evidence
  2. Verify from multiple independent sources
  3. Never make assumptions about API behavior without documentation
  4. If debugging, confirm the actual cause before implementing workarounds
- **Study open source code:** When using libraries, read the actual source code to understand internal behavior. Do not rely solely on documentation.
- **No circular development:** Never cycle between approaches (try A, fail, try B, fail, try A again). If an approach should theoretically work, investigate WHY it does not instead of reverting.
