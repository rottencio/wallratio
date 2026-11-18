"""Prepare wallpapers for a chosen aspect ratio without resizing source pixels."""

from __future__ import annotations

import argparse
import math
import queue
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

SUPPORTED_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
OUTPUT_FORMATS = {
    "png": ("PNG", ".png"),
    "jpeg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
}
MAX_OUTPUT_PIXELS = 200_000_000
MAX_OUTPUT_SIDE = 32_767


@dataclass(frozen=True)
class ProcessResult:
    output_dir: Path
    processed: int
    errors: tuple[str, ...]


class Ratio(float):
    def __new__(cls, value: float, label: str) -> "Ratio":
        instance = super().__new__(cls, value)
        instance.label = label
        return instance


def parse_ratio(value: str) -> Ratio:
    """Parse a positive decimal ratio or a width:height pair."""
    try:
        if ":" in value:
            width, height = value.split(":", maxsplit=1)
            ratio = float(width) / float(height)
            label = f"{width.strip()}-{height.strip()}"
        else:
            ratio = float(value)
            label = f"{ratio:.4g}".replace(".", "_")
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError(
            "La proporción debe ser un número positivo o tener el formato ancho:alto (por ejemplo, 16:9)."
        ) from exc
    if not math.isfinite(ratio) or not 0.1 <= ratio <= 10:
        raise argparse.ArgumentTypeError("La proporción debe estar entre 0,1 y 10.")
    return Ratio(ratio, label)


def canvas_size(width: int, height: int, ratio: float) -> tuple[int, int]:
    """Return the smallest canvas with the requested ratio that contains the image."""
    if width / height < ratio:
        return math.ceil(height * ratio), height
    return width, math.ceil(width / ratio)


