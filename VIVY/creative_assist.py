from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class LoadedDocument:
    path: str
    ext: str
    text: str


def load_document_text(path: str, max_chars: int = 60_000) -> LoadedDocument:
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")
    if ext in ("txt", "md"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        return LoadedDocument(path=str(p), ext=ext, text=text[:max_chars])

    if ext == "docx":
        try:
            from docx import Document  # type: ignore
        except Exception as e:
            raise RuntimeError("缺少依赖 python-docx，无法读取 .docx") from e
        doc = Document(str(p))
        parts = []
        for para in doc.paragraphs:
            t = (para.text or "").strip()
            if t:
                parts.append(t)
        text = "\n".join(parts)
        return LoadedDocument(path=str(p), ext=ext, text=text[:max_chars])

    if ext == "pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as e:
            raise RuntimeError("缺少依赖 pypdf，无法读取 .pdf") from e
        reader = PdfReader(str(p))
        parts = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            t = t.strip()
            if t:
                parts.append(t)
        text = "\n\n".join(parts)
        return LoadedDocument(path=str(p), ext=ext, text=text[:max_chars])

    raise RuntimeError(f"不支持的文件类型：.{ext}（支持 txt/md/docx/pdf）")


def build_creative_prompt(doc: LoadedDocument, user_goal: Optional[str] = None) -> str:
    goal = (user_goal or "").strip()
    header = "你将作为创作助手阅读用户文档，并给出可直接采纳的建议。"
    if goal:
        header += f"\n用户目标：{goal}"
    return (
        header
        + "\n\n【文档内容（已截断）】\n"
        + doc.text.strip()
        + "\n\n请输出：\n"
        + "1) 作品一句话提炼\n"
        + "2) 3 条最具体可执行的改进建议\n"
        + "3) 给 2 段可直接复制粘贴的“替换/续写”文本（各 2-4 句）\n"
    )

