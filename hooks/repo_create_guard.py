"""#1270: does a Bash command create a GitHub repository? Prints `bypass <reason>`,
`create`, or nothing. Tokenises like the shell, so quoted text is never a command,
and checks only the command position. Exit 0 always (fail open: a guard, not a wall)."""
import json
import os
import re
import shlex
import sys

WRAPPERS = {"env", "command", "sudo", "time", "nohup", "exec", "xargs", "timeout"}
SHELLS = {"bash", "sh", "zsh", "dash"}
SEPARATORS = set("();<>|&\n")


def tokens(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return text.split()


def simple_commands(text):
    cur = []
    for tok in tokens(text):
        if tok and set(tok) <= SEPARATORS:
            if cur:
                yield cur
            cur = []
        else:
            cur.append(tok)
    if cur:
        yield cur


def gh_creates(args):
    if args[:2] in (["repo", "create"], ["repo", "fork"]):
        return True
    if args[:1] != ["api"]:
        return False
    rest = args[1:]
    for i, tok in enumerate(rest[:-1]):
        if tok in ("-X", "--method") and rest[i + 1].upper() == "GET":
            return False
    path = [x.lstrip("/") for x in rest if not x.startswith("-")]
    if not any(re.fullmatch(r"(user|orgs/[^/]+)/repos|repos/.+/(generate|forks)", p) for p in path):
        return False
    return "POST" in rest or any(x in ("--field", "--raw-field", "--input")
                                 or x.startswith(("-f", "-F")) for x in rest)


def creates(toks, depth):
    while toks and re.fullmatch(r"\w+=.*", toks[0]):
        toks = toks[1:]
    if not toks or depth > 3:
        return False
    head = os.path.basename(toks[0])
    if head == "gh":
        return gh_creates(toks[1:])
    if head in WRAPPERS:
        return any(creates(toks[i:], depth + 1) for i in range(1, len(toks))
                   if os.path.basename(toks[i]) in {"gh", "ssh"} | SHELLS)
    if head in SHELLS:
        for i, tok in enumerate(toks[:-1]):
            if re.fullmatch(r"-\w*c\w*", tok):
                return command_creates(toks[i + 1], depth + 1)
        return False
    if head == "ssh":
        return any(command_creates(" ".join(toks[i:]), depth + 1) for i in range(2, len(toks)))
    return False


def command_creates(text, depth=0):
    text = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\s*\1\s*$", " ", text, flags=re.S | re.M)
    unsingle = re.sub(r"'[^']*'", " ", text)
    subs = re.findall(r"\$\(([^()]*)\)|`([^`]*)`", unsingle)
    if depth <= 3 and any(command_creates(a or b, depth + 1) for a, b in subs):
        return True
    return any(creates(t, depth) for t in simple_commands(text))


def main():
    try:
        cmd = (json.load(sys.stdin).get("tool_input") or {}).get("command") or ""
    except ValueError:
        return
    m = re.search(r"airuleset:repo-create-ok\s*(.*)", cmd)
    if m:
        print("bypass " + m.group(1).strip())
    elif command_creates(cmd):
        print("create")


if __name__ == "__main__":
    main()
