#!/usr/bin/env python3
"""Self-check for docparse.py — .docx (stdlib zip+xml), .html (stdlib parser), plain
text, raw fallback, and the .pdf error path. No network, no external deps required."""
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import docparse as dp            # noqa: E402

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_docx(path: Path, lines):
    body = "".join(f"<w:p><w:r><w:t>{ln}</w:t></w:r></w:p>" for ln in lines)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')


def test_docx():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "paper.docx"
        _make_docx(p, ["Title line", "A finding about CUDA OOM.", "Conclusion."])
        txt = dp.extract_text(p)
        assert "CUDA OOM" in txt and "Conclusion." in txt, txt
        assert txt.count("\n") == 2, repr(txt)            # one line per <w:p>
    print("ok test_docx")


def test_html_strips_script_and_tags():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "page.html"
        p.write_text("<html><head><style>a{}</style><script>var x=1</script></head>"
                     "<body><h1>Heading</h1><p>Body text here.</p></body></html>",
                     encoding="utf-8")
        txt = dp.extract_text(p)
        assert "Heading" in txt and "Body text here." in txt, txt
        assert "var x" not in txt and "a{}" not in txt, txt   # script/style dropped
    print("ok test_html_strips_script_and_tags")


def test_plain_text_and_raw_fallback():
    with tempfile.TemporaryDirectory() as d:
        md = Path(d) / "notes.md"
        md.write_text("# Notes\nplain markdown stays verbatim", encoding="utf-8")
        assert "plain markdown stays verbatim" in dp.extract_text(md)
        weird = Path(d) / "log.weirdext"
        weird.write_text("raw fallback content", encoding="utf-8")
        assert dp.extract_text(weird) == "raw fallback content"          # unknown ext → text
        try:
            dp.extract_text(weird, raw_fallback=False)
        except dp.DocError:
            pass
        else:
            raise AssertionError("raw_fallback=False should reject an unknown ext")
    print("ok test_plain_text_and_raw_fallback")


def test_docx_zip_bomb_capped():
    # a .docx whose document.xml declares a huge uncompressed size must be refused before read
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bomb.docx"
        big = ("<w:p><w:r><w:t>" + "A" * 100 + "</w:t></w:r></w:p>") * 5000
        _make_docx(p, [])
        # rewrite with an oversized body and a low cap via env
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml",
                       f'<?xml version="1.0"?><w:document xmlns:w="{_W}"><w:body>{big}</w:body></w:document>')
        old = dp.MAX_DOC_BYTES
        dp.MAX_DOC_BYTES = 1000                      # force the cap to trip
        try:
            try:
                dp.extract_text(p)
            except dp.DocError as e:
                assert "too large" in str(e)
                print("ok test_docx_zip_bomb_capped")
                return
            raise AssertionError("oversized .docx body must raise DocError")
        finally:
            dp.MAX_DOC_BYTES = old


def test_pdf_error_path():
    # a non-PDF (or absent pypdf) must raise DocError, never crash or read binary as text
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "broken.pdf"
        p.write_bytes(b"%PDF-1.4 not really a pdf")
        try:
            dp.extract_text(p)
        except dp.DocError:
            print("ok test_pdf_error_path")
            return
    raise AssertionError("a broken/unsupported .pdf must raise DocError")


if __name__ == "__main__":
    test_docx()
    test_html_strips_script_and_tags()
    test_plain_text_and_raw_fallback()
    test_docx_zip_bomb_capped()
    test_pdf_error_path()
    print("\nall docparse self-checks passed")
