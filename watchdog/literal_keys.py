"""#1319 -- tmux treats a `;` that ENDS a command argument as a command separator
and drops it, so `send-keys -l -- 'do 1 h;'` types `do 1 h`. `_type_literal`
types long text in 120-char chunks, so any chunk ending in `;` lost a byte, the
type verify failed, and the gk /goal arm was never submitted.

`split_trailing_semicolons` rewrites one `keys()` keystroke tuple into the tuples
to send: a literal ending in `;` becomes the literal without its trailing `;`s
plus one `-H 3b ...` hex send for them. Every other tuple passes through as is.
`watchdog.keys` stays the only place that builds the send-keys argv (#1002).
"""


def split_trailing_semicolons(keystrokes):
    ks = tuple(keystrokes)
    if len(ks) != 3 or ks[:2] != ("-l", "--") or not str(ks[2]).endswith(";"):
        return [ks]
    text = str(ks[2])
    body = text.rstrip(";")
    hex_send = ("-H",) + ("3b",) * (len(text) - len(body))
    return ([("-l", "--", body)] if body else []) + [hex_send]
