---
name: codex-reset-credit-watch
description: Use when watching Codex reset credits or weekly quota.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [codex, openai, quota, monitor, cron]
    related_skills: [codex, hermes-agent]
---

# Codex 重置卡 / 额度监视

## When to Use

- 用户要盯 Codex/ChatGPT 账号的「重置卡（rate limit reset credits）」数量变化
- 用户要盯 Codex 额度窗口（每周/5 小时）是否回升（发生重置）
- 需要给 Hermes 加一个「有变化才通知」的低成本轮询任务

盯 ChatGPT/Codex 账号的「rate limit reset credits（重置卡）」和额度窗口，在**重置卡变多**或
**额度回升**时通知用户（本机走 QQ）。适合做成 Hermes cron 的 `no_agent` 纯脚本任务。

## 数据源（Codex CLI 自己在用的后端接口，官方未公开）

源码位置：`openai/codex` 仓库 `codex-rs/backend-client/src/client/rate_limit_resets.rs`；
ChatGPT 类型的 base = `https://chatgpt.com/backend-api`（另一套 `PathStyle::CodexApi` 用 `/api/codex/...`）。

    GET  {base}/wham/usage                    → 额度窗口 + rate_limit_reset_credits.available_count
    GET  {base}/wham/rate-limit-reset-credits → {credits:[{id,reset_type,status,granted_at,expires_at,title,description}], available_count}
    POST {base}/wham/rate-limit-reset-credits/consume   # 消耗一张（监视器绝不调用）

请求头：`Authorization: Bearer <access_token>`、`ChatGPT-Account-Id: <account_id>`、`User-Agent: codex-cli`。

`/wham/usage` 响应（`RateLimitStatusPayload`）：
`{plan_type, rate_limit:{allowed, limit_reached, primary_window, secondary_window}, credits, spend_control, rate_limit_reset_credits}`
窗口字段：`used_percent`、`limit_window_seconds`、`reset_after_seconds`、`reset_at`(unix 秒)。

## 凭据与刷新

`~/.codex/auth.json`（`codex login` 写入）：`{auth_mode, tokens:{access_token, id_token, refresh_token, account_id}, last_refresh}`。
access_token 是 JWT，读 `exp` 判断是否要刷新；**刷新用 JSON body，不是 form**：

    POST https://auth.openai.com/oauth/token
    {"grant_type":"refresh_token","client_id":"app_EMoamEEZ73f0CkXaXp7hrann","refresh_token":"<rt>"}
    → {access_token, id_token, refresh_token}

**必做**：OpenAI 会轮换 refresh_token，旧的重放会报 “already used”。刷新后要把新的
access/id/refresh 三个一起**回写 auth.json**（临时文件 + `os.replace` 原子替换），
否则 codex CLI 自己的刷新就废了 —— CLI 内部也是这套逻辑。

## 本机落地（2026-10-08 建）

- 脚本：`~/AppData/Local/hermes/scripts/codex_watch.py`（纯标准库；`--show` 打快照、`--reset-state` 清基线）
- 状态/日志：`~/AppData/Local/hermes/codex-watch/{state.json,watch.log}`
- 调度：Hermes cron `a3e27c1a6272`，`every 15m`，`no_agent=true` + `script=codex_watch.py`
  → stdout 原样投递 QQ，空输出 = 完全不打扰（watchdog 模式），不烧 token。
- 首次运行只记基线不通知；命中才输出 `🔔 重置卡 +N` / `📈 额度回升 +N 个百分点`，
  并区分「窗口自然滚动」与「官方/重置卡重置」（看上次快照的 `reset_at` 是否已过期）。

## 顺带能读的东西

- **订阅到期时间**：不用调接口，直接解码 `id_token` 里 `https://api.openai.com/auth` 的声明：
  `chatgpt_plan_type`、`chatgpt_subscription_active_start/until`、`chatgpt_subscription_last_checked`。
  `active_until` = 当期结束（下一个扣费日），**令牌里不含「是否自动续费」**，别把当期到期说成「会员到期」。
- `GET {base}/wham/accounts/check` → 账号列表（`plan_type`、`is_deactivated`、structure…），同样没有续费信息。

## 注意：Hermes 自己也持有同一账号的凭据

Hermes 有内置的 `openai-codex` provider，会**导入** `~/.codex/auth.json` 的 token
（`hermes_cli/auth_codex.py:_import_codex_cli_tokens`），并把轮换后的副本存在**自己的**
`~/AppData/Local/hermes/auth.json`（`providers.openai-codex.tokens`）。
若把 Hermes 主模型切到 `openai-codex`，两方都会刷新同一个 refresh_token，
可能撞上 “refresh token already used”。此时要么只留一方管凭据，要么把监视器改成只读。
当前本机 provider 是 deepseek，不冲突。

## 坑

- **槽位名不可信**：实测 plus 账号的「每周额度」落在 `primary_window`（604800 秒），
  `secondary_window` 为 null，且该账号根本没有 5 小时窗口。必须按 `limit_window_seconds`
  判断周期、自动挑最长窗口来盯，别按 primary/secondary 硬编码。
- 只盯每周窗口。若也盯 5 小时窗口，每 5 小时自然回满 → 通知变白噪音。
- 额度阈值默认回升 ≥1 个百分点就报：窗口内 `used_percent` 单调递增，回退即代表发生过重置。
- 装 CLI 用用户级 npm 前缀：`npm install -g @openai/codex --prefix "C:/Users/<user>/AppData/Roaming/npm"`
  （该目录在 PATH 上，且不会被 `hermes update` 覆盖 Hermes 自带 node 目录而丢失）。
- 服务器上登录用 `codex login --device-auth`（给用户 auth.openai.com/codex/device + 一次性码，15 分钟有效）；
  默认 `codex login` 的回调是 `127.0.0.1:1455`，只适合本机有浏览器的场景，别隔着 QQ 让用户点。
- 改脚本后：`py_compile` + 用 `CODEX_WATCH_DIR=<临时目录>` 造一份假 state，把
  「额度回升」「重置卡 +1」「无变化」三个分支各跑一遍再交付。
