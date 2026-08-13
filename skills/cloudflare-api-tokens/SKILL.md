---
name: cloudflare-api-tokens
description: Use when doing ANY Cloudflare API work (DNS records, zones) or when you need a Cloudflare API token — how to FIND, VERIFY (the verify endpoint lies), NAME, and PERSIST tokens correctly. Load before asking the user for a Cloudflare token, or before deciding a token is invalid.
user-invocable: false
---

# Using Cloudflare API Tokens

Hard-won from the spinbike DNS incident that filed this skill: adding ONE DNS
record cost the owner four round trips, every wrong turn avoidable. This is the
procedure that prevents all four.

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

**Never validate by length or shape.** Cloudflare tokens are NOT a fixed 40 chars
(a real one was `<prefix>_` + 48 chars). Length/format is not a validity test —
the API is. Rejecting a token on its length is how a valid token got called
invalid three times.

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
