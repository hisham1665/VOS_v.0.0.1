"""Phase-3 tests for the document intake and pre-processing pipeline."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from aos_v0.exam.intake import (
    IntakeError,
    IntakeResult,
    PageIssue,
    UnsupportedFormatError,
    ingest,
)
from aos_v0.exam.intake.formats import sniff_kind
from aos_v0.exam.intake.normalize import detect_skew
from aos_v0.exam.intake.pipeline import main as cli_main


def build_pdf(npages: int = 1, size=(300, 400), label: str = "Page") -> bytes:
    """A minimal-but-valid single-font PDF (renders with pypdfium2)."""
    header = b"%PDF-1.4\n"
    parts: list[bytes] = []

    def add(body: str) -> None:
        parts.append(body.encode())

    kids = " ".join(f"{3 + 3 * i} 0 R" for i in range(npages))
    add("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    add(f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {npages} >>\nendobj\n")
    for i in range(npages):
        p, fnt, ct = 3 + 3 * i, 4 + 3 * i, 5 + 3 * i
        text = f"({label} Page {i + 1})"
        stream = f"BT /F1 24 Tf 100 200 Td {text} Tj ET"
        add(
            f"{p} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} "
            f"{size[1]}] /Resources << /ProcSet [/PDF /Text] /Font << /F1 {fnt} "
            f"0 R >> >> /Contents {ct} 0 R >>\nendobj\n"
        )
        add(f"{fnt} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
        add(f"{ct} 0 obj\n<< /Length {len(stream)} >>\nstream\n{stream}\nendstream\nendobj\n")

    body = b"".join(parts)
    offsets = [0]
    pos = len(header)
    for part in parts:
        offsets.append(pos)
        pos += len(part)
    total = 2 + 3 * npages
    xref = f"xref\n0 {total}\n0000000000 65535 f \n".encode()
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = (
        f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{len(header) + len(body)}\n%%EOF"
    ).encode()
    return header + body + xref + trailer


def make_page(
    w: int = 1200,
    h: int = 1600,
    *,
    seed: int = 0,
    rotated=None,
    blur: float = 0.0,
    dark: bool = False,
    skew: float = 0.0,
    cropped: bool = False,
) -> Image.Image:
    """A synthetic answer-sheet-like page (header band + text lines)."""
    base = 60 if dark else 255
    im = Image.new("RGB", (w, h), (base, base, base))
    d = ImageDraw.Draw(im)
    header_len = 120 + seed * 40
    if not cropped:
        d.rectangle([80, 80, w - 80, 240 + seed * 40], fill=(40, 40, 40))
    else:
        d.rectangle([0, 0, w - 1, header_len], fill=(30, 30, 30))
    x0 = 0 if cropped else 80
    y0 = header_len + 80 if cropped else 320
    for i, y in enumerate(range(y0, h - 4 if cropped else h - 80, 70)):
        d.line(
            [x0, y, w - (4 if cropped else 80), y],
            fill=(5, 5, 5) if dark else (20 + (i * 7) % 40, 20, 20),
            width=9,
        )
    if skew:
        im = im.rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=(base, base, base))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if rotated:
        im = im.transpose(rotated)
    return im


class TestFormatDetection(unittest.TestCase):
    def test_sniff_png(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.png"
            make_page(seed=1).save(p)
            self.assertEqual(sniff_kind(p), "image")

    def test_sniff_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.pdf"
            p.write_bytes(build_pdf(1))
            self.assertEqual(sniff_kind(p), "pdf")

    def test_sniff_zip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.zip"
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("x.txt", "hi")
            self.assertEqual(sniff_kind(p), "zip")

    def test_sniff_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.txt"
            p.write_text("plain text")
            self.assertEqual(sniff_kind(p), "unknown")


class TestImageIntake(unittest.TestCase):
    def test_png_single_page(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sheet.png"
            make_page(seed=1).save(p)
            result = ingest(p, dpi=300)
            self.assertIsInstance(result, IntakeResult)
            self.assertEqual(result.summary.kind, "image")
            self.assertEqual(result.summary.page_count, 1)
            page = result.pages[0]
            self.assertEqual(page.meta.metrics.width, 1200)
            self.assertEqual(page.meta.metrics.height, 1600)
            self.assertEqual(page.meta.metrics.dpi, 300)
            self.assertEqual([], page.meta.issues)
            self.assertEqual(page.meta.quality_score, 1.0)
            self.assertEqual(page.meta.status.value, "ok")

    def test_jpeg_and_tiff_suffixes(self):
        for ext in ("jpg", "jpeg", "tiff"):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / f"sheet.{ext}"
                make_page(seed=2).save(p)
                result = ingest(p, dpi=300)
                self.assertEqual(result.summary.page_count, 1, ext)

    def test_multipage_tiff(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "multi.tiff"
            image = make_page(seed=1)
            image.save(
                p,
                save_all=True,
                append_images=[make_page(seed=2), make_page(seed=3)],
            )
            result = ingest(p, dpi=300)
            self.assertEqual(result.summary.page_count, 3)
            self.assertEqual(len(result.pages), 3)

    def test_page_documents_serializable(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sheet.png"
            make_page(seed=1).save(p)
            result = ingest(p, dpi=300)
            docs = result.page_documents()
            payload = json.dumps(docs)
            self.assertIn("issues", docs[0])
            self.assertTrue(payload)


class TestPdfIntake(unittest.TestCase):
    def test_pdf_page_count_and_size(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "booklet.pdf"
            p.write_bytes(build_pdf(3))
            result = ingest(p, dpi=300)
            self.assertEqual(result.summary.kind, "pdf")
            self.assertEqual(result.summary.page_count, 3)
            for page in result.pages:
                self.assertEqual(page.meta.metrics.width, round(300 * 300 / 72))
                self.assertEqual(page.meta.metrics.height, round(400 * 300 / 72))
                self.assertEqual(page.meta.metrics.dpi, 300)

    def test_pdf_declared_page_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "booklet.pdf"
            p.write_bytes(build_pdf(3))
            result = ingest(p, dpi=72)
            self.assertEqual([page.meta.page_no for page in result.pages], [1, 2, 3])

    def test_corrupted_pdf_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.pdf"
            p.write_bytes(b"%PDF-1.4 this is not a real pdf at all" + b"\x00" * 128)
            with self.assertRaises(IntakeError):
                ingest(p, dpi=300)


class TestZipAndFolderIntake(unittest.TestCase):
    def test_zip_archive_order_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = td / "book.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                for name in ("p3.png", "p1.png", "p2.png"):
                    make_page(seed=int(name[1])).save(td / name)
                    zf.write(td / name, name)
            result = ingest(zpath, dpi=300)
            self.assertEqual(result.summary.kind, "zip")
            self.assertEqual(
                [page.meta.member for page in result.pages],
                ["p3.png", "p1.png", "p2.png"],
            )
            self.assertEqual(
                [page.meta.page_no for page in result.pages], [3, 1, 2]
            )
            self.assertEqual(result.summary.out_of_order_pages, [1])

    def test_zip_skips_unsupported_members(self):
        with tempfile.TemporaryDirectory() as td:
            png = Path(td) / "a.png"
            make_page(seed=1).save(png)
            zpath = Path(td) / "mixed.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.write(png, "a.png")
                zf.writestr("notes.txt", "unused")
            result = ingest(zpath, dpi=300)
            self.assertEqual(result.summary.page_count, 1)
            self.assertEqual(result.summary.rejected_count, 1)
            self.assertTrue(any("notes.txt" in r for r in result.rejected))

    def test_folder_natural_sort_and_nested(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sub = td / "sub"
            sub.mkdir()
            for name in ("p10.png", "p2.png"):
                make_page(seed=int(name[1 if name[1] != "1" else 2])).save(td / name)
            make_page(seed=9).save(sub / "nested.png")
            result = ingest(td, dpi=300)
            self.assertEqual(result.summary.kind, "folder")
            self.assertEqual(result.summary.page_count, 3)
            self.assertEqual(result.pages[0].meta.member, "p2.png")
            self.assertEqual(result.pages[1].meta.member, "p10.png")
            self.assertEqual(result.pages[2].meta.member, "sub/nested.png")

    def test_duplicate_pages_detected(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            image = make_page(seed=1)
            image.save(td / "a.png")
            image.save(td / "b.png")
            result = ingest(td, dpi=300)
            self.assertEqual(result.summary.duplicate_pages, [1])
            self.assertIn(PageIssue.DUPLICATE, result.pages[1].meta.issues)
            self.assertEqual(result.pages[1].meta.metrics.duplicate_of, 0)


class TestQualityChecks(unittest.TestCase):
    def test_blank_page(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "blank.png"
            Image.new("RGB", (1200, 1600), "white").save(p)
            result = ingest(p, dpi=300)
            self.assertIn(PageIssue.BLANK, result.pages[0].meta.issues)
            self.assertEqual(result.pages[0].meta.status.value, "review")

    def test_blurred_page(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "blur.png"
            make_page(seed=1, blur=8).save(p)
            result = ingest(p, dpi=300)
            self.assertIn(PageIssue.BLURRY, result.pages[0].meta.issues)

    def test_dark_page(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dark.png"
            make_page(seed=1, dark=True).save(p)
            result = ingest(p, dpi=300)
            self.assertIn(PageIssue.EXCESSIVE_DARKNESS, result.pages[0].meta.issues)
            self.assertNotIn(PageIssue.SKEWED, result.pages[0].meta.issues)

    def test_rotated_ccw(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rot.png"
            make_page(seed=1, rotated=Image.Transpose.ROTATE_90).save(p)
            result = ingest(p, dpi=300)
            meta = result.pages[0].meta
            self.assertIn(PageIssue.ROTATED, meta.issues)
            self.assertEqual(meta.metrics.rotation_degrees, -90)
            self.assertGreaterEqual(meta.metrics.rotation_confidence, 0.5)

    def test_rotated_cw(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rot.png"
            make_page(seed=2, rotated=Image.Transpose.ROTATE_270).save(p)
            result = ingest(p, dpi=300)
            self.assertEqual(result.pages[0].meta.metrics.rotation_degrees, 90)

    def test_auto_rotate_restores_dimensions(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rot.png"
            make_page(seed=1, rotated=Image.Transpose.ROTATE_90).save(p)
            result = ingest(p, dpi=300, auto_rotate=True)
            meta = result.pages[0].meta
            self.assertIn("rotation", meta.adjusted)
            self.assertEqual((meta.metrics.width, meta.metrics.height), (1200, 1600))

    def test_skew_detected_and_corrected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "skew.png"
            make_page(seed=1, skew=3.0).save(p)
            result = ingest(p, dpi=300)
            meta = result.pages[0].meta
            self.assertIn(PageIssue.SKEWED, meta.issues)
            self.assertIn("deskew", meta.adjusted)
            self.assertAlmostEqual(meta.metrics.skew_degrees, -3.0, delta=0.5)
            residual = detect_skew(result.pages[0].image)
            self.assertLess(abs(residual), 1.0)

    def test_cropped_page(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "crop.png"
            make_page(seed=1, cropped=True).save(p)
            result = ingest(p, dpi=300)
            self.assertIn(PageIssue.CROPPED, result.pages[0].meta.issues)

    def test_low_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "low.png"
            make_page(seed=1).save(p)
            result = ingest(p, dpi=72)
            self.assertIn(PageIssue.LOW_RESOLUTION, result.pages[0].meta.issues)

    def test_washout_detection_heuristic(self):
        im = Image.new("RGB", (1200, 1600), (241, 241, 241))
        d = ImageDraw.Draw(im)
        for y in range(300, 1500, 60):
            d.line([80, y, 1120, y], fill=(233, 233, 233), width=6)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "wash.png"
            im.save(p)
            result = ingest(p, dpi=300)
            self.assertIn(PageIssue.EXCESSIVE_BRIGHTNESS, result.pages[0].meta.issues)
            self.assertNotIn(PageIssue.BLANK, result.pages[0].meta.issues)


class TestOrderingEdgeCases(unittest.TestCase):
    def test_missing_and_extra_pages(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for seed in (1, 2, 3):
                make_page(seed=seed).save(td / f"p{seed}.png")
            result = ingest(td, dpi=300, expected_page_count=5)
            self.assertEqual(result.summary.missing_page_count, 2)
            result = ingest(td, dpi=300, expected_page_count=2)
            self.assertEqual(result.summary.extra_page_count, 1)

    def test_mixed_page_sizes(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            make_page(seed=1).save(td / "a.png")
            make_page(seed=2, w=800, h=600).save(td / "b.png")
            result = ingest(td, dpi=300)
            self.assertTrue(result.summary.mixed_page_sizes)
            self.assertEqual(len(result.summary.unique_page_sizes), 2)

    def test_max_pages_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for seed in range(1, 6):
                make_page(seed=seed).save(td / f"p{seed}.png")
            result = ingest(td, dpi=100, max_pages=3)
            self.assertEqual(result.summary.page_count, 3)
            self.assertTrue(result.summary.truncated)

    def test_write_normalized_pages(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "src.png"
            out = td / "out"
            make_page(seed=1).save(src)
            result = ingest(src, dpi=300, write_pages_to=out)
            self.assertTrue((out / "page_0001.png").is_file())
            self.assertEqual(result.summary.page_count, 1)


class TestErrors(unittest.TestCase):
    def test_missing_source(self):
        with self.assertRaises(IntakeError):
            ingest("/nonexistent/nope.png")

    def test_unsupported_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "memo.txt"
            p.write_text("hello world")
            with self.assertRaises(UnsupportedFormatError):
                ingest(p)

    def test_magic_mismatch_extension(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fake.png"
            p.write_bytes(b"%PDF-1.4 fake")
            with self.assertRaises(IntakeError):
                ingest(p)

    def test_png_content_with_txt_extension_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sheet.txt"
            make_page(seed=1).save(p, format="PNG")
            with self.assertRaises(UnsupportedFormatError):
                ingest(p)

    def test_damaged_image(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            with self.assertRaises(IntakeError):
                ingest(p)

    def test_empty_folder(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(IntakeError):
                ingest(td)


class TestSummaryShapes(unittest.TestCase):
    def test_verdict_is_worst_page(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            make_page(seed=1).save(td / "good.png")
            Image.new("RGB", (300, 400), "white").save(td / "bad.png")
            result = ingest(td, dpi=72)
            self.assertEqual(result.summary.verdict.value, "rejected")

    def test_kind_labels(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            make_page(seed=1).save(td / "a.png")
            self.assertEqual(ingest(td, dpi=300).summary.kind, "folder")
            pdf = td / "a.pdf"
            pdf.write_bytes(build_pdf(1))
            self.assertEqual(ingest(pdf, dpi=300).summary.kind, "pdf")


class TestCli(unittest.TestCase):
    def test_cli_valid(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.png"
            make_page(seed=1).save(p)
            self.assertEqual(cli_main([str(p), "--dpi", "300"]), 0)

    def test_cli_json(self):
        import io
        import contextlib

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.png"
            make_page(seed=1).save(p)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli_main([str(p), "--dpi", "300", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("summary", payload)
            self.assertIn("pages", payload)

    def test_cli_error(self):
        self.assertEqual(cli_main(["/nonexistent/x.png"]), 1)


if __name__ == "__main__":
    unittest.main()