---
name: view-image-urls
user-invocable: false
description: How to VIEW a URL the user pasted when it must be SEEN, or when WebFetch can't read it — image/screenshot hosts (prnt.sc, imgur, gyazo, ibb.co, direct .png/.jpg/.webp links) AND JS-walled / bot-blocked social pages (X.com, twitter.com, mobile.twitter.com posts, Instagram, Facebook, LinkedIn posts). For an IMAGE the FIRST move needs NO browser — download it locally with curl and open it with the Read tool (Read renders local image pixels). Playwright is only for a genuinely hostile JS-gated CDN or a JS-walled social page. NEVER answer "I can't read this", and NEVER claim Playwright "is not installed". Load the moment a user message contains such a URL to look at, or when WebFetch already failed/refused to read a pasted link.
---

### Viewing Image URLs and JS-Walled Pages — Download-and-Read FIRST, Browser Only When Needed, NEVER "I can't read this"

**When the user pastes a URL and wants you to SEE or READ it, the FIRST move for an IMAGE/screenshot needs NO browser: download the image to a local file with `curl` and open that file with the Read tool — the Read tool renders local image pixels, so you never needed a browser to SEE a picture. A browser (Playwright) is only for a genuinely hostile JS-gated CDN, or for a JS-walled social page (X/Twitter, Instagram, Facebook, LinkedIn) whose CONTENT is client-rendered. WebFetch is still wrong for all of these — it returns raw HTML/text or an empty JS shell — but the fix is download-and-Read for images, browser for JS-walled pages, never a giving-up "I can't read X".**

This is the rule that ends the inconsistency AND the friction #415 introduced: an owner-pasted screenshot is read by downloading it and Read-ing it, with zero browser and zero per-project setup.

#### Trigger — any of these

- Screenshot hosts: `prnt.sc`, `prntscr.com`, `image.prntscr.com`, `img.lightshot.app` (Lightshot), `gyazo.com`, `imgur.com` / `i.imgur.com`, `ibb.co`, `postimg.cc`, `snipboard.io`, `prntscrn.com`
- Any direct image file URL: ends in `.png` / `.jpg` / `.jpeg` / `.webp` / `.gif` / `.bmp`
- **JS-walled / bot-blocked social posts**: `x.com`, `twitter.com`, `mobile.twitter.com` post/status links; `instagram.com`, `facebook.com`, `linkedin.com` post links — these serve an empty JS shell or a login interstitial to a plain-text fetcher, so WebFetch reads nothing useful even though the content is public in a real browser.
- Any URL the user describes as "screenshot", "image", "this picture", "see this", "look at this post/tweet", "read this"
- **WebFetch already returned empty/blocked/login-wall content for a pasted link** — that is the signal to switch to download-and-Read (image) or Playwright (JS-walled page), not to give up.

#### FIRST path — images (wrapper pages AND direct images): download locally, then Read (NO browser)

The Read tool renders local image files — so the whole job is: get the real image bytes onto a local scratch file (pick a unique path — e.g. `mktemp --suffix=.png` — on a shared, many-workers box; `/tmp/shot.png` below is only illustrative), then Read that file. No browser, no plugin, no per-project opt-in.

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

A rare CDN gates the raw image behind JavaScript / a per-request token so `curl` cannot reach the bytes. THEN, and only then, use a browser — two sanctioned ways, in order:

1. **Per-project one-line opt-in** — add to THIS project's OWN `<repo>/.claude/settings.json` (git-tracked, the project's own repo — never airuleset's):
   ```json
   {"enabledPlugins": {"playwright@claude-plugins-official": true}}
   ```
   Project scope resolves above the user-scope default-off, so this one project gets Playwright while every other stays browser-free. The plugin is already installed and the browser cache is already warm (#415), so no install step — then `browser_navigate` the CDN URL and `browser_take_screenshot`.
2. **Dispatch a general-purpose agent that has bundled Chromium** — the sanctioned fallback (this is exactly what montalu3 did, correctly): dispatch a fresh agent whose environment carries a bundled-Chromium Playwright, have it open the URL and return the screenshot/description. Use this when you cannot or should not edit the current project's settings.

**#415-aware messaging — never lie about the tool state.** On a managed box **Playwright is INSTALLED and its browser cache is provisioned; it is only DEFAULT-DISABLED per project (#415)** — force-enabling it fleet-wide kept a resident ~144MB headless Chrome tree alive in browser-free projects, so it was switched to per-project opt-in. Therefore:
- **NEVER say "Playwright is not installed" / "Playwright nie je nainštalovaný"** — it is installed; it is disabled-by-default, and for a screenshot you don't need it at all (download-and-Read). That phrasing is BANNED because it is factually false on a managed box.
- **NEVER answer "I can't read this"** (or any rewording — "this link isn't accessible", "I don't have access", "requires login") for an image: you can download it and Read it.

#### Procedure — X/Twitter, Instagram, Facebook, LinkedIn posts (JS-walled pages, genuinely need a browser)

These are client-rendered SPAs: the CONTENT (post text, author, replies) is built by JavaScript and there is no static `og:image` of the whole post, so download-and-Read cannot read them — a browser IS required. Use the per-project opt-in / bundled-Chromium agent above to get Playwright, then:

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

**NEVER answer "I can't read this" (image or page) while you have an untried path.** For an image: download it with `curl` and Read the file first. For a JS-walled page: render it in Playwright first (navigate + snapshot, with one retry on a login wall). Only after a genuine attempt may you report what is actually blocked — and even then, show what you DID get (the placeholder state, or the partial snapshot), never a bare refusal. And never explain a gap with "Playwright is not installed" — it is installed on every managed box, default-disabled, and an image never needed it.

#### Anti-patterns (all banned — these are WHY it fails or adds friction)

- `WebFetch(prnt.sc/...)` or `WebFetch(x.com/.../status/...)` to "read" the content → **WRONG.** Returns wrapper HTML/JS or an empty client-rendered shell / login wall. For an image, curl the `og:image` to a file and Read it; for a JS-walled page, use Playwright.
- `Read(https://...png)` → **WRONG.** Read opens LOCAL files only — download the image to a local path first, THEN Read that path.
- "Playwright is not installed, so I can't read the screenshot" → **WRONG and factually false.** Playwright is installed (default-disabled, #415), AND a screenshot needs no browser — download it and Read it.
- "I cannot read X.com / Twitter / Instagram posts" stated without having tried Playwright at all → **WRONG.** Render it first.
- Jumping straight to a nitter mirror instead of retrying the direct x.com navigate once → **WRONG.** Nitter instances are unreliable; retry the real site first.
- "I couldn't open the URL" / asking the user to paste a screenshot instead → **WRONG.** Download it and Read it (image), or render it in Playwright (JS-walled page).

Applies to all screenshot/image hosts, all JS-walled social platforms, and all rewordings — the intent is: to SEE an image, download it locally and Read the file (no browser); to READ a JS-walled page, render it in Playwright (snapshot for text); never fetch-as-text, never claim a site "can't be read" without a real attempt, and never claim Playwright "is not installed".
