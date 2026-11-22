"""Regenerate synthetic examples: python -m samples.generate (from repo root)."""
from pathlib import Path
import io

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from PIL import Image, ImageDraw

from app.demo import generate_sample


def main():
    directory = Path(__file__).parent
    stems = {1: "investment-v1-initial", 2: "investment-v2-counterparty", 3: "investment-v3-revision"}
    for stage, stem in stems.items():
        for format in ("md", "docx", "pdf"):
            (directory / f"{stem}.{format}").write_bytes(generate_sample(stage, format))
    image = Image.new("RGB", (1400, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((90, 160), "DEMONSTRATION DATA - FICTIONAL SCAN", fill="black", font_size=40)
    draw.text((90, 260), "Image only, no searchable text layer.", fill="black", font_size=32)
    draw.text((90, 340), "This fixture should be marked: OCR required.", fill="black", font_size=32)
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(612, 792))
    canvas.drawImage(ImageReader(image), 0, 0, 612, 792)
    canvas.showPage()
    canvas.save()
    (directory / "fictional-scan-needs-ocr.pdf").write_bytes(output.getvalue())


if __name__ == "__main__":
    main()
