---
name: view-image-urls
description: How to VIEW an image/screenshot URL the user pasted (prnt.sc, imgur, gyazo, ibb.co, direct .png/.jpg/.webp links) — ALWAYS render in Playwright and screenshot; NEVER WebFetch/Read an image URL. Load the moment a user message contains an image/screenshot URL to look at.
user-invocable: false
---

### Viewing Image / Screenshot URLs — ALWAYS Playwright, NEVER WebFetch

**When the user pastes an image or screenshot URL and wants you to SEE it, you MUST view it through Playwright (browser MCP). WebFetch and the Read tool CANNOT show you image pixels — WebFetch returns HTML/text, Read opens only LOCAL files. Using either to "view" an image is the failure mode that makes this work sometimes and fail other times.**

This is the rule that ends the inconsistency: image URL → Playwright, every time, no exceptions.

#### Trigger — any of these is an "image URL"

- Screenshot hosts: `prnt.sc`, `prntscr.com`, `image.prntscr.com` (Lightshot), `gyazo.com`, `imgur.com` / `i.imgur.com`, `ibb.co`, `postimg.cc`, `snipboard.io`, `prntscrn.com`
- Any direct image file URL: ends in `.png` / `.jpg` / `.jpeg` / `.webp` / `.gif` / `.bmp`
- Any URL the user describes as "screenshot", "image", "this picture", "see this"

#### Mandatory procedure (works for wrapper pages AND direct images)

1. **`browser_navigate(url)`** — open the URL in the real browser.
2. **Wrapper page?** (prnt.sc, imgur.com/abc, gyazo) — the page is HTML, the real image is inside it:
   - `browser_snapshot` or `browser_evaluate(() => document.querySelector('img.screenshot-image, meta[property="og:image"]')?.src || document.querySelector('img')?.src)` to get the real CDN image URL.
   - `browser_navigate(realImageUrl)` to load the raw image on its own.
3. **`browser_take_screenshot`** — this returns the rendered pixels to you. NOW you can actually see it.
4. If the image looks like a "removed / expired" placeholder, say so — do not pretend you saw content.

#### Anti-patterns (all banned — these are WHY it fails intermittently)

- `WebFetch(prnt.sc/...)` to "read the screenshot" → **WRONG.** Returns wrapper HTML/JS. You see no pixels. This is the broken path.
- `Read(https://...png)` → **WRONG.** Read opens local files only, never URLs.
- Hotlinking `image.prntscr.com/...` via curl/WebFetch → **WRONG.** The CDN blocks non-browser referers and returns a placeholder. Navigate the page in Playwright instead.
- "I couldn't open the URL" / asking the user to paste the image → **WRONG.** You have Playwright. Use it. If Playwright MCP is not installed, ask for the TOOL (`install plugin:playwright`), not for the user to describe the image — see `autonomous-verification.md`.

Applies to all screenshot/image hosts and all rewordings — the intent is: to SEE an image URL, render it in a browser and screenshot it, never fetch-as-text.
