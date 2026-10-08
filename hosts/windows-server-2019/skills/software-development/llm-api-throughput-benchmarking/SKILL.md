---
name: llm-api-throughput-benchmarking
description: Compare LLM API/gateway output speed and latency.
version: 1.0.0
author: hermes-curator
license: MIT
metadata:
  hermes:
    tags: [llm, benchmarking, throughput, latency, openrouter, provider-switching]
    related_skills: [hermes-provider-switching]
---

# LLM 通道吞吐与延迟实测

## When to Use / 何时用
- 用户问「X 和 Y 哪个 token 输出更快」（官方直连 vs 聚合网关/中转）。
- 切换 Hermes 的 provider 或 model 之前，先验证新通道是不是真的更快。
- 怀疑网关把请求转给了第三方算力商而不是供应商自家集群。

## 核心原则
1. **只比同一个模型**。先列两边 `/v1/models`，挑同一个版本号；不同模型之间比速度没有意义。
2. **必须实测，不引用公开基准**。跨境网络路径会主导结果，且用户要的是「参照值 / 改前 / 改后」式的可核验一手证据。
3. **流式 + 分阶段计时**。只测总耗时会被思考长度随机性完全淹没。

## 步骤
1. **取 key**。本机在 `~/AppData/Local/hermes/.env`（不是 `~/.hermes/.env`）。先 `grep -nE '^[A-Z_]+=' .env` 看哪些 key 已启用；打印时脱敏 `sed -E 's/=(.{8}).*/=\1****/'`，绝不回显完整 key。
2. **列模型**。`curl -s <base>/v1/models -H "Authorization: Bearer $KEY"`，两边挑同版本模型。注意同一模型在网关上的 ID 带前缀（`deepseek/deepseek-v4.1-flash`），在官方端点是裸名（`deepseek-flash`）。
3. **跑脚本**。`scripts/bench_stream.py`，每个通道 10 次流式请求，串行执行（并发会互相抢带宽，数据不可比）。
4. **报中位数 + 全部样本 + n**，不要只报一个数字。

## 指标口径（关键）
- 首个 token 到达时间（含思考）＝ 用户感知的「开始吐字」延迟。
- 思考阶段速率 ＝ `reasoning_tokens / (首个正文 − 首个 delta)`。
- 正文阶段速率 ＝ `(completion_tokens − reasoning_tokens) / (总耗时 − 首个正文)`。
- **不要把 `completion_tokens / 总耗时` 当成唯一结论**：思考阶段解码明显快于正文阶段，思考越长这个比值越高，于是「输出 token 更多」被误读成「速度更快」。要么分阶段，要么固定思考长度。

## 坑
- **思考字段名两边不同**：官方 DeepSeek 用 `delta.reasoning_content`，OpenRouter 用 `delta.reasoning`。只判一个名字会漏掉整段思考，首个 delta 时间、分阶段速率全错。两个都要判。
- **`max_tokens` 会被思考吃光**：模型可能把整个上限用在思维链上，`completion_tokens` 有值但一个正文字符都没有，据此算出的速率全是垃圾。设大（8000 起）或先探一次思考长度。
- **网关默认路由不等于供应商自家算力**：OpenRouter 会把请求派给第三方（实测见过 AtlasCloud、Together、Morph），同一通道不同次落点不同、速度差几倍。要测「网关本身的开销」就 pin：`"provider": {"order": ["DeepSeek"], "allow_fallbacks": true}`；要测「用户默认体验」就别 pin。无论哪种，都从 SSE chunk 的 `provider` 字段记录实际落点并写进结果。
- **不要用 curl 发含中文的 JSON**：git-bash 下按 GBK 编码，服务端报 `Failed to parse the request body as JSON: ... invalid unicode code point`。用 Python `json.dumps(body).encode("utf-8")`。
- **单次样本不可信**：思考长度随机波动能让同一通道的速率差 3 倍以上。至少 5 次，10 次更稳。
- **失败样本如实计入**：SSL 中断、连接被掐这类要报出来并说明有效样本量，不要静默丢掉或拿剩下的充数。

## 汇报格式（用户偏好）
- 结论先行一句话：谁更快、差多少、在不在噪声范围内。
- 紧跟一张**对照表**：通道 / 首个 token / 思考阶段 / 正文阶段 / 备注。
- 单位写全中文：`token/秒`、`秒`，不要 `tok/s`、`MB/s` 这类缩写（用户会逐条挑出来）。路径、命令、模型名保留原样。
- 表后给 2-3 条关键发现，说清差异来自**路由**还是来自**网络路径**，不要只丢数字。
- 结尾给一个可执行的下一步建议（例如「要不要把 provider 切到官方直连」），并提示该动作需要改配置 + 重启网关，会先备份。
- 附上可复现脚本和原始样本的路径。

## 脚本
`scripts/bench_stream.py` — 参数化流式实测：逐次明细 + 分阶段中位数，并写 `bench_result.json`。

```
python scripts/bench_stream.py --trials 10 \
  --target "官方直连|https://api.deepseek.com/v1/chat/completions|DEEPSEEK_API_KEY|deepseek-flash" \
  --target "OpenRouter|https://openrouter.ai/api/v1/chat/completions|OPENROUTER_API_KEY|deepseek/deepseek-v4.1-flash" \
  --target "OpenRouter(pin DeepSeek)|https://openrouter.ai/api/v1/chat/completions|OPENROUTER_API_KEY|deepseek/deepseek-v4.1-flash|DeepSeek"
```

target 格式：`名称|url|KEY_ENV名|模型[|provider pin]`。key 先查环境变量，再查 `~/AppData/Local/hermes/.env`。
