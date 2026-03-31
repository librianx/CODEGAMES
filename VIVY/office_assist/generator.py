"""从文档文本生成“可执行的办公建议”（本地规则优先）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class OfficeSuggestions:
    title: str
    bullets: List[str]


_RE_TITLE = re.compile(r"^\s*([^\n]{4,40})\s*$")
_RE_DATE = re.compile(r"(\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[:：]\d{2})")
_RE_ACTION = re.compile(r"(请|需要|务必|必须|截止|前|在.*?之前|提交|发送|确认|对齐|更新|修改|评审)")


def _lines(text: str) -> Iterable[str]:
    for raw in (text or "").splitlines():
        s = raw.strip().strip("•*-—\t ")
        if not s:
            continue
        if len(s) <= 1:
            continue
        yield s


def _guess_title(text: str) -> str:
    for s in _lines(text):
        if 4 <= len(s) <= 28 and not _RE_ACTION.search(s):
            if _RE_TITLE.match(s):
                return s
    return ""


def _pick_action_lines(text: str, limit: int = 4) -> List[str]:
    picked: List[str] = []
    for s in _lines(text):
        if _RE_ACTION.search(s) or _RE_DATE.search(s):
            if len(s) > 90:
                s = s[:90].rstrip("，,;；:：") + "…"
            picked.append(s)
        if len(picked) >= limit:
            break
    return picked


def _fallback_prompts(text: str) -> List[str]:
    # 给用户“下一步操作”的最小可用建议
    n = max(0, min(3, len(text or "")))
    _ = n
    return [
        "先确定：这份文档的目标/结论是什么？",
        "抽 3 条：需要你执行的动作（提交/确认/修改）。",
        "标记：任何时间点/截止要求（日期/时间/版本）。",
    ]


def summarize_to_suggestions(text: str, max_bullets: int = 3) -> OfficeSuggestions:
    t = (text or "").strip()
    title = _guess_title(t)
    action_lines = _pick_action_lines(t, limit=max(3, max_bullets))

    bullets: List[str] = []
    for s in action_lines:
        # 规范化成简短 bullet
        if _RE_DATE.search(s) and ("截止" not in s and "前" not in s):
            bullets.append(f"时间点：{s}")
        else:
            bullets.append(s)
        if len(bullets) >= max_bullets:
            break

    if not bullets:
        bullets = _fallback_prompts(t)[:max_bullets]
    return OfficeSuggestions(title=title, bullets=bullets)

