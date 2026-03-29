"""Cursor Cloud Agents API（控制台 → Cloud Agents → API Keys）。

文档：https://cursor.com/docs/cloud-agent/api/endpoints
认证：HTTP Basic，用户名为 API Key，密码留空。

注意：这是「在 GitHub 仓库上跑云 Agent」，不是 OpenAI 兼容的 /chat/completions。
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth

CURSOR_API_ORIGIN = "https://api.cursor.com"

# 轮询时认为仍在运行中的状态（小写比较）
_ACTIVE_STATUSES = frozenset(
    {
        "creating",
        "running",
        "queued",
        "pending",
        "waiting",
    }
)


def cursor_basic_auth(api_key: str) -> HTTPBasicAuth:
    return HTTPBasicAuth(api_key.strip(), "")


def launch_agent(
    api_key: str,
    *,
    prompt_text: str,
    repository: str,
    ref: Optional[str] = None,
    model: str = "default",
    timeout: int = 120,
) -> dict[str, Any]:
    """POST /v0/agents"""
    url = f"{CURSOR_API_ORIGIN}/v0/agents"
    body: dict[str, Any] = {
        "prompt": {"text": (prompt_text or "").strip()},
        "source": {"repository": (repository or "").strip()},
        "model": model or "default",
    }
    r = (ref or "").strip()
    if r:
        body["source"]["ref"] = r

    resp = requests.post(
        url,
        json=body,
        auth=cursor_basic_auth(api_key),
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    if not resp.ok:
        raise RuntimeError(_format_http_error("启动 Agent 失败", resp))
    return resp.json()


def get_agent(api_key: str, agent_id: str, timeout: int = 60) -> dict[str, Any]:
    url = f"{CURSOR_API_ORIGIN}/v0/agents/{agent_id}"
    resp = requests.get(url, auth=cursor_basic_auth(api_key), timeout=timeout)
    if not resp.ok:
        raise RuntimeError(_format_http_error("查询 Agent 状态失败", resp))
    return resp.json()


def get_conversation(api_key: str, agent_id: str, timeout: int = 120) -> dict[str, Any]:
    url = f"{CURSOR_API_ORIGIN}/v0/agents/{agent_id}/conversation"
    resp = requests.get(url, auth=cursor_basic_auth(api_key), timeout=timeout)
    if not resp.ok:
        raise RuntimeError(_format_http_error("拉取对话记录失败", resp))
    return resp.json()


def wait_agent_terminal(
    api_key: str,
    agent_id: str,
    *,
    poll_seconds: float = 4.0,
    max_wait_seconds: float = 900.0,
) -> dict[str, Any]:
    """轮询直到 Agent 不再处于运行中状态（或超时）。"""
    deadline = time.time() + max_wait_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = get_agent(api_key, agent_id)
        st = str(last.get("status") or "").strip().lower()
        if st and st not in _ACTIVE_STATUSES:
            return last
        time.sleep(poll_seconds)
    raise TimeoutError(f"等待 Agent 结束超时（>{int(max_wait_seconds)} 秒）。最后状态：{last.get('status')!r}")


def format_conversation_text(conv: dict[str, Any], max_chars: int = 12_000) -> str:
    """将 /conversation 接口结果格式化为气泡可读文本。"""
    msgs = conv.get("messages") or []
    if not isinstance(msgs, list):
        return "（对话记录格式异常）"
    parts: list[str] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        mtype = str(m.get("type") or "")
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        if mtype == "user_message":
            parts.append(f"【任务 / 补充】\n{text}")
        elif mtype == "assistant_message":
            parts.append(f"【云 Agent】\n{text}")
        else:
            parts.append(text)
    out = "\n\n———\n\n".join(parts) if parts else "（暂无对话正文）"
    if len(out) > max_chars:
        out = out[: max_chars - 20] + "\n…（已截断）"
    return out


def _format_http_error(prefix: str, resp: requests.Response) -> str:
    body = (resp.text or "")[:500].strip()
    if body:
        return f"{prefix} HTTP {resp.status_code}: {body}"
    return f"{prefix} HTTP {resp.status_code}"
