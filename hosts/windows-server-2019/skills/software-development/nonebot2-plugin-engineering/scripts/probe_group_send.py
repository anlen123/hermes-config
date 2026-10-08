"""跑群聊 handler 的真链路，截获它通过匹配器发出的每一条消息。

与 probe_send.py 的区别：这里造的是**真实事件对象**（`model_validate`），所以 handler 里读
`event.group_id` / `event.get_plaintext()` / `event.reply` 都不会缺字段；匹配器的 `send` / `finish`
换成假函数，消息不会真发到 QQ。假 `finish` 按真实语义抛 `FinishedException`，否则 handler 会在
`finish()` 之后继续往下跑，测出生产里不存在的路径。

用法（在仓库根目录跑，`nonebot.init()` 会自己读 `.env`）：

    <Python313> probe_group_send.py <插件模块> <handler 函数名> <群号> <消息文本> [仓库根目录]
    # 顺带看某条规则在群聊 / 私聊下的判定：
    <Python313> probe_group_send.py <插件模块> <handler> <群号> <文本> --rule-name <规则函数名>

例：

    <Python313> probe_group_send.py plugins.openlive_check handle_openlive 548912612 开播 --rule-name _has_trigger_word

注意：
  * handler 的 bot 参数传的是 None —— 会直接调 `bot.send(...)` 的 handler 别用本脚本，
    改去测它调用的那几个内部协程。
  * 要看多个群 / 多个分支，复制本脚本另存一份改用例，或把用例列表加在 `_main()` 里；
    挑用例时挑**真实数据必然命中某个分支**的群（有在播的 / 全都未播的 / 没配置的），
    比对着代码想象分支可靠。
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import io
import os
import re
import sys
from pathlib import Path

DEFAULT_REPO = r"C:\Users\Administrator\Desktop\nb2\my_nonebot2"


def _prepare(repo: str) -> None:
    path = Path(repo)
    if not path.is_dir():
        raise SystemExit(f"仓库目录不存在：{repo}")
    sys.path.insert(0, str(path))
    os.chdir(path)  # nonebot.init() 按 cwd 找 .env / .env.dev


def _group_event(cls, group_id, text):
    return cls.model_validate(
        {
            "time": 1759100000,
            "self_id": 100000,
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 1,
            "user_id": 10001,
            "group_id": group_id,
            "message": [{"type": "text", "data": {"text": text}}],
            "raw_message": text,
            "font": 0,
            "sender": {"user_id": 10001, "nickname": "tester", "card": "", "role": "member"},
        }
    )


def _private_event(cls, text):
    return cls.model_validate(
        {
            "time": 1759100000,
            "self_id": 100000,
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "message_id": 2,
            "user_id": 10001,
            "message": [{"type": "text", "data": {"text": text}}],
            "raw_message": text,
            "font": 0,
            "sender": {"user_id": 10001, "nickname": "tester"},
        }
    )


def _dump(sent: list, message_cls) -> None:
    print(f"共发出 {len(sent)} 条消息")
    for index, message in enumerate(sent, 1):
        if not isinstance(message, message_cls):
            message = message_cls(message)  # handler 可能直接传裸 MessageSegment
        kinds = "、".join(segment.type for segment in message)
        print(f"\n── 第 {index} 条（{len(message)} 段：{kinds}）")
        for segment in message:
            if segment.type == "text":
                body = segment.data.get("text", "")
                for row in body.splitlines():
                    print("   | " + row)
                links = re.findall(r"https?://\S+", body)
                print(f"   → 链接：{links or '无'}")
            elif segment.type == "image":
                payload = segment.data.get("file", "")
                if "base64://" in payload:
                    raw = base64.b64decode(payload.split("base64://", 1)[1])
                    try:
                        from PIL import Image

                        with Image.open(io.BytesIO(raw)) as image:
                            print(
                                f"   → 图片 {image.format} {image.size} "
                                f"{len(raw) / 1024:.0f} KB"
                            )
                    except Exception as exc:  # noqa: BLE001
                        print(f"   → 图片解不开（{type(exc).__name__}）：{exc}")
                else:
                    print(f"   → 图片（非 base64）：{payload[:60]}…")
            else:
                print(f"   → 其它段：{segment}")


async def _call_rule(rule, event):
    """规则函数可能是普通函数也可能是协程函数，都吃。"""
    result = rule(event)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _main() -> None:
    argv = sys.argv[1:]
    rule_name = None
    if "--rule-name" in argv:
        index = argv.index("--rule-name")
        rule_name = argv[index + 1]
        del argv[index : index + 2]
    if len(argv) < 4:
        raise SystemExit(__doc__)
    module_name, handler_name, group_id, text = argv[0], argv[1], argv[2], argv[3]
    _prepare(argv[4] if len(argv) > 4 else DEFAULT_REPO)

    import nonebot
    from nonebot.adapters.onebot.v11 import (
        Adapter,
        GroupMessageEvent,
        Message,
        PrivateMessageEvent,
    )
    from nonebot.matcher import FinishedException

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_plugin(module_name)
    module = sys.modules[module_name]

    handler = getattr(module, handler_name, None)
    if handler is None:
        raise SystemExit(f"{module_name} 里没有 {handler_name}")
    matcher = getattr(module, "matcher", None)
    if matcher is None:
        raise SystemExit(f"{module_name} 里没有模块级 matcher，本脚本靠覆盖它的 send / finish 工作")

    sent: list = []

    async def fake_send(message=None, **kwargs):
        sent.append(message)
        return None

    async def fake_finish(message=None, **kwargs):
        if message is not None:
            sent.append(message)
        raise FinishedException

    matcher.send = fake_send
    matcher.finish = fake_finish

    if rule_name:
        rule = getattr(module, rule_name, None)
        if rule is None:
            raise SystemExit(f"{module_name} 里没有 {rule_name}")
        print("── 规则判定")
        for label, event in (
            (f"群聊 {group_id}：{text}", _group_event(GroupMessageEvent, group_id, text)),
            (f"私聊：{text}", _private_event(PrivateMessageEvent, text)),
        ):
            print(f"   {label} → {await _call_rule(rule, event)}")
        print()

    print(f"── 输入：群 {group_id} 发「{text}」")
    event = _group_event(GroupMessageEvent, group_id, text)
    try:
        await handler(None, event)
    except FinishedException:
        pass
    _dump(sent, Message)
    if not sent:
        print("（一条消息都没发：要么走了「静默」分支，要么 handler 提前 return 了）")


if __name__ == "__main__":
    asyncio.run(_main())
