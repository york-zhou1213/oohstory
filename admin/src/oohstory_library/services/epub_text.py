"""Bounded, non-extracting EPUB text parser."""

from __future__ import annotations

import io
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
import zipfile

from bs4 import BeautifulSoup
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


MAX_EPUB_BYTES = 128 * 1024 * 1024
MAX_CONTAINER_BYTES = 256 * 1024
MAX_OPF_BYTES = 2 * 1024 * 1024
MAX_HTML_ENTRY_BYTES = 8 * 1024 * 1024
MAX_HTML_TOTAL_BYTES = 128 * 1024 * 1024
MAX_HTML_ENTRIES = 5_000


def _safe_member(value: str) -> str:
    split = urlsplit(unquote(str(value or "").strip()))
    if split.scheme or split.netloc:
        raise ValueError("EPUB 包含外部资源路径")
    path = PurePosixPath(split.path)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("EPUB 包含越界资源路径")
    return path.as_posix()


def _bounded_read(
    archive: zipfile.ZipFile,
    name: str,
    limit: int,
    *,
    label: str,
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ValueError(f"EPUB 缺少{label}") from exc
    if info.file_size > limit:
        raise ValueError(f"EPUB {label}超过安全大小限制")
    raw = archive.read(info)
    if len(raw) > limit:
        raise ValueError(f"EPUB {label}超过安全大小限制")
    return raw


def _parse_xml(raw: bytes, *, label: str):
    try:
        return ElementTree.fromstring(raw)
    except DefusedXmlException as exc:
        raise ValueError(f"EPUB {label}包含不安全 XML") from exc
    except ElementTree.ParseError as exc:
        raise ValueError(f"EPUB {label}格式无效") from exc


def epub_to_text(raw: bytes) -> str:
    """Convert an EPUB to plain text under strict memory and XML budgets."""

    if len(raw) > MAX_EPUB_BYTES:
        raise ValueError("远程 EPUB 超过安全大小限制")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("远程 EPUB 文件损坏") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > 20_000:
            raise ValueError("EPUB 文件条目过多")
        names = {info.filename for info in infos}
        container = _parse_xml(
            _bounded_read(
                archive,
                "META-INF/container.xml",
                MAX_CONTAINER_BYTES,
                label="容器清单",
            ),
            label="容器清单",
        )
        try:
            rootfile = next(
                node for node in container.iter() if node.tag.endswith("rootfile")
            )
            opf_name = _safe_member(str(rootfile.attrib["full-path"]))
        except (KeyError, StopIteration) as exc:
            raise ValueError("EPUB 容器清单缺少正文索引") from exc

        opf = _parse_xml(
            _bounded_read(archive, opf_name, MAX_OPF_BYTES, label="OPF 清单"),
            label="OPF 清单",
        )
        manifest: dict[str, str] = {}
        for node in opf.iter():
            if not node.tag.endswith("item"):
                continue
            media_type = str(node.attrib.get("media-type") or "").lower()
            href = str(node.attrib.get("href") or "")
            if "html" not in media_type and not href.lower().endswith(
                (".xhtml", ".html", ".htm")
            ):
                continue
            item_id = str(node.attrib.get("id") or "")
            if item_id:
                manifest[item_id] = href

        ordered_names: list[str] = []
        opf_parent = PurePosixPath(opf_name).parent
        for node in opf.iter():
            if not node.tag.endswith("itemref"):
                continue
            href = manifest.get(str(node.attrib.get("idref") or ""))
            if not href:
                continue
            candidate = _safe_member((opf_parent / _safe_member(href)).as_posix())
            if candidate in names and candidate not in ordered_names:
                ordered_names.append(candidate)

        if not ordered_names:
            ordered_names = sorted(
                name
                for name in names
                if name.lower().endswith((".xhtml", ".html", ".htm"))
                and _safe_member(name)
            )
        if len(ordered_names) > MAX_HTML_ENTRIES:
            raise ValueError("EPUB 正文章节条目过多")

        sections: list[str] = []
        total_bytes = 0
        for name in ordered_names:
            page = _bounded_read(
                archive, name, MAX_HTML_ENTRY_BYTES, label="HTML 正文章节"
            )
            total_bytes += len(page)
            if total_bytes > MAX_HTML_TOTAL_BYTES:
                raise ValueError("EPUB 正文累计大小超过安全限制")
            soup = BeautifulSoup(page, "html.parser")
            for node in soup(["script", "style", "nav", "svg"]):
                node.decompose()
            text = "\n".join(
                line.strip()
                for line in soup.get_text("\n").splitlines()
                if line.strip()
            )
            if text:
                sections.append(text)

    content = "\n\n".join(sections).strip()
    if len(content) < 128:
        raise ValueError("远程 EPUB 未提取到有效正文")
    return content
