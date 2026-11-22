"""Conservative document ingestion and plain-text delivery snapshots.

Original bytes are never modified here. Extraction is a reading aid, not a Word
round trip, OCR engine, signature verifier, antivirus scanner or legal opinion.
"""
from __future__ import annotations

import base64
import io
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any
from xml.sax.saxutils import escape

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree as LET
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_PACKAGE_BYTES = 60 * 1024 * 1024
MAX_PART_BYTES = 8 * 1024 * 1024
MAX_PARTS = 1000
MAX_PDF_PAGES = 100
MAX_TEXT_CHARS = 500_000
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W}


def _safety() -> dict[str, Any]:
    return {
        "requires_manual_review": False,
        "tracked_changes": False,
        "comments": False,
        "hidden_content": False,
        "personal_metadata": False,
        "external_relationships": False,
        "embedded_objects": False,
        "complex_content": False,
        "findings": [],
    }


def _flag(safety: dict, field: str, note: str) -> None:
    safety[field] = True
    safety["requires_manual_review"] = True
    if note not in safety["findings"]:
        safety["findings"].append(note)


def _xml(data: bytes) -> ET.Element:
    if len(data) > MAX_PART_BYTES:
        raise ValueError("文档 XML 部件超过解析大小限制。")
    # Do not accept DTD or user-defined entities, even on parsers that disable
    # external entity resolution by default. Nothing follows external links.
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.IGNORECASE):
        raise ValueError("文档含不支持的 XML DTD 或实体声明。")
    try:
        # Secure parser also handles UTF-16 declarations, which a byte-level
        # ASCII DTD scan alone would miss. Entity expansion and network disabled.
        root = LET.fromstring(data, parser=LET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False, remove_comments=True, remove_pis=True))
        if root.getroottree().docinfo.doctype:
            raise ValueError("文档含不支持的 XML DTD 或实体声明。")
        stack, count = [(root, 1)], 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if depth > 80 or count > 120_000:
                raise ValueError("文档 XML 层级或节点数量超过解析限制。")
            stack.extend((child, depth + 1) for child in node)
        return root
    except LET.XMLSyntaxError as exc:
        raise ValueError("文档 XML 损坏，未进行不可靠提取。") from exc


