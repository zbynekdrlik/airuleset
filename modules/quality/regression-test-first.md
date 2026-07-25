### Regression Test First — Bug Reports Are Missing-Test Reports → on-demand skill `regression-test-first`

The full RED/GREEN commit protocol moved VERBATIM to the `regression-test-first` skill — load it before fixing any bug, and when writing a bug-fix completion report. Non-negotiable that survives here: every bug fix ships a failing (`[red]`) test commit BEFORE the fixing (`[green]`) commit, same PR, commit order non-negotiable — a test written after the fix never proved it catches the bug. Hook-enforced (`[no-test: <reason>]` bypass is logged, never for a real bug).
