"""用假 bot 跑指定 handler，检查它实际发出的消息。

用法（必须在仓库根目录跑，nonebot.init() 会自动读 .env / .env.dev）：

    <Python313> probe_send.py <插件模块> <handler 函数名> <输入文本> [仓库根目录]

例：

    python probe_send.py plugins.bazaardb bazaar_rev "巴扎 光纤"

输出每条被 send 的消息分了哪些段、文字全文与其中的链接、图片段解 base64 后的
格式/尺寸/大小。用来证明「发了几条、发的是什么图、文案里有没有链接」，比读代码可靠。
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import sys
from pathlib import Path

DEFAULT_REPO = r"C:\Users\Administrator\Desktop\nb2\my_nonebot2"


def _prepare(repo: str) -> None:
    path = Path(repo)
    if not path.is_dir():
        raise SystemExit(f"仓库目录不存在：{repo}")
    sys.path.insert(0, str(path))
    import os

    os.chdir(path)  # nonebot.init() 按 cwd 找 .env / .env.dev


class FakeBot:
    """把 send 的 kwargs 原样记下来，不真连 QQ。"""

    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeEvent:
    def __init__(self, text, message_cls):
        self.message = message_cls(text)


def _dump(bot: FakeBot, message_cls) -> None:
    print(f"共发出 {len(bot.sent)} 条消息")
    for index, kwargs in enumerate(bot.sent, 1):
        message = kwargs.get("message")
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
                size = len(payload)
                if "base64://" in payload:
                    raw = base64.b64decode(payload.split("base64://", 1)[1])
                    size = len(raw)
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
                    print(f"   → 图片（非 base64，{size} 字符）：{payload[:60]}…")
            else:
                print(f"   → 其它段：{segment}")


async def _main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    module_name, handler_name, text = sys.argv[1], sys.argv[2], sys.argv[3]
    _prepare(sys.argv[4] if len(sys.argv) > 4 else DEFAULT_REPO)

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter, Message

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_plugin(module_name)
    module = sys.modules[module_name]

    handler = getattr(module, handler_name, None)
    if handler is None:
        raise SystemExit(f"{module_name} 里没有 {handler_name}")

    bot = FakeBot()
    await handler(bot, FakeEvent(text, Message))
    print(f"输入：{text}")
    _dump(bot, Message)


if __name__ == "__main__":
    asyncio.run(_main())
