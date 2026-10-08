# 用 PIL 画通知卡片（QQ 群发用）

适用：用户说「现在发的是封面图 + 文字，不美观，做成一张图、可爱一点」。骨架：一张不透明画布 +
圆角白卡片 + 圆角封面 + 几行文字 + 少量手绘装饰，导出 JPEG 发出。

## 字体（Windows 本机实测可用）

| 用途 | 候选（按序取第一个存在的） |
| --- | --- |
| 粗体标题 | `C:/Windows/Fonts/msyhbd.ttc`、`simhei.ttf`、`msyh.ttc`、`PingFang.ttc`、`wqy-microhei.ttc` |
| 正文 | `C:/Windows/Fonts/msyh.ttc`、`simhei.ttf`、`simkai.ttf`、`PingFang.ttc`、`wqy-microhei.ttc` |

- 本机**没有**幼圆/圆体（`ls /c/Windows/Fonts/ | grep -iE "you|yuan|kai"` 只有 `simkai.ttf`）。
  「可爱」靠配色 + 圆角 + 手绘爱心，不要指望换字体。
- 按 `(粗体, 字号)` 缓存 `ImageFont.truetype` 对象（dict 或 lru_cache）：一次渲染要取十几个字号，
  每次重新打开 20 MB 的 ttc 会明显变慢。
- 取不到字体时 `ImageFont.load_default(size=size)` 兜底，别让出图直接抛异常。

## 不要用 emoji 字形

`🔴📺🔗` 这类彩色 emoji 在 `msyh.ttc` 里**没有字形**，PIL 画出来是豆腐块（`□`）。
「直播中」的标签、爱心、星点一律**用形状画**：

- 胶囊标签：`draw.rounded_rectangle(box, radius=22, fill=INK_PILL)`，左侧一个
  `draw.ellipse([cx-7, cy-7, cx+7, cy+7], fill=(255,255,255))` 当红点，文字用
  `draw.text((dot_cx + 15, dot_cy), "直播中", anchor="lm")` 贴着画。
- 爱心：两个圆 + 一个三角（`ellipse` ×2 + `polygon`），一个 `size` 参数控大小、颜色单独传，
  背景上撒几颗、卡片角落再点两颗就够了。
- 画布用 `ImageDraw.Draw(canvas, "RGBA")`，装饰才能用带 alpha 的色值叠上去。

## 版面骨架（这套坐标实测好看）

- 画布**不要**做整体圆角带透明 —— QQ 端可能把透明角渲染成黑角。整张画布填渐变，
  圆角只做卡片和封面。
- 卡片：`rounded_rectangle` 白底 + 粉色描边（`width=3`），先画一层偏移 3~5px 的半透明色当阴影。
- 渐变底：逐行 `draw.line([(0, y), (w, y)], fill=...)` 按比例插值；再叠一层横向 `Image.composite`
  让左上角更粉、右下角偏蓝紫，比单方向渐变自然。
- 封面：等比缩放到 cover、居中裁剪到 16:9，再用 `Image.new("L")` 画圆角 mask，
  `canvas.paste(cover, (x, y), mask)` —— 直接给照片画圆角框是切不到照片的。
  封面取不到就用占位图（浅粉底 + 一颗大爱心），**不要因此不出图**。
- 文字区自上而下：名字（粗 38，文案写成「{名字} 开播啦~」）→ 标题（24，超宽截断）→ 细分隔线 →
  底部一行「左侧信息 + 右对齐链接」。
- 截断用 `draw.textlength` 循环去尾字再加 `…`；宽度上限取**卡片内宽**（`card_right - 2*pad`），
  不是画布宽。
- 底部左右两段同排：右侧先算 `textlength` 再 `inner_right - width` 定位，左侧的可用宽度要减掉它
  —— 与 `references/plugin-runtime-state.md`「Pillow 渲染的坐标账」是同一条账。

## 导出格式

- 卡片里有**照片**（B 站封面等）时导出 **JPEG `quality=92, optimize=True`**：同一张卡 PNG 435 KB、
  JPEG 135 KB，画质看不出差别（这两档字号的文字仍清晰，已用 `vision_analyze` 逐字复核）。
- 纯图形、色块少的卡片再用 PNG。
- `build_xxx_card(...) -> Optional[bytes]` 整包 try/except，异常就 `return None`，**出图永远不能让
  通知本身失败**（调用方退回「封面图 + 文字」的降级路径，见 SKILL.md）。

## 透明图层：必须 alpha_composite，不能 paste 完直接 convert

词云/贴纸这类透明底图层，如果 `canvas.paste(layer, pos, mask)` 之后再 `canvas.convert("RGB")`，
**透明区域会变成纯黑**（paste 把透明像素的 RGB=0 一并搬了进来，convert 只是丢掉 alpha）。
正确做法：先铺一块浅色底（例如淡粉白面板），再用 `Image.alpha_composite` 把图层叠上去；
叠完要**重新取一次 `ImageDraw.Draw(canvas)`**，旧 draw 还指着旧对象。

## 先把数据源验通，再谈展示

图做得再好看，数据源是空的也白搭。做「把某项数据画进图里」之前，先用最小脚本打一遍真实接口：

- 看接口实际返回什么（`code`、条数、字段），别信「文档说会返回」；
- 同一时间窗做 A/B（例如系统 DNS vs DoH 直连），别用两个时刻的结果下结论；
- 结论落到「这条路现在到底能不能用」。实测踩过的坑：B 站弹幕历史接口恒返回 0 条，
  却先花时间做了「弹幕排版」，等于给空数据做样式。

## 把轮询状态机抽成可调用的函数

`scheduled_job` 里的跳变逻辑（开播/下播/去抖/续接）别只写在轮询函数内部：抽成
`async def _process_transition(uid, info, is_live)` 之后，验证脚本能直接按
「开播 → 抖动一轮 → 真下播 → 30 秒后重开播」驱动它，一次跑完几十项断言，不用真等十几分钟。

## 复核

1. 出图落盘到 scratch，用 `vision_analyze` 逐字读回文字，并专门问「有没有重叠 / 越界 / 截断 / 贴边」。
2. 一次把分支摆全：正常 / 超长名字与标题（验截断）/ 没有封面（验占位图），一轮看完再改。
3. 子模块要能单独出图：带个 `if __name__ == "__main__"` 的 CLI
   （`python live_card.py out.jpg [封面路径] [--name --title --room --online]`）。注意**按文件路径**
   `importlib.util.spec_from_file_location` 加载它 —— 直接 `from plugins.x.card import ...` 会先执行
   包 `__init__`（`require` nonebot 运行时）并以 `ValueError: NoneBot has not been initialized.` 失败。
   若子模块之间还用了相对导入（`from .live_card import ...`），单靠 spec_from_file_location 会
   `ImportError: attempted relative import with no known parent package`；先造一个**假包**即可：
   `pkg = types.ModuleType("blpkg"); pkg.__path__ = [str(插件目录)]; sys.modules["blpkg"] = pkg`，
   然后按名字 `blpkg.<模块名>` 加载各个子模块 —— 相对导入可用，包 `__init__` 又不执行。
4. 真正要验的是「插件真链路发出去的那张图」：跑一遍 handler，把图片段的 base64 解出来落盘再看，
   而不是只看自己单独渲染的样张。
