#!/usr/bin/env python3
"""Document → plain text for ingestion - so ANY document (a paper, a spec, meeting
notes, a Markdown design doc), not only a chat transcript, can be mined into memory.

stdlib-first, in keeping with the project's zero-dependency core:
  * .md / .txt / .log / .rst / .csv / ...  → read directly
  * .docx                                  → zipfile + xml (OOXML is a zip of XML)
  * .html / .htm                           → html.parser (tags/script/style stripped)
  * .pdf                                   → optional `pypdf`; absence is a clear
                                             message, never a crash

Used by `ingest.py` (both `--file` and the `--dir` sweep). Unknown extensions fall
back to a best-effort text read (`raw_fallback=True`) so existing text-glob sweeps
are unchanged; the structured parsers only fire for the formats above.
"""
import os
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path

# read straight as UTF-8 text. `.csv`/`.tsv` are here on purpose: a small delimited file
# reads fine as text and the extractor distills it like any note. `.xlsx` is deliberately
# NOT supported - it needs a third-party dep (openpyxl) and dense tabular data does not
# distill into mistakes/patterns/decisions, so it would add weight for little memory value
# (parsing the input never changes the store, which stays Markdown either way).
TEXT_EXTS = {".md", ".markdown", ".mdx", ".txt", ".text", ".log", ".rst",
             ".jsonl", ".json", ".csv", ".tsv", ".org", ".adoc"}
# parsed by a structured extractor below
DOC_EXTS = {".docx", ".pdf", ".html", ".htm"}
SUPPORTED = TEXT_EXTS | DOC_EXTS

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# zip-bomb cap. This module stays import-standalone (no memory_hook), so the safe-int
# fallback is inlined rather than imported: a mistyped env var degrades, never crashes.
try:
    MAX_DOC_BYTES = int(os.environ.get("NEVERTWICE_MAX_DOC_BYTES", "") or 50 * 1024 * 1024)
except ValueError:
    MAX_DOC_BYTES = 50 * 1024 * 1024


class DocError(Exception):
    """Extraction failed for a reason worth surfacing (missing optional dep, corrupt
    file, unsupported type) - the caller decides whether to skip or abort."""


def _docx_text(path: Path) -> str:
    """Plain text from a .docx: concatenate the <w:t> runs of word/document.xml, one
    line per <w:p> paragraph. Pure stdlib (a .docx is a zip of XML). ElementTree's expat
    backend does not resolve external entities (no XXE), and requires-python >= 3.10 rules out
    the old billion-laughs expansion - the remaining intake risk is a zip bomb, capped below."""
    try:
        with zipfile.ZipFile(path) as z:
            # refuse a decompression bomb. The declared uncompressed size (getinfo().file_size)
            # is a cheap first reject, but it lives in the zip's central directory and is
            # attacker-controlled - a crafted .docx can understate it and still inflate to
            # gigabytes. So we ALSO bound the actual read: z.open() streams the decompression,
            # and read(cap+1) never inflates more than the cap into memory, catching a forged
            # size (code-review 2026-07 - a real risk on the ingest/MCP document intake path).
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOC_BYTES:
                raise DocError(f".docx body too large ({info.file_size} bytes > {MAX_DOC_BYTES})")
            with z.open("word/document.xml") as fh:
                xml = fh.read(MAX_DOC_BYTES + 1)
            if len(xml) > MAX_DOC_BYTES:
                raise DocError(".docx body exceeded the cap while decompressing "
                               "(declared size was understated)")
    except DocError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise DocError(f"not a readable .docx ({type(e).__name__})")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise DocError(f"corrupt .docx XML ({e})")
    paras = []
    for para in root.iter(f"{_W}p"):
        runs = [node.text for node in para.iter(f"{_W}t") if node.text]
        paras.append("".join(runs))
    return "\n".join(paras).strip()


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _html_text(path: Path) -> str:
    """Visible text from an HTML file - stdlib parser, script/style dropped."""
    p = _HTMLStripper()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(p.parts).strip()


def _pdf_text(path: Path) -> str:
    """Plain text from a .pdf via the optional `pypdf` (or legacy PyPDF2). A clear
    DocError if neither is installed or the file will not parse."""
    reader = None
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
    except ImportError:
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(path))
        except ImportError:
            raise DocError("PDF support needs `pip install pypdf`")
        except Exception as e:
            raise DocError(f"unreadable .pdf ({type(e).__name__})")
    except Exception as e:
        raise DocError(f"unreadable .pdf ({type(e).__name__})")
    try:
        return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
    except Exception as e:
        raise DocError(f"PDF text extraction failed ({type(e).__name__})")


def extract_text(path, raw_fallback: bool = True) -> str:
    """Plain text from a document, dispatched by extension. Structured formats
    (.docx/.html/.pdf) use the parsers above; plain-text extensions are read directly.
    An unknown extension is read as UTF-8 text when `raw_fallback` (default), else
    raises DocError - so existing arbitrary-glob sweeps keep working while .pdf/.docx
    gain real parsing. Raises DocError on a parser failure or a missing PDF dep.

    The on-disk size is capped HERE, at the shared dispatch point, so every format and
    every caller (--file, MCP ingest, future ones) is bounded - the .docx zip-bomb cap
    guarded only one format while pdf/html/raw stayed unbounded (critic 2026-07)."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as e:
        raise DocError(f"cannot stat {path.name} ({type(e).__name__})")
    if size > MAX_DOC_BYTES:
        raise DocError(f"{path.name} too large ({size} bytes > {MAX_DOC_BYTES}; "
                       f"raise NEVERTWICE_MAX_DOC_BYTES to override)")
    ext = path.suffix.lower()
    if ext == ".docx":
        return _docx_text(path)
    if ext in (".html", ".htm"):
        return _html_text(path)
    if ext == ".pdf":
        return _pdf_text(path)
    if ext in TEXT_EXTS or raw_fallback:
        return path.read_text(encoding="utf-8", errors="replace")
    raise DocError(f"unsupported document type {ext!r} "
                   f"(supported: {', '.join(sorted(SUPPORTED))})")
