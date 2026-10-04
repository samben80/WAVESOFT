#!/usr/bin/env python3
"""Compresse des photos sous une taille maximale (50 Ko par défaut) sans toucher
à leur résolution, puis les enregistre renommées dans un dossier de destination.

Exemple :
    python photocompress.py ./photos ./sortie --pattern "produit_{n:03}"
"""

from __future__ import annotations

import argparse
import io
import multiprocessing
import re
import sys
import unicodedata
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}

# Format Pillow et extension de sortie par défaut pour chaque format.
FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
# Formats qu'on ne sait pas compresser efficacement : convertis en JPEG (ou PNG si transparence).
CONVERTED_EXTENSIONS = {".bmp", ".tif", ".tiff", ".gif"}

MAX_QUALITY = 95
PNG_PALETTE_SIZES = (256, 128, 64, 32, 16, 8, 4, 2)


@dataclass
class EncodeResult:
    data: bytes
    format: str
    detail: str
    fits: bool


@dataclass
class Job:
    source: Path
    destination: Path


@dataclass
class JobResult:
    source: Path
    destination: Path
    original_size: int
    final_size: int
    detail: str
    fits: bool
    error: str | None = None


# --------------------------------------------------------------------------- #
# Encodage
# --------------------------------------------------------------------------- #

def has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA", "PA"):
        return img.getextrema()[-1][0] < 255
    if img.mode == "P" and "transparency" in img.info:
        return True
    return False


def to_rgb(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """Aplatit la transparence sur un fond blanc (le JPEG ne gère pas l'alpha)."""
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA", "PA", "P"):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return img.convert("RGB")


def _encode(img: Image.Image, fmt: str, **params) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **params)
    return buf.getvalue()


def _search_quality(encode, max_bytes: int, min_quality: int) -> tuple[bytes, int, bool]:
    """Recherche dichotomique de la meilleure qualité qui tient sous max_bytes."""
    best: tuple[bytes, int] | None = None
    lo, hi = min_quality, MAX_QUALITY
    while lo <= hi:
        q = (lo + hi) // 2
        data = encode(q)
        if len(data) <= max_bytes:
            best = (data, q)
            lo = q + 1
        else:
            hi = q - 1
    if best is not None:
        return best[0], best[1], True
    return encode(min_quality), min_quality, False


def encode_jpeg(img: Image.Image, max_bytes: int, min_quality: int) -> EncodeResult:
    rgb = to_rgb(img)

    def encode(q: int) -> bytes:
        return _encode(rgb, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)

    data, q, fits = _search_quality(encode, max_bytes, min_quality)
    return EncodeResult(data, "JPEG", f"JPEG qualité {q}", fits)


def encode_webp(img: Image.Image, max_bytes: int, min_quality: int) -> EncodeResult:
    src = img if img.mode in ("RGB", "RGBA") else img.convert("RGBA" if has_alpha(img) else "RGB")

    def encode(q: int) -> bytes:
        return _encode(src, "WEBP", quality=q, method=6)

    data, q, fits = _search_quality(encode, max_bytes, min_quality)
    return EncodeResult(data, "WEBP", f"WebP qualité {q}", fits)


def encode_png(img: Image.Image, max_bytes: int) -> EncodeResult:
    """PNG : d'abord sans perte, puis réduction de la palette de couleurs."""
    alpha = has_alpha(img)
    src = img.convert("RGBA" if alpha else "RGB") if img.mode not in ("RGB", "RGBA", "L") else img

    data = _encode(src, "PNG", optimize=True)
    if len(data) <= max_bytes:
        return EncodeResult(data, "PNG", "PNG sans perte", True)

    method = Image.Quantize.FASTOCTREE if src.mode == "RGBA" else Image.Quantize.MEDIANCUT
    base = src.convert("RGB") if src.mode == "L" else src

    def encode(colors: int) -> bytes:
        quantized = base.quantize(colors=colors, method=method, dither=Image.Dither.FLOYDSTEINBERG)
        return _encode(quantized, "PNG", optimize=True)

    # Recherche dichotomique du plus grand nombre de couleurs qui tient sous la limite.
    best: tuple[bytes, int] | None = None
    lo, hi = 0, len(PNG_PALETTE_SIZES) - 1  # indices : 256 couleurs ... 2 couleurs
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = encode(PNG_PALETTE_SIZES[mid])
        if len(candidate) <= max_bytes:
            best = (candidate, PNG_PALETTE_SIZES[mid])
            hi = mid - 1
        else:
            lo = mid + 1
    if best is not None:
        return EncodeResult(best[0], "PNG", f"PNG {best[1]} couleurs", True)
    colors = PNG_PALETTE_SIZES[-1]
    return EncodeResult(encode(colors), "PNG", f"PNG {colors} couleurs", False)


