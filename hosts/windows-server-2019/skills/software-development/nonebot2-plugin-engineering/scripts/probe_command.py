"""跑一个 on_command 处理函数（真查接口），把发出的消息与图片落盘检查。

与 probe_send.py 的分工：
  * probe_send.py     —— handler 签名是 (bot, event) 时用它，直接 await。
  * probe_command.py  —— handler 无参（nonebot v2 常见写法，从匹配器上下文取 bot / event）时用它。
    按 (bot, event) 调无参 handler 会直接 TypeError，而走了匹配器就会真发到 QQ；
    这里把**匹配器实例**的 send / finish 换成假函数，两头都堵上。

假 finish 会**抛出 FinishedException**（真实 Matcher.finish 就是靠抛异常结束流程的）：
返回 None 的假 finish 会让 handler 在 finish() 之后继续往下跑，测出生产里不存在的路径。

用法（第 4 个参数是仓库根目录，省略则用 DEFAULT_REPO）：

    <Python313> probe_command.py <插件模块> <匹配器变量名> <handler 函数名> [仓库根目录]

例：

    python probe_command.py plugins.deepseek_balance ai_balance_cmd _handle_ai_balance

输出：每条消息的分段类型与文字全文；图片段解 base64 后存成 PNG 并打印绝对路径，
直接拿去 vision_analyze 逐字复核排版（尺寸对不代表没叠字）。
必须从仓库根目录运行 —— nonebot.init() 按 cwd 找 .env / .env.dev。
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import sys
import tempfile
from pathlib import Path

from nonebot.exception import FinishedException

DEFAULT_REPO = r"C:\Users\Administrator\Desktop\nb2\my_nonebot2"


def _prepare(repo: str) -> None:
    path = Path(repo)
    if not path.is_dir():
        raise SystemExit(f"仓库目录不存在：{repo}")
    sys.path.insert(0, str(path))
    os.chdir(path)  # nonebot.init() 按 cwd 找 .env / .env.dev


class Recorder:
    """替掉匹配器的 send / finish：只记账，不真发。

    Matcher 的 send / finish 是类方法，但给**实例**赋同名属性即可覆盖，
    于是 handler 里的 matcher.send(...) 走的就是这里。
    """

    def __init__(self) -> None:
        self.calls: list = []

    async def send(self, *args, **kwargs):
        self.calls.append(("send", args, kwargs))

    async def finish(self, *args, **kwargs):
        self.calls.append(("finish", args, kwargs))
        # 与真实 Matcher.finish 一致：抛异常结束流程，否则 handler 会接着往下跑
        raise FinishedException


def _message(call, message_cls):
    """从记录下来的调用里取出消息；handler 有时传 Message、有时传裸 MessageSegment"""
    _, args, kwargs = call
    message = kwargs.get("message") if isinstance(kwargs, dict) else None
    if message is None and args:
        message = args[0]
    if message is None:
        return None
    return message if isinstance(message, message_cls) else message_cls(message)


def _dump(recorder: Recorder, message_cls, out_dir: Path, stem: str) -> None:
    print(f"共 {len(recorder.calls)} 次发送调用")
    index = 0
    for call in recorder.calls:
        kind = call[0]
        message = _message(call, message_cls)
        if message is None:
            print(f"\n── [{kind}] 没有消息内容：{call[1]} {call[2]}")
            continue
        kinds = "、".join(segment.type for segment in message)
        print(f"\n── [{kind}] {len(message)} 段（{kinds}）")
        for segment in message:
            if segment.type == "text":
                body = segment.data.get("text", "")
                for row in body.splitlines():
                    print("   | " + row)
                links = re.findall(r"https?://\S+", body)
                if links:
                    print(f"   → 链接：{links}")
            elif segment.type == "image":
                payload = segment.data.get("file", "")
                if "base64://" not in payload:
                    print(f"   → 图片（非 base64）：{payload[:60]}…")
                    continue
                raw = base64.b64decode(payload.split("base64://", 1)[1])
                index += 1
                path = out_dir / f"{stem}_{index}.png"
                path.write_bytes(raw)
                info = f"{len(raw) / 1024:.0f} KB"
                try:
                    from PIL import Image

                    with Image.open(io.BytesIO(raw)) as image:
                        info = f"{image.format} {image.size} {info}"
                except Exception as exc:  # noqa: BLE001 —— 解不开只影响这一行提示
                    info = f"解不开（{type(exc).__name__}） {info}"
                print(f"   → 图片已存 {path}（{info}）")
                print("     ↑ 用 vision_analyze 打开它逐字复核排版，别只看尺寸")
            else:
                print(f"   → 其它段：{segment}")


async def _run(handler) -> None:
    """跑 handler；finish() 抛出的 FinishedException 对本次探测而言是正常结束"""
    try:
        await handler()
    except FinishedException:
        pass


async def _main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    module_name, matcher_name, handler_name = sys.argv[1:4]
    _prepare(sys.argv[4] if len(sys.argv) > 4 else DEFAULT_REPO)

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter, Message

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_plugin(module_name)
    module = sys.modules[module_name]

    matcher = getattr(module, matcher_name, None)
    if matcher is None:
        raise SystemExit(f"{module_name} 里没有匹配器 {matcher_name}")
    handler = getattr(module, handler_name, None)
    if handler is None:
        raise SystemExit(f"{module_name} 里没有 handler {handler_name}")

    recorder = Recorder()
    matcher.send = recorder.send
    matcher.finish = recorder.finish

    await _run(handler)

    out_dir = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    stem = "probe_" + module_name.replace(".", "_")
    _dump(recorder, Message, out_dir, stem)


if __name__ == "__main__":
    asyncio.run(_main())
