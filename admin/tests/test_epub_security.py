from __future__ import annotations

import io
import zipfile

import pytest

from oohstory_library.services.epub_text import (
    MAX_HTML_ENTRY_BYTES,
    epub_to_text,
)


def _epub(*, container: bytes | None = None, page: bytes | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            container
            or b'<container><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OPS/content.opf",
            b'<package><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
            b'<spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OPS/chapter.xhtml",
            page or (b"<html><body><p>" + "有效正文".encode() * 80 + b"</p></body></html>"),
        )
    return output.getvalue()


def test_epub_parser_extracts_bounded_spine_content() -> None:
    text = epub_to_text(_epub())
    assert "有效正文" in text


def test_epub_parser_rejects_xml_entities() -> None:
    malicious = (
        b'<!DOCTYPE container [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<container><rootfiles><rootfile full-path="&xxe;"/></rootfiles></container>'
    )
    with pytest.raises(ValueError, match="不安全 XML"):
        epub_to_text(_epub(container=malicious))


def test_epub_parser_rejects_oversized_html_member() -> None:
    with pytest.raises(ValueError, match="超过安全大小限制"):
        epub_to_text(_epub(page=b"x" * (MAX_HTML_ENTRY_BYTES + 1)))
