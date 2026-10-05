"""从 PDF / Word / txt 抽纯文本，供面经入库。不走 plan 主链路。"""
from __future__ import annotations

from pathlib import Path


def extract_text_from_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        return _docx(path)
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".doc":
        raise ValueError("暂不支持 .doc，请另存为 .docx 或 PDF")
    raise ValueError(f"不支持的类型：{suffix}（请用 pdf / docx / txt）")


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        pages.append((page.extract_text() or "").strip())
    text = "\n".join(p for p in pages if p)
    if not text.strip():
        raise ValueError("PDF 没有可提取文字（可能是扫描件，暂不支持 OCR）")
    return text


def _docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    text = "\n".join(parts)
    if not text.strip():
        raise ValueError("Word 文档没有可提取段落")
    return text