def _solid_edge_profile(
    image: Image.Image, horizontal_padding: bool
) -> tuple[tuple[int, int, int], ...] | None:
    """Find matching opposite edge colors, including smooth color gradients."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    if horizontal_padding:
        first_edge = [rgb.getpixel((0, y)) for y in range(height)]
        second_edge = [rgb.getpixel((width - 1, y)) for y in range(height)]
    else:
        first_edge = [rgb.getpixel((x, 0)) for x in range(width)]
        second_edge = [rgb.getpixel((x, height - 1)) for x in range(width)]

    matching = sum(
        max(abs(first[channel] - second[channel]) for channel in range(3)) <= 12
        for first, second in zip(first_edge, second_edge)
    )
    if matching / len(first_edge) < 0.92:
        return None
    return tuple(
        tuple((first[channel] + second[channel] + 1) // 2 for channel in range(3))
        for first, second in zip(first_edge, second_edge)
    )


def _has_black_background(image: Image.Image, horizontal_padding: bool) -> bool:
    """Detect a mostly black or neutral-charcoal background, not dark saturated colors."""
    rgb = image.convert("RGB")
    channel_max = ImageChops.lighter(
        ImageChops.lighter(rgb.getchannel("R"), rgb.getchannel("G")),
        rgb.getchannel("B"),
    )
    channel_min = ImageChops.darker(
        ImageChops.darker(rgb.getchannel("R"), rgb.getchannel("G")),
        rgb.getchannel("B"),
    )
    channel_spread = ImageChops.difference(channel_max, channel_min)
    dark_pixels = Image.eval(channel_max, lambda value: 255 if value <= 64 else 0)
    neutral_pixels = Image.eval(channel_spread, lambda value: 255 if value <= 20 else 0)
    near_black = ImageChops.darker(dark_pixels, neutral_pixels)
    width, height = near_black.size
    band = min(16, max(1, round(min(width, height) * 0.03)))
    if horizontal_padding:
        edge_boxes = ((0, 0, band, height), (width - band, 0, width, height))
    else:
        edge_boxes = ((0, 0, width, band), (0, height - band, width, height))

    def dark_fraction(box: tuple[int, int, int, int]) -> float:
        histogram = near_black.crop(box).histogram()
        return histogram[255] / sum(histogram)

    edge_fractions = [dark_fraction(box) for box in edge_boxes]
    whole_image_dark_fraction = dark_fraction((0, 0, width, height))
    return (
        whole_image_dark_fraction >= 0.7 and max(edge_fractions) >= 0.6
    ) or (
        whole_image_dark_fraction >= 0.5
        and (
            min(edge_fractions) >= 0.75
            or (min(edge_fractions) >= 0.65 and max(edge_fractions) >= 0.95)
        )
    )


def _black_background(
    image: Image.Image, size: tuple[int, int], source_position: tuple[int, int]
) -> Image.Image:
    """Extend dark artwork with black and softly blend only non-black edge details."""
    background = Image.new("RGB", size, (0, 0, 0))
    source = image.convert("RGB")
    source_x, source_y = source_position
    width, height = source.size
    target_width, target_height = size
    feather_size = max(2, round(min(source.size) * 0.01))

    if target_width > width:
        for is_left, padding in ((True, source_x), (False, target_width - source_x - width)):
            feather = min(padding, feather_size)
            if not feather:
                continue
            edge_x = 0 if is_left else width - 1
            edge = source.crop((edge_x, 0, edge_x + 1, height))
            edge = edge.resize((feather, height), Image.Resampling.NEAREST)
            mask = _feather_mask(feather, feather, is_left, True, height)
            x = source_x - feather if is_left else source_x + width
            box = (x, source_y, x + feather, source_y + height)
            background.paste(Image.composite(edge, background.crop(box), mask), box[:2])
    else:
        for is_top, padding in ((True, source_y), (False, target_height - source_y - height)):
            feather = min(padding, feather_size)
            if not feather:
                continue
            edge_y = 0 if is_top else height - 1
            edge = source.crop((0, edge_y, width, edge_y + 1))
            edge = edge.resize((width, feather), Image.Resampling.NEAREST)
            mask = _feather_mask(feather, feather, is_top, False, width)
            y = source_y - feather if is_top else source_y + height
            box = (source_x, y, source_x + width, y + feather)
            background.paste(Image.composite(edge, background.crop(box), mask), box[:2])
    return background


def _feather_mask(
    length: int, feather: int, reverse: bool, horizontal: bool, span: int
) -> Image.Image:
    values = []
    for position in range(length):
        distance = length - 1 - position if reverse else position
        amount = min(distance / feather, 1)
        weight = 1 - amount * amount * (3 - 2 * amount)
        values.append(round(weight * 255))
    line_size = (length, 1) if horizontal else (1, length)
    mask = Image.new("L", line_size)
    mask.putdata(values)
    size = (length, span) if horizontal else (span, length)
    return mask.resize(size, Image.Resampling.NEAREST)


def _edge_matte_background(
    image: Image.Image, size: tuple[int, int], source_position: tuple[int, int]
) -> Image.Image:
    """Use a quiet edge-derived matte with a short, softly blurred transition."""
    image = image.convert("RGB")
    source_x, source_y = source_position
    width, height = image.size
    target_width, target_height = size
    background = Image.new("RGB", size, (0, 0, 0))
    matte_axis_length = width if target_width > width else height
    sample_depth = min(matte_axis_length, 16, max(3, round(matte_axis_length * 0.05)))
    feather_size = max(2, round(min(image.size) * 0.02))

    def extend_side(
        padding: int,
        sample_box: tuple[int, int, int, int],
        destination: tuple[int, int, int, int],
        horizontal: bool,
        reverse: bool,
    ) -> None:
        if not padding:
            return
        edge_sample = image.crop(sample_box)
        matte_color = tuple(round(channel) for channel in ImageStat.Stat(edge_sample).median)
        background.paste(matte_color, destination)

        feather = min(padding, feather_size)
        if not feather:
            return
        edge = image.crop(sample_box)
        if horizontal:
            edge = edge.resize((feather, height), Image.Resampling.LANCZOS)
            edge = ImageOps.mirror(edge)
            edge = edge.filter(ImageFilter.GaussianBlur(radius=max(1, feather * 0.2)))
            mask = _feather_mask(feather, feather, reverse, True, height)
        else:
            edge = edge.resize((width, feather), Image.Resampling.LANCZOS)
            edge = ImageOps.flip(edge)
            edge = edge.filter(ImageFilter.GaussianBlur(radius=max(1, feather * 0.2)))
            mask = _feather_mask(feather, feather, reverse, False, width)
        if horizontal:
            boundary_x = feather - 1 if reverse else 0
            boundary = image.crop((0 if reverse else width - 1, 0, 1 if reverse else width, height))
            edge.paste(boundary, (boundary_x, 0))
        else:
            boundary_y = feather - 1 if reverse else 0
            boundary_box = (0, 0, width, 1) if reverse else (0, height - 1, width, height)
            boundary = image.crop(boundary_box)
            edge.paste(boundary, (0, boundary_y))
        feather_box = destination
        if horizontal:
            feather_box = (
                destination[2] - feather if reverse else destination[0],
                destination[1],
                destination[2] if reverse else destination[0] + feather,
                destination[3],
            )
        else:
            feather_box = (
                destination[0],
                destination[3] - feather if reverse else destination[1],
                destination[2],
                destination[3] if reverse else destination[1] + feather,
            )
        region = background.crop(feather_box)
        background.paste(Image.composite(edge, region, mask), feather_box[:2])

    if target_width > width:
        left_padding = source_x
        right_padding = target_width - source_x - width
        extend_side(
            left_padding,
            (0, 0, sample_depth, height),
            (0, source_y, source_x, source_y + height),
            True,
            True,
        )
        extend_side(
            right_padding,
            (width - sample_depth, 0, width, height),
            (source_x + width, source_y, target_width, source_y + height),
            True,
            False,
        )
    elif target_height > height:
        top_padding = source_y
        bottom_padding = target_height - source_y - height
        extend_side(
            top_padding,
            (0, 0, width, sample_depth),
            (source_x, 0, source_x + width, source_y),
            False,
            True,
        )
        extend_side(
            bottom_padding,
            (0, height - sample_depth, width, height),
            (source_x, source_y + height, source_x + width, target_height),
            False,
            False,
        )
    return background


def _prepare_image(image: Image.Image, ratio: float) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    image.load()
    width, height = image.size
    target_size = canvas_size(width, height, ratio)
    target_width, target_height = target_size
    if target_width * target_height > MAX_OUTPUT_PIXELS or max(target_size) > MAX_OUTPUT_SIDE:
        raise ValueError(
            f"El lienzo resultante ({target_width} x {target_height}) supera el límite seguro "
            f"({MAX_OUTPUT_PIXELS:,} píxeles o {MAX_OUTPUT_SIDE:,} por lado)."
        )

    left = (target_width - width) // 2
    top = (target_height - height) // 2
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    source = image.convert("RGBA" if has_alpha else "RGB")

    if has_alpha:
        canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
    else:
        horizontal_padding = target_width > width
        if _has_black_background(source, horizontal_padding):
            canvas = _black_background(source, target_size, (left, top))
        else:
            flat_profile = _solid_edge_profile(source, horizontal_padding)
            if flat_profile is not None:
                profile_size = (1, height) if horizontal_padding else (width, 1)
                profile = Image.new("RGB", profile_size)
                profile.putdata(flat_profile)
                canvas = profile.resize(target_size, Image.Resampling.NEAREST)
            else:
                canvas = _edge_matte_background(source, target_size, (left, top))

    canvas.paste(source, (left, top))
    return canvas


def _output_path(source: Path, source_dir: Path, output_dir: Path, output_format: str) -> Path:
    relative = source.relative_to(source_dir)
    if output_format == "original":
        return output_dir / relative
    _, extension = OUTPUT_FORMATS[output_format]
    return (output_dir / relative).with_suffix(extension)


def _path_key(path: Path) -> str:
    return path.as_posix().casefold()


def _plan_output_paths(
    files: list[Path], source_dir: Path, output_dir: Path, output_format: str
) -> dict[Path, Path]:
    base_paths = {source: _output_path(source, source_dir, output_dir, output_format) for source in files}
    collisions: dict[str, list[Path]] = defaultdict(list)
    for source, target in base_paths.items():
        collisions[_path_key(target)].append(source)

    planned: dict[Path, Path] = {}
    occupied: set[str] = set()
    for source in files:
        target = base_paths[source]
        if len(collisions[_path_key(target)]) > 1:
            source_type = source.suffix.lower().lstrip(".")
            target = target.with_name(f"{target.stem}_{source_type}{target.suffix}")

        candidate = target
        suffix = 2
        while _path_key(candidate) in occupied:
            candidate = target.with_name(f"{target.stem}_{suffix}{target.suffix}")
            suffix += 1
        planned[source] = candidate
        occupied.add(_path_key(candidate))
    return planned


def default_output_dir(
    source_dir: Path,
    ratio: float,
    output_format: str = "png",
) -> Path:
    ratio_label = getattr(ratio, "label", f"{ratio:.4g}".replace(".", "_"))
    format_label = "ORIGINAL" if output_format == "original" else output_format.upper()
    base = source_dir.parent / f"{source_dir.name}_{ratio_label}_{format_label}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{suffix}")
        suffix += 1
    return candidate


def process_folder(
    source_dir: Path,
    output_dir: Path,
    ratio: float,
    output_format: str = "png",
    progress: Callable[[int, int, str], None] | None = None,
) -> ProcessResult:
    source_dir = source_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError(f"La carpeta de entrada no existe: {source_dir}")
    if output_format not in {"original", *OUTPUT_FORMATS}:
        raise ValueError(f"Formato de salida no admitido: {output_format}")
    if not 0.1 <= ratio <= 10 or not math.isfinite(ratio):
        raise ValueError("La proporción debe estar entre 0,1 y 10.")
    if output_dir.exists():
        raise FileExistsError(
            f"La carpeta de salida ya existe y no se sobrescribirá: {output_dir}"
        )
    if output_dir == source_dir or source_dir in output_dir.parents:
        raise ValueError("La carpeta de salida no puede estar dentro de la carpeta de entrada.")

    files = sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not files:
        raise ValueError(f"No se encontraron imágenes compatibles en {source_dir}")

    output_paths = _plan_output_paths(files, source_dir, output_dir, output_format)

    output_dir.mkdir(parents=True)
    errors: list[str] = []
    processed = 0
    for index, source in enumerate(files, start=1):
        target = output_paths[source]
        if progress:
            progress(index, len(files), source.relative_to(source_dir).as_posix())
        try:
            with Image.open(source) as opened:
                if getattr(opened, "n_frames", 1) > 1:
                    raise ValueError("las imágenes animadas no se admiten; no se ha guardado ningún fotograma")
                image = _prepare_image(opened, ratio)
                target.parent.mkdir(parents=True, exist_ok=True)
                save_format = (
                    opened.format
                    if output_format == "original"
                    else OUTPUT_FORMATS[output_format][0]
                )
                if save_format == "JPEG" and image.mode == "RGBA":
                    flattened = Image.new("RGB", image.size, "white")
                    flattened.paste(image, mask=image.getchannel("A"))
                    image = flattened
                save_options = {}
                icc_profile = opened.info.get("icc_profile")
                if icc_profile:
                    save_options["icc_profile"] = icc_profile
                if save_format == "JPEG":
                    save_options["quality"] = 95
                    save_options["optimize"] = True
                image.save(target, format=save_format, **save_options)
                processed += 1
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            errors.append(f"{source.relative_to(source_dir)}: {exc}")

    return ProcessResult(output_dir, processed, tuple(errors))


def _run_cli() -> int:
    parser = argparse.ArgumentParser(
        description="Adapta wallpapers a una proporción sin ampliar, reducir ni recortar los píxeles originales."
    )
    parser.add_argument("input", nargs="?", type=Path, help="Carpeta con las imágenes")
    parser.add_argument(
        "-r", "--ratio", type=parse_ratio, help="Proporción objetivo, por ejemplo 16:9, 9:16 o 1:1"
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("png", "jpeg", "webp", "original"),
        default="png",
        help="Formato de salida (predeterminado: png; original conserva el formato de cada archivo)",
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="Carpeta de salida nueva; por defecto se crea junto a la entrada"
    )
    parser.add_argument("--gui", action="store_true", help="Abrir la interfaz gráfica")
    args = parser.parse_args()

    if args.gui or (not args.input and not args.ratio):
        return launch_gui()
    if not args.input or args.ratio is None:
        parser.error("indica la carpeta y la proporción, o usa --gui.")

    source_dir = args.input.expanduser().resolve()
    output_dir = args.output or default_output_dir(source_dir, args.ratio, args.format)
    try:
        result = process_folder(source_dir, output_dir, args.ratio, args.format)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"Imágenes procesadas: {result.processed}")
    print(f"Carpeta de salida: {result.output_dir}")
    if result.errors:
        print(f"Imágenes con errores: {len(result.errors)}")
        for error in result.errors:
            print(f"  - {error}")
        return 1
    return 0


def launch_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Adaptador de wallpapers")
    root.resizable(False, False)

    input_var = tk.StringVar()
    output_var = tk.StringVar()
    ratio_var = tk.StringVar(value="16:9")
    format_var = tk.StringVar(value="png")
    status_var = tk.StringVar(value="Elige una carpeta y la proporción deseada.")

    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)

    def choose_input() -> None:
        selected = filedialog.askdirectory(title="Selecciona la carpeta de imágenes")
        if selected:
            input_var.set(selected)
            try:
                ratio = parse_ratio(ratio_var.get())
                output_var.set(str(default_output_dir(Path(selected), ratio, format_var.get())))
            except argparse.ArgumentTypeError:
                pass

    def choose_output() -> None:
        selected = filedialog.askdirectory(title="Selecciona la carpeta contenedora de salida")
        if selected:
            parent = Path(selected)
            try:
                ratio = parse_ratio(ratio_var.get())
                output_var.set(str(default_output_dir(parent / "wallpapers", ratio, format_var.get())))
            except argparse.ArgumentTypeError:
                output_var.set(str(parent / "wallpapers_procesados"))

    def refresh_output_name(*_args: object) -> None:
        if input_var.get():
            try:
                ratio = parse_ratio(ratio_var.get())
                output_var.set(
                    str(default_output_dir(Path(input_var.get()), ratio, format_var.get()))
                )
            except argparse.ArgumentTypeError:
                pass

    ratio_var.trace_add("write", refresh_output_name)
    format_var.trace_add("write", refresh_output_name)

    ttk.Label(frame, text="Carpeta de imágenes").grid(row=0, column=0, sticky="w", pady=5)
    ttk.Entry(frame, textvariable=input_var, width=48).grid(row=0, column=1, padx=8)
    ttk.Button(frame, text="Examinar...", command=choose_input).grid(row=0, column=2)

    ttk.Label(frame, text="Carpeta de salida nueva").grid(row=1, column=0, sticky="w", pady=5)
    ttk.Entry(frame, textvariable=output_var, width=48).grid(row=1, column=1, padx=8)
    ttk.Button(frame, text="Examinar...", command=choose_output).grid(row=1, column=2)

    ttk.Label(frame, text="Proporción").grid(row=2, column=0, sticky="w", pady=5)
    ratio_box = ttk.Combobox(
        frame,
        textvariable=ratio_var,
        values=("16:9", "9:16", "4:3", "3:4", "1:1", "21:9"),
        width=16,
    )
    ratio_box.grid(row=2, column=1, sticky="w", padx=8)
    ttk.Label(frame, text="También puedes escribir otra (ancho:alto).").grid(
        row=2, column=2, sticky="w"
    )

    ttk.Label(frame, text="Formato").grid(row=3, column=0, sticky="w", pady=5)
    ttk.Combobox(
        frame,
        textvariable=format_var,
        values=("png", "jpeg", "webp", "original"),
        state="readonly",
        width=16,
    ).grid(row=3, column=1, sticky="w", padx=8)

    progress = ttk.Progressbar(frame, mode="determinate", length=430)
    progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(14, 4))
    ttk.Label(frame, textvariable=status_var, wraplength=560).grid(
        row=5, column=0, columnspan=3, sticky="w", pady=4
    )

    events: queue.Queue[tuple[str, object]] = queue.Queue()

    def start_processing() -> None:
        if not input_var.get() or not output_var.get():
            messagebox.showerror("Faltan datos", "Selecciona las carpetas de entrada y salida.")
            return
        try:
            ratio = parse_ratio(ratio_var.get())
        except argparse.ArgumentTypeError as exc:
            messagebox.showerror("Proporción no válida", str(exc))
            return
        start_button.configure(state="disabled")
        status_var.set("Preparando imágenes...")

        def worker() -> None:
            try:
                result = process_folder(
                    Path(input_var.get()),
                    Path(output_var.get()),
                    ratio,
                    format_var.get(),
                    lambda current, total, name: events.put(("progress", (current, total, name))),
                )
                events.put(("done", result))
            except (OSError, ValueError) as exc:
                events.put(("failure", exc))

        threading.Thread(target=worker, daemon=True).start()

    def poll_events() -> None:
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "progress":
                    current, total, name = payload
                    progress.configure(maximum=total, value=current)
                    status_var.set(f"Procesando {current}/{total}: {name}")
                elif kind == "done":
                    result = payload
                    start_button.configure(state="normal")
                    status_var.set(f"Terminado: {result.processed} imágenes.")
                    if result.errors:
                        details = "\n".join(result.errors[:10])
                        remaining = len(result.errors) - 10
                        if remaining > 0:
                            details += f"\n... y {remaining} errores más."
                        messagebox.showwarning(
                            "Proceso completado con errores",
                            f"Se procesaron {result.processed} imágenes.\n"
                            f"Salida: {result.output_dir}\n\n{details}",
                        )
                    else:
                        messagebox.showinfo(
                            "Proceso completado",
                            f"Se procesaron {result.processed} imágenes.\nSalida: {result.output_dir}",
                        )
                elif kind == "failure":
                    start_button.configure(state="normal")
                    status_var.set("El proceso no pudo completarse.")
                    messagebox.showerror("Error", str(payload))
        except queue.Empty:
            pass
        root.after(100, poll_events)

    start_button = ttk.Button(frame, text="Crear wallpapers", command=start_processing)
    start_button.grid(row=6, column=0, columnspan=3, pady=(12, 0))
    root.after(100, poll_events)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
