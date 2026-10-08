# 123盘 share page — verified API map (2026-10, free/no-password share)

Host: `https://{userID}.share.123pan.com/123pan/{shareKey}` — often redirects to `{userID}.mshare.123pan.com` (mobile) or `.share.` (desktop). Same gateway serves both.

Gateway prefixes found on one gateway: `/b/api/...`, `/api/...`, `/gsb/s/...`. Endpoints live under different ones; probe all.

## Anonymous-accessible (raw curl OK, UA + `platform: web` header)

| Purpose | Call |
|---|---|
| Share metadata | GET `/b/api/share/info?shareKey={key}` → name, HasPwd, expiry, uploader |
| Pay type | GET `/b/api/restful/goapi/v1/share/report/info?shareKey={key}` → isPayShare |
| File list | GET `/gsb/s/share-list?OrderId=&SharePwd=&shareKey={key}` → `InfoList[]` with FileId, S3KeyFlag, Etag, Size, FileName |
| Wildcard info | GET `/gsb/s/{shareKey}/anything` → same share info (handler ignores subpath) |
| Download ticket | POST `/b/api/share/download-list` `{ShareKey, fileIdList:[id], fileIds:[id], FileIdList:[id], SharePwd:''}` → `data.uniID` (also works via `/api/` prefix from page context) |

## Download URL — blocked anonymously

POST `/b/api/v2/share/download/info` body `{ShareKey, fileId (singular int), S3KeyFlag, Etag, FileName, Size}`.
- Wrong shape → 400 「文件ID不能为空」 / 「DownloadSingleReq.fileId格式异常」.
- Correct shape, anonymous → **5112 「您需要注册登录或付费后下载」**. Adding `uniID`/`orderId` does not help.
- Batch variant POST `/b/api/v2/file/batch_download_share_info` wants `fileIdList` (fussy shape).

The page hands `uniID` to the desktop client (ULink deep-link, `action: share_download`); client exchanges it for CDN links. Web session cannot complete this.

## Misc

- Guest endpoints requiring a cookie token (e.g. `/b/api/share/visitor/info`) return 401 「cookie token is empty」 without one; no anonymous token-issuing endpoint was found under common names.
- Old logged-in flow (for reference): `www.123pan.cn/b/api/file/list/new?...&shareKey=` → 20101 未登录 without login token.
- Extracting the conversation from a `chatgpt.com/share/...` page: web_extract may refuse; curl with a browser UA returns the full HTML (~1 MB) with the conversation embedded as escaped React-Flight strings — grep for the question text and read the surrounding escaped string literals instead of trying to parse the whole payload as JSON.
