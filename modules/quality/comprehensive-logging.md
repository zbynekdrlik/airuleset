### Comprehensive Logging — More Logs, Never Fewer → on-demand skill `comprehensive-logging`

The full logging protocol (what to log, log levels, DB-vs-log-line decision tree, anti-patterns) moved VERBATIM to the `comprehensive-logging` skill — load it before/while writing feature code, or when reviewing a PR that trims logging. Non-negotiable that survives here: these are MVP, bug-prone projects — log every external boundary, state transition, decision branch, and error path; when in doubt, log it; stripping logs "to reduce noise" needs proof, not a feeling.
