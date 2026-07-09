### Mutation Testing → on-demand skill `mutation-testing`

The full policy moved VERBATIM to the `mutation-testing` skill — load it BEFORE adding/auditing/debugging any mutation CI job or when a gate overruns budget. Non-negotiables that survive here: PR gate is diff-scoped and HARD-BOUNDED (≤20 min; overrun = setup bug, never a bigger timeout); the full-tree sweep is ON-DEMAND via `/mutation-sweep` (user-fired, never cron); surviving mutants in YOUR diff = work not done.
