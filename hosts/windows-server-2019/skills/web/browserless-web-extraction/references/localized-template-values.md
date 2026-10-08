# 从「模板 + 已代入正文」反推数值，再代回译文

## 问题形状

站点只给英文原句和它的模板，而译文词条是按**模板**建的：

```json
{"text": "When you Shield, this gains {ability.1}",
 "type": "Passive",
 "resolved": {"Silver": "When you Shield, this gains 3",
              "Gold":   "When you Shield, this gains 6",
              "Diamond":"When you Shield, this gains 9"}}
```

而译文表里只有：`"When you Shield, this gains {ability.1}" -> "获得护盾时，此物品获得{ability.1}"`。

直接把译文吐出去会变成「获得护盾时，此物品获得{ability.1}」——占位符还在。
要填上数，就得知道 `{ability.1}` 在每个档位分别是多少。

## 为什么不能直接抽数字

用正则把 `resolved` 里的数字全抽出来，再按顺序填进译文的占位符——**语序一变就错位**，
而且原句里本来就有的字面数字会被当成占位符的值。宁可显示英文，不可猜一个错的数值。

## 算法：按模板字面量对齐

原理：`resolved` 是服务端把 `text` 里的占位符**逐个替换**成数值后的结果。
所以把 `text` 按占位符切分得到字面量序列，再按顺序在 `resolved` 里定位每个字面量，
**相邻两个字面量之间的那一段就是被代入的数值**。

```python
import re

_PLACEHOLDER = re.compile(r"\{[^{}]+\}")

def placeholder_values(template: str, rendered: str) -> dict[str, str]:
    """从「模板 + 代入后的正文」里取出每个占位符对应的数值。

    任一段对不上就返回空字典——调用方据此回落原文，绝不猜。
    """
    literals = _PLACEHOLDER.split(template)      # n 个占位符 → n+1 段字面量
    values: dict[str, str] = {}
    cursor = 0
    for index, placeholder in enumerate(_PLACEHOLDER.findall(template)):
        literal = literals[index]
        if literal:
            found = rendered.find(literal, cursor)
            if found < 0:
                return {}
            cursor = found + len(literal)
        following = literals[index + 1]
        if not following:
            values[placeholder] = rendered[cursor:].strip()
            break
        stop = rendered.find(following, cursor)
        if stop < 0:
            return {}
        values[placeholder] = rendered[cursor:stop].strip()
        cursor = stop
    return values
```

代回译文：

```python
zh_template = strings.get(template)          # 翻译表按原模板 key 索引
text = rendered                              # 默认：原文
if zh_template:
    placed = placeholder_values(template, rendered)
    if placed:
        text = zh_template
        for placeholder, value in placed.items():
            text = text.replace(placeholder, value)
    elif not _PLACEHOLDER.search(template):
        text = zh_template                      # 模板本身无占位符，整句就是译文
```

因为替换是按**占位符名字**做的，目标语言的语序多不同都不影响。

## 验证与边界

- 全量跑一次覆盖率统计，把「对齐失败数」打出来——实测这类站点的对齐成功率是 100%，
  出现失败基本意味着你拿错了模板字段或 `resolved` 不是同一档位。
- 同时统计译文覆盖率（卡名、模板分别算了多少）。没覆盖到的会回落原文，
  要在报告里如实说明比例，不要假称「已全部汉化」。
- 多占位符例子：`Deal {x} Damage and {y} Burn` / `Deal 10 Damage and 5 Burn`
  → `{x}=10`、`{y}=5`（字面量是 `Deal `、` Damage and `、` Burn`）。
- 模板里有而 `resolved` 里没有的字面量（或反过来）会让定位失败，此时正确行为是**放弃并回落**，
  不是截取一段将就。
