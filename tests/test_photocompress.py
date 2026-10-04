from pathlib import Path

import pytest
from PIL import Image

import photocompress as pc

LIMIT = 50_000


def noisy_image(size=(1600, 1200), mode="RGB", seed=0) -> Image.Image:
    """Image détaillée (difficile à compresser) : dégradé + bruit."""
    w, h = size
    gradient = Image.linear_gradient("L")
    img = Image.merge("RGB", (
        gradient.rotate(90).resize(size),
        gradient.resize(size),
        Image.effect_noise(size, 60 + seed),
    ))
    if mode == "RGBA":
        img = img.convert("RGBA")
        alpha = Image.new("L", size, 255)
        alpha.paste(0, (0, 0, w // 4, h // 4))
        img.putalpha(alpha)
    return img


@pytest.fixture
def photos(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    noisy_image(seed=1).save(src / "Photo Été.JPG", quality=95)
    noisy_image(seed=2).save(src / "b.jpeg", quality=95)
    noisy_image((800, 600), seed=3).save(src / "c.png")
    noisy_image((800, 600), "RGBA", seed=4).save(src / "d.png")
    noisy_image((640, 480), seed=5).save(src / "e.bmp")
    noisy_image((640, 480), seed=6).save(src / "f.webp", quality=95)
    Image.new("RGB", (100, 100), "red").save(src / "petit.jpg")
    (src / "notes.txt").write_text("pas une image")
    return src


def run(src: Path, dest: Path, *extra: str) -> int:
    return pc.main([str(src), str(dest), "--workers", "1", *extra])


def test_all_outputs_under_limit_and_same_resolution(photos, tmp_path):
    dest = tmp_path / "out"
    assert run(photos, dest) == 0

    outputs = sorted(dest.iterdir())
    assert len(outputs) == 7  # le .txt est ignoré
    for out in outputs:
        assert out.stat().st_size <= LIMIT, out
    sizes = {p.stem: Image.open(p).size for p in outputs}
    assert sizes["Photo Été"] == (1600, 1200)
    assert sizes["c"] == (800, 600)
    assert sizes["e"] == (640, 480)


def test_formats(photos, tmp_path):
    dest = tmp_path / "out"
    run(photos, dest)
    names = {p.name for p in dest.iterdir()}
    assert {"Photo Été.jpg", "b.jpg", "e.jpg", "f.webp", "petit.jpg"} <= names
    # Les PNG restent PNG s'ils passent sous la limite, sinon convertis (transparence -> WebP).
    assert any(n.startswith("d.") for n in names)
    d = next(dest.glob("d.*"))
    assert Image.open(d).mode in ("RGBA", "P", "LA")


def test_rename_pattern_and_slug(photos, tmp_path):
    dest = tmp_path / "out"
    run(photos, dest, "--pattern", "Lot {n:03} {name}", "--slug", "--start", "5")
    names = sorted(p.name for p in dest.iterdir())
    assert names[0] == "lot-005-b.jpg"
    assert "lot-011-photo-ete.jpg" in names


def test_name_collisions_get_suffix(photos, tmp_path):
    dest = tmp_path / "out"
    run(photos, dest, "--pattern", "same", "--format", "jpg")
    names = sorted(p.name for p in dest.iterdir())
    assert names == ["same.jpg"] + [f"same_{i}.jpg" for i in range(1, 7)]


def test_small_file_copied_unchanged(photos, tmp_path):
    dest = tmp_path / "out"
    run(photos, dest)
    assert (dest / "petit.jpg").read_bytes() == (photos / "petit.jpg").read_bytes()


def test_dry_run_writes_nothing(photos, tmp_path):
    dest = tmp_path / "out"
    assert run(photos, dest, "--dry-run") == 0
    assert not dest.exists()


def test_recursive_keeps_tree(photos, tmp_path):
    sub = photos / "sous dossier"
    sub.mkdir()
    noisy_image((400, 300)).save(sub / "g.jpg", quality=95)
    dest = tmp_path / "out"
    run(photos, dest, "-r")
    assert (dest / "sous dossier" / "g.jpg").stat().st_size <= LIMIT


def test_exif_orientation_applied(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    img = noisy_image((1200, 800))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotation 90°
    img.save(src / "rot.jpg", quality=95, exif=exif)
    run(src, tmp_path / "out")
    assert Image.open(tmp_path / "out" / "rot.jpg").size == (800, 1200)


def test_unreachable_limit_reports_too_big(photos, tmp_path):
    dest = tmp_path / "out"
    assert run(photos, dest, "--max-size", "1", "--keep-format") == 2


def test_invalid_pattern(photos, tmp_path):
    assert run(photos, tmp_path / "out", "--pattern", "{inconnu}") == 1


def test_existing_file_with_other_extension_not_overwritten(photos, tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "c.jpg").write_bytes(b"deja la")
    run(photos, dest)
    assert (dest / "c.jpg").read_bytes() == b"deja la"
    assert (dest / "c_1.png").exists()


def test_dotted_names(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    noisy_image((300, 200)).save(src / "photo.v1.jpg")
    noisy_image((300, 200)).save(src / "photo.v2.jpg")
    run(src, tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["photo.v1.jpg", "photo.v2.jpg"]
