"""面经 chunk 索引 + 检索。延迟加载 embedding/Chroma，失败则关键词兜底。

禁止被 app.graph / plan 主链路 import；仅 ask 路由与面试写入钩子引用。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = ROOT / "data" / "chroma"
QA_HISTORY_PATH = ROOT / "data" / "qa_history.json"
NOTE_DOCS_PATH = ROOT / "data" / "note_docs.json"
UPLOAD_DIR = ROOT / "data" / "uploads"
COLLECTION = "interview_notes"
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
DOC_CHUNK = 700

_rebuild_lock = threading.Lock()
_embed_lock = threading.Lock()
_embedder: Any = None
_embed_failed = False
_chroma_client: Any = None

TIME_RE = re.compile(
    r"最近|近一周|近\s*\d+\s*天|近7天|近14天|近三十天|总结最近|最近面试|这两周",
    re.I,
)


def classify_ask_mode(query: str) -> str:
    q = (query or "").strip()
    if TIME_RE.search(q):
        return "time"
    return "semantic"


def interview_body(iv: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (
        ("notes", "准备"),
        ("post_notes", "面试后"),
        ("insights", "面经"),
    ):
        text = str(iv.get(key) or "").strip()
        if text:
            parts.append(f"{label}：{text}")
    return "\n".join(parts)


def chunk_id(iv: dict[str, Any]) -> str:
    return f"{iv.get('date','')}|{iv.get('time','')}|{iv.get('company','')}"


def iter_chunks() -> list[dict[str, Any]]:
    from app.tools import list_all_interviews

    out: list[dict[str, Any]] = []
    for iv in list_all_interviews():
        body = interview_body(iv)
        if not body:
            continue
        meta = {
            "company": str(iv.get("company") or ""),
            "role": str(iv.get("role") or ""),
            "stage": str(iv.get("stage") or ""),
            "date": str(iv.get("date") or ""),
            "time": str(iv.get("time") or ""),
        }
        header = (
            f"{meta['date']} {meta['time']} {meta['company']} "
            f"{meta['role']} {meta['stage']}"
        ).strip()
        out.append(
            {
                "id": chunk_id(iv),
                "text": f"{header}\n{body}",
                "metadata": meta,
            }
        )
    for doc in list_note_docs():
        text = str(doc.get("text") or "").strip()
        if not text:
            continue
        meta = {
            "company": str(doc.get("company") or "面经文档"),
            "role": str(doc.get("role") or ""),
            "stage": "文档",
            "date": str(doc.get("date") or ""),
            "time": str(doc.get("time") or ""),
            "filename": str(doc.get("filename") or ""),
            "kind": "doc",
        }
        header = f"文档 {doc.get('filename') or ''} {meta['company']}".strip()
        for i, piece in enumerate(_split_text(text, DOC_CHUNK)):
            out.append(
                {
                    "id": f"doc|{doc.get('id')}|{i}",
                    "text": f"{header}\n{piece}",
                    "metadata": meta,
                }
            )
    return out


def _split_text(text: str, size: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    i = 0
    while i < len(text):
        parts.append(text[i : i + size])
        i += size
    return parts


def list_note_docs() -> list[dict[str, Any]]:
    if not NOTE_DOCS_PATH.exists():
        return []
    try:
        data = json.loads(NOTE_DOCS_PATH.read_text(encoding="utf-8"))
        return list(data.get("docs") or [])
    except json.JSONDecodeError:
        return []


def save_note_doc(doc: dict[str, Any]) -> dict[str, Any]:
    NOTE_DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
    docs = list_note_docs()
    docs.append(doc)
    NOTE_DOCS_PATH.write_text(
        json.dumps({"docs": docs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return doc


def list_recent_chunks(n: int = 7) -> dict[str, Any]:
    chunks = iter_chunks()
    chunks.sort(
        key=lambda c: (
            str(c["metadata"].get("date") or ""),
            str(c["metadata"].get("time") or ""),
        ),
        reverse=True,
    )
    picked = chunks[: max(1, int(n or 7))]
    return {"mode": "time", "via": "date", "chunks": picked}


def _keywords(query: str) -> list[str]:
    q = (query or "").strip()
    terms: list[str] = []
    for part in re.split(r"[\s,，。？?、]+", q):
        part = part.strip()
        if len(part) >= 2:
            terms.append(part)
    if q and q not in terms and len(q) >= 2:
        terms.append(q)
    # 中文双字滑动，提高「状态合并」类命中
    han = re.sub(r"[^\u4e00-\u9fff]", "", q)
    for i in range(len(han) - 1):
        terms.append(han[i : i + 2])
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def keyword_retrieve(query: str, k: int = 5) -> dict[str, Any]:
    terms = _keywords(query)
    scored: list[tuple[int, dict]] = []
    for ch in iter_chunks():
        blob = (
            ch["text"]
            + " "
            + str(ch["metadata"].get("company") or "")
            + " "
            + str(ch["metadata"].get("role") or "")
            + " "
            + str(ch["metadata"].get("filename") or "")
        )
        score = sum(1 for t in terms if t and t in blob)
        if score:
            scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [c for _, c in scored[: max(1, int(k or 5))]]
    if not picked:
        # 无命中则退回最近若干条，避免空答
        picked = list_recent_chunks(k).get("chunks") or []
    return {"mode": "semantic", "via": "keyword", "chunks": picked}


def _get_embedder(*, allow_download: bool = False) -> Any:
    global _embedder, _embed_failed
    if os.getenv("RAG_SKIP_EMBED") == "1":
        return None
    with _embed_lock:
        if _embedder is not None:
            return _embedder
        if _embed_failed and not allow_download:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            kwargs: dict[str, Any] = {}
            if not allow_download:
                kwargs["local_files_only"] = True
            _embedder = SentenceTransformer(EMBED_MODEL, **kwargs)
            _embed_failed = False
            return _embedder
        except Exception:  # noqa: BLE001
            if not allow_download:
                return None
            _embed_failed = True
            _embedder = None
            return None


def _chroma_collection(create: bool = True) -> Any:
    global _chroma_client
    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if create:
        return _chroma_client.get_or_create_collection(COLLECTION)
    try:
        return _chroma_client.get_collection(COLLECTION)
    except Exception:  # noqa: BLE001
        return _chroma_client.get_or_create_collection(COLLECTION)


def rebuild_index() -> dict[str, Any]:
    """全量重建 Chroma；失败吞掉，不影响写入 API。"""
    chunks = iter_chunks()
    embedder = _get_embedder(allow_download=False)
    if embedder is None:
        return {"ok": False, "reason": "embedder_unavailable", "n": len(chunks)}
    try:
        with _rebuild_lock:
            coll = _chroma_collection(create=True)
            existing = coll.get(include=[])
            ids = list(existing.get("ids") or [])
            if ids:
                coll.delete(ids=ids)
            if not chunks:
                return {"ok": True, "n": 0}
            texts = [c["text"] for c in chunks]
            vectors = embedder.encode(texts, normalize_embeddings=True).tolist()
            coll.add(
                ids=[c["id"] for c in chunks],
                embeddings=vectors,
                documents=texts,
                metadatas=[c["metadata"] for c in chunks],
            )
        return {"ok": True, "n": len(chunks), "via": "chroma"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc), "n": len(chunks)}


def rebuild_index_async() -> None:
    def _run() -> None:
        try:
            rebuild_index()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, daemon=True, name="rag-rebuild").start()


def retrieve_semantic(query: str, k: int = 5) -> dict[str, Any]:
    k = max(1, int(k or 5))
    embedder = _get_embedder(allow_download=True)
    if embedder is None:
        return keyword_retrieve(query, k)
    try:
        coll = _chroma_collection(create=True)
        if coll.count() == 0:
            rebuild_index()
            coll = _chroma_collection(create=True)
        if coll.count() == 0:
            return keyword_retrieve(query, k)
        qv = embedder.encode([query], normalize_embeddings=True).tolist()
        res = coll.query(query_embeddings=qv, n_results=min(k, coll.count()))
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        chunks = []
        for i, doc in enumerate(docs):
            chunks.append(
                {
                    "id": ids[i] if i < len(ids) else "",
                    "text": doc,
                    "metadata": metas[i] if i < len(metas) else {},
                }
            )
        if not chunks:
            return keyword_retrieve(query, k)
        return {"mode": "semantic", "via": "chroma", "chunks": chunks}
    except Exception:  # noqa: BLE001
        return keyword_retrieve(query, k)


def retrieve_for_query(query: str, k: int = 5, n_recent: int = 7) -> dict[str, Any]:
    mode = classify_ask_mode(query)
    if mode == "time":
        data = list_recent_chunks(n_recent)
        data["query"] = query
        return data
    data = retrieve_semantic(query, k)
    data["query"] = query
    return data


def append_qa_history(item: dict[str, Any]) -> None:
    QA_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"items": []}
    if QA_HISTORY_PATH.exists():
        try:
            data = json.loads(QA_HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(data.get("items"), list):
                data = {"items": []}
        except json.JSONDecodeError:
            data = {"items": []}
    items = list(data.get("items") or [])
    row = dict(item)
    row.setdefault("at", datetime.now().isoformat(timespec="seconds"))
    items.append(row)
    QA_HISTORY_PATH.write_text(
        json.dumps({"items": items[-50:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_qa_history(limit: int = 20) -> list[dict[str, Any]]:
    if not QA_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(QA_HISTORY_PATH.read_text(encoding="utf-8"))
        items = list(data.get("items") or [])
        return items[-limit:]
    except Exception:  # noqa: BLE001
        return []
