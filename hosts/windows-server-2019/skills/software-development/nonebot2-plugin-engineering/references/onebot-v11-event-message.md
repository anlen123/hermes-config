# OneBot v11 的事件与消息形态

改「引用某条消息 / 处理图文消息 / 按段类型分发」这类功能时要用到的适配器事实。
这些都是读适配器源码 + 用真事件对象实测确认的，别再凭猜。

## 事件上的三个入口

| 入口 | 是什么 | 坑 |
| --- | --- | --- |
| `event.message` | 本条消息的全部段，`Message`（`MessageSegment` 对象列表） | 带引用时里面有一条 `type="reply"` 的段 |
| `event.get_plaintext()` | 只取文字段（`extract_plain_text()`），`reply` / `image` / `at` 都不混进来 | 所以锚在「前缀 + 内容」上的规则照样能命中带引用的消息 |
| `event.reply` | `Optional[Reply]`，**适配器已经替你取好了** | 见下节 |

`event.to_me` 也在同一处理过程中被设置：回复机器人、或 @ 机器人时为真。

## `event.reply` 是预取的，别再调 `get_msg`

适配器处理事件时会先跑一次 `_check_reply`：消息里有 `reply` 段就调 `bot.get_msg(message_id=...)`，
成功则放进 `event.reply`，失败只记一条 warning、`event.reply` 保持 `None`。

所以插件里**不要**自己再调一次 `get_msg`，直接用 `event.reply` 即可。要分清两种「没有内容」：

```python
has_reply_seg = any(seg.type == "reply" for seg in event.message)  # 用户确实引用过
if has_reply_seg and event.reply is None:
    ...  # 引用取不到（已撤回 / 太旧 / 客户端不支持）→ 回一句明确的提示
```

`Reply` 的字段：`time` / `message_type` / `message_id` / `real_id` / `sender` / `message`。
`reply.message` 就是被引用消息的 `Message`。

## 消息段的三种形态，解析器必须都吃

| 来源 | 形态 |
| --- | --- |
| 事件、`event.reply.message` | `Message`：`MessageSegment` 对象列表（`.type` + `.data`） |
| 裸 API 返回、部分实现 | 段字典列表：`[{"type": "text", "data": {...}}, ...]` |
| `raw_message`、老实现 | CQ 码字符串：`[CQ:at,qq=123]文字[CQ:image,file=a.jpg]` |

只认 dict 的解析器遇到第一种会**静默**返回空 —— 症状是每个分支都回「引用里没有内容」，
而代码看起来完全正确。写解析器时先把三种形态统一成 `{type, data}`：
`getattr(seg, "type", None)` 拿对象形态，`isinstance(dict)` 拿字典形态，字符串则用
`\[CQ:([a-zA-Z_]+)((?:,[^\]]*)?)\]` 拆出段与夹在中间的文字。

## 常见段类型怎么处理

- `text` → 取 `data["text"]`，原样拼接（不要加分隔空格，会切碎句子）。
- `image` → 文本类上游接口吃不下：只记一个「含图片」标记，别把 base64 塞进去。
- `at` → 还原成 `@<qq>`，否则「你看这个 @某人」这种上下文会断成两截。
- `face` / `mface` → 还原成 `[表情]` 占位。
- **只有占位符没有真文字时（纯表情 / 纯语音 / 纯图片）要单独判定**，不能因为拼接结果非空就当有内容，
  否则会把 `[表情]` 当成一句待判断的话发出去。判定的标志是「有没有出现过非空的 text 段」。

## 在探针里造一个真事件

```python
from nonebot.adapters.onebot.v11.event import PrivateMessageEvent, Reply

event = PrivateMessageEvent.model_validate({
    "time": 0, "self_id": 3000000000, "post_type": "message",
    "message_type": "private", "sub_type": "friend", "message_id": 5000,
    "user_id": 10001,
    "message": [{"type": "reply", "data": {"id": "9001"}},
                {"type": "text", "data": {"text": "算 这个对吗"}}],
    "raw_message": "算 这个对吗", "font": 0,
    "sender": {"user_id": 10001, "nickname": "tester"},
})
# event.reply 由 _check_reply 在真实链路里赋值；探针里要手工塞，否则永远是 None
event.reply = Reply.model_validate({
    "time": 0, "message_type": "private", "message_id": 9001, "real_id": 9001,
    "sender": {"user_id": 20002, "nickname": "someone"},
    "message": [{"type": "text", "data": {"text": "地球是圆的"}}],
})
```

配套：假机器人的 `send` 收 `**kwargs` 并返回 `{"message_id": ...}`（handler 里 `_drop` 一类的
删除提示会用到返回值），`delete_msg` 也要有；规则函数（如 `_is_xxx`）是 async 的，直接 `await`
它就能单独验证「哪些消息会命中」。用 `scripts/probe_command.py` 时把 `mod.ask = fake_ask` 这类
替换放在 `load_plugin` 之后、调 handler 之前，就能用假响应把每条分支跑一遍而不烧额度。
