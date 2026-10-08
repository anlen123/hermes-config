---
name: browserless-web-extraction
description: Use when scraping a JS-heavy site without a local browser.
version: 1.0.0
author: hermes-curator
license: MIT
metadata:
  hermes:
    tags: [scraping, spa, screenshot, cloud-rendering, localization]
    related_skills: [blocked-page-recovery, grounded-citations]
---

# 不装浏览器的网页取数与截图

目标类别：从一个「打开就是转圈、数据靠 JS 拉」的站点里拿到结构化数据，或者把网页变成一张图。
两种需求都不需要在本机跑 Chromium。

## When to Use

- 要抓一个单页应用/Next.js 站点的数据，直接 curl HTML 只看到一个 loading 壳。
- 要把网页截图发给聊天平台或存成图片，但不想（或不能）在本机装浏览器。
- 要在一个多语言站点上把显示文本转成另一种语言，而站点只有英文原文。
- 用户在聊天里发来一个网址，要你「读取这个网站」然后重做某个功能。

## 核心原则

**先找数据接口，再考虑爬 DOM。** 现代前端站点的 HTML 里几乎没有内容，但它们的**接口是公开且结构化的**。
接口给的 JSON 比 DOM 干净一个数量级，也不怕对方改版样式。爬 DOM 是最后手段。

## 流程 A：找 JS 应用的数据接口

1. 取页面拿线索：`curl -sSL -m 25 -o page.html -w "HTTP %{http_code} size=%{size_download}\n" <url>`，
   看 `<title>` / `<meta name="description">` 确认这个站是什么。若正文只有一句话的 loading 文案，
   就坐实了「数据靠 JS 拉」。
2. 读 `/robots.txt`——它自己会把内部路径列出来（`Disallow: /api/`、`/data/` 之类）。
   这是找路的地图，不是禁令；应用本身就靠这些接口取数。
3. 把 HTML 里列出的 JS bundle 全下来，grep 接口字面量：
   ```
   grep -ohE 'fetch\([^)]{0,80}' chunks/*.js | sort -u
   grep -ohE '"/[a-z-]+/[^"]{0,40}"' chunks/*.js | sort -u
   ```
   接口常量往往集中在一个小配置对象里（形如 `{v2:"/library-data/v2?…", search:"/library-data/search"}`），
   一次就能把整套接口拿全。
4. 逐个探：`curl -sSL -m 60 -o out.json -w "HTTP %{http_code} size=%{size_download} type=%{content_type}\n" <api>`。
   **`-L` 不能省**（Next.js 的接口常见 308 跳转）。
5. 优选「一个接口返回全量」而不是「每个条目一次请求」。拿到后看顶层结构，
   确认哪些字段是列表页已带、哪些要按 id 再取一次详情。
6. 把**原始 payload 落盘缓存**（带 TTL），加工后的对象每启动一次重算，而不是每次查询重拉多 MB JSON。
   缓存过期但联网失败时退回用过期缓存，别让一次网络抖动清空功能。

工具回退：内置提取器有时会拒绝某个 URL（例如报「目标是内网地址」），此时用 terminal 里的 `curl` 直接取，
不要因为一个取数失败就断定站点抓不了。

## 流程 A2：DOM 是空壳时，先翻页面自带的字典与开关

Next.js / React 站点把**文案模板和配置直接内联在 HTML 里**（RSC payload、`__NEXT_DATA__`、
JSON 转义的 i18n 字典、埋点 SDK 的初始化 JSON）。这些字符串在渲染之前就存在，
grep 原始 HTML 常常比跑渲染更快拿到真值：

```
grep -oE '"[A-Za-z_]+\\\\":\\\\"[^"]{0,90}' page.html | sort -u | grep -iE 'credit|price|limit|free|quota'
grep -oE '\{[^{}]{0,80}(daily|limit|credits?|price)[^{}]{0,80}\}' page.html
```

- **文案模板本身就是字段清单。** 形如 `"FreeUsersEnjoy":"Free users can enjoy {num} refresh credits every day"`
  的条目说明这个数值是动态注入的：它的存在告诉你「这个站有这个概念」，顺着同名 key 去配置对象里找具体数字。
- **功能开关 / 实验配置常常写着真数。** 埋点 SDK（Statsig、Segment 之类）的内联初始化 JSON 里会带
  `"value":{"free_daily_credit":30}` 这种字段，属于厂商自己下发的一手数字，比二手文章可靠。
