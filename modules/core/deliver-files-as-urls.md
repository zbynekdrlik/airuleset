### Delivering Files to the User — ALWAYS a Clickable LAN URL, NEVER a /tmp Path

**Context gate — related rules you MUST also apply:**
- `no-localhost-urls.md` — use the machine's real LAN IP, never localhost; verify the URL is live (200) before presenting it
- `view-image-urls.md` — the INPUT counterpart (user→you: view an image URL via Playwright); this is the OUTPUT counterpart (you→user: hand back a file URL)
- `receive-files-via-upload-url.md` — the FILE-INPUT counterpart (user→you: they upload via a web URL from `airuleset.py upload`; NEVER ask them to scp)
- `security-basics.md` — never `share` a credentials/secret file unless the user explicitly asked for that exact file

**The user has NO direct filesystem access to the dev machines.** A `/tmp/...` path, an absolute path, or "the file is saved at `<path>`" is USELESS to them — they cannot open it. When the user asks you for a file, or you produce a file they need to open, you MUST deliver it as a **clickable web URL on the machine's LAN IP**, every time, no exceptions. This is the rule that ends the recurring "I keep explaining I can't reach /tmp" friction.

#### Trigger — any artifact the user needs to open or download

A recording / audio / video, an image or screenshot, a PDF, a CSV / dataset / export, a log bundle, a `.zip` / archive, a generated report, a downloaded attachment — anything the user asked for or that you saved for them to look at.

#### The mechanism — one command, always available

```bash
python3 ~/devel/airuleset/airuleset.py share <path-to-file>
```

It copies the file into the always-on file-drop server (systemd `--user`, runs on dev1 AND dev2) and prints a clickable URL:

```
http://100.104.8.125:8788/<token>/<name>
```

The unguessable token IS the link's authorization (click-to-open, no login). The command auto-prunes old files and runs on whichever machine you are on (it uses that machine's LAN IP). Present THAT URL to the user. It already live-checks for 200 before printing — if it errors, fix the file-drop service (it's yours — `airuleset.py filedrop status`), do not fall back to a path.

#### Banned (intent — all rewordings and semantic equivalents)

- Handing a local path as the deliverable: "the file is at `/tmp/centrum/x.wav`", "you can find it in `~/...`", "saved to `/tmp/...`" → **WRONG.** The user cannot open a path. Run `share`, give the URL.
- Claiming a URL is impossible: "recordings aren't hosted via URL", "I don't have a way to give you a link", "this can't be served over the web" → **WRONG.** The file-drop server exists for exactly this. Use it.
- Relying ONLY on a file-attachment tool (it can fail — e.g. `Invalid tool parameters`) and giving up to a `/tmp` path → **WRONG.** The URL is mandatory; an attachment may be sent IN ADDITION, never instead.
- Presenting the URL without it returning 200, or using `localhost`/`127.0.0.1` → **WRONG** (`no-localhost-urls.md`).

The intent: every file the user needs lands in their hands as one clickable LAN link — never a path they can't reach, never a "can't be linked" excuse. Applies to all file types and all rewordings.