def encode_image(img: Image.Image, fmt: str, max_bytes: int, min_quality: int) -> EncodeResult:
    if fmt == "JPEG":
        return encode_jpeg(img, max_bytes, min_quality)
    if fmt == "WEBP":
        return encode_webp(img, max_bytes, min_quality)
    if fmt == "PNG":
        return encode_png(img, max_bytes)
    raise ValueError(f"Format de sortie non géré : {fmt}")


def output_format(source: Path, img: Image.Image, requested: str) -> str:
    if requested != "auto":
        return {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[requested]
    ext = source.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "JPEG"
    if ext == ".png":
        return "PNG"
    if ext == ".webp":
        return "WEBP"
    return "PNG" if has_alpha(img) else "JPEG"


def fallback_format(img: Image.Image, current: str) -> str | None:
    """Format plus compact à essayer quand le format d'origine ne passe pas sous la limite."""
    if current == "PNG":
        return "WEBP" if has_alpha(img) else "JPEG"
    if current == "JPEG":
        return "WEBP"
    return None


def compress_file(
    source: Path,
    max_bytes: int,
    min_quality: int,
    requested_format: str = "auto",
    allow_fallback: bool = True,
) -> EncodeResult:
    """Retourne le fichier compressé (en mémoire). La résolution n'est jamais modifiée."""
    with Image.open(source) as opened:
        opened.seek(0)  # GIF/TIFF multi-images : on garde la première
        # Applique la rotation EXIF, car les métadonnées (dont l'orientation) sont supprimées.
        img = ImageOps.exif_transpose(opened)
        img.load()

    fmt = output_format(source, img, requested_format)

    # Déjà assez léger et pas de changement de format : copie à l'identique.
    original_size = source.stat().st_size
    same_format = FORMAT_EXTENSIONS[fmt] == normalized_extension(source.suffix)
    if same_format and original_size <= max_bytes:
        return EncodeResult(source.read_bytes(), fmt, "déjà sous la limite (copie)", True)

    result = encode_image(img, fmt, max_bytes, min_quality)
    if not result.fits and allow_fallback and requested_format == "auto":
        alt = fallback_format(img, fmt)
        if alt:
            alt_result = encode_image(img, alt, max_bytes, min_quality)
            if alt_result.fits:  # on ne change de format que si ça permet de tenir la limite
                alt_result.detail += f" (converti depuis {fmt})"
                return alt_result
    return result


def normalized_extension(ext: str) -> str:
    ext = ext.lower()
    return ".jpg" if ext == ".jpeg" else ext


# --------------------------------------------------------------------------- #
# Renommage
# --------------------------------------------------------------------------- #

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\-]+", "-", text.lower()).strip("-_")
    return re.sub(r"-{2,}", "-", text) or "image"


def cut_name(stem: str, cut: str) -> str:
    """Supprime tout à partir de la dernière occurrence de `cut` (sans tenir compte de la casse).
    Ex. cut_name("125-1E-D100-2B-3K-W BK_photo_1", "_photo") -> "125-1E-D100-2B-3K-W BK"."""
    if not cut:
        return stem
    pos = stem.lower().rfind(cut.lower())
    return stem[:pos].rstrip() if pos > 0 else stem


def build_name(pattern: str, source: Path, index: int, use_slug: bool, cut: str = "") -> str:
    try:
        name = pattern.format(name=cut_name(source.stem, cut), n=index, date=date.today().strftime("%Y%m%d"))
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            f"Motif de nommage invalide « {pattern} » ({exc}). "
            "Variables disponibles : {name}, {n}, {date}."
        ) from exc
    name = slugify(name) if use_slug else name.strip()
    # Interdit les séparateurs de dossier dans le nom généré.
    return re.sub(r'[\\/:*?"<>|]', "_", name) or "image"


def unique_path(path: Path, taken: set[Path], overwrite: bool) -> Path:
    """Évite les doublons. Le contrôle porte sur le nom sans extension, car l'extension
    peut encore changer à l'écriture (ex. PNG trop lourd converti en JPEG)."""
    def is_free(candidate: Path) -> bool:
        stem = candidate.parent / candidate.stem
        if stem in taken:
            return False
        return overwrite or not any(
            (candidate.parent / (candidate.stem + ext)).exists()
            for ext in FORMAT_EXTENSIONS.values()
        )

    candidate, i = path, 1
    while not is_free(candidate):
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        i += 1
    taken.add(candidate.parent / candidate.stem)
    return candidate


