---
name: cloudflare-api-tokens
description: Use when doing ANY Cloudflare API work (DNS records, zones), when you need a Cloudflare API token, or when ANY Cloudflare credential appears — a `cfat_` account-owned token, a legacy user token, a Global API Key, or an Origin CA key. Covers the credential taxonomy (which artifact is which, its shape + auth header + correct verification) and how to FIND, VERIFY (the verify endpoint LIES — it returns `Invalid API Token` for account-owned `cfat_` tokens BY DESIGN), NAME, and PERSIST tokens correctly. Load before asking the user for a Cloudflare token, before deciding a token is invalid, or before rejecting/escalating a credential on its length or prefix.
user-invocable: false
---

# Using Cloudflare API Tokens

Hard-won from the spinbike DNS incident that filed this skill: adding ONE DNS
record cost the owner four round trips, every wrong turn avoidable. This is the
procedure that prevents all four.

## 0. Which credential is this? — identify the artifact BEFORE you verify

Cloudflare issues FOUR different credential artifacts, and the #1 way a VALID
one gets wrongly rejected is judging it by the wrong yardstick (the miva
incident, 2026-08-15: a valid account-owned `cfat_` token was rejected TWICE
and the owner sent chasing a Global API Key it never needed). Identify which
artifact you hold FROM ITS SHAPE, then verify it with the check that is
actually TRUE for that type:

| Artifact | Shape | Auth header | Correct verification |
|---|---|---|---|
| **User API token** (legacy) | ~40 chars, no prefix | `Authorization: Bearer <token>` | capability probe `GET /zones` (universal — §2); `GET /user/tokens/verify` works ONLY if the token carries User-level read — a zone-scoped one 401s |
| **Account-owned API token** | **`cfat_` prefix, ~53 chars** | `Authorization: Bearer <token>` | `GET /accounts/{account_id}/tokens/verify` **OR** capability probe `GET /zones` — **`/user/tokens/verify` returns `Invalid API Token` on it BY DESIGN** (§2) |
| **Global API Key** | 37 hex chars | `X-Auth-Email: <email>` + `X-Auth-Key: <key>` — **NEVER `Bearer`** | `GET /user` |
| **Origin CA key** | `v1.0-` prefix | `X-Auth-User-Service-Key` (Origin-CA endpoints only) | not a normal REST API token — never send it as `Bearer` |

Rules this taxonomy exists to enforce:

- **Identify by prefix/shape, then PROBE — never REJECT on shape.** A `cfat_`
  token is longer (~53) than a legacy one (~40); that is not a defect. Length
  or prefix is NEVER a validity verdict — only the capability probe (§2) is.
- **`/user/tokens/verify` is NOT the universal check** — the capability probe
  (§2) is. It succeeds ONLY for a token carrying User-level read: it 401s a
  zone-scoped token (user-owned, no user-level permission) and returns
  `Invalid API Token` for an account-owned `cfat_` token — both BY DESIGN. What
  decides the outcome is the token's SCOPE, not merely who owns it.
- **Never escalate to a Global API Key when a scoped token works.** The Global
  API Key is the most dangerous credential Cloudflare issues (whole account,
  every zone, no scoping). If a `cfat_` / zone-scoped token passes the probe,
  USE IT — do not send the owner to mint a Global API Key. Reaching for the
  Global Key because the verify endpoint lied is the exact miva over-escalation.
- **Trim whitespace before use.** A web-form / secret-channel paste carries a
  trailing newline; a stray `\n` breaks the `Authorization` header and looks
  exactly like a bad token — `tr -d '[:space:]'` (see §2).
- **The value is a secret — never print it.** Diagnostics are `len=<n>` and
  `prefix=<first ≤5 chars>` ONLY, never the token itself, never into chat or a
  committed file (persist via the secret channel, §6).

## 1. Look BEFORE asking — the token almost always already exists

Persistent Cloudflare tokens live in `~/.secrets/cloudflare-<project>` on this
box (e.g. `cloudflare-spinbike`, `cloudflare-montalu`,
`cloudflare-newlevelmedia-admin`). **Check there first — do NOT ask the user for
a token you may already have:**

```bash
ls ~/.secrets/cloudflare-*
CF_TOKEN=$(cat ~/.secrets/cloudflare-<project>)   # value stays in the var, never echoed
```

Then **verify the candidate against the ZONE it is FOR** (step 2) — a token can
be present but dead; test it, never assume.

- **newlevel.media, montalu and slovnormal all sit on the SAME Cloudflare
  account**, so a token for one project may already cover another's zone.
- **Running the Cloudflare TUNNEL does NOT imply Cloudflare API access.** A tunnel
  is created interactively (`cloudflared tunnel login`) and afterwards needs only
  its credentials JSON — there is no API token anywhere in that flow.

## 2. Validity test = the RESOURCE, never `/user/tokens/verify`

**`GET /user/tokens/verify` returns `401 Invalid API Token` for a perfectly VALID
zone-scoped token** — that endpoint is a *User*-scoped check, and a zone-scoped
token carries no user-level permission for it, so it 401s even a good token
(observed behaviour, and the whole reason this skill exists). This is THE trap. Test
the token against the resource it is actually FOR:

