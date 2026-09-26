#!/usr/bin/env python3
"""hermes-i18n-fix.py — 补上 Hermes 官方 i18n 漏掉的用户可见英文（QQBot /new 横幅）。

上游 v0.20.1 里 /new、/reset 的横幅由三段拼成：
    ✨ 会话已重置！重新开始。            <- locales/zh.yaml 已汉化
    ◆ Model / Provider / Context ...     <- gateway/run.py::_format_session_info 裸 f-string（英文）
    ✦ 提示：<随机小贴士>                  <- hermes_cli/tips.py 的 TIPS 全是英文（380 条）

本脚本把这两处搬进官方 i18n 机制：
  1. 往 locales/en.yaml 与 locales/zh.yaml 的 gateway 段插入 session_info.* 键；
  2. 把 gateway/run.py::_format_session_info 的裸 f-string 换成 t("gateway.session_info.*")；
  3. 把中文小贴士写进 hermes_cli/tips.py（TIPS_ZH），并让 get_random_tip 按 display.language 选语言。

中文贴士来源：/opt/data/cache/tips-zh/part{1,2,3}.zh.json
（由 TIPS 按 stride-3 切分后翻译，重建时按 round-robin 还原原顺序）

幂等：重复执行不重复插入；`hermes update`/换镜像覆盖源码后重跑即可恢复。
用法：
    /opt/hermes/.venv/bin/python /opt/data/scripts/hermes-i18n-fix.py          # 应用
    /opt/hermes/.venv/bin/python /opt/data/scripts/hermes-i18n-fix.py --check  # 只报告
    /opt/hermes/.venv/bin/python /opt/data/scripts/hermes-i18n-fix.py --rollback <dir>
"""

from __future__ import annotations

import argparse
import json
import py_compile
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERMES = Path("/opt/hermes")
LOCALES = HERMES / "locales"
RUN_PY = HERMES / "gateway" / "run.py"
TIPS_PY = HERMES / "hermes_cli" / "tips.py"
VENV_PY = HERMES / ".venv" / "bin" / "python"

TIPS_ZH_DIR = Path("/opt/data/cache/tips-zh")
BACKUP_ROOT = Path("/opt/data/backups")

SESSION_INFO_EN = '''  session_info:
    # /new //reset banner session-info block (gateway/run.py::_format_session_info)
    model: "◆ Model: `{model}`"
    provider: "◆ Provider: {provider}"
    context: "◆ Context: {ctx} tokens ({source})"
    endpoint: "◆ Endpoint: {endpoint}"
    source_config: "config"
    source_detected: "detected"
    source_default: "default - set model.context_length in config to override"
'''

SESSION_INFO_ZH = '''  session_info:
    # /new、/reset 横幅里的会话信息块（gateway/run.py::_format_session_info）
    model: '◆ 模型：`{model}`'
    provider: '◆ 提供商：{provider}'
    context: '◆ 上下文：{ctx} tokens（{source}）'
    endpoint: '◆ 端点：{endpoint}'
    source_config: 读自 config.yaml
    source_detected: 自动探测
    source_default: 默认值 — 可在 config.yaml 里设置 model.context_length 覆盖
'''

RUN_PY_OLD_SOURCE = '''        if config_context_length is not None:
            ctx_source = "config"
        elif context_length == DEFAULT_FALLBACK_CONTEXT:
            ctx_source = "default — set model.context_length in config to override"
        else:
            ctx_source = "detected"
'''

RUN_PY_NEW_SOURCE = '''        if config_context_length is not None:
            ctx_source = t("gateway.session_info.source_config")
        elif context_length == DEFAULT_FALLBACK_CONTEXT:
            ctx_source = t("gateway.session_info.source_default")
        else:
            ctx_source = t("gateway.session_info.source_detected")
'''

RUN_PY_OLD_LINES = '''        lines = [
            f"◆ Model: `{model}`",
            f"◆ Provider: {provider or 'openrouter'}",
            f"◆ Context: {ctx_display} tokens ({ctx_source})",
        ]
'''

