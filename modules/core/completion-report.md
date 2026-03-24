### Completion Report

When work is complete, always provide a structured status block:

```
PR: <url> | CI: green | Deploy: verified | Dashboard: <url>
```

- **PR** — the mergeable PR URL (must be green and clean)
- **CI** — status of all CI jobs (must all be green)
- **Deploy** — whether the deployed app was verified running on the target machine
- **Dashboard** — the URL where the user can see the deployed result (if the project has one)

If any field is not applicable (e.g., no dashboard), omit it. If any field is not yet resolved (e.g., CI still running), do not send the completion report — wait until everything is confirmed.

**Never claim "done" without this report.** Never send a partial report with "CI is still running" — that means you are not done yet.
