---
name: view-image-urls
user-invocable: false
description: How to VIEW a URL the user pasted when it must be SEEN, or when WebFetch can't read it — image/screenshot hosts (prnt.sc, imgur, gyazo, ibb.co, direct .png/.jpg/.webp links) AND JS-walled / bot-blocked social pages (X.com, twitter.com, mobile.twitter.com posts, Instagram, Facebook, LinkedIn posts). ALWAYS render in Playwright; for images take a screenshot, for JS-walled pages use browser_snapshot (and screenshot when the visual matters). NEVER WebFetch/Read these — WebFetch is JS-blind and gets bounced by login/bot walls. Load the moment a user message contains such a URL to look at, or when WebFetch already failed/refused to read a pasted link.
---

### Viewing Image URLs and JS-Walled Pages — ALWAYS Playwright, NEVER "I can't read this"

**When the user pastes a URL and wants you to SEE or READ it, and it's either an image/screenshot host or a page that blocks plain-text scraping (X/Twitter, Instagram, Facebook, LinkedIn posts, and similar JS-walled sites), you MUST render it through Playwright (browser MCP). WebFetch and the Read tool CANNOT show you image pixels or execute JavaScript — WebFetch returns raw HTML/text (often a login interstitial or an empty JS shell for these sites), Read opens only LOCAL files. Using either to "view" one of these URLs is the failure mode that makes this work sometimes and fail other times — and reporting "I cannot read X.com" when Playwright is available and untried is the exact banned outcome.**

This is the rule that ends the inconsistency: an image URL or a JS-walled page → Playwright, every time, no exceptions.

#### Trigger — any of these

- Screenshot hosts: `prnt.sc`, `prntscr.com`, `image.prntscr.com` (Lightshot), `gyazo.com`, `imgur.com` / `i.imgur.com`, `ibb.co`, `postimg.cc`, `snipboard.io`, `prntscrn.com`
- Any direct image file URL: ends in `.png` / `.jpg` / `.jpeg` / `.webp` / `.gif` / `.bmp`
- **JS-walled / bot-blocked social posts**: `x.com`, `twitter.com`, `mobile.twitter.com` post/status links; `instagram.com`, `facebook.com`, `linkedin.com` post links — all of these serve an empty JS shell or a login interstitial to a plain-text fetcher, so WebFetch reads nothing useful even though the content is public in a real browser.
- Any URL the user describes as "screenshot", "image", "this picture", "see this", "look at this post/tweet", "read this"
- **WebFetch already returned empty/blocked/login-wall content for a pasted link** — that is the signal to switch to Playwright, not to give up.

#### Mandatory procedure — images (wrapper pages AND direct images)

1. **`browser_navigate(url)`** — open the URL in the real browser.
2. **Wrapper page?** (prnt.sc, imgur.com/abc, gyazo) — the page is HTML, the real image is inside it:
   - `browser_snapshot` or `browser_evaluate(() => document.querySelector('img.screenshot-image, meta[property="og:image"]')?.src || document.querySelector('img')?.src)` to get the real CDN image URL.
   - `browser_navigate(realImageUrl)` to load the raw image on its own.
3. **`browser_take_screenshot`** — this returns the rendered pixels to you. NOW you can actually see it.
4. If the image looks like a "removed / expired" placeholder, say so — do not pretend you saw content.

#### Mandatory procedure — X/Twitter, Instagram, Facebook, LinkedIn posts (JS-walled pages)

1. **`browser_navigate(url)`** — open the post URL directly. For X/Twitter, use the direct status URL form (`https://x.com/<user>/status/<id>`) rather than a shortened or redirected link, first try.
2. **Wait for content to load** (`browser_wait_for` on visible text, or a short pause) — these are JS SPAs; the accessibility tree is empty until the client-side render finishes.
3. **`browser_snapshot`** — this gives you the accessibility tree, which IS the post text, author, timestamps, and visible replies. This is how you READ the post, not just see it.
4. **`browser_take_screenshot`** when the visual layout matters (images/video in the post, or the user explicitly asked to "look at" it) — take it in addition to the snapshot, not instead of it.
5. **Login wall / interstitial gotcha (X.com specific):** x.com sometimes shows a "Log in to see more" wall over public content. If the first navigate shows a wall instead of the post:
   - Retry with a fresh `browser_navigate` to the same direct status URL once — the wall is often inconsistent per-request.
   - Do NOT reach for a `nitter` mirror as a first resort — public nitter instances are unreliable/mostly dead and waste a turn.
   - If the wall persists after the retry, report EXACTLY what IS visible in the snapshot (e.g. author name, partial text, media count) rather than claiming "cannot read X.com" — a partial read is real information; a blanket refusal is not.
   - Attach the screenshot when reporting a partial/blocked result so the user can see what you saw.

#### The iron rule

**NEVER answer "I can't read X.com / this site" (or any rewording — "this link isn't accessible", "I don't have access to Twitter", "that page requires login") while Playwright is available and untried.** Render it first. Only after a genuine render attempt (navigate + snapshot, with one retry on a login wall) may you report what is actually blocked — and even then, show the screenshot and describe exactly what the snapshot DID surface, never a bare refusal.

If Playwright MCP is not installed in the session, that is a missing-tool situation, not a "can't read this site" situation — ask the user to install it (`autonomous-verification.md` → ask for the TOOL, not the test), do not fall back to WebFetch and report failure.

#### Anti-patterns (all banned — these are WHY it fails intermittently)

- `WebFetch(prnt.sc/...)` or `WebFetch(x.com/.../status/...)` to "read" the content → **WRONG.** Returns wrapper HTML/JS or an empty client-rendered shell / login wall. You see no pixels and no real post text. This is the broken path.
- `Read(https://...png)` → **WRONG.** Read opens local files only, never URLs.
- Hotlinking `image.prntscr.com/...` via curl/WebFetch → **WRONG.** The CDN blocks non-browser referers and returns a placeholder. Navigate the page in Playwright instead.
- "I cannot read X.com / Twitter / Instagram posts" stated without having tried Playwright at all → **WRONG.** This is the exact user-reported failure this skill exists to kill.
- Jumping straight to a nitter mirror instead of retrying the direct x.com navigate once → **WRONG.** Nitter instances are unreliable; retry the real site first.
- "I couldn't open the URL" / asking the user to paste a screenshot instead → **WRONG.** You have Playwright. Use it. If Playwright MCP is not installed, ask for the TOOL (`install plugin:playwright`), not for the user to describe the content — see `autonomous-verification.md`.

Applies to all screenshot/image hosts, all JS-walled social platforms, and all rewordings — the intent is: to SEE or READ a URL that WebFetch can't handle, render it in a browser (snapshot for text, screenshot for pixels), never fetch-as-text, and never claim a site "can't be read" without a real render attempt.
