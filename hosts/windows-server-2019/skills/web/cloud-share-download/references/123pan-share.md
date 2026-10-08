# 123pan share page (123云盘免登录分享)

Validated against the share-host layout `<uid>.share.123pan.com/123pan/<shareKey>` (2026-09). The SPA may redirect to `.mshare.` host — same APIs.

## Validated endpoints (host: `https://<uid>.share.123pan.com`)

- `GET /gsb/s/<shareKey>` — share metadata (owner, name, HasPwd, expiration, isPayShare).
- `GET /gsb/s/share-list?OrderId=&SharePwd=&shareKey=<key>` — file list: `InfoList[]` with `FileId`, `S3KeyFlag`, `Etag`, `Size`, `DownloadUrl` (usually empty).
- `GET /api/share/info?shareKey=<key>` — same metadata, alternate shape.
- `POST /api/share/download-list` body `{ShareKey, fileIdList:[id], SharePwd:''}` — returns `{data:{uniID}}` WITHOUT login. The uniID is a download-task ticket, but it is consumed by the **123pan desktop client** (handed over via ULink deep link `action:share_download`), not exchangeable for a browser URL.
- `POST /api/v2/share/download/info` body `{ShareKey, fileId:<int>, S3KeyFlag, Etag, FileName, Size}` — the direct-URL endpoint. Returns code 5112 「您需要注册登录或付费后下载」 for anonymous sessions even when called from inside the real page with its cookies, and even with uniID attached.

## Flow facts

- Anonymous web download is gated server-side (feature flag `share_login` grayscale hit). 「下载免登录」marketing means download via their desktop client, not via browser.
- API route prefixes seen: `/gsb/s/*`, `/api/*`, `/api/restful/goapi/v1/*` on the share host. `www.123pan.com` returns the SPA shell for API paths; `www.123pan.cn/b/api/*` exists but requires login.

## Practical outcome

Without a 123pan account or the desktop client, the direct link cannot be obtained. Options to offer the user: log in (account or cookie) then re-call `/api/v2/share/download/info`; install the official client and let it consume the uniID flow; or manual browser download.
