"""Prepare wallpapers for a chosen aspect ratio without resizing source pixels."""

from __future__ import annotations

import argparse
import math
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

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


def parse_ratio(value: str) -> float:
    """Parse a positive decimal ratio or a width:height pair."""
    try:
        if ":" in value:
            width, height = value.split(":", maxsplit=1)
            ratio = float(width) / float(height)
        else:
            ratio = float(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError(
            "La proporción debe ser un número positivo o tener el formato ancho:alto (por ejemplo, 16:9)."
        ) from exc
    if not math.isfinite(ratio) or not 0.1 <= ratio <= 10:
        raise argparse.ArgumentTypeError("La proporción debe estar entre 0,1 y 10.")
    return ratio


def canvas_size(width: int, height: int, ratio: float) -> tuple[int, int]:
    """Return the smallest canvas with the requested ratio that contains the image."""
    if width / height < ratio:
        return math.ceil(height * ratio), height
    return width, math.ceil(width / ratio)


def _solid_edge_color(image: Image.Image, horizontal_padding: bool) -> tuple[int, int, int] | None:
    """Find a nearly uniform color along the edges that will be extended."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    edge_pixels: list[tuple[int, int, int]] = []
    if horizontal_padding:
        edge_pixels.extend(rgb.getpixel((x, y)) for x in (0, width - 1) for y in range(height))
    else:
        edge_pixels.extend(rgb.getpixel((x, y)) for y in (0, height - 1) for x in range(width))

    channels = list(zip(*edge_pixels))
    median = tuple(sorted(channel)[len(channel) // 2] for channel in channels)
    matching = sum(
        max(abs(pixel[channel] - median[channel]) for channel in range(3)) <= 12
        for pixel in edge_pixels
    )
    if matching / len(edge_pixels) < 0.92:
        return None
    return median


def _smooth_background(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Create a soft, full-bleed backdrop; the original pixels are overlaid unchanged."""
    background = ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    blur_radius = max(12, min(size) * 0.035)
    return background.filter(ImageFilter.GaussianBlur(radius=blur_radius))


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
        flat_color = _solid_edge_color(source, horizontal_padding)
        if flat_color is not None:
            canvas = Image.new("RGB", target_size, flat_color)
        else:
            canvas = _smooth_background(source, target_size)

    canvas.paste(source, (left, top))
    return canvas


def _output_path(source: Path, source_dir: Path, output_dir: Path, output_format: str) -> Path:
    relative = source.relative_to(source_dir)
    if output_format == "original":
        return output_dir / relative
    _, extension = OUTPUT_FORMATS[output_format]
    return (output_dir / relative).with_suffix(extension)


def default_output_dir(source_dir: Path, ratio: float) -> Path:
    ratio_label = f"{ratio:.4g}".replace(".", "_")
    base = source_dir.parent / f"{source_dir.name}_wallpapers_{ratio_label}"
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

    planned: dict[Path, Path] = {}
    for source in files:
        target = _output_path(source, source_dir, output_dir, output_format)
        if target in planned:
            raise ValueError(
                f"Conflicto de nombres: {planned[target].relative_to(source_dir)} y "
                f"{source.relative_to(source_dir)} producirían el mismo archivo ({target.name})."
            )
        planned[target] = source

    output_dir.mkdir(parents=True)
    errors: list[str] = []
    processed = 0
    for index, source in enumerate(files, start=1):
        target = _output_path(source, source_dir, output_dir, output_format)
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
    output_dir = args.output or default_output_dir(source_dir, args.ratio)
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
                output_var.set(str(default_output_dir(Path(selected), ratio)))
            except argparse.ArgumentTypeError:
                pass

    def choose_output() -> None:
        selected = filedialog.askdirectory(title="Selecciona la carpeta contenedora de salida")
        if selected:
            parent = Path(selected)
            try:
                ratio = parse_ratio(ratio_var.get())
                output_var.set(str(default_output_dir(parent / "wallpapers", ratio)))
            except argparse.ArgumentTypeError:
                output_var.set(str(parent / "wallpapers_procesados"))

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
