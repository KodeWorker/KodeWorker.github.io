#!/usr/bin/env python3
"""Resize images so their long side matches a given length.

Aspect ratio is preserved, EXIF orientation is applied (so portrait photos stay
portrait), and EXIF/ICC metadata is carried over to the output when possible.

Examples
--------
Preview what would happen to an album (nothing is written):

    python3 tools/resize_images.py "assets/images/album/2026-11-05-2026秋季伯斯家庭旅遊" \
        --long-side 1600 --dry-run

Write resized copies next to the originals in a new folder:

    python3 tools/resize_images.py "assets/images/album/2026-11-05-2026秋季伯斯家庭旅遊" \
        --long-side 1600 -o /tmp/resized

Overwrite the originals (keeps a .bak copy of each file unless --no-backup):

    python3 tools/resize_images.py "assets/images/album/2026-11-05-2026秋季伯斯家庭旅遊" \
        --long-side 1600 --in-place
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required: pip install Pillow")

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}


def collect(targets: list[Path], recursive: bool) -> list[Path]:
    """Expand files/directories into a sorted list of image paths."""
    found: list[Path] = []
    for target in targets:
        if target.is_dir():
            it = target.rglob("*") if recursive else target.glob("*")
            found += [p for p in it if p.is_file() and p.suffix.lower() in SUFFIXES]
        elif target.is_file():
            found.append(target)
        else:
            print(f"skip (not found): {target}", file=sys.stderr)
    return sorted(set(found))


def new_size(width: int, height: int, long_side: int) -> tuple[int, int]:
    """Scale (width, height) so the longer edge equals long_side."""
    scale = long_side / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def dest_path(src: Path, root: Path | None, out_dir: Path | None, suffix: str) -> Path:
    if out_dir is None:
        return src.with_name(f"{src.stem}{suffix}{src.suffix}") if suffix else src
    rel = src.relative_to(root) if root and root in src.parents else Path(src.name)
    return out_dir / rel.parent / f"{rel.stem}{suffix}{rel.suffix}"


def resize_one(src: Path, dst: Path, args: argparse.Namespace) -> str:
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)  # bake rotation into pixels
        old_w, old_h = img.size
        target_w, target_h = new_size(old_w, old_h, args.long_side)

        if max(old_w, old_h) <= args.long_side and not args.upscale:
            if dst != src and not args.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            return f"keep   {old_w}x{old_h} (long side already <= {args.long_side})"

        if args.dry_run:
            return f"would  {old_w}x{old_h} -> {target_w}x{target_h}"

        out = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

        save_kwargs: dict = {}
        if dst.suffix.lower() in {".jpg", ".jpeg", ".webp"}:
            save_kwargs.update(quality=args.quality, optimize=True)
            if dst.suffix.lower() in {".jpg", ".jpeg"}:
                save_kwargs["progressive"] = True
            if out.mode in {"RGBA", "P"} and dst.suffix.lower() != ".webp":
                out = out.convert("RGB")
        if (icc := img.info.get("icc_profile")) and not args.strip_metadata:
            save_kwargs["icc_profile"] = icc
        if not args.strip_metadata and (exif := img.getexif()):
            # orientation is already applied, so drop the tag to avoid double rotation
            exif.pop(274, None)
            save_kwargs["exif"] = exif.tobytes()

        if dst == src and args.backup:
            shutil.copy2(src, src.with_suffix(src.suffix + ".bak"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(dst, **save_kwargs)

    return f"ok     {old_w}x{old_h} -> {target_w}x{target_h}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Resize images to a specific long-side length.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("paths", nargs="+", type=Path, help="image files and/or directories")
    p.add_argument("-l", "--long-side", type=int, default=1600,
                   help="length of the longer edge in pixels (default: 1600)")
    p.add_argument("-o", "--out-dir", type=Path,
                   help="write results here, mirroring the input tree")
    p.add_argument("--in-place", action="store_true",
                   help="overwrite the originals (requires neither -o nor --suffix)")
    p.add_argument("--suffix", default="",
                   help="append this to each output stem, e.g. --suffix _1600")
    p.add_argument("-q", "--quality", type=int, default=85,
                   help="JPEG/WebP quality 1-95 (default: 85)")
    p.add_argument("--upscale", action="store_true",
                   help="also enlarge images smaller than the target")
    p.add_argument("--strip-metadata", action="store_true",
                   help="drop EXIF and ICC profile from the output")
    p.add_argument("--no-backup", dest="backup", action="store_false",
                   help="with --in-place, do not keep a .bak copy")
    p.add_argument("-r", "--recursive", action="store_true",
                   help="descend into sub-directories")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="report planned changes without writing anything")
    args = p.parse_args(argv)

    if args.long_side < 1:
        p.error("--long-side must be >= 1")
    if not (args.in_place or args.out_dir or args.suffix or args.dry_run):
        p.error("refusing to overwrite originals: pass --in-place, -o DIR, or --suffix")
    if args.in_place and (args.out_dir or args.suffix):
        p.error("--in-place cannot be combined with -o or --suffix")

    images = collect(args.paths, args.recursive)
    if not images:
        print("no images found", file=sys.stderr)
        return 1

    roots = [p for p in args.paths if p.is_dir()]
    root = roots[0] if len(roots) == 1 else None
    saved = 0
    failed = 0

    for src in images:
        dst = dest_path(src, root, args.out_dir, args.suffix)
        before = src.stat().st_size
        try:
            status = resize_one(src, dst, args)
        except Exception as exc:  # noqa: BLE001 - keep going through the batch
            failed += 1
            print(f"FAIL   {src}: {exc}", file=sys.stderr)
            continue
        if not args.dry_run and dst.exists():
            saved += before - dst.stat().st_size
        print(f"{status}  {src}" + ("" if dst == src else f" -> {dst}"))

    print(f"\n{len(images) - failed}/{len(images)} processed"
          + ("" if args.dry_run else f", {saved / 1048576:.1f} MiB saved"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
