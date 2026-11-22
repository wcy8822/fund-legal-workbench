import hashlib
import io
import unittest
import zipfile
from pathlib import Path

from docx import Document
from lxml import etree
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from PIL import Image, ImageDraw

from app.demo import SAMPLE_TEXTS, TITLE, generate_sample
from app.documents import export_document, inspect_export, parse_document


def patch_docx(data, changes):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, changes.pop(name, source.read(name)))
        for name, raw in changes.items():
            target.writestr(name, raw)
    return output.getvalue()


def simple_docx_xml(body):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>' + body + '<w:sectPr/></w:body></w:document>').encode()


def image_pdf():
    image = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.text((45, 80), "DEMONSTRATION DATA", fill="black")
    draw.text((45, 140), "Scanned image only. OCR required.", fill="black")
    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.drawImage(ImageReader(image), 50, 250, 500, 278)
    canvas.showPage()
    canvas.save()
    return output.getvalue()


class DocumentTests(unittest.TestCase):
    def test_all_fictional_versions_round_trip(self):
        for stage in (1, 2, 3):
            for extension in ("docx", "md", "pdf"):
                with self.subTest(stage=stage, extension=extension):
                    data = generate_sample(stage, extension)
                    before = hashlib.sha256(data).hexdigest()
                    parsed = parse_document(f"v{stage}.{extension}", data)
                    self.assertEqual(parsed["parse_status"], "parsed")
                    self.assertIn("第5.2条", parsed["text"])
                    self.assertIn("第8.2条", parsed["text"])
                    self.assertIn("虚构", parsed["text"])
                    self.assertEqual(before, hashlib.sha256(data).hexdigest())
                    check = inspect_export(data, extension)
                    self.assertTrue(check["passed"], check)

    def test_revision_changes_only_deadline(self):
        self.assertIn("5个工作日", SAMPLE_TEXTS[2])
        self.assertIn("10个工作日", SAMPLE_TEXTS[3])
        self.assertIn("应送达管理人", SAMPLE_TEXTS[3])
        self.assertNotIn("## 附件一 董事会批准决议", SAMPLE_TEXTS[3])
        self.assertIn("## 附件一 董事会批准决议", SAMPLE_TEXTS[1])
        self.assertEqual(SAMPLE_TEXTS[2].replace("5个工作日", "10个工作日"), SAMPLE_TEXTS[3])

    def test_samples_are_byte_deterministic_across_calls(self):
        from unittest.mock import patch
        import time
        # ZIP timestamps are normally derived from wall time. Simulate a later
        # call without sleeping to ensure fixture hashes are stable.
        later_clock = time.localtime(1_900_000_000)
        for extension in ("md", "docx", "pdf"):
            before = generate_sample(2, extension)
            with patch("zipfile.time.localtime", return_value=later_clock):
                later = generate_sample(2, extension)
            self.assertEqual(before, later, extension)

    def test_tracked_changes_are_detected_and_reading_view_is_explicit(self):
        body = '<w:p><w:r><w:t>期限</w:t></w:r><w:del w:author="Old reviewer"><w:r><w:delText>5日</w:delText></w:r></w:del><w:ins w:author="New reviewer"><w:r><w:t>10日</w:t></w:r></w:ins></w:p>'
        data = patch_docx(generate_sample(1), {"word/document.xml": simple_docx_xml(body)})
        result = parse_document("tracked.docx", data)
        self.assertTrue(result["safety"]["tracked_changes"])
        self.assertEqual(result["parse_status"], "needs_review")
        self.assertEqual(result["text"], "期限10日")
        self.assertFalse(inspect_export(data, "docx")["passed"])

    def test_hidden_text_not_in_reading_view(self):
        body = '<w:p><w:r><w:t>可见合同条款</w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr><w:t>内部底价秘密</w:t></w:r></w:p>'
        data = patch_docx(generate_sample(1), {"word/document.xml": simple_docx_xml(body)})
        result = parse_document("hidden.docx", data)
        self.assertTrue(result["safety"]["hidden_content"])
        self.assertNotIn("内部底价", result["text"])

    def test_comments_and_personal_metadata_are_flagged(self):
        comments = b'<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:comment w:author="Reviewer"><w:p><w:r><w:t>Private note</w:t></w:r></w:p></w:comment></w:comments>'
        core = b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Real Person</dc:creator></cp:coreProperties>'
        data = patch_docx(generate_sample(1), {"word/comments.xml": comments, "docProps/core.xml": core})
        result = parse_document("comments.docx", data)
        self.assertTrue(result["safety"]["comments"])
        self.assertTrue(result["safety"]["personal_metadata"])
        self.assertNotIn("Private note", result["text"])

    def test_external_relationship_and_embedding_are_detected_without_fetching(self):
        rels = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="external" Target="https://example.invalid/private-template" TargetMode="External"/></Relationships>'
        data = patch_docx(generate_sample(1), {"word/_rels/document.xml.rels": rels, "word/embeddings/hidden.bin": b"private"})
        result = parse_document("external.docx", data)
        self.assertTrue(result["safety"]["external_relationships"])
        self.assertTrue(result["safety"]["embedded_objects"])

    def test_table_text_is_preserved_but_geometry_requires_review(self):
        doc = Document()
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "第4.1条"
        table.cell(0, 1).text = "董事会审批附件"
        raw = io.BytesIO()
        doc.save(raw)
        result = parse_document("table.docx", raw.getvalue())
        self.assertIn("第4.1条 | 董事会审批附件", result["text"])
        self.assertEqual("needs_review", result["parse_status"])

    def test_bad_docx_and_xml_entities_rejected(self):
        with self.assertRaises(ValueError):
            parse_document("bad.docx", b"not zip")
        xml = b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><x>&leak;</x>'
        data = patch_docx(generate_sample(1), {"word/document.xml": xml})
        with self.assertRaisesRegex(ValueError, "DTD"):
            parse_document("entity.docx", data)
        utf16_xml = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x [<!ENTITY leak "private">]><x>&leak;</x>'.encode("utf-16")
        data = patch_docx(generate_sample(1), {"word/document.xml": utf16_xml})
        with self.assertRaisesRegex(ValueError, "DTD"):
            parse_document("utf16-entity.docx", data)

    def test_duplicate_zip_entries_rejected(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as package:
            package.writestr("word/document.xml", b"<x/>")
            package.writestr("../word/document.xml", b"<x/>")
        with self.assertRaisesRegex(ValueError, "路径"):
            parse_document("unsafe.docx", raw.getvalue())

    def test_high_compression_ratio_rejected(self):
        data = patch_docx(generate_sample(1), {"huge.xml": b" " * (2 * 1024 * 1024)})
        with self.assertRaisesRegex(ValueError, "压缩比"):
            parse_document("bomb.docx", data)

    def test_scan_is_marked_needs_ocr(self):
        result = parse_document("scanned.pdf", image_pdf())
        self.assertEqual("needs_ocr", result["parse_status"])
        self.assertEqual("", result["text"])
        self.assertTrue(any("尚未执行 OCR" in warning for warning in result["warnings"]))

    def test_mixed_pdf_retains_ocr_warning_for_scanned_page(self):
        writer = PdfWriter()
        for data in (generate_sample(1, "pdf"), image_pdf()):
            for page in PdfReader(io.BytesIO(data)).pages:
                writer.add_page(page)
        output = io.BytesIO()
        writer.write(output)
        result = parse_document("mixed.pdf", output.getvalue())
        self.assertEqual("needs_ocr", result["parse_status"])
        self.assertIn("第5.2条", result["text"])

    def test_encrypted_pdf_and_fake_pdf_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        writer.encrypt("secret")
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(ValueError, "加密 PDF"):
            parse_document("encrypted.pdf", output.getvalue())
        with self.assertRaises(ValueError):
            parse_document("fake.pdf", b"not a PDF")

    def test_pdf_javascript_is_detected_and_not_executed(self):
        reader = PdfReader(io.BytesIO(generate_sample(1, "pdf")))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_js("app.alert('DO NOT EXECUTE')")
        output = io.BytesIO()
        writer.write(output)
        self.assertFalse(inspect_export(output.getvalue(), "pdf")["passed"])
        self.assertTrue(parse_document("active.pdf", output.getvalue())["safety"]["embedded_objects"])

    def test_markdown_encoding_and_hidden_comments(self):
        with self.assertRaises(ValueError):
            parse_document("legacy.md", "中文".encode("gbk"))
        result = parse_document("notes.md", b"Visible text\n<!-- confidential -->")
        self.assertTrue(result["safety"]["hidden_content"])
        self.assertFalse(inspect_export(b"<img src=x>\nVisible text", "md")["passed"])

    def test_clean_snapshot_does_not_copy_comments_or_author(self):
        body = '<w:p><w:r><w:t>第5.2条 付款期限为10个工作日。</w:t></w:r><w:commentReference w:id="0"/></w:p>'
        data = patch_docx(generate_sample(1), {"word/document.xml": simple_docx_xml(body)})
        result = parse_document("review.docx", data)
        clean = export_document(result["text"], "docx", "审阅后文本快照")
        self.assertTrue(inspect_export(clean, "docx")["passed"])
        self.assertNotEqual(data, clean)
        self.assertTrue(parse_document("review.docx", data)["safety"]["comments"])

    def test_export_rejects_empty_text_and_unknown_format(self):
        with self.assertRaises(ValueError):
            export_document("", "docx")
        with self.assertRaises(ValueError):
            export_document("演示正文", "html")

    def test_docx_still_opens_as_word_document(self):
        data = generate_sample(1)
        doc = Document(io.BytesIO(data))
        self.assertEqual(TITLE, doc.paragraphs[0].text)
        self.assertEqual("", doc.core_properties.author)
        self.assertTrue(any("第8.2条" in paragraph.text for paragraph in doc.paragraphs))

    def test_docx_preserves_prefix_bindings_and_removes_title_border(self):
        # Regression for a failure caught by the LibreOffice rendering gate:
        # rewriting namespaces with ElementTree invalidated mc:Ignorable values.
        data = generate_sample(1)
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            root = etree.fromstring(package.read("word/document.xml"))
            ignorable = root.get("{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable", "")
            for prefix in ignorable.split():
                self.assertIn(prefix, root.nsmap)
            styles = etree.fromstring(package.read("word/styles.xml"))
            self.assertEqual([], styles.xpath("//w:pBdr", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))


if __name__ == "__main__":
    unittest.main()
