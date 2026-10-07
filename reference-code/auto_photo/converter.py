"""Serial, full RAW decoding with metadata-based, non-overwriting caching."""
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

import PIL
from PIL import Image, ImageCms
import rawpy
from .artifacts import stamp, atomic_json

SUPPORTED = {".arw", ".nef", ".nrw", ".cr2", ".cr3", ".crw", ".raf", ".rw2",
             ".orf", ".pef", ".dng", ".srw", ".rwl", ".3fr", ".fff", ".iiq", ".kdc", ".mos", ".mrw", ".raw"}


def _inside(path, root):
    return path == root or root in path.parents


def discover_inputs(input_path, recursive=False, output_dir=None):
    source = Path(input_path).expanduser().resolve()
    excluded = Path(output_dir).expanduser().resolve() if output_dir else None
    if not source.exists():
        raise ValueError(f"Input does not exist: {source}")
    if source.is_file():
        if source.suffix.lower() not in SUPPORTED:
            raise ValueError(f"Unsupported input: {source}")
        return [source]
    paths = source.rglob("*") if recursive else source.iterdir()
    return sorted({p.resolve() for p in paths if p.is_file()
                   and p.suffix.lower() in SUPPORTED
                   and (excluded is None or not _inside(p.resolve(), excluded))})


def _atomic_json(path, data):
    atomic_json(path, data)


def _target_path(root, source, old, reserved, suffix='.jpg'):
    """Keep a recorded filename, or allocate a readable collision-free sequence."""
    previous = Path(old.get('output', ''))
    if old.get('source_stamp') and previous.parent == root and previous.name not in ('', '.', '..'):
        reserved.add(previous.name)
        return previous
    sequence = 1
    while True:
        candidate = root / f'{source.name}.{sequence:04d}{suffix}'
        if candidate.name not in reserved and not candidate.exists() and not candidate.is_symlink():
            reserved.add(candidate.name)
            return candidate
        sequence += 1


def _valid_output(path, old):
    if path.is_symlink() or old.get('output_stamp') != stamp(path):
        return False
    with Image.open(path) as image:
        image.load()
        return image.format == 'JPEG' and image.size == (old.get('width'), old.get('height'))


def _decode(source):
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    with rawpy.imread(str(source)) as raw:
        pixels = raw.postprocess(use_camera_wb=True, use_auto_wb=False,
                                 output_color=rawpy.ColorSpace.sRGB,
                                 output_bps=8, half_size=False, no_auto_bright=True,
                                 bright=1.0, gamma=(2.222, 4.5))
    return Image.fromarray(pixels), srgb.tobytes(), "RAW EXIF metadata is not copied; source remains authoritative."


def convert_batch(input_path, output_dir, recursive=False, quality=95, max_size=None, progress=None, *, write_record=True):
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")
    if max_size is not None and max_size < 1:
        raise ValueError("max_size must be positive")
    source_root = Path(input_path).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    if output_root == source_root or (source_root.is_dir() and _inside(source_root, output_root)):
        raise ValueError("Output must not equal or contain the input directory")
    sources = discover_inputs(source_root, recursive, output_root)
    if not sources:
        raise ValueError("No supported RAW inputs found")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Refusing a symlink manifest path")
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        old_items = {item["source"]: item for item in previous.get("items", [])}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        old_items = {}
    if old_items and previous.get('schema_version') != 2:
        raise ValueError('Historical conversion records are read-only; use a new output directory')
    engines = {"rawpy": rawpy.__version__, "libraw": list(rawpy.libraw_version), "Pillow": PIL.__version__}
    recipe = {"version": 2, "quality": quality, "max_size": max_size,
              "raw": {"use_camera_wb": True, "use_auto_wb": False, "half_size": False,
                      "output_color": "sRGB", "output_bps": 8, "no_auto_bright": True,
                      "bright": 1.0, "gamma": [2.222, 4.5]},
              "jpeg": {"color": "sRGB", "subsampling": 0, "copy_exif": False}, "engines": engines}
    manifest = {"schema_version": 2, "manifest": str(manifest_path) if write_record else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": str(source_root), "output_dir": str(output_root),
                "recipe": recipe, "engines": engines,
                "items": [dict(old_items[str(source)]) if old_items.get(str(source), {}).get('source_stamp') else {"source": str(source), "status": "pending"}
                          for source in sources]}
    reserved = {Path(item['output']).name for item in old_items.values() if item.get('output')}
    for index, source in enumerate(sources):
        started = time.monotonic()
        old = old_items.get(str(source), {})
        target = _target_path(output_root, source, old, reserved)
        item = {"source": str(source), "output": str(target), "recipe": recipe}
        temporary = None
        try:
            item["source_stamp"] = stamp(source)
            if target.exists() or target.is_symlink():
                if (not target.is_symlink() and old.get("status") in {"converted", "cached"}
                        and old.get("output") == str(target) and old.get("recipe") == recipe
                        and old.get("source_stamp") == item["source_stamp"]
                        and _valid_output(target, old)):
                    item.update({key: old[key] for key in ("width", "height", "output_stamp")})
                    if "metadata_note" in old:
                        item["metadata_note"] = old["metadata_note"]
                    item["status"] = "cached"
                else:
                    raise ValueError("Output metadata or recipe differs; use a new output directory")
            else:
                image, icc, note = _decode(source)
                if max_size:
                    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                item.update(width=image.width, height=image.height, metadata_note=note)
                fd, temporary = tempfile.mkstemp(prefix=".photo-", suffix=".jpg", dir=output_root)
                os.close(fd)
                try:
                    image.save(temporary, format="JPEG", quality=quality, subsampling=0,
                               icc_profile=icc)
                finally:
                    image.close()
                # Link publishes atomically and fails if another writer created the target.
                if stamp(source) != item['source_stamp']:
                    raise ValueError('Source changed during decoding')
                os.link(temporary, target)
                item["output_stamp"] = stamp(target)
                item["status"] = "converted"
        except Exception as exc:
            item.update(status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        item["elapsed_seconds"] = round(time.monotonic() - started, 4)
        manifest["items"][index] = item
        manifest["summary"] = {status: sum(i["status"] == status for i in manifest["items"])
                               for status in ("converted", "cached", "error", "pending")}
        manifest["summary"]["total"] = len(manifest["items"])
        if write_record:
            _atomic_json(manifest_path, manifest)
        if progress is not None:
            progress(index + 1, len(sources), item)
    return manifest
