"""Windows UIA 文档读取（A 路线：可访问性优先）。

最小能力：
- 获取前台窗口 hwnd / exe / title
- 尝试用 UIA 抽取可见文本（失败时返回空字符串 + 原因）
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class ForegroundApp:
    hwnd: int
    exe: str
    title: str


_WHITELIST_EXES = frozenset(
    {
        "winword.exe",
        "wps.exe",
        "et.exe",
        "wpp.exe",
        "chrome.exe",
        "msedge.exe",
        "acrord32.exe",
        "acrobat.exe",
        "wechat.exe",
        "wechatapp.exe",
    }
)


def is_supported_app(app: ForegroundApp) -> bool:
    return (app.exe or "").lower() in _WHITELIST_EXES


def _dedup_keep_order(items: Iterable[str], max_items: int = 500) -> list[str]:
    seen = set()
    out: list[str] = []
    for s in items:
        ss = (s or "").strip()
        if not ss:
            continue
        key = re.sub(r"\s+", " ", ss)
        if key in seen:
            continue
        seen.add(key)
        out.append(ss)
        if len(out) >= max_items:
            break
    return out


def clean_text(text: str, max_chars: int = 6000) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > max_chars:
        t = t[: max_chars - 1] + "…"
    return t


def truncate_head_mid_tail(text: str, total_chars: int = 3500) -> str:
    t = (text or "").strip()
    if len(t) <= total_chars:
        return t
    # head/mid/tail sampling to preserve context
    head = t[: int(total_chars * 0.55)]
    tail = t[-int(total_chars * 0.25) :]
    mid_start = max(0, (len(t) // 2) - int(total_chars * 0.10))
    mid = t[mid_start : mid_start + int(total_chars * 0.20)]
    return head.rstrip() + "\n…\n" + mid.strip() + "\n…\n" + tail.lstrip()


def _safe_import_win32():
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
        import psutil  # type: ignore

        return win32gui, win32process, psutil, ""
    except Exception as e:  # pragma: no cover
        return None, None, None, f"win32 deps missing: {e}"


def get_foreground_app_info() -> Tuple[Optional[ForegroundApp], str]:
    win32gui, win32process, psutil, err = _safe_import_win32()
    if err:
        return None, err
    try:
        hwnd = int(win32gui.GetForegroundWindow() or 0)
        if not hwnd:
            return None, "no foreground hwnd"
        title = str(win32gui.GetWindowText(hwnd) or "").strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = ""
        try:
            p = psutil.Process(int(pid))
            exe = (p.name() or "").lower()
        except Exception:
            exe = ""
        return ForegroundApp(hwnd=hwnd, exe=exe, title=title), ""
    except Exception as e:
        return None, f"get_foreground failed: {e}"


def _safe_import_uia():
    try:
        from pywinauto import Desktop  # type: ignore

        return Desktop, ""
    except Exception as e:  # pragma: no cover
        return None, f"uia deps missing: {e}"


def read_document_text(app: ForegroundApp, max_chars: int = 4000) -> Tuple[str, str]:
    """尝试从前台窗口读取可见文本（UIA）。返回 (text, error_message)。"""
    Desktop, err = _safe_import_uia()
    if err:
        return "", err
    try:
        desk = Desktop(backend="uia")
        win = desk.window(handle=app.hwnd)
        exe = (app.exe or "").lower()

        # app-specific preferred control types (best-effort)
        preferred = []
        if exe in ("chrome.exe", "msedge.exe"):
            preferred = ["Document", "Text", "Edit"]
        elif exe in ("winword.exe", "wps.exe"):
            preferred = ["Document", "Text", "Edit"]
        elif exe in ("acrord32.exe", "acrobat.exe"):
            preferred = ["Document", "Text"]
        elif exe in ("wechat.exe", "wechatapp.exe"):
            preferred = ["Text"]
        else:
            preferred = ["Text", "Document", "Edit"]

        chunks: list[str] = []
        for ct in preferred:
            try:
                for el in win.descendants(control_type=ct):
                    try:
                        if hasattr(el, "is_visible") and not el.is_visible():
                            continue
                        t = (el.window_text() or "").strip()
                        if t:
                            chunks.append(t)
                    except Exception:
                        continue
            except Exception:
                continue
            # if we already collected a lot, stop early
            if sum(len(x) for x in chunks) >= max_chars * 2:
                break

        if not chunks:
            try:
                t = (win.window_text() or "").strip()
                if t:
                    chunks.append(t)
            except Exception:
                pass

        chunks = _dedup_keep_order(chunks, max_items=900)
        out = clean_text("\n".join(chunks), max_chars=max_chars * 3)
        out = truncate_head_mid_tail(out, total_chars=max_chars)
        return out, "" if out else "empty uia text"
    except Exception as e:
        return "", f"uia read failed: {e}"

