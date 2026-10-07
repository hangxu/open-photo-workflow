"""Non-destructive native FSRCNN finishing with explicit metadata-based caching."""
from __future__ import annotations
import importlib, json, os, sys, time, io, tempfile
import PIL
from pathlib import Path
from PIL import Image, ImageOps, ImageFilter, ImageCms
from .artifacts import stamp, atomic_json

VERSION = 'fsrcnn-3'
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB'))
SRGB_BYTES = SRGB.tobytes()
DEFAULT_SHARPEN = {'radius': 1.2, 'percent': 80, 'threshold': 2}


def load_srgb(path):
    with Image.open(path) as raw:
        profile = raw.info.get('icc_profile')
        color_space = raw.getexif().get(40961)
        image = ImageOps.exif_transpose(raw)
        if profile:
            try:
                source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                image = ImageCms.profileToProfile(image, source_profile, SRGB, outputMode='RGB')
            except Exception as exc:
                raise ValueError('Invalid or unsupported ICC profile') from exc
        else:
            if color_space not in (None, 1) or image.mode not in ('RGB', 'RGBA', 'L'):
                raise ValueError('Unknown untagged color space; provide a valid ICC profile')
            image = image.convert('RGB')
        image.load()
        return image


def versions():
    result = {'Pillow': PIL.__version__}
    try:
        cv2, np = runtime()
        result.update(opencv=cv2.__version__, numpy=np.__version__)
    except ImportError:
        result.update(opencv=None, numpy=None)
    return result


def runtime():
    """Optional compatibility runtime must be explicitly selected by the caller."""
    selected = os.environ.get('AUTO_PHOTO_CV_RUNTIME')
    if selected:
        directory = Path(selected).resolve(strict=True)
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    return importlib.import_module('cv2'), importlib.import_module('numpy')


def safe_output(output_dir, inputs):
    out = Path(output_dir).absolute()
    for p in [out, *out.parents]:
        if p.is_symlink():
            raise ValueError('Symlink output paths are forbidden')
    resolved = out.resolve()
    for source in inputs:
        source = Path(source).resolve(strict=True)
        if resolved == source or resolved == source.parent or resolved in source.parents:
            raise ValueError('Output must be a dedicated directory, not an input directory or ancestor')
    return out


def cached(out, recipe):
    """Compare explicit parameters and filesystem metadata; this is not content validation."""
    if not out.exists() or not any(out.iterdir()):
        return None
    record = out / 'record.json'
    if record.is_file() and not record.is_symlink():
        try:
            data = json.loads(record.read_text())
            if data.get('recipe') == recipe and data.get('status') == 'completed':
                artifacts = data.get('artifacts', {})
                if not artifacts:
                    raise FileExistsError('Cache has no recorded artifacts')
                if {p.name for p in out.iterdir()} != {'record.json', *artifacts}:
                    raise FileExistsError('Cache directory has unexpected files')
                for name, entry in artifacts.items():
                    if Path(name).name != name or name in ('.', '..'):
                        raise FileExistsError('Invalid cache artifact name')
                    path = out / name
                    if path.is_symlink() or not path.is_file() or stamp(path) != entry.get('stamp'):
                        raise FileExistsError('Cached artifact metadata changed; select a new output directory')
                return dict(data, cached=True)
        except (ValueError, TypeError, AttributeError) as exc:
            raise FileExistsError('Invalid cache record; select a new output directory') from exc
    raise FileExistsError('Output exists with a different or incomplete recipe; select a new directory')


def image_artifact(path, dimensions):
    return {'path': str(path), 'stamp': stamp(path), 'dimensions': list(dimensions)}


def publish_image(image, path, **kwargs):
    """Publish an image atomically without replacing another writer's output."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.image-', suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        image.save(temporary, **kwargs)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_record(out, record, *, write_record=True):
    if write_record:
        atomic_json(out / 'record.json', record)
    return record


def finish_generated(generated_path, output_dir, scale=2, model_path=None, *,
                     sharpen=True, diagnostics=False, write_record=True):
    if type(scale) is not int or scale not in (1, 2, 4):
        raise ValueError('Native FSRCNN scale must be 1, 2 or 4')
    if any(type(value) is not bool for value in (sharpen, diagnostics, write_record)):
        raise ValueError('sharpen, diagnostics and write_record must be booleans')
    source = Path(generated_path).resolve(strict=True)
    out = safe_output(output_dir, [source])
    model = Path(model_path).resolve(strict=True) if model_path and scale > 1 else None
    recipe = {'version': VERSION, 'runtime_versions': versions(), 'source_stamp': stamp(source), 'scale': scale,
              'model_stamp': stamp(model) if model else None,
              'sharpen': dict(DEFAULT_SHARPEN) if sharpen else None, 'diagnostics': diagnostics}
    hit = cached(out, recipe)
    if hit:
        return hit
    out.mkdir(parents=True, exist_ok=True)
    record = dict(recipe, recipe=recipe, source=str(source), output_dir=str(out), status='blocked', artifacts={})
    if scale > 1 and model is None:
        return save_record(out, dict(record, reason='Native FSRCNN model required; no interpolation fallback'), write_record=write_record)
    start = time.monotonic()
    try:
        image = load_srgb(source)
        if scale == 1:
            up = image.copy()
        else:
            _, np = runtime()
            from .tiled import fsrcnn_array
            result = fsrcnn_array(np.asarray(image), model, scale)
            if result.shape[:2] != (image.height * scale, image.width * scale):
                raise ValueError('FSRCNN returned unexpected dimensions')
            up = Image.fromarray(result)
        finished = up.filter(ImageFilter.UnsharpMask(**DEFAULT_SHARPEN)) if sharpen else up
        if stamp(source) != recipe['source_stamp']:
            raise ValueError('Source changed during processing')
        if model is not None and stamp(model) != recipe['model_stamp']:
            raise ValueError('Model changed during processing')
        artifacts = {}
        if diagnostics:
            publish_image(up, out / 'upscaled.png', icc_profile=SRGB_BYTES)
            artifacts['upscaled.png'] = image_artifact(out / 'upscaled.png', up.size)
        publish_image(finished, out / 'final.png', icc_profile=SRGB_BYTES)
        artifacts['final.png'] = image_artifact(out / 'final.png', finished.size)
        record.update(status='completed', input_dimensions=list(image.size), dimensions=list(up.size),
                      final_path=str(out / 'final.png'), upscaled_path=str(out / ('upscaled.png' if diagnostics else 'final.png')),
                      output_stamp=stamp(out / 'final.png'), model_path=str(model) if model else None,
                      seconds=time.monotonic()-start, artifacts=artifacts)
    except Exception as exc:
        record.update(reason=str(exc), seconds=time.monotonic()-start)
    return save_record(out, record, write_record=write_record)
