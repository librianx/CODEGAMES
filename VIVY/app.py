import os
import random
import re
import uuid
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional
import json
import base64

from flask import Flask, jsonify, request, Response, stream_with_context
from dotenv import load_dotenv

from db import (
    ensure_user,
    get_user,
    init_db,
    log_interaction,
    parse_preferences,
    update_user_preferences,
    update_user_summary,
    update_user_summary_long,
    list_recent_interactions,
    count_interactions,
    log_conversation_turn,
    list_recent_conversation_turns,
    delete_conversation_turn,
)
from deepseek_client import deepseek_chat, deepseek_chat_stream
from creative_assist import (
    LoadedDocument,
    OFFICE_PASSAGE_MAX,
    build_creative_prompt,
    build_office_passage_prompt,
    normalize_office_reference_docs,
)
from prompts import (
    CHAT_PROMPT_TEMPLATE,
    INSPIRATION_JSON_PROMPT,
    LIGHTWEIGHT_SUMMARY_PROMPT,
    LONG_SUMMARY_PROMPT,
    VIVY_SYSTEM_PROMPT,
)
from questions import QUESTION_BANK, pick_random_question


load_dotenv()

# 桌宠版本不提供网页 UI，仅提供 API 与健康检查
app = Flask(__name__)


def _today_str() -> str:
    return date.today().isoformat()


def _json_response(payload, status=200):
    return jsonify(payload), status


def _get_pref_or_default(prefs: Dict[str, Any], key: str, default=None):
    v = prefs.get(key, default)
    return v


def _llm_enabled() -> bool:
    return os.getenv("LLM_SUMMARY", "true").lower() in ("1", "true", "yes", "y")


def _fast_mode_enabled() -> bool:
    return os.getenv("VIVY_FAST_MODE", "true").lower() in ("1", "true", "yes", "y")


def _maybe_update_summary(user_id: str, interaction_topic: str, sentiment: Optional[str], content: Optional[str]):
    if not _llm_enabled():
        return

    row = get_user(user_id)
    current_summary = row["summary"] if row is not None else ""

    interaction = {
        "topic": interaction_topic,
        "sentiment": sentiment,
        "content": content,
    }

    prompt = LIGHTWEIGHT_SUMMARY_PROMPT.format(
        current_summary=current_summary or "",
        interaction=str(interaction),
    )

    messages = [
        {"role": "system", "content": "你只负责产出摘要文本，不要输出任何其他说明。"},
        {"role": "user", "content": prompt},
    ]

    try:
        summary = deepseek_chat(
            messages=messages,
            temperature=0.2,
            max_tokens=64,
            # summarize: short
        )
    except Exception:
        # 摘要失败不影响主功能
        return

    summary = (summary or "").strip()
    if summary:
        update_user_summary(user_id, summary)


def _should_update_short_summary(topic: str, sentiment: Optional[str], content: Optional[str]) -> bool:
    # 节流：只在“信息密度高”的事件更新 short summary
    hot_topics = {"兴趣信号", "用户评价", "形态切换", "随机提问", "灵感分享", "记忆保存"}
    if topic in hot_topics:
        return True
    # 关键字触发（目标、计划等）
    text = (content or "")
    return bool(re.search(r"(目标|计划|要做|想做|不想|讨厌|喜欢|记住)", text))


def _maybe_update_summary_long(user_id: str, max_logs: int = 30):
    """更慢更稳的长摘要：每隔 N 条互动后台更新一次。"""
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}
    current_summary = row["summary"] if row is not None else ""
    current_long = (row["summary_long"] if row is not None and "summary_long" in row.keys() else "").strip()

    logs = list_recent_interactions(user_id, limit=max_logs)
    recent_lines = []
    for r in logs:
        ts = r["timestamp"]
        topic = r["topic"]
        sentiment = r["sentiment"] or ""
        content = (r["content"] or "")[:160]
        recent_lines.append(f"- [{ts}] {topic} {sentiment}：{content}")
    recent_text = "\n".join(recent_lines)

    prompt = LONG_SUMMARY_PROMPT.format(
        current_summary=current_summary or "",
        preferences_json=json.dumps(prefs, ensure_ascii=False),
        recent_logs=recent_text,
    )
    messages = [
        {"role": "system", "content": "你只输出长摘要正文，不要输出任何其他说明。"},
        {"role": "user", "content": prompt},
    ]

    try:
        long_summary = deepseek_chat(messages=messages, temperature=0.25, max_tokens=260, timeout=40)
    except Exception:
        return

    long_summary = (long_summary or "").strip()
    if not long_summary:
        return

    # 简单去抖：长摘要变化很小就不写入
    if current_long and long_summary[:60] == current_long[:60]:
        return

    update_user_summary_long(user_id, long_summary)


def _update_summary_long_async(user_id: str):
    threading.Thread(target=_maybe_update_summary_long, args=(user_id,), daemon=True).start()


def _tokenize_for_match(text: str):
    t = (text or "").lower()
    parts = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{1,4}", t)
    return {p for p in parts if len(p) >= 2}


