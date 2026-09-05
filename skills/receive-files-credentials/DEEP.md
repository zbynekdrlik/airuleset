# Receiving Credentials — Deep Reference (#859)

Companion to `modules/core/receive-files-via-upload-url.md` — the vault-based
credential exchange (both directions: FROM the user, TO the owner) moved here
VERBATIM so the always-on module stays lean. Load this file whenever you need
to send or receive a credential via the vault (`secret request`/`secret
exec`/`secret show`).

#### Receiving CREDENTIALS FROM the User — `secret request`/`secret exec`, NEVER `upload` or chat

**A password, API key, token, PAT, or connection string is a CREDENTIAL, not a file — `upload` above is the wrong channel for it, and pasting it into chat is worse.** When the user needs to give you a credential:

1. `python3 ~/devel/airuleset/airuleset.py secret request <NAME>` — prints a one-shot URL on PRIVATE interfaces only, defaulting to ENCRYPTED (tailscale) ones — a plaintext LAN URL is opt-in only, via `--allow-plain`. Hand the user THAT URL; they paste the value from their own browser. The session learns only that `<NAME>` is ready — never the value.
2. `python3 ~/devel/airuleset/airuleset.py secret exec <NAME> -- <cmd>` — runs `<cmd>` with the value handed to it (an env var named `<NAME>`, or `--stdin`); the command's own captured stdout/stderr are redacted before you see them, but a child that WRITES the value to a FILE is outside that filter — treat what the child does with the value as your own responsibility, same as any other command you run. `secret list` / `secret status <NAME>` read metadata only (never the value); `secret forget <NAME>` drops it early.

**When to reach for which:** the user is sending you BYTES you will read as-is (a recording, an export, a config, a screenshot) → `upload`. The user is sending you a SECRET you will only ever hand to another process, never read → `secret request` + `secret exec`.

**The vault is a DELIVERY CHANNEL, not STORAGE — persist a credential meant for REPEATED use at RECEIPT (#529).** `secret request`/`secret exec` deliberately keep the value at most ~24h (the vault ages it out — that is by design). So a credential you will need AGAIN (an API key a nightly job re-uses, a token every deploy needs) MUST also be written to **durable storage at the moment it is received**, before its first use, or it is lost when the vault expires and the user has to re-generate and re-paste it. The fleet-standard durable home is a **mode-600 file under `~/.secrets/<name>`** (raw value + one trailing newline — READ it, e.g. `$(cat ~/.secrets/<name>)`, never `source` it). Opt-in per credential:

- `python3 ~/devel/airuleset/airuleset.py secret request <NAME> --persist ~/.secrets/<name>` — the value is written to that mode-600 file at the moment the user pastes it (survives the vault TTL from receipt onward). The vault NAME is underscore-only; the on-disk `~/.secrets/<name>` file keeps its natural hyphens — the two are chosen independently, never mechanically derived.
- A consumer reads `~/.secrets/<name>` **FIRST** and falls back to the vault only on a MISS — and on that fallback it PERSISTS: `python3 ~/devel/airuleset/airuleset.py secret exec <NAME> --persist ~/.secrets/<name> -- <cmd>` writes the durable file if it is absent (never overwrites an existing one), so the NEXT use is vault-independent. This is the sanctioned way to capture the value once (a `secret exec` child that writes the value to a file is outside the stdout-redaction filter, as noted above — so persisting from inside `secret exec` is deliberate, not a leak).
- **A ONE-SHOT secret (a rotation bootstrap used once) is NEVER persisted** — leave off `--persist`. The distinction is "repeated future use" vs "single use", the same split this module draws between `upload` (bytes) and `secret request` (secret). Never write a credential's durable file into a git repo (`--persist` refuses a git-tracked path — one `git add` from a committed secret).

**BANNED (all rewordings and semantic equivalents):** asking the user to paste a password/key/token/PAT/connection-string into chat ("send me the API key here", "paste the token", "čo je to heslo?"), and using `upload`/`share` for a credential. Both permanently write the value into the session transcript, where it survives compaction and cannot be revoked — the exact outcome `secret request`/`secret exec` exists to prevent. If a credential ever lands in chat by accident (the user pasted it unprompted), do not echo it back — have them run `secret request <NAME>` to resubmit it through the real channel (and rotate it if they can), then use `secret exec` from then on.

#### Delivering a CREDENTIAL TO the Owner — `secret show`, NEVER chat or "run `cat` yourself"

**The vault has BOTH directions.** `secret request` / `secret exec` above receive a credential FROM the owner; `secret show` (#580) is the OUTPUT counterpart — when the owner needs a credential the BOX already holds (a value in the vault, or a `~/.secrets/<name>` durable file), you deliver it through a ONE-SHOT render URL, never by printing it into chat (transcript-forever, unrevocable) and never by telling the owner to "run `cat` yourself":

1. `python3 ~/devel/airuleset/airuleset.py secret show <NAME>` — a value that is `ready` in the vault; OR `secret show --file ~/.secrets/<name>` — a mode-600 durable file (refused if it is group/world-readable, a symlink, or inside a git repo). Prints a one-shot URL on PRIVATE, ENCRYPTED (tailscale) interfaces by default; `--allow-plain` opts into a cleartext LAN URL. Hand the owner THAT URL.
2. The owner opens it: the page shows the value ONCE (copy button), then the endpoint tears down (after the first view + TTL; no-store headers, never a public interface, ≥128-bit token). The SESSION never sees the value — the server reads it only at GET time, and only the NAME / file path (never the value) ever reaches this process.

**BANNED (all rewordings and semantic equivalents):** printing a credential the box holds into the chat, and telling the owner to `cat` / `less` / `echo` a secret file in their own terminal ("spusti si `cat ~/.secrets/...`"). A credential reaches the owner through `secret show`, exactly as it comes back through `secret request` — a credential has its OWN channel in BOTH directions, never `upload`/`share`/chat.
