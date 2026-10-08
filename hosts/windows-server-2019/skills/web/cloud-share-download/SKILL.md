---
name: cloud-share-download
description: Use when a user posts a cloud-drive share link to download.
---

# Cloud share link -> local download

## When to Use

- A user pastes a share URL from a cloud-drive service (123pan, Quark, Baidu pan, etc.) and wants the file on this machine.
- A JS-heavy share/download page must be reverse-engineered for its JSON API.

## Workflow

1. **Identify the file first.** Fetch the share page or its info API to learn the file name, size, and whether it needs a passcode. Tell the user what the file is before downloading anything.
2. **Try the platform's share API before scraping.** Most pan services expose JSON endpoints the SPA itself calls. Probe them directly with curl (see references for known hosts/prefixes). A response of `{"code":...}` means the route exists; keep the prefix that works and enumerate siblings of it.
3. **If the API needs a token/cookie, drive a real browser.** Launch headless Chrome with `--remote-debugging-port`, connect with a Node script using Node's built-in `WebSocket` (see references/cdp-network-watch.md), let the page run, and either read its network traffic or `Runtime.evaluate` fetches inside page context so requests carry its cookies.
4. **If download requires an account and none is available, stop and report.** Give the user the file identity you verified plus the concrete options (log in, install official client, manual download). Do not keep probing for bypasses — pan services gate these deliberately.

## Standing rules

- Report honestly what was verified vs. blocked. A partially completed reverse-engineering is a finding, not a failure to hide.
- Third-party downloads get an offer to inspect contents (scripts/executables) before installing anything system-level — this user audits before installing.
- Cloud-drive pages are SPAs: curl of the page HTML yields only a JS shell. Never conclude from the shell that data isn't fetchable.

## Known limitations

- If the platform returns an account-required error for every download path even in page context, that gate is server-side. The session ends with options for the user, not a fabricated direct link.

## References

- references/123pan-share.md — validated 123pan share-page endpoints and flow.
- references/cdp-network-watch.md — headless Chrome CDP recipe: launch, connect, capture requests/bodies, click buttons, evaluate fetches.