def find_images(source_dir: Path, recursive: bool) -> list[Path]:
    iterator = source_dir.rglob("*") if recursive else source_dir.glob("*")
    files = [p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    return sorted(files, key=lambda p: str(p.relative_to(source_dir)).lower())


def plan_jobs(
    files: list[Path],
    source_dir: Path,
    dest_dir: Path,
    pattern: str,
    start: int,
    use_slug: bool,
    requested_format: str,
    overwrite: bool,
    cut: str = "",
) -> list[Job]:
    """Calcule le nom de destination de chaque fichier (extension provisoire, ajustée à l'écriture)."""
    taken: set[Path] = set()
    jobs = []
    for index, source in enumerate(files, start=start):
        if requested_format != "auto":
            ext = FORMAT_EXTENSIONS[{"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[requested_format]]
        elif source.suffix.lower() in CONVERTED_EXTENSIONS:
            ext = ".jpg"
        else:
            ext = normalized_extension(source.suffix)
        subdir = source.parent.relative_to(source_dir)
        target = dest_dir / subdir / (build_name(pattern, source, index, use_slug, cut) + ext)
        jobs.append(Job(source, unique_path(target, taken, overwrite)))
    return jobs


# --------------------------------------------------------------------------- #
# Exécution
# --------------------------------------------------------------------------- #

def run_job(job: Job, max_bytes: int, min_quality: int, requested_format: str,
            allow_fallback: bool) -> JobResult:
    original_size = job.source.stat().st_size
    try:
        result = compress_file(job.source, max_bytes, min_quality, requested_format, allow_fallback)
        destination = job.destination
        ext = FORMAT_EXTENSIONS[result.format]
        if normalized_extension(destination.suffix) != ext:
            destination = destination.with_suffix(ext)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result.data)
        return JobResult(job.source, destination, original_size, len(result.data),
                         result.detail, result.fits)
    except Exception as exc:  # noqa: BLE001 - on continue avec les autres fichiers
        return JobResult(job.source, job.destination, original_size, 0, "", False, str(exc))


def prepare_jobs(source: Path, destination: Path, pattern: str = "{name}", start: int = 1,
                 use_slug: bool = False, requested_format: str = "auto", overwrite: bool = False,
                 recursive: bool = False, cut: str = "") -> tuple[Path, Path, list[Job]]:
    """Vérifie les dossiers et calcule les noms de destination. Lève ValueError si invalide."""
    source_dir = source.resolve()
    dest_dir = destination.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"Le dossier source « {source} » n'existe pas.")
    if dest_dir == source_dir:
        raise ValueError("Le dossier de destination doit être différent du dossier source.")
    files = find_images(source_dir, recursive)
    if recursive:  # ne pas retraiter une destination placée dans la source
        files = [f for f in files if dest_dir not in f.parents]
    jobs = plan_jobs(files, source_dir, dest_dir, pattern, start, use_slug, requested_format,
                     overwrite, cut)
    return source_dir, dest_dir, jobs


def run_jobs(jobs: list[Job], max_bytes: int, min_quality: int = 10,
             requested_format: str = "auto", allow_fallback: bool = True,
             workers: int | None = None) -> Iterator[JobResult]:
    """Traite les images en parallèle et renvoie les résultats dans l'ordre des fichiers."""
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(run_job, job, max_bytes, min_quality, requested_format, allow_fallback)
            for job in jobs
        ]
        for future in futures:
            yield future.result()


def describe_result(r: JobResult, source_dir: Path, dest_dir: Path) -> str:
    name = r.source.relative_to(source_dir)
    if r.error:
        return f"{'ERREUR':<9} {name} : {r.error}"
    status = "OK" if r.fits else "TROP GROS"
    return (f"{status:<9} {name} -> {r.destination.relative_to(dest_dir)}  "
            f"{human_size(r.original_size)} -> {human_size(r.final_size)}  [{r.detail}]")


def summarize(results: list[JobResult]) -> tuple[int, int, int]:
    """Retourne (nb OK, nb au-dessus de la limite, nb erreurs)."""
    errors = sum(1 for r in results if r.error)
    too_big = sum(1 for r in results if not r.error and not r.fits)
    return len(results) - errors - too_big, too_big, errors