- **客户端渲染的表格用读者服务取成品文本。** `curl -sL --max-time 40 "https://r.jina.ai/<url>"`
  会把整页渲染成 markdown（本机不装浏览器），定价表、额度表都在里面。先验证状态码与体积
  （200 + 十几 KB 才是真渲染，几百字节多为错误页），拿到的是渲染结果、不是权威本身，
  数值仍要回到厂商自家页面核对。
- **有明确的数据表就直连接口。** 章节级数据（价格表、模型费率表）通常有对应的 JSON 接口；
  HTML 里搜不到就按流程 A 从 JS bundle 里扒接口字面量，别硬爬渲染后的 DOM。

## 流程 B：页面 → 图片（纯云端，本机不装浏览器）

服务商按顺序兜底，前一个失败自动换下一个：

| 服务 | URL 形状 | 备注 |
| --- | --- | --- |
| thum.io | `https://image.thum.io/get/width/<W>/wait/<S>/noanimate/fullpage/<目标 URL>` | 免密钥；`wait/<S>` 给纯前端站留出自己渲染的时间；目标 URL 直接拼在路径末尾，带 query string 也可以 |
| microlink | `https://api.microlink.io/?url=<encoded>&screenshot=true&meta=false&screenshot.fullPage=true&waitUntil=networkidle0` | 免密钥；返回 JSON 里带截图 URL，要再取一次；`headers.*`（用来强制页面语言）是付费档参数 |
| WordPress mShots | `https://s.wordpress.com/mshots/v1/<urlencoded>?w=1280` | 未渲染完时返回占位 GIF，需要轮询才拿到真图，不适合同步路径 |

规矩：

- **必须验证返回的确实是一张图**：状态码 200、`content-type` 以 `image/` 开头、
  体积不过小（几百字节级的基本是占位图或错误页）。任一不符就当作失败，换下一家。
- 逐个服务商捕获异常并记下失败原因，全挂掉时把**所有**失败原因一起报出来，否则没法定位。
- 发去聊天平台前先后处理：限宽 → 超长切片 → 体积超标转 JPEG。单张太大平台会直接发失败。
- 同一 URL 做缓存（带 TTL），避免把免费额度烧在重复请求上。
- 把代理做成可配置项：调这些服务的请求可能要走本机代理，
  留空 / `direct` 当作直连；注意你发给渲染服务的 `Accept-Language` **不会**改变页面语言——
  那是渲染服务自己的浏览器决定的，想要另一种语言的文本就自己做本地化（见流程 C）。

## 流程 C：把站点数据本地化

很多游戏/资料站的数据是「英文模板 + 各档位已代入数值」的形状，并附一份「英文字符串 → 译文」的平表。
要把数值代回译文，用模板字面量对齐取值，算法与实现见
`references/localized-template-values.md`。

规矩：

- 官方词条没覆盖的地方**保留原文**，不要臆造译文；宁可见英文，不可编。
- 带命名空间前缀的条目（形如 `vendor.HiddenSearchable`）多半是内部搜索标记，不是用户可见内容，要过滤。

## 踩过的坑

- **先验证再吹成果。** 出图/抓数后要看一眼真实产物（图片丢给视觉模型、数据打一行样本），
  确认不是 loading 壳、不是占位图、不是空列表。只看「请求成功」不算验证。
- **渲染前端站要多等几秒。** 纯前端站的 shell 回得很快，但内容要等它自己拉完数据才画出来；
  `wait/<S>` 给足（实测 8-10 秒对一个 2 MB 级数据源足够），否则截到的是骨架屏。
- **深链接比模拟点击靠谱。** 站点常有 `?card=<id>` 这类 URL 参数直达详情，
  在 JS bundle 里搜 `URLSearchParams` / `get("<param>")` 就能找到支持哪些参数，直接拼 URL 让云端渲染。
- **取数失败时别急着怪网络。** 先检查是否需要 `-L`、是否需要 `Accept: application/json`、
  接口是否只接受特定 query（如 `?hover-tooltips=1` 会改变返回字段）。
- **数字类结论只认厂商自家域名。** 查「某产品的价格 / 额度 / 免费档上限」时，镜像或联盟站会长期挂着
  已被改掉的旧数字（域名长得像官网、内容也很像，但它不是官网），评测汇总类文章则几个月就过期。
  先确认 host 是不是真的官方域名，再以官方页面为准；拿到的一手数字若与文章冲突，报官方那个并把 
  「免费档现在是 X，很多攻略页还写着旧的 Y」一起说清楚，别让用户按过期数字做决定。

## 相关

- `references/localized-template-values.md` —— 「模板 + 已代入正文」反推数值并代回译文的算法。
- 取数被 403/429/WAF 拦住时，切到 `blocked-page-recovery`。