def _zip_parts(data: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_PARTS:
                raise ValueError("DOCX 包含过多部件。")
            if sum(info.file_size for info in entries) > MAX_PACKAGE_BYTES:
                raise ValueError("DOCX 解压后超过大小限制。")
            names: set[str] = set()
            for info in entries:
                path = PurePosixPath(info.filename)
                if info.filename in names or path.is_absolute() or ".." in path.parts:
                    raise ValueError("DOCX 包含重复或不安全部件路径。")
                names.add(info.filename)
                if info.flag_bits & 1:
                    raise ValueError("暂不解析加密 DOCX，请先在 Word 中解密副本。")
                if info.file_size > MAX_PART_BYTES:
                    raise ValueError("DOCX 单个部件超过大小限制。")
                if info.file_size > 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 200:
                    raise ValueError("DOCX 压缩比异常，已拒绝解析。")
            return {info.filename: archive.read(info) for info in entries if not info.is_dir()}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError("这不是可读取的 DOCX，或文件已损坏/加密。") from exc


def _enabled(node: ET.Element | None) -> bool:
    return node is not None and node.get(f"{{{W}}}val", "true") not in {"false", "0", "off"}


def _paragraph_text(node: ET.Element) -> str:
    """Read the accepted/included view; omit deletions and explicitly hidden runs."""
    pieces: list[str] = []

    def walk(current: ET.Element) -> None:
        local = current.tag.rsplit("}", 1)[-1]
        if local in {"del", "moveFrom", "instrText", "delText", "txbxContent"}:
            return
        if local == "r":
            props = current.find("w:rPr", NS)
            if props is not None and any(_enabled(props.find(f"w:{key}", NS)) for key in ("vanish", "webHidden")):
                return
        if current.tag == f"{{{W}}}t":
            pieces.append(current.text or "")
        elif current.tag == f"{{{W}}}tab":
            pieces.append("\t")
        elif current.tag in {f"{{{W}}}br", f"{{{W}}}cr"}:
            pieces.append("\n")
        else:
            for child in current:
                walk(child)

    walk(node)
    return "".join(pieces).strip()


def _docx(data: bytes) -> dict:
    parts = _zip_parts(data)
    if "word/document.xml" not in parts or "[Content_Types].xml" not in parts:
        raise ValueError("DOCX 缺少正文或内容类型部件。")
    safety = _safety()
    parsed = {name: _xml(raw) for name, raw in parts.items() if name.endswith((".xml", ".rels"))}
    for name, root in parsed.items():
        if name.startswith("word/"):
            for node in root.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local in {"ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange", "tblPrChange", "sectPrChange"}:
                    _flag(safety, "tracked_changes", "原件包含 Word 修订；阅读文本暂按新增保留、删除排除提取，需在 Word 核对。")
                if local in {"comment", "commentReference", "commentRangeStart", "commentRangeEnd"}:
                    _flag(safety, "comments", "原件包含批注；批注仅保留在原件，不自动混入正文或对外稿。")
                if local in {"vanish", "webHidden"} and _enabled(node):
                    _flag(safety, "hidden_content", "原件含隐藏文字或隐藏样式；提取结果需人工核对。")
                if name not in {"word/styles.xml", "word/stylesWithEffects.xml", "word/numbering.xml"} and local in {"drawing", "pict", "object", "altChunk", "txbxContent", "fldChar", "instrText", "footnoteReference", "endnoteReference", "numPr", "sdt"}:
                    _flag(safety, "complex_content", "原件含图片、域、自动编号、文本框或其他复杂构件；纯文本不保证完整还原。")
            if name in {"word/footnotes.xml", "word/endnotes.xml"} or name.startswith(("word/header", "word/footer")):
                if any((node.text or "").strip() for node in root.iter(f"{{{W}}}t")):
                    _flag(safety, "complex_content", "页眉页脚或脚注含文字；正文快照未完整保留这些构件，请核对原件。")
        if "comments" in name.lower():
            _flag(safety, "comments", "原件含批注部件。")
        if name.endswith(".rels"):
            if any(node.get("TargetMode", "").lower() == "external" for node in root):
                _flag(safety, "external_relationships", "原件含外部链接或外部模板引用；系统不会访问这些链接。")
        if name == "docProps/core.xml":
            sensitive = {"creator", "lastModifiedBy", "subject", "description", "keywords", "category"}
            if any(node.tag.rsplit("}", 1)[-1] in sensitive and (node.text or "").strip() for node in root):
                _flag(safety, "personal_metadata", "原件核心属性可能含作者、最后编辑者或说明；对外快照将重新生成并检查。")
        if name == "docProps/app.xml":
            if any(node.tag.rsplit("}", 1)[-1] in {"Company", "Manager", "HyperlinkBase", "Template"} and (node.text or "").strip() for node in root):
                _flag(safety, "personal_metadata", "原件扩展属性含公司、模板或其他可识别信息。")
    for name in parts:
        if name.startswith(("word/embeddings/", "customXml/")) or "vbaproject" in name.lower():
            _flag(safety, "embedded_objects", "原件含嵌入对象、宏或自定义 XML；不执行且不复制到清洁快照。")
        if name == "docProps/custom.xml":
            _flag(safety, "personal_metadata", "原件含自定义元数据。")
    body = parsed["word/document.xml"].find("w:body", NS)
    if body is None:
        raise ValueError("DOCX 没有可读取的正文。")
    lines = []
    # Document-order traversal keeps table cell paragraphs near their position.
    # Table geometry, merged cells and automatic numbering are intentionally not
    # reconstructed. Original download remains the authoritative representation.
    for child in body:
        if child.tag == f"{{{W}}}p":
            lines.append(_paragraph_text(child))
        elif child.tag == f"{{{W}}}tbl":
            _flag(safety, "complex_content", "原件含表格；按单元格段落提取文本，不保证合并单元格与版式语义。")
            for row in child.findall("w:tr", NS):
                cells = [" / ".join(_paragraph_text(p) for p in cell.findall(".//w:p", NS)) for cell in row.findall("w:tc", NS)]
                lines.append(" | ".join(cells))
        elif child.tag != f"{{{W}}}sectPr":
            _flag(safety, "complex_content", "正文含暂不支持完整提取的块级构件，请核对原件。")
    text = "\n".join(lines).strip()
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("提取文字超过演示版解析限制。")
    if not text:
        _flag(safety, "complex_content", "未提取到可靠正文，请在 Word 查看原件。")
    return {"text": text, "parse_status": "needs_review" if safety["requires_manual_review"] else "parsed", "warnings": safety["findings"][:], "safety": safety}


def _pdf_stream_guard(stream: Any) -> None:
    """Bound decompression before pypdf tries to interpret a content stream."""
    stream = stream.get_object()
    if not hasattr(stream, "_data"):
        return
    raw = stream._data
    filters = stream.get("/Filter", [])
    if isinstance(filters, str):
        filters = [filters]
    for filter_name in filters:
        if filter_name in {"/ASCII85Decode", "/A85"}:
            try:
                raw = base64.a85decode(raw, adobe=raw.rstrip().endswith(b"~>"))
            except (ValueError, OverflowError) as exc:
                raise ValueError("PDF 文本流编码损坏。") from exc
        elif filter_name in {"/FlateDecode", "/Fl"}:
            try:
                decoder = zlib.decompressobj()
                raw = decoder.decompress(raw, MAX_PART_BYTES + 1)
                if len(raw) > MAX_PART_BYTES or decoder.unconsumed_tail:
                    raise ValueError("PDF 解压后的文本流超过解析限制。")
            except zlib.error as exc:
                raise ValueError("PDF 文本流压缩格式损坏。") from exc
        else:
            raise ValueError("PDF 使用暂不支持安全提取的文本流编码；请先转换为标准文本 PDF。")
        if len(raw) > MAX_PART_BYTES:
            raise ValueError("PDF 文本流超过解析限制。")
    if len(raw) > MAX_PART_BYTES:
        raise ValueError("PDF 文本流超过解析限制。")


def _pdf(data: bytes) -> dict:
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("文件扩展名为 PDF，但内容不是 PDF。")
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("暂不解析加密 PDF，请先在授权范围内解密副本。")
        if not reader.pages or len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError("PDF 无页面或超过 100 页的演示版解析限制。")
        safety = _safety()
        metadata = reader.metadata or {}
        if any(str(metadata.get(key, "")).strip() for key in ("/Author", "/Subject", "/Keywords", "/Creator")):
            _flag(safety, "personal_metadata", "PDF 元数据含作者、创建工具或其他说明。")
        root = reader.trailer["/Root"]
        if root.get("/Metadata"):
            _flag(safety, "personal_metadata", "PDF 含 XMP 元数据。")
        if root.get("/OpenAction") or root.get("/AA"):
            _flag(safety, "embedded_objects", "PDF 含打开动作或自动动作；不执行。")
        names = root.get("/Names")
        if names:
            names = names.get_object()
            if names.get("/EmbeddedFiles") or names.get("/JavaScript"):
                _flag(safety, "embedded_objects", "PDF 含附件或脚本；不执行、不复制到快照。")
        if root.get("/AcroForm"):
            _flag(safety, "complex_content", "PDF 含表单或签名字段；文本提取不验证签名或表单有效性。")
        texts, low_text_pages = [], []
        for number, page in enumerate(reader.pages, 1):
            if page.get("/Annots"):
                _flag(safety, "comments", "PDF 含批注、链接或表单控件，请核对原件。")
            if page.get("/AA"):
                _flag(safety, "embedded_objects", "PDF 页面含动作；不执行。")
            contents = page.get("/Contents")
            if contents:
                contents = contents.get_object()
                for stream in contents if isinstance(contents, list) else [contents]:
                    _pdf_stream_guard(stream)
            resources = page.get("/Resources") or {}
            if hasattr(resources, "get_object"):
                resources = resources.get_object()
            xobjects = resources.get("/XObject") if resources else None
            if xobjects:
                for obj in xobjects.get_object().values():
                    obj = obj.get_object()
                    if obj.get("/Subtype") == "/Form":
                        # Avoid recursive PDF form parsing on untrusted uploads.
                        raise ValueError("PDF 含嵌套表单绘图，暂不进行自动文本提取；请转换为标准文本 PDF。")
                    if obj.get("/Subtype") == "/Image":
                        _flag(safety, "complex_content", "PDF 含图像；文字提取不会识别图像内文字或签章。")
            value = page.extract_text() or ""
            if len(value.strip()) < 20 or value.count("\ufffd") > max(3, len(value) // 20):
                low_text_pages.append(number)
            texts.append(value.strip())
        text = "\n\n".join(texts).strip()
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError("PDF 提取文字超过演示版解析限制。")
        warnings = safety["findings"][:]
        if low_text_pages:
            warnings.append("第 " + "、".join(map(str, low_text_pages)) + " 页未得到可靠文字，标记待 OCR；系统尚未执行 OCR。")
            safety["requires_manual_review"] = True
        status = "needs_ocr" if low_text_pages else ("needs_review" if safety["requires_manual_review"] else "parsed")
        return {"text": text, "parse_status": status, "warnings": warnings, "safety": safety}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 损坏或无法安全提取，请保留原件并人工核验。") from exc


def parse_document(filename: str, data: bytes) -> dict:
    """Extract a bounded text reading view, with explicit manual-review flags."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("文件为空。")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("文件超过 15 MB 演示版限制。")
    extension = PurePosixPath(filename.lower()).suffix
    if extension == ".docx":
        return _docx(data)
    if extension == ".pdf":
        return _pdf(data)
    if extension == ".md":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Markdown 需使用 UTF-8 编码。") from exc
        if "\x00" in text or len(text) > MAX_TEXT_CHARS:
            raise ValueError("Markdown 包含二进制内容或超过文字限制。")
        safety = _safety()
        if "<!--" in text or re.search(r"<\s*(?:script|iframe|style)\b", text, re.I):
            _flag(safety, "hidden_content", "Markdown 含 HTML 注释或活动标签；只按文字显示，导出前需人工清理。")
        return {"text": text, "parse_status": "needs_review" if safety["requires_manual_review"] else "parsed", "warnings": safety["findings"][:], "safety": safety}
    return {"text": "", "parse_status": "unsupported", "warnings": ["首版只解析 DOCX、PDF、UTF-8 Markdown。"], "safety": _safety()}


def _clean_docx_package(data: bytes) -> bytes:
    """Scrub newly generated OOXML; never use this to silently alter an original."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            name, value = info.filename, source.read(info)
            if name in {"docProps/custom.xml", "docProps/thumbnail.jpeg"} or name.startswith("customXml/"):
                continue
            if name.endswith((".xml", ".rels")):
                # Preserve OOXML prefix mappings such as mc:Ignorable="w14".
                # ElementTree rewrites prefixes and can make Word packages fail
                # to load despite their XML being syntactically well-formed.
                root = LET.fromstring(value, parser=LET.XMLParser(resolve_entities=False, no_network=True))
                if name == "docProps/core.xml":
                    for child in list(root):
                        root.remove(child)
                if name == "docProps/app.xml":
                    for child in list(root):
                        if child.tag.rsplit("}", 1)[-1] in {"Company", "Manager", "HyperlinkBase", "Template"}:
                            root.remove(child)
                for node in root.iter():
                    for attr in list(node.attrib):
                        if attr.rsplit("}", 1)[-1].startswith("rsid"):
                            del node.attrib[attr]
                    for child in list(node):
                        if child.tag.rsplit("}", 1)[-1] == "rsids":
                            node.remove(child)
                if name.endswith(".rels"):
                    for child in list(root):
                        if "thumbnail" in child.get("Type", "") or child.get("Type", "").endswith(("/custom-properties", "/customXml")):
                            root.remove(child)
                if name == "[Content_Types].xml":
                    for child in list(root):
                        if child.get("PartName") in {"/docProps/custom.xml", "/docProps/thumbnail.jpeg"} or child.get("PartName", "").startswith("/customXml/"):
                            root.remove(child)
                value = LET.tostring(root, encoding="utf-8", xml_declaration=True)
            # Stable package timestamps keep fixture hashes and import retries
            # reproducible; they are not the contract's creation timestamp.
            item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            target.writestr(item, value, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _normal_lines(text: str, title: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    if lines and lines[0].lstrip("# ").strip() == title.strip():
        lines = lines[1:]
    return lines


def export_document(text: str, format: str, title: str = "投资合同文本快照", internal: bool = False) -> bytes:
    """Create a NEW reading snapshot; no original styling, revisions or signatures."""
    if not text.strip():
        raise ValueError("没有可靠正文可生成快照。")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("导出内容超过演示版文字限制。")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise ValueError("正文含不支持的控制字符，请先清理。")
    format = format.lower().lstrip(".")
    lines = _normal_lines(text, title)
    if format == "md":
        prefix = f"# {title}\n\n"
        if internal:
            prefix += "内部审核清单  仅供法务复核\n\n"
        return (prefix + "\n".join(lines).strip() + "\n").encode("utf-8")
    if format == "docx":
        doc = Document()
        section = doc.sections[0]
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        section.top_margin = section.bottom_margin = Inches(0.72)
        section.left_margin = section.right_margin = Inches(0.82)
        for name in ("Normal", "Title", "Heading 1", "Heading 2"):
            style = doc.styles[name]
            style.font.name = "Arial"
            style.font.color.rgb = RGBColor(0, 0, 0)
            style.font.size = Pt(11 if name == "Normal" else (20 if name == "Title" else 12))
            style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Noto Sans CJK SC")
            style.paragraph_format.space_before = Pt(8 if name.startswith("Heading") else 0)
            style.paragraph_format.space_after = Pt(6 if name == "Normal" else 10)
            style.paragraph_format.line_spacing = Pt(16 if name == "Normal" else (27 if name == "Title" else 18))
            props = style.element.get_or_add_pPr()
            snap = OxmlElement("w:snapToGrid")
            snap.set(qn("w:val"), "0")
            props.append(snap)
        # The bundled default Word template can have a blue rule in Title.
        # New legal snapshots use typography and whitespace, with no title rule.
        for style in doc.styles:
            for border in style.element.xpath(".//w:pBdr"):
                border.getparent().remove(border)
        doc.add_paragraph(title, "Title")
        if internal:
            doc.add_paragraph("内部审核清单  仅供法务复核")
        for line in lines:
            if not line.strip():
                continue
            if line.startswith(("## ", "### ")):
                doc.add_paragraph(line.lstrip("# "), "Heading 1")
            else:
                paragraph = doc.add_paragraph(line)
                paragraph.paragraph_format.widow_control = True
        raw = io.BytesIO()
        doc.save(raw)
        return _clean_docx_package(raw.getvalue())
    if format == "pdf":
        # A standard PDF CJK font, tested with Poppler. Requires a viewer with
        # standard CJK font support; this is a text snapshot, never a signed PDF.
        if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        body = ParagraphStyle("Body", fontName="STSong-Light", fontSize=11, leading=17, spaceAfter=7, wordWrap="CJK", textColor=colors.black)
        heading = ParagraphStyle("Title", parent=body, fontSize=20, leading=27, spaceAfter=15)
        section_heading = ParagraphStyle("Heading", parent=body, fontSize=12, leading=18, spaceBefore=8, spaceAfter=9, keepWithNext=True)
        output = io.BytesIO()
        document = SimpleDocTemplate(output, pagesize=letter, rightMargin=59, leftMargin=59, topMargin=52, bottomMargin=52, title="", author="", subject="")
        flow = [Paragraph(escape(title), heading)]
        if internal:
            flow.append(Paragraph("内部审核清单  仅供法务复核", body))
        for line in lines:
            if not line.strip():
                continue
            is_heading = line.startswith(("## ", "### "))
            flow.append(Paragraph(escape(line.lstrip("# ") if is_heading else line).replace("\t", "    "), section_heading if is_heading else body))
        document.build(flow)
        reader = PdfReader(io.BytesIO(output.getvalue()))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.metadata = None
        clean = io.BytesIO()
        writer.write(clean)
        return clean.getvalue()
    raise ValueError("导出格式仅支持 docx、pdf、md。")


def inspect_export(data: bytes, format: str) -> dict:
    """A scoped structural check, not a proof of confidentiality or legal approval."""
    format = format.lower().lstrip(".")
    findings: list[str] = []
    checked = []
    try:
        result = parse_document("delivery." + format, data)
        safety = result["safety"]
        keys = ("tracked_changes", "comments", "hidden_content", "personal_metadata", "external_relationships", "embedded_objects", "complex_content")
        findings.extend(safety["findings"])
        if format == "docx":
            checked = ["修订与批注部件", "显式隐藏文字与隐藏样式", "作者及自定义元数据", "外部关系", "嵌入对象与复杂构件"]
        elif format == "pdf":
            checked = ["页面批注与表单", "目录动作及脚本附件", "作者与 XMP 元数据", "正文可提取性"]
        elif format == "md":
            checked = ["UTF-8 编码", "HTML 注释与活动标签", "可见纯文本内容"]
            # Markdown may be opened in a viewer that executes HTML. External
            # output containing any raw HTML is held for manual cleanup.
            if re.search(r"<[/!]?[A-Za-z][^>]*>", result["text"]):
                findings.append("Markdown 含原始 HTML 标签；请清理后重新生成。")
        else:
            findings.append("不支持该格式的交付检查。")
        if result["parse_status"] in {"unsupported", "needs_ocr"}:
            findings.extend(result["warnings"])
        passed = not findings and not any(safety[key] for key in keys)
        return {"passed": passed, "findings": list(dict.fromkeys(findings)), "checked": checked, "scope": "结构检查只覆盖列明项目，不证明正文无敏感信息、版式完整、签名有效或法律结论正确；仍须人工批准。"}
    except ValueError as exc:
        return {"passed": False, "findings": [str(exc)], "checked": checked, "scope": "文件未能通过结构检查。"}
