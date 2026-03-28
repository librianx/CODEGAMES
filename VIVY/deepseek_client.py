import os
import requests
import json


def deepseek_chat(messages, model=None, temperature=0.7, max_tokens=800, base_url=None, api_key=None, timeout=60):
    """调用 DeepSeek 的 chat/completions 接口。

    返回值：assistant 内容字符串。
    """
    api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")

    base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {
        "Content-Type": "application/json",
    }

    # 尝试 1：Bearer 方式
    headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)

    if resp.status_code != 200:
        # 尝试 2：直接 token 方式（部分网关会用这种）
        if resp.status_code in (401, 403):
            headers["Authorization"] = api_key
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)

    resp.raise_for_status()

    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"DeepSeek 返回非 JSON：{e}") from e

    # OpenAI 兼容结构：choices[0].message.content
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        # 避免 KeyError 变成“看不懂的 500”，给更明确的报错
        raise RuntimeError(f"DeepSeek 返回结构不符合预期：{data}") from e


def deepseek_chat_stream(
    messages,
    model=None,
    temperature=0.7,
    max_tokens=800,
    base_url=None,
    api_key=None,
    timeout=60,
):
    """DeepSeek 流式输出：逐段 yield assistant 内容字符串。"""
    api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")

    base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout, stream=True)
    if resp.status_code != 200 and resp.status_code in (401, 403):
        headers["Authorization"] = api_key
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout, stream=True)

    resp.raise_for_status()

    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if not line:
            continue

        # OpenAI 风格 SSE：data: {...} / data: [DONE]
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        if line == "[DONE]":
            break

        try:
            obj = json.loads(line)
        except Exception:
            continue

        try:
            delta = obj["choices"][0].get("delta") or {}
            content = delta.get("content") or ""
        except Exception:
            content = ""

        if content:
            yield content