def human_size(n: int) -> str:
    return f"{n / 1000:.1f} Ko" if n < 1_000_000 else f"{n / 1_000_000:.2f} Mo"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compresse des photos sous une taille maximale sans changer leur résolution, "
                    "et les renomme dans un dossier de destination.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Variables du motif de nommage (--pattern) :
  {name}   nom d'origine sans extension (et sans la fin coupée par --cut)
  {n}      numéro (formatable : {n:03} -> 001, 002...)
  {date}   date du jour AAAAMMJJ

Exemples :
  python photocompress.py photos/ sortie/
  python photocompress.py photos/ sortie/ --pattern "produit_{n:03}" --start 1
  python photocompress.py photos/ sortie/ --pattern "{date}_{name}" --slug --max-size 80
""",
    )
    parser.add_argument("source", type=Path, help="dossier contenant les photos")
    parser.add_argument("destination", type=Path, help="dossier où écrire les photos compressées")
    parser.add_argument("--max-size", type=float, default=50,
                        help="taille maximale par fichier en Ko (1 Ko = 1000 octets, défaut : 50)")
    parser.add_argument("--pattern", default="{name}",
                        help="motif de renommage, sans extension (défaut : {name})")
    parser.add_argument("--cut", default="_photo",
                        help="supprime du nom d'origine tout ce qui suit ce texte, lui compris "
                             "(défaut : _photo, donc « REF BK_photo_1 » -> « REF BK » ; "
                             "--cut \"\" pour désactiver)")
    parser.add_argument("--start", type=int, default=1, help="premier numéro pour {n} (défaut : 1)")
    parser.add_argument("--slug", action="store_true",
                        help="noms en minuscules, sans accents ni espaces (ex. « Été 2024 » -> ete-2024)")
    parser.add_argument("--format", dest="output_format", choices=("auto", "jpg", "png", "webp"),
                        default="auto",
                        help="format de sortie ; auto garde le format d'origine (BMP/TIFF/GIF -> JPEG)")
    parser.add_argument("--keep-format", action="store_true",
                        help="ne jamais changer de format, même si la limite n'est pas atteignable")
    parser.add_argument("--min-quality", type=int, default=10,
                        help="qualité JPEG/WebP minimale autorisée, 1-95 (défaut : 10)")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="inclure les sous-dossiers (l'arborescence est conservée)")
    parser.add_argument("--overwrite", action="store_true",
                        help="écraser les fichiers existants dans la destination")
    parser.add_argument("--workers", type=int, default=None,
                        help="nombre de traitements en parallèle (défaut : nombre de cœurs)")
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche le renommage prévu sans rien écrire")
    args = parser.parse_args(argv)
    if not 1 <= args.min_quality <= MAX_QUALITY:
        parser.error(f"--min-quality doit être entre 1 et {MAX_QUALITY}")
    if args.max_size <= 0:
        parser.error("--max-size doit être positif")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source_dir, dest_dir, jobs = prepare_jobs(
            args.source, args.destination, args.pattern, args.start, args.slug,
            args.output_format, args.overwrite, args.recursive, args.cut)
    except ValueError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    if not jobs:
        print("Aucune image trouvée (extensions : " + ", ".join(sorted(SUPPORTED_EXTENSIONS)) + ").")
        return 0

    if args.dry_run:
        for job in jobs:
            print(f"{job.source.relative_to(source_dir)}  ->  {job.destination.relative_to(dest_dir)}")
        print(f"\n{len(jobs)} fichier(s) seraient traités (aucune écriture : --dry-run).")
        return 0

    max_bytes = int(args.max_size * 1000)
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"Traitement de {len(jobs)} image(s), limite {human_size(max_bytes)}...\n")

    results: list[JobResult] = []
    for r in run_jobs(jobs, max_bytes, args.min_quality, args.output_format,
                      not args.keep_format, args.workers):
        results.append(r)
        print("  " + describe_result(r, source_dir, dest_dir))

    ok, too_big, errors = summarize(results)
    print(f"\nTerminé : {ok} OK, {too_big} au-dessus de la limite, {errors} erreur(s).")
    if too_big:
        print("Les fichiers « TROP GROS » ont été enregistrés au plus petit possible sans changer "
              "la résolution.\nPistes : baisser --min-quality, utiliser --format webp, ou accepter "
              "une taille plus grande (--max-size).")
    return 0 if not errors and not too_big else 2


if __name__ == "__main__":
    multiprocessing.freeze_support()  # nécessaire pour l'exécutable Windows (PyInstaller)
    sys.exit(main())