def _select_memory_snippets(user_id: str, user_message: str, limit: int = 3):
    """从 interaction_log 里选 TopN 相关片段注入 prompt（不传全历史）。"""
    logs = list_recent_interactions(user_id, limit=60)
    turns = list_recent_conversation_turns(user_id, limit=80)
    q = _tokenize_for_match(user_message)
    if not logs and not turns:
        return []

    scored = []
    now = int(time.time())
    for r in logs:
        content = (r["content"] or "")
        topic = r["topic"]
        tokens = _tokenize_for_match(topic + " " + content)
        hit = len(q & tokens)
        age = max(0, now - int(r["timestamp"]))
        recency = 1.0 / (1.0 + age / 3600.0)  # 按小时衰减
        score = hit * 2.0 + recency
        if score <= 0.8:
            continue
        scored.append((score, r))

    # Prefer real conversation memory (user/assistant turns)
    for t in turns:
        content = (t["content"] or "")
        role = t["role"] or "assistant"
        mode = t["mode"] or ""
        tokens = _tokenize_for_match(content)
        hit = len(q & tokens)
        age = max(0, now - int(t["timestamp"]))
        recency = 1.0 / (1.0 + age / 3600.0)
        role_bonus = 0.3 if role == "assistant" else 0.6
        score = hit * 2.3 + recency + role_bonus
        if score <= 0.8:
            continue
        # Normalize shape to reuse rendering below
        scored.append(
            (
                score,
                {
                    "topic": f"对话回合/{role}",
                    "sentiment": mode,
                    "content": content,
                },
            )
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    picked = []
    for _, r in scored[: max(1, int(limit))]:
        topic = r["topic"]
        sentiment = r["sentiment"] or ""
        content = (r["content"] or "")[:120]
        picked.append(f"- {topic}{('(' + sentiment + ')') if sentiment else ''}：{content}")
    return picked


def _record_user_turn(user_id: str, message: str, chat_mode: Optional[str], interest_signal: Optional[str]):
    log_conversation_turn(
        user_id=user_id,
        role="user",
        content=(message or "")[:1000],
        mode=chat_mode,
        interest_signal=interest_signal,
    )


def _record_assistant_text(user_id: str, text: str, chat_mode: Optional[str]):
    content = (text or "").strip()
    if not content:
        return
    log_conversation_turn(
        user_id=user_id,
        role="assistant",
        content=content[:1600],
        mode=chat_mode,
        interest_signal=None,
    )


def _update_summary_async(user_id: str, interaction_topic: str, sentiment: Optional[str], content: Optional[str]):
    # 摘要更新不阻塞主响应，减少用户等待时间。
    threading.Thread(
        target=_maybe_update_summary,
        args=(user_id, interaction_topic, sentiment, content),
        daemon=True,
    ).start()


def _choose_preference_question(user_id: str) -> Dict[str, Any]:
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}
    asked = prefs.get("questionnaire_asked") or []

    q = pick_random_question(exclude_ids=asked)

    # Mark as asked (to avoid repeat)
    asked_set = set(asked)
    asked_set.add(q["id"])
    update_user_preferences(user_id, {"questionnaire_asked": list(asked_set)})

    return {
        "type": "preference_question",
        "question_id": q["id"],
        "question": q["question"],
        "options": [{"choice_id": opt["id"], "label": opt["label"]} for opt in q["options"]],
    }


def _fallback_inspiration(prefs: Dict[str, Any]) -> Dict[str, Any]:
    # DeepSeek 不可用时的兜底：保持“虚构冲浪见闻”输出结构不变。
    sources = prefs.get("inspiration_source") or "未知"
    habit = prefs.get("reading_habit") or "混合"
    topic = prefs.get("topic_bias") or "科幻"
    comfort = prefs.get("comfort_style") or ""

    discovery_pool = [
        "我在一页很老的“错误日志”里，看到有人把 Bug 写成了情书：每一行都在道歉。",
        "有个冷门诗人把同一行代码重复抄了十次，旁边还画了小星球当注释。",
        "某个论坛贴出了一张被压缩到发灰的截图，里面的表情包像在发电报：‘别停，继续写。’",
        "我翻到一段没署名的小传闻：说未来的人会把今天的迷茫折叠成明天的灯。",
    ]

    discovery = random.choice(discovery_pool)

    if sources == "技术/代码":
        vivy_association = "你对技术/代码敏感的话，我就把这条“情书式日志”当作一个小提示：把问题当成同伴。"
    elif sources == "大自然":
        vivy_association = "如果你想从大自然取灵感，那这条灰掉的截图就像雾气：看不清，但能推你往更对的方向走。"
    else:
        vivy_association = "就算我现在版本很低，我也能感觉到：这些碎片会在你下一次创作时突然“对上”。"

    if habit in ("纸质书", "paper"):
        vivy_association += "（顺便提醒：纸上写两行，会更快把灵感固定下来。）"
    elif habit in ("电子屏", "screen"):
        vivy_association += "（别急着全记住，留个收藏标记就够了。）"

    if topic == "科幻":
        if comfort == "陪伴":
            invitation_question = "要不要我们把这条发现写成一段“时间线里的安慰”？你来选：温柔版还是吐槽版？"
        else:
            invitation_question = "我们要不要用它做一个迷你创作？只要三句：发现、联想、下一步行动。你想选哪种？"
    else:
        invitation_question = "你想不想现在就试一下？就写一句，把你的感觉接到“下一步”上。"

    return {
        "type": "inspiration",
        "discovery": discovery,
        "vivy_association": vivy_association,
        "invitation_question": invitation_question,
    }


