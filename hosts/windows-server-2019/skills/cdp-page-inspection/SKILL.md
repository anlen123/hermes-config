---
name: cdp-page-inspection
description: Use when curl gets an SPA shell or an API rejects raw curl.
category: web
---

# Inspecting JS-heavy pages via headless Chrome (CDP)

Use when plain curl gets an SPA shell instead of content, or when an API rejects raw curl but works from inside the page (cookies, device fingerprint, referer checks). Verified against 123盘 share pages; the workflow is generic.

## Workflow

1. Confirm the page is client-rendered: curl the URL; if you get an empty `<div id=root>`/script-only shell, skip API guessing and go straight to a browser.

2. Launch headless Chrome with a debugging port and a scratch profile:
   `"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --remote-debugging-port=9222 --user-data-dir="$LOCALAPPDATA/hermes/cache/scratch/chrome-profile" --no-first-run --window-size=1600,1000 about:blank &`

3. Drive it from Node (v18+ has built-in WebSocket — no npm install needed). Skeleton: fetch `http://127.0.0.1:9222/json/list`, take the `page` target's `webSocketDebuggerUrl`, then send CDP commands over it. Minimum set:
   - `Network.enable` + handlers on `Network.requestWillBeSent` / `Network.loadingFinished` → log every request (method, URL, POST body) and pull response bodies via `Network.getResponseBody`.
   - `Page.navigate` → wait 15–25 s for the SPA to hydrate.
   - `Runtime.evaluate` with `returnByValue:true, awaitPromise:true` → run page-context code, including `fetch(..., {credentials:'include'})` which inherits the page's cookies/headers — this is the whole trick for APIs that reject raw curl.
   Always write the driver as a standalone scratch `.mjs` file, not a one-liner.

4. Read the captured traffic BEFORE writing any custom API client: the page's own requests reveal the real endpoints, parameter names, and auth flow — far faster than brute-forcing routes.

5. If a request only works in-page, replicate it via `Runtime.evaluate` fetches; extract any issued token from `document.cookie` / `localStorage` via the same evaluate.

## Pitfalls

- Ignore the marketing/domain first hit — the HTML may contain a redirect script to a different subdomain (e.g. `share.` → `mshare.`); watch where the browser actually lands and probe THAT host.
- SPA gateways return several distinct 404 shapes: HTTP `Not Found`, Go `404 page not found`, and JSON `no Route matched` — all mean wrong path, but they tell you which layer rejected it. Enumerate prefixes systematically when one works.
- The page may pick a mobile or desktop layout from the physical screen size, not the window. Headless defaults to a small screen → mobile layout, different DOM and flows. Force desktop with `Emulation.setDeviceMetricsOverride` (width ≥ 1200) BEFORE navigating.
- Clicking via `.click()` on a text node often hits a tooltip/badge wrapper, not the button — check the ancestor chain in the click result and, when in doubt, skip UI-driving entirely and call the API from page context.
- Static asset hosts can be flaky (TLS handshake failures) — retry each download 3–4 times with backoff, and download chunk bundles in parallel (`xargs -P`) rather than serially.
- In-page `fetch` with relative paths uses the page origin automatically; absolute-URL fetches to another host will not carry its cookies.

## 123盘 share pages (domain notes)

See references/123pan-share-api.md for the verified endpoint map.

- **Free share pages give file metadata without login** (share info, file list incl. FileId/S3KeyFlag/Etag/Size), but the actual download URL endpoint rejects anonymous callers with code 5112 (「注册登录或付费后下载」). The page's 「下载免登录」 flow issues a `uniID` task ticket that is consumed by the 123盘 desktop client via a ULink deep link — a browser-only session cannot convert it to a direct URL.
- Practical outcomes to offer the user: log in with their account (then metadata + download endpoints both work), install the official client, or download manually in their browser and hand the file over. Do not burn turns hunting for an anonymous direct link — it is server-side blocked.
