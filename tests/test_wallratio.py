# SPDX-License-Identifier: GPL-3.0-only
import argparse
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from wallratio import (
    _prepare_image,
    canvas_size,
    default_output_dir,
    parse_ratio,
    process_folder,
)


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

    def test_default_output_dir_uses_ratio_and_format_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "wallpapersEjemplo"
            source.mkdir()

            png_output = default_output_dir(source, parse_ratio("16:9"), "png")
            original_output = default_output_dir(source, parse_ratio("16:9"), "original")

            self.assertEqual(png_output.name, "wallpapersEjemplo_16-9_PNG")
            original_output.mkdir()
            suffixed_output = default_output_dir(source, parse_ratio("16:9"), "original")
            self.assertEqual(suffixed_output.name, "wallpapersEjemplo_16-9_ORIGINAL_2")

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

    def test_matching_edge_gradient_is_extended_instead_of_blurred(self):
        image = Image.new("RGB", (40, 30))
        for y in range(image.height):
            image.paste((255 - y * 3, 0, 0), (0, y, image.width, y + 1))
        image.putpixel((20, 15), (0, 255, 0))

        output = _prepare_image(image, 2)

        self.assertEqual(output.size, (60, 30))
        self.assertEqual(output.getpixel((0, 15)), image.getpixel((0, 15)))
        self.assertEqual(output.getpixel((59, 15)), image.getpixel((39, 15)))
        self.assertEqual(output.getpixel((30, 15)), (0, 255, 0))

    def test_dark_background_is_extended_as_black_not_blurred(self):
        image = Image.new("RGB", (40, 30), "black")
        for y in range(10, 17):
            for x in range(10, 40):
                image.putpixel((x, y), (240, 120, 20))

        output = _prepare_image(image, 2)

        self.assertEqual(output.size, (60, 30))
        self.assertEqual(output.getpixel((0, 15)), (0, 0, 0))
        self.assertEqual(output.getpixel((59, 15)), (0, 0, 0))
        self.assertEqual(output.getpixel((50, 15)), image.getpixel((39, 15)))
        self.assertEqual(output.getpixel((10, 15)), image.getpixel((0, 15)))

    def test_dark_saturated_background_keeps_its_color(self):
        image = Image.new("RGB", (40, 30), (18, 16, 48))
        image.putpixel((20, 15), (255, 0, 0))

        output = _prepare_image(image, 2)

        self.assertEqual(output.getpixel((0, 15)), (18, 16, 48))

    def test_complex_edges_use_matte_with_a_narrow_soft_transition(self):
        image = Image.new("RGB", (40, 30), (40, 180, 90))
        for y in range(image.height):
            image.putpixel((0, y), (255, 0, 0))
            image.putpixel((39, y), (0, 0, 255))

        output = _prepare_image(image, 2)

        self.assertEqual(output.getpixel((9, 15)), image.getpixel((0, 15)))
        self.assertEqual(output.getpixel((50, 15)), image.getpixel((39, 15)))
        self.assertNotEqual(output.getpixel((0, 15)), image.getpixel((0, 15)))
        self.assertEqual(output.getpixel((1, 15)), (40, 180, 90))
        self.assertEqual(output.getpixel((10, 15)), image.getpixel((0, 15)))

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

    def test_duplicate_stems_get_source_format_in_output_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            source.mkdir()
            Image.new("RGB", (12, 8), "red").save(source / "wallpaper.jpg")
            Image.new("RGB", (12, 8), "blue").save(source / "wallpaper.png")

            result = process_folder(source, root / "output", 16 / 9, "png")

            self.assertEqual(result.processed, 2)
            self.assertFalse(result.errors)
            self.assertEqual(
                {path.name for path in result.output_dir.iterdir()},
                {"wallpaper_jpg.png", "wallpaper_png.png"},
            )
            for path in result.output_dir.iterdir():
                with Image.open(path) as output:
                    self.assertEqual(output.format, "PNG")


if __name__ == "__main__":
    unittest.main()