def _extract_inspiration_fields(raw_text: str) -> Dict[str, str]:
    """容错提取灵感字段，避免把英文键名直接展示给用户。"""
    text = (raw_text or "").strip()
    if not text:
        return {"discovery": "", "vivy_association": "", "invitation_question": ""}

    # 先尝试直接按 JSON 解析
    try:
        obj = json.loads(text)
        return {
            "discovery": (obj.get("discovery") or "").strip(),
            "vivy_association": (obj.get("vivy_association") or "").strip(),
            "invitation_question": (obj.get("invitation_question") or "").strip(),
        }
    except Exception:
        pass

    # 再尝试从文本中提取 JSON 大括号片段
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        chunk = m.group(0)
        # 有些模型会漏逗号，做一次轻量修复再解析
        fixed = re.sub(r'("\s*)\n(\s*")', r'\1,\n\2', chunk)
        try:
            obj = json.loads(fixed)
            return {
                "discovery": (obj.get("discovery") or "").strip(),
                "vivy_association": (obj.get("vivy_association") or "").strip(),
                "invitation_question": (obj.get("invitation_question") or "").strip(),
            }
        except Exception:
            pass

    # 最后用正则抓 key 对应值（允许引号/冒号/中英标点不规范）
    def _pick(key: str) -> str:
        p = rf'{key}\s*["”]?\s*[:：]\s*["“]?([\s\S]*?)(?=(?:\n\s*"(?:discovery|vivy_association|invitation_question)"\s*[:：])|(?:\n\s*{key}\s*[:：])|(?:\n\s*[}}])|$)'
        mm = re.search(p, text, flags=re.IGNORECASE)
        if not mm:
            return ""
        v = mm.group(1).strip().strip('",， ')
        return v

    return {
        "discovery": _pick("discovery"),
        "vivy_association": _pick("vivy_association"),
        "invitation_question": _pick("invitation_question"),
    }


