"""Download and extract text from uploaded service-report files."""

from __future__ import annotations

import io
import zipfile
from typing import List, Tuple
from xml.etree import ElementTree

import httpx

MAX_BYTES = 20 * 1024 * 1024
_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def download_file(url: str, timeout_s: float = 30.0) -> bytes:
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.content
    if len(data) > MAX_BYTES:
        raise ValueError("file exceeds 20 MB")
    return data


def extract_text(data: bytes, mime: str, name: str) -> str:
    mime_l = (mime or "").lower()
    name_l = (name or "").lower()
    if "pdf" in mime_l or name_l.endswith(".pdf"):
        return _pdf_text(data)
    if "wordprocessingml" in mime_l or name_l.endswith(".docx"):
        return _docx_text(data)
    if name_l.endswith(".doc") or mime_l == "application/msword":
        return _latin_strings(data)
    if "spreadsheet" in mime_l or name_l.endswith(".xlsx"):
        return _xlsx_text(data)
    if mime_l.startswith("text/") or name_l.endswith(".txt") or name_l.endswith(".csv"):
        return data.decode("utf-8", errors="replace")
    return ""


def is_image(mime: str, name: str) -> bool:
    mime_l = (mime or "").lower()
    name_l = (name or "").lower()
    return mime_l.startswith("image/") or name_l.endswith((".jpg", ".jpeg", ".png", ".webp"))


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(io.BytesIO(data))
    parts: List[str] = []
    for page in reader.pages[:30]:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except Exception:
        return ""
    root = ElementTree.fromstring(xml)
    texts = [n.text or "" for n in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
    return "\n".join(t for t in texts if t).strip()


def _xlsx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            shared: List[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"):
                    shared.append(si.text or "")
            sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
            chunks: List[str] = []
            for sheet in sheets[:5]:
                root = ElementTree.fromstring(zf.read(sheet))
                for c in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v"):
                    chunks.append(c.text or "")
            return "\n".join(shared + chunks).strip()
    except Exception:
        return ""


def _latin_strings(data: bytes) -> str:
    out: List[str] = []
    cur: List[str] = []
    for b in data:
        if 32 <= b < 127:
            cur.append(chr(b))
        else:
            if len(cur) >= 4:
                out.append("".join(cur))
            cur = []
    if len(cur) >= 4:
        out.append("".join(cur))
    return "\n".join(out[:400])


def files_to_prompt_blocks(files: List[dict]) -> Tuple[str, List[dict]]:
    """Return concatenated text and image parts ({mime, bytes}) for vision."""
    texts: List[str] = []
    images: List[dict] = []
    for f in files[:5]:
        url = f.get("url")
        if not url:
            continue
        mime = str(f.get("type") or "")
        name = str(f.get("name") or "file")
        try:
            raw = download_file(url)
        except Exception:
            continue
        if is_image(mime, name):
            images.append({"mime": mime or "image/jpeg", "data": raw, "name": name})
            continue
        text = extract_text(raw, mime, name)
        if text:
            texts.append(f"--- {name} ---\n{text[:20000]}")
    return "\n\n".join(texts), images
