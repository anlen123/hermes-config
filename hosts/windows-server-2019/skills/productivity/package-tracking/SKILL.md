---
name: package-tracking
description: Use when user says 快递 单号 to watch a package hourly.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [快递, 物流, kuaidi100, kdniao, cron, monitor]
    related_skills: [codex-reset-credit-watch, hermes-agent]
---

# 快递物流监视（QQ 上说「快递 单号」触发）

每小时查一次物流轨迹，**只在有新增轨迹时**通知；说「快递已取 <单号>」就停。

## When to Use

- 用户发「快递 <单号>」或「快递 <承运商> <单号>」 → 加入监视
- 用户发「快递已取 <单号>」/「不用盯了 <单号>」 → 停止监视
- 想看当前在盯什么 → `--list`

## 流程

1. 加入：
   `python ~/AppData/Local/hermes/scripts/package_watch.py --add <单号> [--carrier <代码>] [--label <备注>] [--phone <手机后四位>]`
   - **用户报了承运商就一定带 `--carrier`**（代码用快递100 的：zhongtong/yuantong/shentong/yunda/shunfeng/jd/ems/jtexpress/debangwuliu）。
   - 脚本自己管状态：`~/AppData/Local/hermes/package-tracking/tracked.json`。
2. 确保定时任务存在（只建一次，不是每个包裹一个）：
   cron 名称「快递物流监视」，`every 1h`，`no_agent=true` + `script=package_watch.py`，deliver=origin。
3. **停止（用户只需要记序号）**：用户说「快递已取 1」/「快递已取 #2」→ 先 `--list` 看清序号对应哪个单号，
   再 `--remove <序号>`（也接受完整单号）。脚本给每个包裹分配**永不变号的 `no`**
   （删 #1 后 #2 仍是 #2，不会串），并把剩余清单打出来 —— 把它转给用户确认删对了哪个。
   列表已空时可以 `cronjob pause` 掉任务（留着也不会打扰）。
4. 回复用户时把脚本 stdout 转述清楚：#编号、单号、承运商、当前状态、最新一条轨迹。

## 数据源（本机默认只走快递鸟）

**成本背景**：快递100 的**基础版不支持三通一达/顺丰/京东/EMS/德邦**等主流快递，要查中通就得企业版按单付费
（约 0.05 元/单）；每小时一查 ≈ 36 元/月·件，用户嫌贵 → **2026-10-08 起改用快递鸟免费版**，
快递100 凭证已在 `.env` 里注释停用（代码不再读，`PACKAGE_SOURCE` 默认 `kdniao`）。

| 优先级 | 来源 | 所需凭证 | 要点 |
|---|---|---|---|
| 默认 | 快递鸟·即时查询 `POST https://api.kdniao.com/Ebusiness/EbusinessOrderHandle.aspx` | `KDNIAO_EBUSINESS_ID`+`KDNIAO_API_KEY` | `RequestType=1002`；`RequestData` = **未编码的原始 JSON**（urlencode 只在上送时做）；`DataSign = Base64( MD5(原始JSON + AppKey) 的32位十六进制小写字符串 )` —— **先取 hex 再 base64**，取 digest 字节或大写 hex 都会报「非法参数」；`Traces` 正序，用前要反转 |
| 备用 | 快递100 开放平台 | `KUAIDI100_CUSTOMER`+`KUAIDI100_KEY` | `PACKAGE_SOURCE=kuaidi100` 才启用；sign=MD5(param+key+customer) 大写 |
| 排查用 | 免 key `GET https://www.kuaidi100.com/query` | 无 | `PACKAGE_SOURCE=free`；只配 `Referer`；**会限流、会串数据，绝不当基线** |

凭证写进 `~/AppData/Local/hermes/.env`（脚本启动时自己读，不覆盖已有环境变量），绝不硬编码进代码。

## 省额度：查询间隔（用户拍板 2026-10-08）

定时任务每小时 tick，但脚本自己按状态决定要不要真查（`due_reason()`）：
**在途/揽收/派件中 180 分钟**、已签收/退签 **720 分钟**，
且**安静时段 03:00-09:00 一次都不查**（用户指定；以前是 23:00-07:00，已改）。
跳过的写进 watch.log，不发通知。用量：允许查询的窗口 09:00–次日 03:00，一天最多约 6 次。
环境变量可调：`PACKAGE_INTERVAL_DELIVERING/DEFAULT/DONE`、`PACKAGE_QUIET_START_HOUR/QUIET_END_HOUR`。

> 间隔改动容易“静默失效”：`due_reason` 算 `elapsed` 时曾调用了不存在的 `now()`，
> 异常被 `except` 吞掉后回落成“永远该查” —— 改完一定要用
> 「距上次 2.9 小时→跳过 / 3.1 小时→该查」和几分钟点边界各测一遍。

### 快递100 开放平台错误码速查（实测）

| returnCode | message | 含义与处置 |
|---|---|---|
| 无（`message=ok`） | ok | 拿到了，`data[]` 倒序 |
| 500 | 查询无结果，请隔段时间再查 | **这个单号不属于该承运商** —— 可用来反推/验证承运商 |
| 408 | 快递公司参数异常：验证码错误 | **缺或错的收件人手机号后四位**（中通、顺丰等隐私面单必需）→ 要 `phone` 参数 |
| 400 | 找不到对应公司 | `com` 代码写错 |
| 503 | 签名错误 | `sign=MD5(param+key+customer)` 算错（注意 param 必须是**实际发出的那一串**，key/customer 别弄反） |

中通、顺丰要收件人手机后四位；注意快递100 的 `phone` 字段只收后四位。