RUN_PY_NEW_LINES = '''        lines = [
            t("gateway.session_info.model", model=model),
            t("gateway.session_info.provider", provider=provider or "openrouter"),
            t("gateway.session_info.context", ctx=ctx_display, source=ctx_source),
        ]
'''

RUN_PY_OLD_ENDPOINT = '''            lines.append(f"◆ Endpoint: {base_url}")
'''

RUN_PY_NEW_ENDPOINT = '''            lines.append(t("gateway.session_info.endpoint", endpoint=base_url))
'''

TIPS_OLD_BODY = "    return random.choice(TIPS)\n"

TIPS_NEW_BODY = '''    try:
        from agent.i18n import get_language

        if get_language() == "zh" and TIPS_ZH:
            return random.choice(TIPS_ZH)
    except Exception:
        pass
    return random.choice(TIPS)
'''

TIPS_ANCHOR = "def get_random_tip(exclude_recent: int = 0) -> str:"


def log(msg: str) -> None:
    print(msg, flush=True)


def backup(path: Path, tag: str, ts: str, made: list) -> None:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_ROOT / f"{path.name}.{tag}-{ts}"
    shutil.copy2(path, dest)
    made.append(dest)
    log(f"  备份 {path} -> {dest}")


def load_tips_zh() -> list[str] | None:
    """按 stride-3 切分的 part 文件重建原始顺序的 380 条中文贴士。"""
    parts = []
    for i in (1, 2, 3):
        p = TIPS_ZH_DIR / f"part{i}.zh.json"
        if not p.is_file():
            log(f"  ! 缺少 {p} — 跳过 tips 汉化（其余照常应用）")
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(s, str) for s in data):
            log(f"  ! {p} 不是字符串数组 — 跳过 tips 汉化")
            return None
        parts.append(data)

    en = json.loads((TIPS_ZH_DIR / "en-tips.json").read_text(encoding="utf-8"))
    total = len(en)
    n_parts = len(parts)
    # 源切分方式：part_i = TIPS[i::3]  →  重建：out[3*j + i] = parts[i][j]
    out: list[str] = [""] * total
    for i, part in enumerate(parts):
        for j, zh in enumerate(part):
            idx = n_parts * j + i
            if idx >= total:
                log(f"  ! part{i + 1} 条目数超出预期（idx={idx} >= {total}）")
                return None
            out[idx] = zh
    if any(not s for s in out):
        log("  ! 重建后有条目为空 — 跳过 tips 汉化")
        return None
    return out


