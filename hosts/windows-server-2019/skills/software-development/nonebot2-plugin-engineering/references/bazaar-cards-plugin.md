# 「大巴扎」插件（plugins/bazaardb）

命令：`巴扎 <关键词>` 查卡；`巴扎别名 …` 管理别名。
**不要**改回「查分 / 绑定 / 排名 / 每日推送」——那些依赖另一个站点的用户排位数据，已按用户要求整体删除。

## 数据源：bazaar-cards.com（纯前端站，数据走公开 JSON）

| 接口 | 内容 |
| --- | --- |
| `GET /library-data/v2?hover-tooltips=1` | 全量卡牌 + 技能 + 商人 + 商店池（约 2.3 MB，1332 张卡） |
| `GET /library-data/translations/zh-CN` | 官方简体中文词条（约 560 KB，`strings` 是「英文字符串 → 中文」的平表） |
| `GET /library-data/cards/<id>` | 单卡补充：各档位 `cooldownMax`（毫秒）、`buyPrice` / `sellPrice`、附魔 |
| `GET /card-images/<id>.webp` | 卡面原图（**唯一格式**：200×400、约 71 KB；`png` / `jpg` / `@2x` / `-large` 变体探测全是 404） |

- `/library-data/v2` 会返回 308，curl 必须带 `-L`。
- 卡片页深链接是 `https://bazaar-cards.com/?card=<id>`；**插件已不再用它**（原用于云端截图渲染，
  已按用户要求改成发卡面原图，`Card.page_url` 随之删除）。
- 站点的 `robots.txt` 把 `/library-data` 一类路径标为 Disallow，但应用本身就靠这些接口取数。

## 中文渲染：模板 + 各档位数值

单卡技能的形态是：

```json
{"text": "Shield {ability.0}", "type": "Active",
 "resolved": {"Silver": "Shield 20", "Gold": "Shield 40", "Diamond": "Shield 80"}}
```

要出中文，需要把 `resolved` 里的**具体数值**代回中文模板（`strings["Shield {ability.0}"] == "获得{ability.0}护盾"`）。
取值算法见 `browserless-web-extraction` 的 `references/localized-template-values.md`，
本插件实测 8212/8212 次代入全部对上、0 失败。

- 官方词条没覆盖的地方（约 18% 卡名、27% 技能模板）**保留英文原文**，不要臆造译文。
- `type` 里带 `bzdbgg.` 前缀的是从上游继承的内部搜索标记（如 `Multicast: 3`），**必须过滤**，否则会漏进用户文案。
- 档位名、英雄名、标签、尺寸都能从同一份 `strings` 里查到中文。

## 缓存与布局

- 文件：`cards.py`（数据客户端）/ `view.py`（文案）/ `__init__.py`（命令 + 别名持久化）。
- `cache/library.json` 存**原始 payload**（不是加工后的对象），过期自动重拉；已加进 `.gitignore`。
- 磁盘缓存过期但联网失败时，退回用过期缓存并缩短重试间隔，别让一次网络抖动清空功能。
- 命令只有**一个** `on_regex(r"^巴扎")`，子命令在 handler 里用 `argument.partition(" ")` 分发。

## 展示约定（被用户反复纠正后定下的，别改回去）

唯一命中 → **描述与卡面原图拼成同一条消息**发出：

- 图取 `Card.image_url`（站点原图 `/card-images/<id>.webp`），**不是**网页截图——
  用户原话「应该是保留网页原图，不是截图」。
- 站点只有 webp，发送前用 PIL **无损转 PNG**（画质不变、QQ 显示更稳）；转不动就原样发，别把图弄丢。
- 文案里**不带网站链接**——用户原话「不要链接，不要把网站链接发出来」。
- 踩过的坑：先发文字、等云端截图渲染好再单独发第二条，两条相隔最久二十多秒，
  看起来就像根本没发图（用户当时的反馈就是「没有发送图片出来」）。**一条消息发完**，别分两条。
- 图取不到只是少一张图，描述必须照常发出。

命中多张 → 只发一览列表，提示说具体一点。