### 快递鸟错误码速查（实测）

| 返回 | 含义与处置 |
|---|---|
| `Success=true` | 拿到，`Traces[]` 正序且带 `AcceptTime/AcceptStation` |
| `非法参数[请核实ID和key是否正确…]` | **签名算法错**（用错 hex/字节/大小写）或 ID/key 不对 |
| `业务错误[没有可用套餐]` | **凭证没问题，但账号没开通这项服务** → 去控制台申请/开通「即时查询」（免费版也要点开通，常需先实名认证）。单号识别报「已到期」同属此类 |
| `无效的输入[【ShipperCode】…不正确]` | `com` 代码写错，或不传 ShipperCode 时它无法识别 |

### ❸ 快递鸟：1002 收费、**8001 免费**（2026-10-08 实测定调）

同一个账号、同一套签名，换 `RequestType` 结果完全不同：

| RequestType | 名称 | 实测结果 |
|---|---|---|
| `1002` | 标准即时查询 | `业务错误[没有可用套餐]` —— **要付费** |
| `8001` | 在途监控（免费即时查询） | ✅ `Success=true`，直接给 `Traces` |
| `2002` | 单号识别 | 实名后可用，`Success=true` 回 `Shippers[{ShipperCode,ShipperName}]` |

**8001 的两个硬要求**：
1. `RequestData` 必须带 `CustomerName` = **收件人手机后四位**，字段名就叫 `CustomerName`
   （写成 `CustomerPwd` 会报「手机尾号不能为空」；中通/顺丰这类隐私面单必需）。
2. `DataSign = Base64( MD5(原始JSON + AppKey) 的32位**十六进制小写字符串** )` —— 先取 hex 再 base64。
   取 `md5(...).digest()` 原始字节、或大写 hex、或不 base64，全部报「非法参数」（三种错法均已实测）。
3. `RequestData` 要传**未 urlencode 的原始 JSON**，编码交给上送时的 form 编码（预先 quote 会二次编码）。

**鉴权与套餐的先后顺序**（排查有用）：套餐错误会**先**抛出，签名错才报「非法参数」；
所以拿到「没有可用套餐」时说明 ID/key/签名已经对了，不要再折腾签名。

**坑：网上复制来的示例通常是废模板。** 用户发过一段带 `CUSTOMER_CODE=1693946` +
`http://<IP>:8660/api/dist` 的样例，实测该 ID/key 在官方域名和那个 IP 端点上一律
`非法参数`；**切勿采用明文 IP 端点（HTTP 传单号+密钥）**，官方域名 `https://api.kdniao.com/Ebusiness/EbusinessOrderHandle.aspx` 就够。

## 坑（都是实测结论）
- **同一个查询函数有多个调用点，改签名/加参数时容易漏传。**
  `--check` 一度因为漏传 `phone` 而永远「暂无轨迹」，而主巡检路径是好的（因为那里多传了一个参数）。
  改完必须把 `grep -n "query("` 的每个调用点都看一遍，并分别实测 `--check` 和整轮巡检。

- **测试时绝不要用 `export VAR=...`**。Hermes 的 terminal 会**跨调用保留环境变量**，
  我曾因此把真实数据写进了临时目录（定时任务读正式路径，看到的是空列表；
  后续 `--list` 也一直看的是临时目录，差点误判）。要临时换目录就用单次覆盖：
  `env PACKAGE_WATCH_DIR=/tmp/x python ...`（不污染会话）。交付前务必：
  `echo "[$PACKAGE_WATCH_DIR]"` 确认为空，并 `ls` 一下正式路径确实有 tracked.json。
- **`--carrier` 必须锁死，不得被自动识别覆盖**。曾经写成「指定的查不到就继续穷举其他承运商」，
  结果用户报中通、脚本自己改成了德邦并记下一条别的件的轨迹。现在：指定了就只查它，
  查不到就如实报失败；记录里打 `locked=true`，巡检时也不允许再改判。
- **免 key 接口的 `type` 基本不被尊重**：同一个中通单号在 `type=zhongtong` 下返回的
  轨迹正文却是「感谢使用圆通速递」，而且 `state` 会出现 201/301/501 这类非法值
  （正常只有 0~14）。这种响应一律不可信，别拿它当基线。
- **免 key 接口在境外出口下基本不可用**：连打 6 次全返回空、9 次只中 1 次（限流）；先取首页拿 cookie 能提高命中但依旧不稳。所以优先要 key。
- 免 key 接口会串数据：`type` 与真实承运商不符时它照样返回别家轨迹 —— demo 号用 `type=shunfeng` 查出的是**圆通**的件（正文带圆通热线 95554）。所以用户给了承运商就绝不要自动识别。
- 状态码只信 0~14（0 在途 / 1 已揽收 / 3 已签收 / 5 派件中…）；出现三位数说明响应是垃圾，应丢弃重试。
- 快递100 免 key 的 `autonumber/autoComNum` 对境外出口直接回 `非法IP`；只有开放平台的 `api.kuaidi100.com/autonumber/auto?num=&key=` 能用。
- 单号形态规则只能用来**排序试探顺序**，不能当判据（中通 75/78/68/73/76、申通 77/88/66/55、圆通 YT、顺丰 SF、京东 JD、极兔 JT）。
- 顺丰常要收件人手机后四位 → `--phone`。
- 查询失败/无轨迹时**静默重试**，不要当成「无变化」或把空数据报给用户；只报真新增的轨迹。
- 签收（state=3）时附一句「取件后回我『快递已取 <单号>』」，且只提一次（`signed_notified`）。
