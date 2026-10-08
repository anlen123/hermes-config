# B 站直播接口：本机实测事实

在这台机器（出口在境外、DNS 被代理接管）上逐个接口实测的结果。**判断「接口不通」之前先读这页**，别重复踩。

## 弹幕：必须带 SESSDATA

| 取法 | 实测结果 |
|---|---|
| `dM/gethistory`（`roomid=...`）**匿名** | code=0，**0 条**（恒为空，不是「这个房间没弹幕」） |
| 同上，带**有效** SESSDATA | code=0，**10 条**（同一分钟、同一房间） |
| 同上，带**已失效** SESSDATA | 0 条 —— 等价于匿名，对照实验会失效 |
| `getDanmuInfo`（新接口） | code=**-352** 风控，拿不到 token |
| `room/v1/Danmu/getConf`（旧接口） | code=0，token 有；服务器常给 `hw-sg-live-comet-*`（新加坡） |
| 弹幕 WebSocket（`wss://<host>:443/sub`，op=7 认证） | 连得上，但**认证回包（op=8）收不到**，随即被服务器关闭 |

结论：**走 `dM/gethistory` + `Cookie: SESSDATA=...`**（`plugins/bilibili_live` 就这么取的）；
WebSocket 那条路在本机当前不通，别再花时间。

- 一次 gethistory 只给最近约 10 条 → 想攒弹幕/做词云必须在直播期间**反复轮询累积**，
  并按 `用户名:原文` 去重（同一批会重复返回）。
- **每小时播报卡有弹幕兜底**（`send_hourly_report`）：会话里维护有序 `recent_danmaku`
  （每轮累积弹幕时同步追加、去重、最多 50 条，随状态落盘与续接）；播报时 gethistory 失败
  **或返回空** → 取累积的**最后 10 条**上卡，并记一条 info 说明用了兜底。注意 `_fetch_data` 把
  网络错误吞成空列表而非异常，兜底条件必须判「列表为空」。排查「播报卡没弹幕」：先看那一分钟
  有没有 `fetch_danmaku … 请求失败` 的 warning 和「改用直播中累积」的 info。
- 验登录态：`api.bilibili.com/x/web-interface/nav` 看 `isLogin`。SESSDATA 放 `Cookie` 头，
  形如 `xxx%2Cxxx%2Cxxx%2Axx`，**不要 URL 解码**，去掉浏览器复制时带的结尾 `;`。
- SESSDATA 从 `.env.dev` 的 `BILIBILI_SESSDATA` 读（该文件不入库）；插件改成带 Cookie 后
  生产环境实测：一场十几分钟的直播能累积到 18～20 条弹幕。
- SESSDATA 过期/被踢后的换新流程（二维码登录，免密码）与定时检查见
  `references/bilibili-sessdata-qr-login.md`。

## 其余用到的接口

- 房间状态：`room/v1/Room/getRoomInfoOld?mid=<uid>` 返回 `roomid`/`liveStatus`/`online`，
  **不给 UP 昵称**；昵称要另打 `api.bilibili.com/x/web-interface/card?mid=<uid>`。
- 网络层：`plugins/bilibili_live/direct_net.py` —— DoH 解析真实 IP + 直连会话 + 自动重试，
  绕开系统 DNS 的 fake-ip。

## 别做的事

- 别把「接口偶发失败」记成「未开播」：TLS 握手在这台机器上时好时坏，必须重试。
- 别用失败/过期的凭据做对照实验并据以下结论 —— 先验 `isLogin`。
