---
name: hermes-provider-switching
description: Use when switching Hermes' LLM provider or model.
version: 1.0.0
author: hermes-curator
license: MIT
metadata:
  hermes:
    tags: [hermes, provider, model, openrouter, config, api-key]
    related_skills: [hermes-agent]
---

# 切换 Hermes 的模型与供应商

用户会说「帮我换成 X 家的 Y 模型」「能接 OpenRouter 吗」「换完后某功能报错了」。
这是本机（Windows、Hermes 装在 `AppData/Local/hermes`）做这件事的固定顺序：写密钥 → 切 provider/model →
解绑辅助模型 → 三层验证 → 报告回滚方式。
供应商清单、模型别名语法等通用知识看官方自带技能 `hermes-agent`；本文只写实操顺序与坑。

## When to Use

- 新增或更换 LLM 供应商、默认模型（含「换完后顺手修辅助模型」）。
- 给某个供应商写入或轮换 API Key。
- 用户报「换了模型后标题/压缩/识图报错」。

## 硬规则

- **密钥只进 `.env`，设置只进 `config.yaml`，且一律用 CLI 改。**
  `~/AppData/Local/hermes/bin/hermes.exe config set|unset|get <键>`；不要手写 YAML 缩进 ——
  文件改坏会直接弄挂正在跑的网关。
- **改前先备份**：`cp config.yaml config.yaml.bak-$(date +%Y%m%d-%H%M%S)` 与 `.env` 同做，
  报告里给出备份文件名（用户会问怎么回滚）。
- **密钥永不回显**：确认写入时只报「长度 + sha256 前 8 位」；用户在聊天里贴过 key，
  提**一次**「可在后台轮换」即可，不要反复念。
- **切换后网关要重启才吃新配置**（config 在启动时读）。报告里必须分开讲两件事：
  「配置已改」与「正在跑的会话/网关仍是旧模型」。重启由用户发话，不自己动手。
- **不静默丢能力**：切 provider 会让钉住旧模型的辅助模型一起失效，必须三处一起处理并说明。
- **按价格选路线时先比价、再让用户拍板**：用户问「X 和 Y 谁更便宜」或要挑便宜的供应商时，先给各路线的人民币单价
  对照表（直连官方站 vs 聚合商、峰谷价、缓存价），他选定链路后再去写配置 —— 这位用户要看方案再决定，
  别拿「哪个模型便宜」直接开切。取数方法、各家定价页入口、会翻转「谁更便宜」的坑：
  `references/provider-cost-comparison.md`。

## 步骤

1. **确认供应商受支持**：本机插件目录 `hermes-agent/plugins/model-providers/<name>/`，
   读 `plugin.yaml` 与 `__init__.py` 里的 provider 注册 —— 密钥变量名、别名、默认端点都在那里，不用猜。
   别只 grep `ProviderProfile(`：插件喜欢用子类就地实例化（本机 zai 就是 `zai = ZaiProfile(...)`，
   `ProviderProfile(` 一个字都搜不到），要 grep `register_provider(` 或 `env_vars=`、`base_url=` 才拿得全。
   本机已有的 `zai`（别名 `glm`/`zhipu`，密钥变量 `GLM_API_KEY`）默认端点是 `https://api.z.ai/api/paas/v4`（海外站）；
   要接国内站得显式把 `model.base_url` 指到 `https://open.bigmodel.cn/api/paas/v4`。
   聚合类（OpenRouter）的模型 ID 是 `厂商/模型` 形式。
2. **写密钥进 `.env`（最容易静默失败的一步）**：`OPENROUTER_API_KEY` 这类名字在 `.env` 里
   **本来就有一行注释模板**，形如 `# OPENROUTER_API_KEY=`（**行尾还带一个 `=`**）。
   用 `startswith("OPENROUTER_API_KEY=")` 或 `== "# OPENROUTER_API_KEY"` 去匹配都会漏，
   替换静默 no-op 却照样打印「已更新」。正确做法：
   先删掉所有以 `OPENROUTER_API_KEY=` 开头的行 → 用 `strip().startswith("# OPENROUTER_API_KEY")`
   定位模板行并在其后插入真正的一行 → **复核「未注释的键恰好一行」**（打印长度与指纹）。
