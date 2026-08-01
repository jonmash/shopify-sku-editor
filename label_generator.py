"""
Generates barcode label sheets that match Linda's existing pre-cut Avery
4"x6" label stock exactly — same grid, same DataMatrix size/position, same
text position — so labels line up on the physical sheet without adjustment.

Geometry below was measured directly from a real printed sample (labels.pdf):
  - Page: 288 x 432 pt (4in x 6in)
  - Grid: 4 columns x 9 rows = 36 labels per sheet
  - Column pitch: 58.5 pt   Row pitch: 40.5 pt
  - First DataMatrix: top-left corner at (48.25, 42.416) from the page's
    top-left, sized 16 x 16 pt, with NO internal quiet zone — it relies on
    the surrounding blank cell space (confirmed to decode reliably at that
    spacing during testing).
  - Text: 5pt Helvetica, centered under the matrix, baseline 22.478pt below
    the matrix's top edge (i.e. baseline at topdown-y 64.894 for row 0).
"""

import io
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from pystrich.datamatrix import DataMatrixEncoder
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = 288.0, 432.0
COLS, ROWS = 4, 9
PER_PAGE = COLS * ROWS
COL_PITCH = 58.5
ROW_PITCH = 40.5

MATRIX0_X = 48.25       # left edge of the row-0/col-0 matrix (topdown coords)
MATRIX0_Y = 42.416      # top edge of the row-0/col-0 matrix (topdown coords)
MATRIX_SIZE = 16.0      # matrix is always drawn at this size, pt

TEXT_BASELINE0 = 64.894  # baseline y (topdown) for row 0 text
FONT_NAME = "Helvetica"
FONT_SIZE = 5.0
FONT_SIZE_MIN = 3.5
TEXT_MAX_WIDTH = COL_PITCH - 8  # leave a little breathing room either side


def _render_datamatrix_png(value: str) -> bytes:
    """Render `value` as a tightly-cropped DataMatrix PNG (no internal
    quiet zone — matches the original template's approach)."""
    encoder = DataMatrixEncoder(value, quiet_zone=2)
    with tempfile.TemporaryDirectory() as tmp:
        png_path = Path(tmp) / "dm.png"
        encoder.save(str(png_path), cellsize=40)
        img = Image.open(png_path).convert("L")
        bbox = ImageOps.invert(img).getbbox()
        trimmed = img.crop(bbox) if bbox else img
        buf = io.BytesIO()
        trimmed.save(buf, format="PNG")
        return buf.getvalue()


def _fit_text(c: canvas.Canvas, text: str) -> tuple[str, float]:
    """Shrink font size to fit TEXT_MAX_WIDTH; truncate with an ellipsis
    as a last resort at the smallest allowed size."""
    size = FONT_SIZE
    while size >= FONT_SIZE_MIN:
        if c.stringWidth(text, FONT_NAME, size) <= TEXT_MAX_WIDTH:
            return text, size
        size -= 0.25
    # Still too wide even at the floor size — truncate.
    size = FONT_SIZE_MIN
    truncated = text
    while truncated and c.stringWidth(truncated + "…", FONT_NAME, size) > TEXT_MAX_WIDTH:
        truncated = truncated[:-1]
    return (truncated + "…") if truncated != text else text, size


def generate_labels_pdf(items: list[dict]) -> bytes:
    """items: [{"barcode": "LNR001", "name": "Arctic Balm"}, ...]
    Returns PDF bytes, paginated 36 labels per 4x6" sheet."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    for i, item in enumerate(items):
        barcode = (item.get("barcode") or "").strip()
        name = (item.get("name") or "").strip()
        if not barcode:
            continue

        pos_in_page = i % PER_PAGE
        if i > 0 and pos_in_page == 0:
            c.showPage()
        row, col = divmod(pos_in_page, COLS)

        matrix_left = MATRIX0_X + col * COL_PITCH
        matrix_top = MATRIX0_Y + row * ROW_PITCH
        img_bytes = _render_datamatrix_png(barcode)
        img_reader = ImageReader(io.BytesIO(img_bytes))
        c.drawImage(
            img_reader,
            matrix_left,
            PAGE_H - (matrix_top + MATRIX_SIZE),
            width=MATRIX_SIZE,
            height=MATRIX_SIZE,
        )

        if name:
            text, size = _fit_text(c, name)
            c.setFont(FONT_NAME, size)
            center_x = matrix_left + MATRIX_SIZE / 2
            baseline_y = PAGE_H - (TEXT_BASELINE0 + row * ROW_PITCH)
            c.drawCentredString(center_x, baseline_y, text)

    c.save()
    return buf.getvalue()
