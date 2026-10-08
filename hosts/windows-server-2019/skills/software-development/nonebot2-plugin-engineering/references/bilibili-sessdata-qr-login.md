# B 站 SESSDATA：二维码登录换新流程（免密码，实测可用）

弹幕功能依赖 `BILIBILI_SESSDATA`（见 bilibili-live-api.md），它会过期/被风控踢掉。换新的正规流程是**二维码登录**：不需要账号密码（NTQQ/密码登录在 B 站一侧被极验滑块挡死，别碰），用户只需手机 B 站 App 扫一次。

## 完整流程（2026-10-02 实测成功）

1. **生成二维码**：
   ```bash
   curl -s -x http://127.0.0.1:7892 "https://passport.bilibili.com/x/passport-login/web/qrcode/generate" -H "User-Agent: Mozilla/5.0" -o qr.json
   ```
   取 `data.url`（二维码内容）与 `data.qrcode_key`（轮询凭据）。直连失败就挂 yeshayun 代理 `-x http://127.0.0.1:7892`（见下）。
2. **画二维码发给用户**：Hermes 的 venv 里有 `qrcode` 包（3.13 系统解释器没有），用它渲染 PNG，回复里 `MEDIA:<绝对路径>` 发出。有效期约 3 分钟，过期就重新生成再发。
3. **用户扫码确认后轮询**：
   ```bash
   curl -s -x http://127.0.0.1:7892 -D poll_headers.txt \
     "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=<key>" -H "User-Agent: Mozilla/5.0"
   ```
   **必须加 `-D` 保存响应头** —— SESSDATA 在 `Set-Cookie` 头里，body 的 `cookie_info.cookies` 是空的。
   踩过：第一次成功时没存响应头，Cookie 直接丢了，只能让用户再扫一次。
4. **提取并验证**：正则取 `Set-Cookie: SESSDATA=([^;]+);`；先打 `api.bilibili.com/x/web-interface/nav`（带该 Cookie）确认 `isLogin=true` 且拿到昵称，**验证通过才算拿到**（呼应「对照实验先验凭据」）。
5. **写入配置**：替换 `~/Desktop/nb2/my_nonebot2/.env.dev` 的 `BILIBILI_SESSDATA=<新值>` 行（保持原样不 URL 解码）。**写之前先备份该文件**。
6. **重启机器人**（必须用户发话）：改完配置不重启不生效。重启后实测 `dM/gethistory` 带 Cookie 直连，10 条真实弹幕即恢复。

## 踩坑

- 直连 B 站接口 TLS 握手时好时坏（尤其 `passport.bilibili.com` 经常连不上）：每次调用都要**循环重试**（5~8 次 × 2s）；yeshayun 代理（`127.0.0.1:7892`）握手成功率高得多，但它**不是常开的**，用户客户端一关就全断。
- 轮询 `poll` 返回 data.code=0=成功；86038=二维码已失效；86090=已扫码未确认；86101=未扫码。
- 成功的 poll 响应**只能有效读取一次**——重复轮询会变成「二维码已失效」。
- yeshayun 代理**绕不开**「境外匿名弹幕恒 0 条」的地域判定：走代理的匿名请求照样 0 条，弹幕内容永远依赖有效 SESSDATA。
- 密码登录自动化不可行：RSA + 极验滑块 + 账号风控风险，用户要求时如实拒绝并给二维码方案。

## 定时检查（已配置）

cron 任务「bilibili-SESSDATA 过期检查」每天凌晨跑：脚本读 `.env.dev` 的 SESSDATA 打 `nav`（重试+代理兜底），输出 `VALID ...` / `EXPIRED ...`；EXPIRED 时提醒用户来扫码换新。
