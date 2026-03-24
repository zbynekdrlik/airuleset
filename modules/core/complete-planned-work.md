### Complete Planned Work

- Finish 100% of planned work before reporting to the user. Do not stop at partial completion.
- On every work interruption (user message, task switch) or implementation finish, commit your work, push, and ensure CI passes.
- Do not interrupt the user mid-pipeline. The full cycle is: code, commit, push, CI green, verify. Complete all steps autonomously.
- If CI fails, fix and repeat. Only report success after the entire pipeline is green and verified.
- The user is not your test suite. Catch regressions with automated tests, not manual user verification.
