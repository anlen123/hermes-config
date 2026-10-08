# CDP network watch on headless Chrome (Windows/git-bash host)

Proven pattern for seeing what a JS-heavy page actually requests, and for making requests inside its authenticated context.

## Launch + connect

```bash
("/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new \
  --remote-debugging-port=9222 \
  --user-data-dir="$LOCALAPPDATA/hermes/cache/scratch/chrome-profile" \
  --no-first-run --window-size=1400,900 about:blank &>/dev/null &)
sleep 6; node script.mjs
```

- No `websocket` npm/py package needed: **Node 18+ has a built-in `WebSocket` global**. Poll `http://127.0.0.1:9222/json/list` for a `type:'page'` target and use its `webSocketDebuggerUrl`.
- Kill afterward: `taskkill //F //IM chrome.exe` (double slash in git-bash).

## Script skeleton (.mjs)

- `Network.enable` + `Page.enable`, then `Page.navigate`.
- On `Network.requestWillBeSent`: record method/url/postData/headers; filter out `.js/.css/.png/...` to cut noise.
- On `Network.loadingFinished`: fetch bodies with `Network.getResponseBody` (keep a pending map keyed by the CDP `id`, and the requestId->url map).
- Page context fetches: `Runtime.evaluate` with `{expression: "fetch('/api/...',{credentials:'include'}).then(r=>r.text())", returnByValue:true, awaitPromise:true}` — carries the page's cookies and origin, so authenticated-ish requests work.
- Force desktop layout on pages that mobile-detect by `screen.width`: send `Emulation.setDeviceMetricsOverride {width:1600,height:1000,mobile:false}` BEFORE navigate.

## Pitfalls

- Clicking React buttons: `el.click()` on the text-bearing leaf often hits a tooltip/badge wrapper, not the handler. Verify by watching for the network request the click should fire; a click that produces no request was the wrong element.
- The gateway distinguishes route misses: `{"message":"no Route matched with those values"}` (gateway-level) vs `404 page not found` (app-level) vs JSON `{code:...}` (route exists). Use that signal when enumerating prefixes/paths.
- Outbound TLS to CDNs may flake (`schannel: failed to receive handshake`): retry the same curl up to ~8 times with short sleeps before concluding failure. For bulk file downloads use `xargs -P 12` with `--retry 6 --retry-all-errors`.
- `awaitPromise:true` + `returnByValue:true` are required or `Runtime.evaluate` returns a promise handle instead of the value.
