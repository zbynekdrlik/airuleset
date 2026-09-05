---
name: view-image-urls
user-invocable: false
description: View image URLs (prnt.sc, imgur, gyazo, ibb.co, .png/.jpg) + JS-walled pages (X.com, twitter.com, Instagram, LinkedIn) + Odoo ir.attachment. Download+Read first; load on pasted image URLs.
---

### Viewing Image URLs and JS-Walled Pages — Download-and-Read FIRST, Browser Only When Needed, NEVER "I can't read this"

**When the user pastes a URL and wants you to SEE or READ it, the FIRST move for an IMAGE/screenshot needs NO browser: download the image to a local file with `curl` and open that file with the Read tool — the Read tool renders local image pixels, so you never needed a browser to SEE a picture. A browser (Playwright) is only for a genuinely hostile JS-gated CDN, or for a JS-walled social page (X/Twitter, Instagram, Facebook, LinkedIn) whose CONTENT is client-rendered. WebFetch is still wrong for all of these — it returns raw HTML/text or an empty JS shell — but the fix is download-and-Read for images, browser for JS-walled pages, never a giving-up "I can't read X".**

This is the rule that ends the inconsistency (WebFetch cannot read an image): an owner-pasted screenshot is read by downloading it and Read-ing it, with zero browser and zero per-project setup — the cheapest path even now that Playwright is force-enabled in every managed project (#542, which reversed #415's default-off).

#### Trigger — any of these

- Screenshot hosts: `prnt.sc`, `prntscr.com`, `image.prntscr.com`, `img.lightshot.app` (Lightshot), `gyazo.com`, `imgur.com` / `i.imgur.com`, `ibb.co`, `postimg.cc`, `snipboard.io`, `prntscrn.com`
- Any direct image file URL: ends in `.png` / `.jpg` / `.jpeg` / `.webp` / `.gif` / `.bmp`
- **JS-walled / bot-blocked social posts**: `x.com`, `twitter.com`, `mobile.twitter.com` post/status links; `instagram.com`, `facebook.com`, `linkedin.com` post links — these serve an empty JS shell or a login interstitial to a plain-text fetcher, so WebFetch reads nothing useful even though the content is public in a real browser.
- Any URL the user describes as "screenshot", "image", "this picture", "see this", "look at this post/tweet", "read this"
- **WebFetch already returned empty/blocked/login-wall content for a pasted link** — that is the signal to switch to download-and-Read (image) or Playwright (JS-walled page), not to give up.

#### FIRST path — images (wrapper pages AND direct images): download locally, then Read (NO browser)

The Read tool renders local image files — so the whole job is: get the real image bytes onto a local scratch file (pick a unique path — e.g. `mktemp --suffix=.png` — on a shared, many-workers box; `/tmp/shot.png` below is only illustrative), then Read that file. No browser needed for a screenshot.

1. **Direct image URL** (ends in `.png`/`.jpg`/`.webp`/...) — download it straight to a scratch file and Read it:
   ```bash
   UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
   curl -sSL -A "$UA" "$IMAGE_URL" -o /tmp/shot.png   # then verify + Read
   ```
2. **Wrapper page** (prnt.sc / Lightshot, imgur.com/<id>, gyazo, ibb.co) — the page is HTML and the real screenshot lives in its `og:image` meta tag, which is STATIC (server-rendered, no JS), so a plain `curl` reads it:
   ```bash
   UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
   curl -sSL -A "$UA" "$WRAPPER_URL" -o /tmp/wrapper.html    # wrapper needs a browser UA (bare curl → HTTP 520)
   IMG=$(grep -oiE 'og:image[^>]*content="[^"]*"' /tmp/wrapper.html | grep -oE 'https?://[^"]+' | tail -1)
   curl -sSL -A "$UA" -e "$WRAPPER_URL" "$IMG" -o /tmp/shot.png   # CDN: send UA + Referer (the wrapper URL)
   file /tmp/shot.png                                             # expect: PNG/JPEG image data, WxH
   ```
   Then **Read `/tmp/shot.png`** — the Read tool renders the local image pixels and you actually SEE the screenshot. (Live-proven 2026-08-18 on `https://prnt.sc/oWhJ7vO62WGg`: wrapper UA-fetch → `og:image=https://img.lightshot.app/...png` → curl → `image/png 143272 B, 2090x951` → Read showed the real screenshot content. #541.)
3. **Verify it is a real image before trusting it** — `file /tmp/shot.png` must report `PNG/JPEG/... image data` (content-type `image/*`, size more than a few KB). A downloaded file that is `HTML document` / `ASCII text`, tiny (< ~1 KB), or empty is NOT the screenshot — it is an error/placeholder page.
4. **Placeholder / removed / expired detection** — do NOT pretend you saw content:
   - The wrapper HTML has **no `og:image`** meta (a removed/expired Lightshot code redirects to the generic `Lightshot — screenshot tool` landing page, which carries no screenshot `og:image`) → the screenshot is gone; report "removed / expired", download nothing.
   - The wrapper fetch returns a tiny non-HTML body (e.g. bare `error code: 520`) → you sent no browser `User-Agent`; add the `-A "$UA"` above and retry (do NOT conclude the screenshot is unreadable).
   - The downloaded image looks like a "removed / expired" placeholder graphic → say so; never fabricate content.
   - **Host caveat:** the "no `og:image` → removed" signal is reliable for prnt.sc / Lightshot (a dead code has no screenshot `og:image` at all). Some hosts instead serve a FALLBACK `og:image` on a dead/expired link (gyazo returns a default graphic), so for those the `file` + size + placeholder-graphic checks above are the real backstop — a present `og:image` does NOT prove the screenshot still exists.

#### If the no-browser path genuinely fails (a hostile JS-gated CDN) — browser fallback

A rare CDN gates the raw image behind JavaScript / a per-request token so `curl` cannot reach the bytes. THEN, and only then, use a browser — Playwright is force-ENABLED in every managed project (#542), so no setup is needed:

1. **`browser_navigate` the CDN URL directly, then `browser_take_screenshot`** — no opt-in, no settings edit: the plugin is installed AND enabled in every project, the browser cache is warm, and Chrome spawns lazily on this first browser call.
2. **Dispatch a general-purpose agent that has bundled Chromium** — the sanctioned fallback (this is exactly what montalu3 did, correctly) for when even the local Playwright genuinely cannot reach the CDN: dispatch a fresh agent whose environment carries a bundled-Chromium Playwright, have it open the URL and return the screenshot/description.

**#542-aware messaging — never lie about the tool state.** On a managed box **Playwright is INSTALLED and force-ENABLED in every project (#542, which reversed #415's default-off)** — the browser is lazy (Chrome spawns only on the first browser call), so availability everywhere costs nothing until used. Therefore:
- **NEVER say "Playwright is not installed" / "Playwright nie je nainštalovaný"** — it is installed AND enabled, and for a screenshot you don't need it at all (download-and-Read). That phrasing is BANNED because it is factually false on a managed box.
- **NEVER answer "I can't read this"** (or any rewording — "this link isn't accessible", "I don't have access", "requires login") for an image: you can download it and Read it.

#### Procedure — X/Twitter, Instagram, Facebook, LinkedIn posts (JS-walled pages, genuinely need a browser)

These are client-rendered SPAs: the CONTENT (post text, author, replies) is built by JavaScript and there is no static `og:image` of the whole post, so download-and-Read cannot read them — a browser IS required. Playwright is enabled in every project (#542), so just drive it directly (bundled-Chromium agent fallback above if the local browser can't reach it), then:

1. **`browser_navigate(url)`** — open the post URL directly. For X/Twitter, use the direct status URL form (`https://x.com/<user>/status/<id>`) rather than a shortened or redirected link, first try.
2. **Wait for content to load** (`browser_wait_for` on visible text, or a short pause) — the accessibility tree is empty until the client-side render finishes.
3. **`browser_snapshot`** — this gives you the accessibility tree, which IS the post text, author, timestamps, and visible replies. This is how you READ the post, not just see it.
4. **`browser_take_screenshot`** when the visual layout matters (images/video in the post, or the user explicitly asked to "look at" it) — in addition to the snapshot, not instead of it.
5. **Login wall / interstitial gotcha (X.com specific):** x.com sometimes shows a "Log in to see more" wall over public content. If the first navigate shows a wall:
   - Retry with a fresh `browser_navigate` to the same direct status URL once — the wall is often inconsistent per-request.
   - Do NOT reach for a `nitter` mirror as a first resort — public nitter instances are unreliable/mostly dead and waste a turn.
   - If the wall persists after the retry, report EXACTLY what IS visible in the snapshot (author name, partial text, media count) rather than claiming "cannot read X.com".
   - Attach the screenshot when reporting a partial/blocked result so the user can see what you saw.

#### The iron rule

**NEVER answer "I can't read this" (image or page) while you have an untried path.** For an image: download it locally with curl and open it with the Read tool first. For a JS-walled page: render it in Playwright first (navigate + snapshot, with one retry on a login wall). Only after a genuine attempt may you report what is actually blocked — and even then, show what you DID get (the placeholder state, or the partial snapshot), never a bare refusal. And NEVER claim Playwright "is not installed" — it is installed AND enabled on every managed box (#542), and an image never needed it.

#### System / client-message attachments (Odoo ir.attachment, mail attachments) — the SAME rule, a different channel

**A client or system message can carry an attachment through the SYSTEM itself, not a pasted URL — Odoo `ir.attachment` on a `mail.message`, a ticket-system attachment, a support-inbox attachment.** The doctrine is identical to the URL case above: to actually READ a message, you SEE every attachment it carries BEFORE you interpret the text — the attachment is a PRIMARY source, equal to the text, never optional context you skip because the text alone "seems clear enough".

Incident that created this half of the skill: an odoo-erp stream read a client's Discuss reply (`mail.message` 1742799) over the Odoo API WITHOUT fetching `attachment_ids`, interpreted the request from the bare text alone, and shipped the wrong/incomplete fix — the client had attached a screenshot (`ir.attachment` 13204) circling the exact UI element the text alone did not make clear (airuleset #709, 2026-08-25/26).

**The recipe:** fetch the message WITH `attachment_ids` in the read (never a bare `body`/`subject` read); for every id, `ir.attachment.read(fields=['name','mimetype','datas'])` returns base64 in `datas` — decode it, write it to a local scratch file, then Read that file exactly like a downloaded image above (or view/convert a non-image attachment). The full XML-RPC recipe + working code shape lives in `odoo-client-messaging`'s companion `read-with-attachments.md` — load it before interpreting ANY Odoo Discuss client message.

**Anti-pattern:** "spracoval som správu" / "I processed the message" / "I read and responded" when the fetch never carried `attachment_ids`, or an attachment was fetched but never actually downloaded-and-Read — banned, the same as claiming "I can't read this" for a pasted URL above. A message with an unread attachment is a message you have not actually read. Applies to all rewordings and semantic equivalents, and to every system-attachment channel, not only Odoo.

#### Anti-patterns (all banned — these are WHY it fails or adds friction)

- `WebFetch(prnt.sc/...)` or `WebFetch(x.com/.../status/...)` to "read" the content → **WRONG.** Returns wrapper HTML/JS or an empty client-rendered shell / login wall. For an image, curl the `og:image` to a file and Read it; for a JS-walled page, use Playwright.
- `Read(https://...png)` → **WRONG.** Read opens LOCAL files only — download the image to a local path first, THEN Read that path.
- "Playwright is not installed, so I can't read the screenshot" → **WRONG and factually false.** Playwright is installed AND enabled in every project (#542), AND a screenshot needs no browser — download it and Read it.
- "I cannot read X.com / Twitter / Instagram posts" stated without having tried Playwright at all → **WRONG.** Render it first.
- Jumping straight to a nitter mirror instead of retrying the direct x.com navigate once → **WRONG.** Nitter instances are unreliable; retry the real site first.
- "I couldn't open the URL" / asking the user to paste a screenshot instead → **WRONG.** Download it and Read it (image), or render it in Playwright (JS-walled page).

Applies to all screenshot/image hosts, all JS-walled social platforms, and all rewordings — the intent is: to SEE an image, download it locally and Read the file (no browser); to READ a JS-walled page, render it in Playwright (snapshot for text); never fetch-as-text, never claim a site "can't be read" without a real attempt, and never claim Playwright "is not installed".