def patch_catalog(path: Path, snippet: str, check_only: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    # 注意：不能用 "session_info:" in text 判断 —— 上游 /usage 段有 header_session_info:
    # 会误命中。必须锚定行首两空格缩进的 session_info: 键。
    if re.search(r"^  session_info:[ \t]*$", text, re.M):
        log(f"  已是汉化版（含 session_info 段）: {path}")
        return False
    if "gateway:" not in text:
        log(f"  ! {path} 里找不到 gateway: 锚点 — 跳过")
        return False
    if check_only:
        log(f"  待插入 session_info 段: {path}")
        return True
    head, sep, tail = text.partition("gateway:\n")
    new_text = head + sep + snippet + tail
    path.write_text(new_text, encoding="utf-8")
    log(f"  已插入 session_info 段: {path}")
    return True


def patch_run_py(check_only: bool) -> str:
    text = RUN_PY.read_text(encoding="utf-8")
    if "gateway.session_info.model" in text and "gateway.session_info.source_default" in text:
        log(f"  已是汉化版（session_info 走 i18n）: {RUN_PY}")
        return "already"
    edits = [
        (RUN_PY_OLD_SOURCE, RUN_PY_NEW_SOURCE, "ctx_source"),
        (RUN_PY_OLD_LINES, RUN_PY_NEW_LINES, "lines"),
        (RUN_PY_OLD_ENDPOINT, RUN_PY_NEW_ENDPOINT, "endpoint"),
    ]
    missing = [name for old, _new, name in edits if old not in text]
    if missing:
        log(f"  ! {RUN_PY} 里找不到待替换片段: {', '.join(missing)} — 上游可能改了代码，需人工处理")
        return "failed"
    if check_only:
        log(f"  待替换 {len(edits)} 处 session_info f-string: {RUN_PY}")
        return "pending"
    for old, new, _name in edits:
        text = text.replace(old, new, 1)
    RUN_PY.write_text(text, encoding="utf-8")
    log(f"  已替换 {len(edits)} 处 session_info f-string: {RUN_PY}")
    return "patched"


def patch_tips_py(tips_zh: list[str] | None, check_only: bool) -> str:
    text = TIPS_PY.read_text(encoding="utf-8")
    if "TIPS_ZH" in text:
        log(f"  已是汉化版（含 TIPS_ZH）: {TIPS_PY}")
        return "already"
    if tips_zh is None:
        return "skipped"
    if TIPS_ANCHOR not in text or TIPS_OLD_BODY not in text:
        log(f"  ! {TIPS_PY} 锚点不匹配 — 上游可能改了代码")
        return "failed"
    if check_only:
        log(f"  待插入 TIPS_ZH（{len(tips_zh)} 条）+ 改 get_random_tip: {TIPS_PY}")
        return "pending"
    block = "TIPS_ZH = [\n" + "".join(
        "    " + json.dumps(s, ensure_ascii=False) + ",\n" for s in tips_zh
    ) + "]\n\n\n"
    text = text.replace(TIPS_ANCHOR, block + TIPS_ANCHOR, 1)
    text = text.replace(TIPS_OLD_BODY, TIPS_NEW_BODY, 1)
    TIPS_PY.write_text(text, encoding="utf-8")
    log(f"  已插入 TIPS_ZH（{len(tips_zh)} 条）并让 get_random_tip 按语言选贴士: {TIPS_PY}")
    return "patched"


CONV_LOOP = HERMES / "agent" / "conversation_loop.py"
MSG_SAN = HERMES / "agent" / "message_sanitization.py"
SKILL_TOOL = HERMES / "tools" / "skill_manager_tool.py"
MEM_TOOL = HERMES / "tools" / "memory_tool.py"
BG_REVIEW = HERMES / "agent" / "background_review.py"
DISCORD_ADP = HERMES / "plugins" / "platforms" / "discord" / "adapter.py"

# ---------------------------------------------------------------------------
# 第四类：官方 i18n 完全没覆盖的「agent 层」用户可见英文
#   1. 思考过程标签（gateway/run.py 渲染 reasoning 时硬编码 "💭 **Reasoning:**"）
#   2. 中断 / 交接提示（agent/conversation_loop.py 的 _interrupt_text 等）
# 都是裸字符串，不走 t()，所以只能用「原文 -> 译文」精确替换；幂等靠「译文已在文中」判断。
# ---------------------------------------------------------------------------
UI_TEXT_EDITS: list[tuple[Path, str, str]] = [
    # --- 1) 思考过程标签 ---
    (RUN_PY, "-# 💭 Reasoning", "-# 💭 思考过程"),
    (RUN_PY, "> 💭 **Reasoning:**", "> 💭 **思考过程：**"),
    (
        RUN_PY,
        'response = f"💭 **Reasoning:**\\n```\\n{display_reasoning}\\n```\\n\\n{response}"',
        'response = f"💭 **思考过程：**\\n```\\n{display_reasoning}\\n```\\n\\n{response}"',
    ),
    (
        RUN_PY,
        'display_reasoning += f"\\n_... ({len(lines) - 15} more lines)_"',
        'display_reasoning += f"\\n_...（另外 {len(lines) - 15} 行）_"',
    ),
    # --- 2) agent 层中断 / 交接提示 ---
    (
        CONV_LOOP,
        'INTERRUPT_WAITING_FOR_MODEL_PREFIX = "Operation interrupted: waiting for model response ("',
        'INTERRUPT_WAITING_FOR_MODEL_PREFIX = "操作已中断：等待模型响应（"',
    ),
    (
        CONV_LOOP,
        'final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."',
        'final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f} 秒）。"',
    ),
    (
        CONV_LOOP,
        '_interrupt_text = f"Operation interrupted during retry ({_failure_hint}, attempt {retry_count}/{max_retries})."',
        '_interrupt_text = f"操作已中断：重试期间（{_failure_hint}，第 {retry_count}/{max_retries} 次尝试）。"',
    ),
    (
        CONV_LOOP,
        '_interrupt_text = f"Operation interrupted: handling API error ({error_type}: {agent._clean_error_message(str(api_error))})."',
        '_interrupt_text = f"操作已中断：处理 API 错误（{error_type}：{agent._clean_error_message(str(api_error))}）。"',
    ),
    (
        CONV_LOOP,
        '_interrupt_text = f"Operation interrupted: retrying API call after error (retry {retry_count}/{max_retries})."',
        '_interrupt_text = f"操作已中断：API 报错后重试（第 {retry_count}/{max_retries} 次）。"',
    ),
    (
        CONV_LOOP,
        'f"Operation interrupted: retrying empty response from model "\n',
        'f"操作已中断：模型返回空响应后重试"\n',
    ),
    (
        CONV_LOOP,
        'f"(retry {agent._empty_content_retries}/3)."',
        'f"（第 {agent._empty_content_retries}/3 次）。"',
    ),
    (
        CONV_LOOP,
        '_HANDOFF_SKIP_FINAL_RESPONSE = (\n'
        '    "Context was compacted. The previous response is complete — "\n'
        '    "awaiting your next message."\n'
        ')',
        '_HANDOFF_SKIP_FINAL_RESPONSE = (\n'
        '    "上下文已压缩。上一条回复已经结束 —— "\n'
        '    "正在等你的下一条消息。"\n'
        ')',
    ),
    (
        MSG_SAN,
        '"content": text.strip() or "Operation interrupted.",',
        '"content": text.strip() or "操作已中断。",',
    ),
    # --- 3) 「💾 自我改进回顾」通知（agent/background_review.py）---
    #    注意：这些 message 是"工具回执"的原文，被非 verbose 分支原样透传，
    #    所以匹配关键字必须同时认英文和中文，否则中文回执会被静默丢弃。
    (
        BG_REVIEW,
        '        message_lower = message.lower()\n'
        '        if not verbose:\n'
        '            if "created" in message_lower:\n',
        '        message_lower = message.lower()\n'
        '        if not verbose:\n'
        '            if "created" in message_lower or "已创建" in message:\n',
    ),
    (
        BG_REVIEW,
        '            if "updated" in message_lower:\n',
        '            if "updated" in message_lower or "已更新" in message:\n',
    ),
    (
        BG_REVIEW,
        '            if is_skill and "patched" in message_lower:\n',
        '            if is_skill and ("patched" in message_lower or "已修改" in message):\n',
    ),
    (
        BG_REVIEW,
        '        if is_skill:\n            label = "Skill"\n',
        '        if is_skill:\n            label = "技能"\n',
    ),
    (
        BG_REVIEW,
        '            label = "Memory" if target == "memory" else "User profile" if target == "user" else target\n',
        '            label = "记忆" if target == "memory" else "用户档案" if target == "user" else target\n',
    ),
    (
        BG_REVIEW,
        '                        f"📝 Skill \'{skill_name}\' patched: "\n',
        '                        f"📝 技能「{skill_name}」已修改："\n',
    ),
    (
        BG_REVIEW,
        'actions.append(f"📝 Skill \'{skill_name}\' created: {description}")',
        'actions.append(f"📝 已创建技能「{skill_name}」：{description}")',
    ),
    (
        BG_REVIEW,
        'actions.append(f"📝 Skill \'{skill_name}\' rewritten: {description}")',
        'actions.append(f"📝 已重写技能「{skill_name}」：{description}")',
    ),
    (
        BG_REVIEW,
        'f"📝 {message}" if message else f"Skill {action}"',
        'f"📝 {message}" if message else f"技能 {action}"',
    ),
    (
        BG_REVIEW,
        '            or "applied" in message_lower\n',
        '            or "applied" in message_lower\n'
        '            or any(\n'
        '                kw in message\n'
        '                for kw in ("已添加", "已替换", "已删除", "已应用", "已创建", "已更新", "已修改")\n'
        '            )\n',
    ),
    (BG_REVIEW, 'f"{label} updated"', 'f"{label}已更新"'),
    (
        BG_REVIEW,
        'f"💾 Self-improvement review: {summary}"',
        'f"💾 自我改进回顾：{summary}"',
    ),
    (
        DISCORD_ADP,
        're.compile(r"^\\s*💾\\s*Self-improvement review:\\s+\\S[\\s\\S]*$", re.IGNORECASE),',
        're.compile(r"^\\s*💾\\s*(?:Self-improvement review|自我改进回顾)：?\\s+\\S[\\s\\S]*$", re.IGNORECASE),',
    ),
    # --- 4) 工具回执（skill_manage / memory），会经上面的通知透传给用户 ---
    (
        SKILL_TOOL,
        '"message": f"Skill \'{name}\' created.",',
        '"message": f"已创建技能「{name}」。",',
    ),
    (
        SKILL_TOOL,
        '"message": f"Skill \'{name}\' updated (full rewrite).",',
        '"message": f"已更新技能「{name}」（整篇重写）。",',
    ),
    (
        SKILL_TOOL,
        '"message": f"Patched {\'SKILL.md\' if not file_path else file_path} in skill \'{name}\' ({match_count} replacement{\'s\' if match_count > 1 else \'\'}).",',
        '"message": f"已修改技能「{name}」的 {\'SKILL.md\' if not file_path else file_path}（{match_count} 处替换）。",',
    ),
    (
        SKILL_TOOL,
        'message = f"Skill \'{name}\' archived ({archive_msg})."',
        'message = f"已归档技能「{name}」（{archive_msg}）。"',
    ),
    (
        SKILL_TOOL,
        'message = f"Skill \'{name}\' deleted."',
        'message = f"已删除技能「{name}」。"',
    ),
    (
        SKILL_TOOL,
        'message += f" Content absorbed into \'{absorbed_target}\'."',
        'message += f" 内容已并入「{absorbed_target}」。"',
    ),
    (
        SKILL_TOOL,
        '"message": f"File \'{file_path}\' written to skill \'{name}\'.",',
        '"message": f"已向技能「{name}」写入文件 {file_path}。",',
    ),
    (
        SKILL_TOOL,
        '"error": f"File \'{file_path}\' not found in skill \'{name}\'.",',
        '"error": f"技能「{name}」里找不到文件 {file_path}。",',
    ),
    (
        SKILL_TOOL,
        '"message": f"File \'{file_path}\' removed from skill \'{name}\'.",',
        '"message": f"已从技能「{name}」删除文件 {file_path}。",',
    ),
    (MEM_TOOL, '"Entry already exists (no duplicate added)."', '"该条目已存在（未重复添加）。"'),
    (MEM_TOOL, '"Entry added."', '"已添加条目。"'),
    (MEM_TOOL, '"Entry replaced."', '"已替换条目。"'),
    (MEM_TOOL, '"Entry removed."', '"已删除条目。"'),
    (MEM_TOOL, 'f"Applied {len(operations)} operation(s)."', 'f"已应用 {len(operations)} 项操作。"'),
]


def patch_ui_text(check_only: bool) -> str:
    """替换 agent 层的裸英文字符串（思考过程标签 + 中断提示）。"""
    files = sorted({p for p, _o, _n in UI_TEXT_EDITS})
    texts = {p: p.read_text(encoding="utf-8") for p in files}
    counts = {"patched": 0, "already": 0, "missing": 0}
    for path, old, new in UI_TEXT_EDITS:
        where = path.name
        snippet = old.splitlines()[0][:52]
        if new in texts[path]:
            counts["already"] += 1
            log(f"  已汉化跳过 [{where}] {snippet}")
        elif old in texts[path]:
            hits = texts[path].count(old)
            if not check_only:
                # 全部替换（同一句可能在多处出现，如 skill 归档的 absorbed 提示）
                texts[path] = texts[path].replace(old, new)
            counts["patched"] += 1
            log(f"  {'待替换' if check_only else '已替换'} [{where}] {snippet}" + (f" ×{hits}" if hits > 1 else ""))
        else:
            counts["missing"] += 1
            log(f"  ! 未匹配 [{where}] {snippet} — 上游可能改了写法，需人工补映射")
    if not check_only:
        for path, text in texts.items():
            path.write_text(text, encoding="utf-8")
        log(f"  写入 {len(texts)} 个文件")
    return (
        f"替换 {counts['patched']} / 已汉化 {counts['already']} / 未匹配 {counts['missing']}"
    )


def verify_bg_review() -> bool:
    """模拟一次「自我改进回顾」，确认中文工具回执仍能进通知、且摘要全中文。"""
    probe = r'''
import sys
sys.path.insert(0, "/opt/hermes")
from agent.background_review import summarize_background_review_actions

def tc(i, name, args):
    return {"role": "assistant", "tool_calls": [
        {"id": i, "function": {"name": name, "arguments": args}}]}

msgs = [
    tc("1", "memory", "{\"target\":\"user\",\"action\":\"replace\",\"content\":\"x\"}"),
    {"role": "tool", "tool_call_id": "1",
     "content": "{\"success\":true,\"target\":\"user\",\"message\":\"已应用 3 项操作。\"}"},
    tc("2", "skill_manage", "{\"action\":\"create\",\"name\":\"demo\"}"),
    {"role": "tool", "tool_call_id": "2",
     "content": "{\"success\":true,\"message\":\"已创建技能「demo」。\"}"},
    tc("3", "skill_manage", "{\"action\":\"patch\",\"name\":\"demo\"}"),
    {"role": "tool", "tool_call_id": "3",
     "content": "{\"success\":true,\"message\":\"已修改技能「demo」的 SKILL.md（1 处替换）。\"}"},
]
actions = summarize_background_review_actions(msgs, [], "on")
print("ACTIONS", actions)
ascii_ok = all(any("\u4e00" <= c <= "\u9fff" for c in a) for a in actions)
print("COUNT", len(actions), "ALLZH", ascii_ok)
'''
    env = {"HERMES_HOME": "/opt/data", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([str(VENV_PY), "-c", probe], capture_output=True, text=True, env=env)
    out = (r.stdout.strip() or r.stderr.strip()).replace("\n", " | ")
    log(f"  回顾通知自测: {out}")
    return r.returncode == 0 and "COUNT 3 ALLZH True" in r.stdout


def verify() -> bool:
    probe = '''
import os, sys
sys.path.insert(0, "/opt/hermes")
os.environ.setdefault("HERMES_HOME", "/opt/data")
from agent.i18n import t, get_language
from hermes_cli.tips import get_random_tip
print("LANGS", get_language())
print("MODEL", t("gateway.session_info.model", model="m"))
print("CTX", t("gateway.session_info.context", ctx="1.0M", source=t("gateway.session_info.source_detected")))
print("TIP", get_random_tip()[:60])
'''
    env = {"HERMES_HOME": "/opt/data", "PATH": "/usr/bin:/bin"}
    ok = True
    for lang in ("zh", "en"):
        e = dict(env)
        e["HERMES_LANGUAGE"] = lang
        r = subprocess.run([str(VENV_PY), "-c", probe], capture_output=True, text=True, env=e)
        out = r.stdout.strip() or r.stderr.strip()
        log(f"  [{lang}] {out.replace(chr(10), ' | ')}")
        if r.returncode != 0 or "LANGS" not in r.stdout:
            ok = False
        lower = r.stdout.lower()
        if lang == "zh" and ("model:" in lower or "tip" in lower and not any("\u4e00" <= c <= "\u9fff" for c in r.stdout.split("TIP", 1)[-1])):
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查、不写入")
    ap.add_argument("--rollback", metavar="DIR", help="从备份目录回滚")
    args = ap.parse_args()

    if args.rollback:
        src = Path(args.rollback)
        targets = {RUN_PY.name: RUN_PY, TIPS_PY.name: TIPS_PY, "zh.yaml": LOCALES / "zh.yaml", "en.yaml": LOCALES / "en.yaml"}
        restored = 0
        for f in sorted(src.iterdir()):
            base = f.name.split(".i18n-")[0].split(".bak")[0]
            for key, target in targets.items():
                if base.startswith(key):
                    shutil.copy2(f, target)
                    log(f"  回滚 {f} -> {target}")
                    restored += 1
        log(f"已回滚 {restored} 个文件。记得重启 gateway。")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    made: list[Path] = []

    # 先备份，再改动（顺序不能反，否则备份里是改后内容）。
    if not args.check:
        for path, tag in (
            (LOCALES / "en.yaml", "i18n-bak"),
            (LOCALES / "zh.yaml", "i18n-bak"),
            (RUN_PY, "i18n-bak"),
            (TIPS_PY, "i18n-bak"),
            (CONV_LOOP, "i18n-bak"),
            (MSG_SAN, "i18n-bak"),
            (SKILL_TOOL, "i18n-bak"),
            (MEM_TOOL, "i18n-bak"),
            (BG_REVIEW, "i18n-bak"),
            (DISCORD_ADP, "i18n-bak"),
        ):
            backup(path, tag, ts, made)

    log("== 1/5 词典 locales/{en,zh}.yaml ==")
    for path, snippet in ((LOCALES / "en.yaml", SESSION_INFO_EN), (LOCALES / "zh.yaml", SESSION_INFO_ZH)):
        patch_catalog(path, snippet, args.check)

    log("== 2/5 gateway/run.py ==")
    patch_run_py(args.check)

    log("== 3/5 hermes_cli/tips.py ==")
    tips_zh = load_tips_zh()
    patch_tips_py(tips_zh, args.check)

    log("== 4/5 agent 层裸英文（思考过程标签 + 中断提示）==")
    summary = patch_ui_text(args.check)
    log(f"  → {summary}")
    if "未匹配 0" not in summary and "未匹配" in summary:
        log("  ⚠ 有未匹配项，请人工核对上游写法变化")

    if args.check:
        log("\n--check 完成，未写入任何文件。")
        return 0

    log("\n== 5/5 语法与效果校验 ==")
    bad = False
    for py in (RUN_PY, TIPS_PY, CONV_LOOP, MSG_SAN, SKILL_TOOL, MEM_TOOL, BG_REVIEW, DISCORD_ADP):
        try:
            py_compile.compile(str(py), doraise=True, cfile="/tmp/_chk.pyc")
            log(f"  ✓ py_compile 通过: {py}")
        except py_compile.PyCompileError as exc:
            log(f"  ✗ py_compile 失败: {py}\n{exc}")
            bad = True
    if bad:
        log("语法校验失败 —— 请用备份回滚。备份目录: " + str(BACKUP_ROOT))
        return 1
    if not verify():
        log("⚠ 效果校验异常，请人工确认。")
        return 1
    if not verify_bg_review():
        log("⚠ 自我改进回顾通知自测异常，请人工确认。")
        return 1
    log("\n完成。重启 gateway 生效：/command/s6-svc -r /run/service/gateway-default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