3. **验密钥、定模型 ID**（外网直连可用，curl 不读 Windows 注册表代理）：

   ```bash
   KEY=$(grep '^OPENROUTER_API_KEY=' .env | cut -d= -f2)
   curl -s https://openrouter.ai/api/v1/key    -H "Authorization: Bearer $KEY"   # 看 limit_remaining
   curl -s https://openrouter.ai/api/v1/models -H "Authorization: Bearer $KEY" | tr ',' '\n' | grep -oE '"id":"[^"]*<关键字>[^"]*"'
   ```

   拿到**准确模型 ID** 再往下（别按用户口语的名字猜 ID）。
4. **直打一次对话接口**确认该模型真能用：`POST /api/v1/chat/completions`，体
   `{"model":"<id>","messages":[{"role":"user","content":"hi"}]}`。
   注意 git-bash 里内联中文会因 GBK 变乱码（模型会去分析乱码），改用英文提示或写 JSON 文件后 `-d @file`。
5. **切主配置**：

   ```bash
   hermes.exe config set model.provider <provider>
   hermes.exe config set model.default  <model-id>
   ```

   `config set model.provider` 会**自动清掉 `model.base_url`**（当它属于上一个供应商时会提示
   「Cleared model.base_url ... that route belonged to <old provider>」）—— 这是预期行为，
   不要手忙脚乱再填回去；只有自建端点才需要显式写。
6. **解绑三个辅助模型（关键、最容易漏）**：`auxiliary.vision.model` / `auxiliary.compression.model` /
   `auxiliary.title_generation.model` 各自钉住旧模型名，切 provider 后它们变成非法 ID，
   症状是跑起来出现 `⚠ Auxiliary title generation failed: HTTP 400: <旧模型> is not a valid model ID`。
   三个都 `hermes.exe config unset auxiliary.<x>.model`（保留 `provider: main` 即跟随主模型）。
   查漏办法：`grep -n "<旧模型名>" config.yaml` —— auxiliary 段在文件很后面，注释行会一起命中，按行号判断。
7. **端到端验证**：`hermes.exe chat -q "<一句话>"` 真跑一轮（十秒上下），确认回复正常**且不再有 aux 报错**，
   把实际输出贴给用户。这一步只证明「能连」，网关侧要等重启后才算生效。
8. **报告**：改前/改后表（provider、model、base_url、辅助模型、`.env` 新增键的指纹）+
   三层验证证据（钥匙有效性 / 模型 ID 存在 / 端到端真跑）+ 备份文件名 + 待办（重启网关）。

## 可选路线：临时切而不是改默认

不想动默认模型时，给目标模型起个别名让用户手动切（会话级生效，`/global` 才持久化）：

```bash
hermes.exe config set model.aliases.<别名> <provider>/<model-id>
```

聊天里 `/model <别名>`。适合「先试一个模型再决定要不要换默认」。

## 坑

- **`hermes model` 是纯交互式的**（只有 `--refresh` / OAuth 相关参数，没有 `--provider` / `--model`），
  脚本化只能用 `hermes config set`；别用 PTY 去驱动它，比直接改键麻烦。
- **只看 `config get model.provider` 不等于切好了**：辅助模型那三个键在 `config.yaml` 的另一段，
  不 grep 一遍不会自己冒出来，而失败要等运行期才报。
- **切完当天「本会话还是旧模型」是正常的**：网关/会话在启动时读配置。用户在这个对话里问
  「你现在是什么模型」，答案要诚实分成「配置文件已切」+「当前会话仍是旧模型、需重启网关」。
- **不要顺手把辅助模型各钉到某个贵模型上**：`provider: main` + 不写 model = 跟随主模型，最省事也不漏配。
- **供应商是聚合商（OpenRouter 这类）时提一句链路**：对话内容会经第三方中转再到模型方，比直连多一跳。
- **凭据池里的状态不是密钥的死活判决**：`auth.json` 的 `credential_pool` 可能给某条凭据记着
  `last_status: exhausted`、`403`、`failure_reason: auth`，但密钥其实还能用 —— 那常是某次针对**单个模型**的
  区域限制留下的残迹。切之前用 `curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $KEY"`
  看 `limit_remaining`，再直打一次目标模型；两条都过就照切，别拿池里的旧状态告诉用户「这条路不通」。
- **别靠「有没有注释模板」判断密钥是否已配置**：`.env` 里既有注释放着的模板行、也可能已有真实行，肉眼分不清。
  直接取出来验（报长度与 sha256 前 8 位）+ 打一次真实接口；已配好的键就别重复写入。
