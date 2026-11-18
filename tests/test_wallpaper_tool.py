import argparse
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from wallpaper_tool import canvas_size, parse_ratio, process_folder


class WallpaperToolTests(unittest.TestCase):
    def test_canvas_expands_without_resizing(self):
        self.assertEqual(canvas_size(1920, 1080, 9 / 16), (1920, 3414))
        self.assertEqual(canvas_size(400, 800, 16 / 9), (1423, 800))
        self.assertEqual(canvas_size(800, 400, 16 / 9), (800, 450))
        self.assertEqual(canvas_size(1920, 1080, 1.777), (1920, 1081))

    def test_parse_ratio_accepts_pair_and_rejects_extreme_values(self):
        self.assertAlmostEqual(parse_ratio("16:9"), 16 / 9)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_ratio("0:5")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_ratio("100:1")

    def test_flat_background_is_extended_and_source_pixels_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            source.mkdir()
            image = Image.new("RGB", (20, 10), "navy")
            image.putpixel((10, 0), (255, 0, 0))
            image.save(source / "sample.png")

            result = process_folder(source, root / "output", 1, "png")

            self.assertEqual(result.processed, 1)
            self.assertFalse(result.errors)
            with Image.open(result.output_dir / "sample.png") as output:
                self.assertEqual(output.size, (20, 20))
                self.assertEqual(output.getpixel((10, 5)), (255, 0, 0))
                self.assertEqual(output.getpixel((0, 0)), (0, 0, 128))

    def test_transparency_is_preserved_in_png_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            source.mkdir()
            image = Image.new("RGBA", (2, 1), (255, 0, 0, 128))
            image.save(source / "transparent.png")

            result = process_folder(source, root / "output", 1, "png")

            with Image.open(result.output_dir / "transparent.png") as output:
                self.assertEqual(output.getpixel((1, 1)), (0, 0, 0, 0))
                self.assertEqual(output.getpixel((0, 0)), (255, 0, 0, 128))

    def test_existing_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("untouched", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                process_folder(source, output, 1)

            self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")


if __name__ == "__main__":
    unittest.main()
