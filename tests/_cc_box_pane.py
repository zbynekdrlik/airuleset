"""A Claude-Code-shaped input box for REAL private-tmux tests (#1157).

Runs INSIDE a pane of a private tmux server (`tmux -L <uuid> -f /dev/null`) so
a test drives the production keystroke primitive end to end: real `send-keys`
(chunked `-l --` types, `BSpace` runs, `Enter`, `Escape`) and real
`capture-pane` reads, with no fake `run`.

The render reproduces what a live Claude Code 2.1.281 pane was measured to do
(issue 1157, `claude --bare` on a private server, width 176, heights 10-51):

* a history line, then the box between two `─` rules, then a footer row;
* the head row is `❯` + NBSP + text; every continuation row is indented by two
  columns; wrapping breaks at spaces, and only a token longer than a row is
  hard-broken mid-token;
* when the wrapped text needs more rows than the box may show, the box SCROLLS:
  only the LAST `--max-rows` rows are drawn, and the first VISIBLE row carries
  the `❯` glyph although it starts mid-payload (live: a 4-row nudge at 176x12-16
  showed `❯ re-audituj (gated → …` with its `nudge: …` head off-screen).

Enter appends the box text to `--transcript` as a top-level `user` turn (the
entry `watchdog._submit_confirmed` reads) and clears the box. Cell width follows
`unicodedata.east_asian_width`, so a wide `❓` never overflows a row."""
import argparse
import codecs
import json
import os
import shutil
import signal
import sys
import termios
import tty
import unicodedata


def _cw(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _width(s):
    return sum(_cw(c) for c in s)


def _hard_split(token, avail):
    parts, cur = [], ""
    for ch in token:
        if _width(cur + ch) > avail:
            parts.append(cur)
            cur = ""
        cur += ch
    return parts + [cur]


def wrap_rows(text, cols):
    """Word-wrap `text` into row strings, each <= cols - 4 cells: the `❯ ` /
    two-space indent takes two columns and CC keeps a two-column right margin
    (live CC 2.1.281 at 176 cols wrapped a 171-cell row rather than grow it to 173)."""
    avail = max(1, cols - 4)
    rows, cur = [], ""
    for word in text.split(" "):
        cand = (cur + " " + word) if cur else word
        if _width(cand) <= avail:
            cur = cand
            continue
        if cur:
            rows.append(cur)
        pieces = _hard_split(word, avail) if _width(word) > avail else [word]
        rows.extend(pieces[:-1])
        cur = pieces[-1]
    rows.append(cur)
    return rows


def render(buf, cols, max_rows):
    rows = wrap_rows(buf, cols) if buf else [""]
    if max_rows and len(rows) > max_rows:
        rows = rows[-max_rows:]
    box = ["❯\xa0" + rows[0] if rows[0] else "❯"] + ["  " + r for r in rows[1:]]
    lines = ["● Hotovo.", "", "─" * cols] + box + ["─" * cols, "  ⏸ manual mode on"]
    return "\x1b[H\x1b[2J" + "\r\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rows", type=int, default=0)
    ap.add_argument("--transcript", default="")
    args = ap.parse_args()
    state = {"buf": ""}
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    dec = codecs.getincrementaldecoder("utf-8")()

    def draw(*_a):
        cols = shutil.get_terminal_size((80, 24)).columns
        os.write(1, render(state["buf"], cols, args.max_rows).encode("utf-8"))

    signal.signal(signal.SIGWINCH, draw)
    draw()
    try:
        while True:
            data = os.read(fd, 4096)
            if not data:
                break
            for ch in dec.decode(data):
                if ch in "\r\n":
                    if state["buf"]:
                        if args.transcript:
                            with open(args.transcript, "a") as f:
                                f.write(json.dumps({"type": "user", "message": {
                                    "content": state["buf"]}}) + "\n")
                        state["buf"] = ""
                elif ch in "\x7f\x08":
                    state["buf"] = state["buf"][:-1]
                elif ch == "\x1b" or ord(ch) < 32:
                    continue
                else:
                    state["buf"] += ch
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