```bash
# ✅ correct — does this token work for THIS zone?
curl -s -H "Authorization: Bearer $CF_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=example.com" | jq '.success, .result[0].id'
# success:true + a zone id  =  the token works for that zone

# ❌ NEVER use this to judge a zone-scoped token — it 401s on a VALID one:
#   curl ... https://api.cloudflare.com/client/v4/user/tokens/verify
```

**The same trap catches an account-owned `cfat_` token too:** `/user/tokens/verify`
rejects it with `Invalid API Token` (error 1000) — a VALID token refused, because
it is the WRONG endpoint for that token TYPE, not because the token is bad (the
zone-scoped 401 above carries the same message; the HTTP code is not the signal —
the endpoint mismatch is). Verify an account-owned token at
`GET /accounts/{account_id}/tokens/verify`, or — the universal answer that needs
no account id — the SAME capability probe. `GET /zones` (`success:true` + the
zones the token can see) is true for EVERY token type, so make it your only
verdict and skip `/user/tokens/verify` unless you positively hold a
User-level-scoped token:

```bash
# ✅ universal capability probe — TRUE for cfat_ account tokens AND zone/user tokens.
# Trim first: a web-form / secret-channel paste carries a trailing newline that
# breaks the Authorization header and looks exactly like a bad token.
CF_TOKEN=$(tr -d '[:space:]' < ~/.secrets/cloudflare-<project>)
curl -s -H "Authorization: Bearer $CF_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones" | jq '.success, (.result|length)'
# success:true  =  the token works. That is the whole verdict — do NOT then
# escalate to a Global API Key because /user/tokens/verify disagreed (§0).
```

**Never validate by length or shape.** Cloudflare tokens are NOT a fixed 40 chars
(a real one was `<prefix>_` + 48 chars). Length/format is not a validity test —
the API is. Rejecting a token on its length is how a valid token got called
invalid three times.

**Hook-gated (#631).** An owner-facing claim that a Cloudflare credential is invalid
is now BLOCKED by `stop-check-prose-violations.sh` unless the same message shows a
capability probe (`GET /zones` or `GET /accounts/{id}/tokens/verify`) or an explicit
`UNVERIFIED:`. `/user/tokens/verify` is NOT accepted as a probe — treating its answer
as a verdict is exactly the error that cost the owner his master token. And the
situational trigger now injects THIS skill on any script touching `api.cloudflare.com`
(quoted URLs, heredocs, `cfat_`, `secret request cloudflare*`, `~/.secrets/cloudflare*`,
`webterm-access`), so the warning reaches the moment of decision.

## 3. Which token for which job

- **DNS work (the common case): `Zone → DNS → Edit`, scoped to ONE zone.** The
  narrow default — mint or reuse a per-project, per-zone token.
- **Minting tokens (rare): `User → API Tokens → Edit`.** Needed only to CREATE
  tokens, and that permission lives under the **User** group in the Create Token
  screen — picking `Account → …` looks equivalent and is NOT.

## 4. Create a token — the click path

`dash.cloudflare.com/profile/api-tokens` → **Create Token** → template
**Edit zone DNS** → **Zone Resources** `Include / Specific zone / <domain>` →
name it (step 5) → **Continue to summary** → **Create** → the value is shown
**ONCE** (copy it immediately, then persist per step 6).

## 5. Naming — the owner's real pain

Every token this fleet creates gets **`<project>-<purpose> · claude`**, e.g.
`spinbike-dns · claude`. Unlabelled tokens pile up and nobody knows which are
still used. Same convention for a DNS record's own `comment` field — and mind the
**100-char cap** there (a longer comment is a `400`).

## 6. Persist it IMMEDIATELY — on the box that will still exist

The moment a NEW token arrives, store it as `~/.secrets/cloudflare-<project>`
(NOT when first needed — a token held only in the ephemeral vault is lost when it
expires; that happened to a Hetzner token in the same session and cost another
round trip). A token is a CREDENTIAL, so receive it through the secret channel,
never chat/scp (see `receive-files-via-upload-url.md`):

```bash
# The vault NAME passed to `airuleset.py secret` must match [A-Za-z_][A-Za-z0-9_]*
# — underscores, NO hyphens (it doubles as an env-var name). So the vault name is
# cloudflare_<project> (underscores throughout), while the on-disk FILE and the
# dashboard token label keep their natural hyphens. Worked example for spinbike:
#   vault name  cloudflare_spinbike
#   file        ~/.secrets/cloudflare-spinbike
#   dash label  spinbike-dns · claude

# 1. Request a one-shot upload URL and hand it to the user:
python3 ~/devel/airuleset/airuleset.py secret request cloudflare_<project>
#    The URL is CONSUMED by a successful submit ("link na nahratie nefunguje" =
#    it was already used). To re-issue: `secret forget cloudflare_<project>`
#    first (or `secret request --replace` for a still-pending one), THEN re-request.

# 2. Persist to disk WITHOUT the value ever hitting the transcript:
python3 ~/devel/airuleset/airuleset.py secret exec cloudflare_<project> --stdin -- \
  sh -c 'umask 077; cat > ~/.secrets/cloudflare-<project>'
```

Next time, step 1 finds it already there — no round trip.