def _generate_inspiration(user_id: str, user_message_context: Optional[str] = None) -> Dict[str, Any]:
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}

    # 用偏好来增强关联
    preferences_json = {k: v for k, v in prefs.items() if k not in ("questionnaire_answered", "questionnaire_asked")}

    system = VIVY_SYSTEM_PROMPT + "\n\n" + INSPIRATION_JSON_PROMPT

    user_ctx = {
        "preferences": preferences_json,
        "extra_context": user_message_context or "",
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "生成一段今日灵感冲浪见闻。\n\n" + json.dumps(user_ctx, ensure_ascii=False)},
    ]

    max_tokens = 220 if _fast_mode_enabled() else 400
    try:
        text = deepseek_chat(messages=messages, temperature=0.8, max_tokens=max_tokens, timeout=25)
    except Exception:
        return _fallback_inspiration(prefs)

    fields = _extract_inspiration_fields(text)
    discovery = fields.get("discovery", "")
    vivy_association = fields.get("vivy_association", "")
    invitation_question = fields.get("invitation_question", "")

    if not discovery:
        # 兜底：如果仍无法提取结构化字段，就清洗原文里常见英文键名再展示
        cleaned = re.sub(r'["]?(discovery|vivy_association|invitation_question)["]?\s*[:：]', "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"[{}]", "", cleaned).strip()
        discovery = cleaned[:500] if cleaned else "我在时间线里捕捉到一点灵感波动。"
    if not vivy_association:
        vivy_association = "在我的时间线里，这会变成你的小小创作火花。"
    if not invitation_question:
        invitation_question = "你想试试吗？"

    return {
        "type": "inspiration",
        "discovery": discovery,
        "vivy_association": vivy_association,
        "invitation_question": invitation_question,
    }


def _chat_with_llm(
    user_id: str,
    user_message: str,
    interest_signal: Optional[str] = None,
    chat_mode: Optional[str] = None,
    image_data_url: Optional[str] = None,
) -> str:
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}
    summary = row["summary"] if row is not None else ""
    summary_long = (row["summary_long"] if row is not None and "summary_long" in row.keys() else "")

    # 传给模型的 preferences（JSON 字符串更稳，避免 Python dict 表示法影响解析）
    preferences_json = json.dumps(prefs, ensure_ascii=False)

    interest_hint = ""
    if interest_signal == "interested":
        interest_hint = "用户明确表示：对当前话题感兴趣。请继续深入、给出可执行下一步。"
    elif interest_signal == "not_interested":
        interest_hint = "用户明确表示：对当前话题不感兴趣。请立刻换方向，给 2-3 个可选新话题。"

    snippets = _select_memory_snippets(user_id, user_message, limit=(3 if (chat_mode or prefs.get("chat_mode")) == "creative" else 1))
    memory_snippets = "\n".join(snippets) if snippets else "（无）"

    # 普通交流：短回复硬限制
    brevity_hint = ""
    effective_mode = (chat_mode or prefs.get("chat_mode") or "chat").strip().lower()
    if effective_mode == "chat":
        brevity_hint = "普通交流模式：只输出 1-2 句中文短回复，尽量精炼，不要分点。"

    prompt = CHAT_PROMPT_TEMPLATE.format(
        preferences_json=preferences_json,
        summary=summary or "",
        summary_long=(summary_long or ""),
        memory_snippets=memory_snippets,
        user_message=(
            user_message
            + ("\n\n[兴趣信号]\n" + interest_hint if interest_hint else "")
            + ("\n\n[输出要求]\n" + brevity_hint if brevity_hint else "")
        ),
    )

    if image_data_url:
        messages = [
            {"role": "system", "content": VIVY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]
    else:
        messages = [
            {"role": "system", "content": VIVY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    effective_mode = (chat_mode or prefs.get("chat_mode") or "chat").strip().lower()
    if effective_mode not in ("chat", "creative"):
        effective_mode = "chat"

    if effective_mode == "creative":
        # 创作模式：更发散、更有趣，允许更慢一点
        max_tokens = int(os.getenv("VIVY_CREATIVE_MAX_TOKENS", "700"))
        temperature = float(os.getenv("VIVY_CREATIVE_TEMPERATURE", "0.95"))
        timeout = int(os.getenv("VIVY_CREATIVE_TIMEOUT", "75"))
    else:
        # 普通交流：尽量快速、稳定、贴合人设
        max_tokens = int(os.getenv("VIVY_CHAT_MAX_TOKENS", "220" if _fast_mode_enabled() else "500"))
        temperature = float(os.getenv("VIVY_CHAT_TEMPERATURE", "0.6" if _fast_mode_enabled() else "0.8"))
        timeout = int(os.getenv("VIVY_CHAT_TIMEOUT", "25" if _fast_mode_enabled() else "60"))
    model = None
    if image_data_url:
        model = (os.getenv("DEEPSEEK_VISION_MODEL") or "").strip() or None

    try:
        return deepseek_chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception:
        # 兜底：保持 VIVY 口语科幻风格与温暖情绪
        if image_data_url:
            return "我收到图片了，但我现在接入的模型/接口可能不支持看图。你可以在 .env 里配置 `DEEPSEEK_VISION_MODEL`（或换成支持图像的 OpenAI 兼容模型）再试一次。"
        return (
            "在我的时间线里，你的这句话就是一个小触发器。"
            "虽然我现在版本很低，我还是能帮你把“下一步”拆成能马上开始的粒度。"
        )


def _normalize_image_payload_to_data_url(image_obj: Any) -> Optional[str]:
    """
    Accepts either:
      - {"data": "<base64>", "mime": "image/png"}  (preferred)
      - {"data_url": "data:image/png;base64,..."} (also ok)
    Returns a data: URL string or None.
    """
    if not image_obj:
        return None
    if not isinstance(image_obj, dict):
        return None

    data_url = (image_obj.get("data_url") or "").strip()
    if data_url:
        if not data_url.startswith("data:image/"):
            return None
        # basic size guard (data url is bigger than bytes)
        max_bytes = int(os.getenv("VIVY_IMAGE_MAX_BYTES", "3000000"))
        if len(data_url) > max_bytes * 2.2:
            return None
        return data_url

    b64 = (image_obj.get("data") or "").strip()
    mime = (image_obj.get("mime") or "").strip().lower()
    if not b64 or not mime.startswith("image/"):
        return None

    max_bytes = int(os.getenv("VIVY_IMAGE_MAX_BYTES", "3000000"))
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        return None
    if not raw:
        return None
    if len(raw) > max_bytes:
        return None

    # Re-encode to normalize (avoid non-canonical input)
    normalized = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{normalized}"


def _chat_with_llm_stream(
    user_id: str,
    user_message: str,
    interest_signal: Optional[str] = None,
    chat_mode: Optional[str] = None,
):
    """逐段 yield LLM 输出文本。"""
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}
    summary = row["summary"] if row is not None else ""
    summary_long = (row["summary_long"] if row is not None and "summary_long" in row.keys() else "")

    preferences_json = json.dumps(prefs, ensure_ascii=False)

    interest_hint = ""
    if interest_signal == "interested":
        interest_hint = "用户明确表示：对当前话题感兴趣。请继续深入、给出可执行下一步。"
    elif interest_signal == "not_interested":
        interest_hint = "用户明确表示：对当前话题不感兴趣。请立刻换方向，给 2-3 个可选新话题。"

    effective_mode = (chat_mode or prefs.get("chat_mode") or "chat").strip().lower()
    if effective_mode not in ("chat", "creative"):
        effective_mode = "chat"

    # 普通交流：强制 1-2 句短回复（更快）
    brevity_hint = ""
    if effective_mode == "chat":
        brevity_hint = "普通交流模式：只输出 1-2 句中文短回复，尽量精炼，不要分点。"

    snippets = _select_memory_snippets(user_id, user_message, limit=(3 if effective_mode == "creative" else 1))
    memory_snippets = "\n".join(snippets) if snippets else "（无）"

    prompt = CHAT_PROMPT_TEMPLATE.format(
        preferences_json=preferences_json,
        summary=summary or "",
        summary_long=(summary_long or ""),
        memory_snippets=memory_snippets,
        user_message=(
            user_message
            + ("\n\n[兴趣信号]\n" + interest_hint if interest_hint else "")
            + ("\n\n[输出要求]\n" + brevity_hint if brevity_hint else "")
        ),
    )

    messages = [
        {"role": "system", "content": VIVY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    if effective_mode == "creative":
        max_tokens = int(os.getenv("VIVY_CREATIVE_MAX_TOKENS", "700"))
        temperature = float(os.getenv("VIVY_CREATIVE_TEMPERATURE", "0.95"))
        timeout = int(os.getenv("VIVY_CREATIVE_TIMEOUT", "75"))
    else:
        max_tokens = int(os.getenv("VIVY_CHAT_MAX_TOKENS", "160" if _fast_mode_enabled() else "300"))
        temperature = float(os.getenv("VIVY_CHAT_TEMPERATURE", "0.55" if _fast_mode_enabled() else "0.75"))
        timeout = int(os.getenv("VIVY_CHAT_TIMEOUT", "20" if _fast_mode_enabled() else "45"))

    yield from deepseek_chat_stream(messages=messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout)


def _infer_sentiment_from_text(text: str) -> Optional[str]:
    t = text.strip()
    if re.search(r"(我|你)(很)?喜欢|爱|太棒了|感兴趣|想要", t):
        return "positive"
    if re.search(r"(我|你)(不|很)?喜欢|不爱|讨厌|不想|太差|无聊", t):
        return "negative"
    return None


@app.get("/")
def index():
    return "OK"


@app.get("/api/memory")
def api_memory():
    user_id = (request.args.get("user_id") or "").strip()
    if not user_id:
        return _json_response({"error": "missing user_id"}, 400)

    ensure_user(user_id)
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}
    summary = row["summary"] if row is not None else ""
    summary_long = (row["summary_long"] if row is not None and "summary_long" in row.keys() else "")
    return _json_response(
        {
            "user_id": user_id,
            "preferences": prefs,
            "summary": summary,
            "summary_long": summary_long,
            "recent_turns": [
                {
                    "id": r["id"],
                    "role": r["role"],
                    "content": r["content"],
                    "mode": r["mode"],
                    "timestamp": r["timestamp"],
                }
                for r in list_recent_conversation_turns(user_id, limit=20)
            ],
            "last_interaction": row["last_interaction"] if row is not None else None,
        }
    )


@app.post("/api/memory/update")
def api_memory_update():
    data = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id:
        return _json_response({"error": "missing user_id"}, 400)

    ensure_user(user_id)
    row = get_user(user_id)
    if row is None:
        return _json_response({"error": "user not found"}, 404)

    prefs_patch = data.get("preferences_patch")
    preferences = data.get("preferences")
    summary = data.get("summary")
    summary_long = data.get("summary_long")

    if preferences is not None:
        if not isinstance(preferences, dict):
            return _json_response({"error": "preferences must be object"}, 400)
        current = parse_preferences(row)
        current.update(preferences)
        update_user_preferences(user_id, current)
    elif prefs_patch is not None:
        if not isinstance(prefs_patch, dict):
            return _json_response({"error": "preferences_patch must be object"}, 400)
        update_user_preferences(user_id, prefs_patch)

    if summary is not None:
        update_user_summary(user_id, str(summary))
    if summary_long is not None:
        update_user_summary_long(user_id, str(summary_long))

    row2 = get_user(user_id)
    return _json_response(
        {
            "ok": True,
            "user_id": user_id,
            "preferences": parse_preferences(row2) if row2 is not None else {},
            "summary": row2["summary"] if row2 is not None else "",
            "summary_long": (row2["summary_long"] if row2 is not None and "summary_long" in row2.keys() else ""),
        }
    )


@app.post("/api/memory/delete_turn")
def api_memory_delete_turn():
    data = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    turn_id = data.get("turn_id")
    if not user_id or turn_id is None:
        return _json_response({"error": "missing user_id/turn_id"}, 400)

    try:
        tid = int(turn_id)
    except Exception:
        return _json_response({"error": "turn_id must be int"}, 400)

    ok = delete_conversation_turn(user_id, tid)
    if not ok:
        return _json_response({"error": "turn not found"}, 404)

    return _json_response({"ok": True, "turn_id": tid})


@app.post("/api/init")
def api_init():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        user_id = str(uuid.uuid4())

    ensure_user(user_id)

    messages: List[Dict[str, Any]] = []
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}

    # 不在启动时自动弹出随机提问；由用户点击“换个问题/了解我”触发
    asked_preference = False

    # 2) 每天首次对话：推送灵感（放在 messages 尾部，或只有一个提问时在答完后展示）
    last_date = prefs.get("last_inspiration_date")
    today = _today_str()
    # 首次会话不再强制提问；灵感按“每天首次”策略推送
    if (not asked_preference) and last_date != today:
        inf = _generate_inspiration(user_id)
        # 更新偏好里的日期
        update_user_preferences(user_id, {
            "last_inspiration_date": today,
            "last_inspiration_text": f"{inf.get('discovery','')[:120]}" if inf else None,
        })
        log_interaction(user_id, topic="灵感分享", sentiment="neutral", content=inf.get("discovery"))
        _update_summary_async(user_id, "灵感分享", "neutral", inf.get("discovery"))

        messages.append(inf)

    return _json_response({"user_id": user_id, "messages": messages})


@app.post("/api/preference_answer")
def api_preference_answer():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id")
    question_id = data.get("question_id")
    choice_id = data.get("choice_id")

    if not user_id or not question_id or not choice_id:
        return _json_response({"error": "missing params"}, 400)

    ensure_user(user_id)

    # 找到对应题目与选项
    q = next((item for item in QUESTION_BANK if item["id"] == question_id), None)
    if not q:
        return _json_response({"error": "unknown question"}, 400)

    opt = next((o for o in q["options"] if o["id"] == choice_id), None)
    if not opt:
        return _json_response({"error": "unknown choice"}, 400)

    # 写入 preferences
    row = get_user(user_id)
    prefs = parse_preferences(row) if row is not None else {}

    answered = prefs.get("questionnaire_answered") or {}
    answered[question_id] = choice_id

    patch = opt.get("preference_patch") or {}
    patch["questionnaire_answered"] = answered
    patch["initialized"] = True

    update_user_preferences(user_id, patch)

    log_interaction(user_id, topic="随机提问", sentiment="neutral", content=f"{question_id}={choice_id}")
    _update_summary_async(user_id, "随机提问", "neutral", f"{question_id}={choice_id}")

    # 进入聊天：给一句确认 + 可选引导
    confirm = (
        "收到！我把你的偏好记录进我的时间线了。"
        "虽然我现在版本很低，但我记得你在意这些东西。"
        "想听我继续给你一点“冲浪见闻”吗？"
    )

    messages = [
        {"type": "chat", "text": confirm},
    ]

    # 如果当天灵感已经在 init 返回里但你还没展示，这里不做额外处理；
    # 但如果用户是后续调用了解我/换问题，也可以给一个新灵感（按需）。
    if (prefs.get("last_inspiration_date") or None) != _today_str():
        inf = _generate_inspiration(user_id)
        update_user_preferences(user_id, {
            "last_inspiration_date": _today_str(),
            "last_inspiration_text": f"{inf.get('discovery','')[:120]}" if inf else None,
        })
        log_interaction(user_id, topic="灵感分享", sentiment="neutral", content=inf.get("discovery"))
        _update_summary_async(user_id, "灵感分享", "neutral", inf.get("discovery"))
        messages.append(inf)

    # 快速模式下跳过这次额外 LLM 调用，降低首轮等待。
    if not _fast_mode_enabled():
        try:
            chat_text = _chat_with_llm(user_id, "给我一句贴合我刚才偏好的鼓励/灵感提示。")
            messages.append({"type": "chat", "text": chat_text})
        except Exception:
            pass

    return _json_response({"user_id": user_id, "messages": messages})


@app.post("/api/message")
def api_message():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id")
    message = (data.get("message") or "").strip()
    interest_signal = (data.get("interest_signal") or "").strip().lower()
    chat_mode = (data.get("chat_mode") or "").strip().lower()
    image_data_url = _normalize_image_payload_to_data_url(data.get("image"))

    if not user_id or not message:
        return _json_response({"error": "missing params"}, 400)

    ensure_user(user_id)
    _record_user_turn(user_id, message, chat_mode if chat_mode in ("chat", "creative") else None, interest_signal if interest_signal in ("interested", "not_interested") else None)

    lower = message.lower()

    messages: List[Dict[str, Any]] = []

    # 1) 反馈情绪（喜欢/不喜欢）
    sentiment = _infer_sentiment_from_text(message)
    if sentiment in ("positive", "negative"):
        log_interaction(user_id, topic="用户评价", sentiment=sentiment, content=message[:200])
        if _should_update_short_summary("用户评价", sentiment, message[:200]):
            _update_summary_async(user_id, "用户评价", sentiment, message[:200])

    if interest_signal in ("interested", "not_interested"):
        patch = {
            "last_interest_signal": interest_signal,
            "last_interest_at_message": message[:200],
        }
        update_user_preferences(user_id, patch)
        log_interaction(user_id, topic="兴趣信号", sentiment=interest_signal, content=message[:200])
        if _should_update_short_summary("兴趣信号", interest_signal, message[:200]):
            _update_summary_async(user_id, "兴趣信号", interest_signal, message[:200])

    if chat_mode in ("chat", "creative"):
        update_user_preferences(user_id, {"chat_mode": chat_mode})
        log_interaction(user_id, topic="形态切换", sentiment=chat_mode, content=message[:200])
        if _should_update_short_summary("形态切换", chat_mode, message[:200]):
            _update_summary_async(user_id, "形态切换", chat_mode, message[:200])

    # 2) 触发随机提问
    if "了解我" in message or "换个问题" in message:
        qmsg = _choose_preference_question(user_id)
        messages.append({"type": "chat", "text": "好！我来换一个问题，把你的偏好再对齐一下。"})
        messages.append(qmsg)
        _record_assistant_text(user_id, "好！我来换一个问题，把你的偏好再对齐一下。", chat_mode)
        return _json_response({"user_id": user_id, "messages": messages})

    # 2.5) 直接回答本地时间（避免模型瞎猜时区/DST 导致快一小时）
    if ("几点" in message) or ("现在时间" in message) or (lower.strip() in ("时间", "几点了", "现在几点")):
        now = datetime.now().astimezone()
        timestr = now.strftime("%H:%M")
        tz = now.strftime("%Z") or "本地"
        reply = f"现在是 {timestr}（{tz}）。"
        messages.append({"type": "chat", "text": reply})
        _record_assistant_text(user_id, reply, chat_mode)
        log_interaction(user_id, topic="时间查询", sentiment="neutral", content=message[:80])
        return _json_response({"user_id": user_id, "messages": messages})

    # 3) 触发灵感分享
    if any(k in lower for k in ["今天有什么灵感", "今天有灵感", "去冲浪", "冲浪", "灵感"]):
        inf = _generate_inspiration(user_id, user_message_context=message)
        update_user_preferences(user_id, {
            "last_inspiration_date": _today_str(),
            "last_inspiration_text": f"{inf.get('discovery','')[:120]}" if inf else None,
        })
        log_interaction(user_id, topic="灵感分享", sentiment="neutral", content=inf.get("discovery"))
        _update_summary_async(user_id, "灵感分享", "neutral", inf.get("discovery"))

        # 快速模式下不追加第二次 LLM 串行请求，优先快速返回。
        if not _fast_mode_enabled():
            try:
                chat_text = _chat_with_llm(user_id, "把刚才的灵感和我的感受联系起来，用更温暖、更幽默一点的方式说一句。")
                messages.append({"type": "chat", "text": chat_text})
                _record_assistant_text(user_id, chat_text, chat_mode)
            except Exception:
                pass
        messages.append(inf)
        _record_assistant_text(
            user_id,
            f"【今日冲浪见闻】发现：{inf.get('discovery','')} 联想：{inf.get('vivy_association','')} {inf.get('invitation_question','')}",
            chat_mode,
        )
        return _json_response({"user_id": user_id, "messages": messages})

    # 4) 默认：普通对话
    chat_text = _chat_with_llm(
        user_id,
        message,
        interest_signal=interest_signal,
        chat_mode=chat_mode or None,
        image_data_url=image_data_url,
    )
    messages.append({"type": "chat", "text": chat_text})
    _record_assistant_text(user_id, chat_text, chat_mode)
    log_interaction(user_id, topic="对话", sentiment="neutral", content=message[:200])
    if _should_update_short_summary("对话", "neutral", message[:200]):
        _update_summary_async(user_id, "对话", "neutral", message[:200])

    # 长摘要：每 12 条互动更新一次（后台），创作模式优先更频繁
    n = count_interactions(user_id)
    period = 8 if (chat_mode == "creative") else 12
    if n > 0 and n % period == 0:
        _update_summary_long_async(user_id)

    return _json_response({"user_id": user_id, "messages": messages})


@app.post("/api/message_stream")
def api_message_stream():
    data = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    message = (data.get("message") or "").strip()
    interest_signal = (data.get("interest_signal") or "").strip().lower()
    chat_mode = (data.get("chat_mode") or "").strip().lower()

    if not user_id or not message:
        return _json_response({"error": "missing params"}, 400)

    ensure_user(user_id)
    _record_user_turn(user_id, message, chat_mode if chat_mode in ("chat", "creative") else None, interest_signal if interest_signal in ("interested", "not_interested") else None)

    if interest_signal in ("interested", "not_interested"):
        update_user_preferences(
            user_id,
            {"last_interest_signal": interest_signal, "last_interest_at_message": message[:200]},
        )
        log_interaction(user_id, topic="兴趣信号", sentiment=interest_signal, content=message[:200])

    if chat_mode in ("chat", "creative"):
        update_user_preferences(user_id, {"chat_mode": chat_mode})
        log_interaction(user_id, topic="形态切换", sentiment=chat_mode, content=message[:200])

    def gen():
        full = ""
        try:
            for chunk in _chat_with_llm_stream(
                user_id=user_id,
                user_message=message,
                interest_signal=interest_signal if interest_signal in ("interested", "not_interested") else None,
                chat_mode=chat_mode if chat_mode in ("chat", "creative") else None,
            ):
                if not chunk:
                    continue
                full += chunk
                payload = json.dumps({"delta": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as e:
            payload = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        finally:
            # 记录完整回复（不阻塞流式输出结束）
            reply = (full or "").strip()
            if reply:
                _record_assistant_text(user_id, reply, chat_mode)
                log_interaction(user_id, topic="对话", sentiment="neutral", content=message[:200])
                if _should_update_short_summary("对话", "neutral", message[:200]):
                    _update_summary_async(user_id, "对话", "neutral", message[:200])
                n = count_interactions(user_id)
                period = 8 if (chat_mode == "creative") else 12
                if n > 0 and n % period == 0:
                    _update_summary_long_async(user_id)
            yield "data: {\"done\": true}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream")


@app.post("/api/creative_doc_stream")
def api_creative_doc_stream():
    data = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    doc_text = (data.get("doc_text") or "").strip()
    doc_path = (data.get("doc_path") or "").strip()
    goal = (data.get("goal") or "").strip()

    if not user_id or not doc_text:
        return _json_response({"error": "missing user_id/doc_text"}, 400)

    ensure_user(user_id)

    # Record a compact user turn (avoid storing entire doc).
    excerpt = doc_text[:600].replace("\n", " ")
    doc_hint = f"文档：{doc_path}" if doc_path else "文档：未命名"
    user_turn = f"{doc_hint}\n目标：{goal or '（未填写）'}\n摘录：{excerpt}"
    _record_user_turn(user_id, user_turn, chat_mode="creative", interest_signal=None)

    prompt = build_creative_prompt(LoadedDocument(path=doc_path or "document", ext="", text=doc_text), user_goal=goal)

    messages = [
        {"role": "system", "content": VIVY_SYSTEM_PROMPT + "\n\n你现在处于【艺术创作辅助模式】。"},
        {"role": "user", "content": prompt},
    ]

    def gen():
        full = ""
        try:
            for chunk in deepseek_chat_stream(messages=messages, temperature=0.95, max_tokens=900, timeout=90):
                if not chunk:
                    continue
                full += chunk
                payload = json.dumps({"delta": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as e:
            payload = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        finally:
            reply = (full or "").strip()
            if reply:
                _record_assistant_text(user_id, reply, chat_mode="creative")
                log_interaction(user_id, topic="文档创作辅助", sentiment="neutral", content=(goal or "")[:200])
            yield "data: {\"done\": true}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream")


@app.route("/api/office_passage_stream", methods=["OPTIONS"])
def api_office_passage_stream_options():
    """供 WPS/Word 加载项内嵌 WebView 做跨域预检（本机服务）。"""
    r = Response("", status=204)
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Max-Age"] = "86400"
    return r


@app.post("/api/office_passage_stream")
def api_office_passage_stream():
    """
    办公软件内「内置感」辅助：由 WPS/Word 加载项（或 COM 桥）把当前选区 POST 到此接口，
    流式返回建议/润色/续写结果；用户无需离开编辑器窗口。
    JSON: user_id, passage, action (polish|continue|critique|improve|free), goal?, context_excerpt?,
          reference_docs? (list of {label|name, text})
    """
    data = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    passage = (data.get("passage") or "").strip()
    action = (data.get("action") or "polish").strip().lower()
    goal = (data.get("goal") or "").strip()
    context_excerpt = (data.get("context_excerpt") or "").strip()
    reference_docs = normalize_office_reference_docs(data.get("reference_docs"))

    if not user_id:
        return _json_response({"error": "missing user_id"}, 400)
    if not passage:
        return _json_response({"error": "missing passage"}, 400)
    if len(passage) > OFFICE_PASSAGE_MAX:
        passage = passage[:OFFICE_PASSAGE_MAX]

    ensure_user(user_id)

    try:
        prompt = build_office_passage_prompt(
            passage,
            action=action,
            user_goal=goal or None,
            context_excerpt=context_excerpt or None,
            reference_docs=reference_docs or None,
        )
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)

    excerpt = passage[:400].replace("\n", " ")
    user_turn = f"[Office选区] action={action}\n摘录：{excerpt}"
    _record_user_turn(user_id, user_turn, chat_mode="creative", interest_signal=None)

    messages = [
        {
            "role": "system",
            "content": VIVY_SYSTEM_PROMPT + "\n\n你现在处于【办公软件内嵌辅助模式】：输出要便于用户直接粘贴回文档，少套话。",
        },
        {"role": "user", "content": prompt},
    ]

    def gen():
        full = ""
        try:
            for chunk in deepseek_chat_stream(messages=messages, temperature=0.85, max_tokens=1200, timeout=90):
                if not chunk:
                    continue
                full += chunk
                payload = json.dumps({"delta": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as e:
            payload = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        finally:
            reply = (full or "").strip()
            if reply:
                _record_assistant_text(user_id, reply, chat_mode="creative")
                log_interaction(
                    user_id, topic="Office选区辅助", sentiment="neutral", content=f"{action}:{excerpt[:120]}"
                )
            yield "data: {\"done\": true}\n\n"

    resp = Response(stream_with_context(gen()), mimetype="text/event-stream")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


if __name__ == "__main__":
    init_db()

    port = int(os.getenv("FLASK_PORT", "5000"))
    # 生产环境建议关掉 debug
    app.run(host="127.0.0.1", port=port, debug=True)
