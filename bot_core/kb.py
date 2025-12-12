import os
import re
import json
import math
from typing import Dict, Any, List

from .config import KB_DIR, KB_INDEX_PATH, FREE_MODE, OPENAI_CLIENT
from .logging_setup import logger

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

_KB_INDEX: Dict[str, Any] = {}


def _chunk_text(txt: str, chunk_size: int = 900, overlap: int = 120) -> List[str]:
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    chunks = []
    i = 0
    while i < len(txt):
        chunk = txt[i:i + chunk_size]
        if i + chunk_size < len(txt):
            j = chunk.rfind(". ")
            if j > 300:
                chunk = chunk[: j + 1]
        chunks.append(chunk.strip())
        i += max(len(chunk) - overlap, 1)
    return [c for c in chunks if len(c) >= 120]


def _pdf_to_text(path: str) -> str:
    if PdfReader is None:
        logger.warning("пакет pypdf не встановлено — пропускаю PDF: %s", path)
        return ""
    try:
        reader = PdfReader(path)
        return "\n".join([(p.extract_text() or "") for p in reader.pages])
    except Exception as e:
        logger.error("[KB] PDF read fail %s: %s", path, e)
        return ""


def _txt_to_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error("[KB] TXT read fail %s: %s", path, e)
        return ""


def _embed_texts(texts: List[str]) -> List[List[float]]:
    if FREE_MODE or OPENAI_CLIENT is None:
        return [[0.0] for _ in texts]
    resp = OPENAI_CLIENT.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [d.embedding for d in resp.data]


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-8
    nb = math.sqrt(sum(y * y for y in b)) or 1e-8
    return dot / (na * nb)


def _tokenize_query(q: str) -> List[str]:
    q = q.lower()
    raw_tokens = re.findall(r"[a-zа-щьюяєіїґ0-9]+", q)
    tokens: List[str] = []
    for t in raw_tokens:
        if t.isdigit():
            tokens.append(t)
        elif len(t) >= 4:
            tokens.append(t)
        elif len(t) >= 3 and re.match(r"[a-z0-9]+$", t):
            tokens.append(t)
    return tokens


def kb_build_or_load() -> Dict[str, Any]:
    os.makedirs(KB_DIR, exist_ok=True)

    if FREE_MODE:
        logger.info("[KB] FREE_MODE: індексація без OpenAI (тільки текстовий пошук).")

    if os.path.exists(KB_INDEX_PATH):
        try:
            with open(KB_INDEX_PATH, "r", encoding="utf-8") as f:
                idx = json.load(f)
            files_now = []
            for fn in os.listdir(KB_DIR):
                if fn.lower().endswith((".txt", ".pdf")):
                    path = os.path.join(KB_DIR, fn)
                    files_now.append({"path": path, "mtime": os.path.getmtime(path)})
            old = {(d["path"], round(d.get("mtime", 0), 6)) for d in idx.get("files", [])}
            cur = {(d["path"], round(d.get("mtime", 0), 6)) for d in files_now}
            if old == cur and idx.get("chunks"):
                logger.info("[KB] Завантажено індекс: %s", KB_INDEX_PATH)
                return idx
        except Exception as e:
            logger.warning("[KB] Неможливо прочитати індекс (%s). Перебудовую…", e)

    pdf_paths = [os.path.join(KB_DIR, fn) for fn in os.listdir(KB_DIR) if fn.lower().endswith(".pdf")]
    txt_paths = [os.path.join(KB_DIR, fn) for fn in os.listdir(KB_DIR) if fn.lower().endswith(".txt")]

    all_chunks = []
    for path in txt_paths:
        txt = _txt_to_text(path)
        for i, ch in enumerate(_chunk_text(txt)):
            all_chunks.append({"text": ch, "source": os.path.basename(path), "i": i, "type": "txt"})

    for path in pdf_paths:
        txt = _pdf_to_text(path)
        if not txt:
            continue
        for i, ch in enumerate(_chunk_text(txt)):
            all_chunks.append({"text": ch, "source": os.path.basename(path), "i": i, "type": "pdf"})

    if not all_chunks:
        logger.warning("[KB] Порожній контент. Поклади .txt або .pdf у %s", KB_DIR)
        return {"model": "text-embedding-3-small", "files": [], "chunks": []}

    embeds = _embed_texts([c["text"] for c in all_chunks])
    for c, emb in zip(all_chunks, embeds):
        c["embedding"] = emb

    files_meta = []
    for fn in os.listdir(KB_DIR):
        if fn.lower().endswith((".txt", ".pdf")):
            p = os.path.join(KB_DIR, fn)
            files_meta.append({"path": p, "mtime": os.path.getmtime(p)})

    idx = {"model": "text-embedding-3-small", "files": files_meta, "chunks": all_chunks}
    try:
        with open(KB_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        logger.info("[KB] Побудовано індекс із %d фрагментів.", len(all_chunks))
    except Exception as e:
        logger.warning("[KB] Не вдалося зберегти індекс: %s", e)
    return idx


def load_kb_index() -> Dict[str, Any]:
    global _KB_INDEX
    _KB_INDEX = kb_build_or_load()
    return _KB_INDEX


def get_kb_chunk_count() -> int:
    return len(_KB_INDEX.get("chunks", []))


def kb_retrieve_smart(query: str, k: int = 6) -> List[Dict[str, Any]]:
    if not _KB_INDEX or not _KB_INDEX.get("chunks"):
        return []

    tokens = _tokenize_query(query)
    chunks = _KB_INDEX["chunks"]

    literal_scored: List[tuple[int, Dict[str, Any]]] = []

    if tokens:
        for ch in chunks:
            t = ch["text"].lower()
            hit_count = sum(1 for tok in tokens if tok in t)
            if hit_count > 0:
                literal_scored.append((hit_count, ch))

    if literal_scored:
        literal_scored.sort(key=lambda x: x[0], reverse=True)
        top_literal = [c for _, c in literal_scored[:k]]

        if not FREE_MODE and OPENAI_CLIENT is not None:
            try:
                q_emb = _embed_texts([query])[0]
            except Exception:
                return top_literal

            scored_sem: List[tuple[float, Dict[str, Any]]] = []
            for ch in chunks:
                sim = _cosine(q_emb, ch["embedding"])
                scored_sem.append((sim, ch))
            scored_sem.sort(key=lambda x: x[0], reverse=True)

            extra: List[Dict[str, Any]] = []
            for _, ch in scored_sem:
                if ch not in top_literal:
                    extra.append(ch)
                if len(extra) >= 2:
                    break

            return top_literal + extra

        return top_literal

    if FREE_MODE or OPENAI_CLIENT is None:
        return []

    q_emb = _embed_texts([query])[0]
    scored_sem: List[tuple[float, Dict[str, Any]]] = []
    for ch in chunks:
        sim = _cosine(q_emb, ch["embedding"])
        scored_sem.append((sim, ch))
    scored_sem.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored_sem[:k]]


def pack_snippets(snips: List[Dict[str, Any]], max_chars: int = 5000) -> str:
    out = []
    total = 0
    for s in snips:
        tag = f"[{s['source']} • {s['i']}]"
        block = f"{tag}\n{s['text'].strip()}"
        if total + len(block) > max_chars and out:
            break
        out.append(block)
        total += len(block)
    return "\n\n---\n\n".join(out)
