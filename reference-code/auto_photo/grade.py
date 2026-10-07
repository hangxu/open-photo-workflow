"""Deterministic full-resolution, pointwise linear-sRGB grading.

EV is a highlight-preserving rational luminance curve, not sensor exposure.
Temperature/tint are relative linear RGB gains; contrast is the exponent of a
symmetric sigmoid; saturation multiplies distance from Rec.709 luminance.
Adjustments are added to preset EV/temp/tint/contrast, and multiply saturation;
the resulting parameters are clamped to the same documented adjustment bounds.
PNG is the lossless 8-bit master; JPEG is a lossy delivery derivative.
"""
from pathlib import Path
import json
import math
import os
import tempfile
import time
import importlib.metadata

import numpy as np
from PIL import Image, ImageCms, __version__ as pillow_version
from .artifacts import stamp
from .color import to_srgb

ENGINE_VERSION = '3.0.1'
BOUNDS = {'exposure_ev': (-2., 2.), 'temperature': (-.15, .15),
          'tint': (-.15, .15), 'contrast': (-.3, .3), 'saturation': (.5, 1.5),
          'highlights': (-1., 1.), 'shadows': (-1., 1.)}
PRESETS = {'neutral': (0., 0., 0., 0., 1.),
           'warm_indoor': (.35, .025, -.005, .015, 1.02),
           'clean_portrait': (.25, .005, .005, -.025, .98),
           'landscape': (.10, 0., 0., .08, 1.10)}


def _subject_regions(regions):
    if not isinstance(regions, list) or len(regions) > 8:
        raise ValueError('subject_lighting must contain at most eight elliptical regions')
    normalized = []
    for region in regions:
        if not isinstance(region, dict) or set(region) != {'bbox', 'ev'}:
            raise ValueError('Each subject_lighting region requires bbox and ev')
        box, ev = region['bbox'], region['ev']
        if (not isinstance(box, list) or len(box) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in box) or
                box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError('subject_lighting bbox must have positive area within normalized image coordinates')
        if isinstance(ev, bool) or not isinstance(ev, (int, float)) or not math.isfinite(ev) or not -1 <= ev <= 1:
            raise ValueError('subject_lighting ev must be finite within [-1, 1]')
        normalized.append({'bbox': list(map(float, box)), 'ev': float(ev)})
    return normalized


def _recipe(recipe):
    if not isinstance(recipe, dict) or set(recipe) - {'preset', 'adjustments', 'subject_lighting'}:
        raise ValueError('Recipe must contain only preset, adjustments and subject_lighting')
    preset = recipe.get('preset', 'neutral')
    if preset not in PRESETS:
        raise ValueError('Unknown preset')
    adjustments = recipe.get('adjustments', {})
    if not isinstance(adjustments, dict) or set(adjustments) - set(BOUNDS):
        raise ValueError('Unknown adjustments')
    normalized, effective = {}, {}
    bases = dict(zip(BOUNDS, PRESETS[preset]))
    for key, (lo, hi) in BOUNDS.items():
        base = bases.get(key, 0.)
        value = adjustments.get(key, 1. if key == 'saturation' else 0.)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'{key} must be finite within [{lo}, {hi}]')
        normalized[key] = float(value)
        result = base * value if key == 'saturation' else base + value
        effective[key] = min(hi, max(lo, result))
    regions = _subject_regions(recipe.get('subject_lighting', []))
    return {'preset': preset, 'adjustments': normalized, 'subject_lighting': regions}, effective


def _transform(array, p, local_ev=None):
    x = array.astype(np.float32) / 255.
    lin = np.where(x <= .04045, x / 12.92, ((x + .055) / 1.055) ** 2.4)
    lin *= np.array([1 + p['temperature'], 1 + p['tint'], 1 - p['temperature']], dtype=np.float32)
    y = lin @ np.array([.2126, .7152, .0722], dtype=np.float32)
    k = 2 ** p['exposure_ev']
    target = k * y / (1 + (k - 1) * y)
    target = np.clip(target, 0., 1.)
    power = 1 + p['contrast']
    a, b = target ** power, (1 - target) ** power
    target = a / (a + b)
    tone_ev = None
    if p.get('highlights', 0.) or p.get('shadows', 0.):
        # Smooth luminance bands keep chroma ratios and avoid hard tonal seams.
        highlights = np.clip((target - .35) / .65, 0., 1.) ** 2
        shadows = np.clip((.65 - target) / .65, 0., 1.) ** 2
        tone_ev = p.get('highlights', 0.) * highlights + p.get('shadows', 0.) * shadows
    if local_ev is not None:
        tone_ev = local_ev if tone_ev is None else tone_ev + local_ev
    if tone_ev is not None:
        lift = 2. ** tone_ev
        target = lift * target / (1 + (lift - 1) * target)
    lin *= np.divide(target, y, out=np.ones_like(y), where=y > 1e-10)[..., None]
    lin = target[..., None] + p['saturation'] * (lin - target[..., None])
    lin = np.maximum(lin, 0.)
    lin /= np.maximum(np.max(lin, axis=-1, keepdims=True), 1.)
    enc = np.where(lin <= .0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - .055)
    return np.uint8(np.clip(np.rint(enc * 255), 0, 255))


def _local_exposure(width, height, row, rows, regions):
    """Compact smooth ellipses in upright source coordinates; no segmentation."""
    if not regions:
        return None
    x = (np.arange(width, dtype=np.float32) + .5) / width
    y = (np.arange(row, row + rows, dtype=np.float32) + .5) / height
    exposure = np.zeros((rows, width), dtype=np.float32)
    for region in regions:
        x1, y1, x2, y2 = region['bbox']
        distance = ((x - (x1+x2)/2) / ((x2-x1)/2))[None, :] ** 2
        distance = distance + ((y - (y1+y2)/2) / ((y2-y1)/2))[:, None] ** 2
        weight = np.clip(1 - distance, 0., 1.)
        weight = weight * weight * (3 - 2 * weight)
        exposure += region['ev'] * weight
    return np.clip(exposure, -1., 1.)


def _safe_path(path):
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Symlink output paths are forbidden')


def _publish(path, writer):
    fd, temporary = tempfile.mkstemp(prefix='.grade-', dir=path.parent)
    os.close(fd)
    try:
        writer(temporary)
        os.link(temporary, path)  # Atomic create; never replace an existing file.
    finally:
        os.unlink(temporary)


def grade_image(source_path, output_dir, recipe, *, preview_max_edge=None, keep_master=True, write_record=True, quality=96):
    """Return a JSON-serializable manifest; reject conflicting/nonempty outputs.

    Untagged RGB is explicitly assumed sRGB; untagged non-RGB and broken ICC
    profiles are rejected. EXIF orientation is applied exactly once. No spatial
    operation affects the master beyond that lossless orientation permutation.
    """
    started = time.perf_counter()
    source = Path(source_path).resolve(strict=True)
    directory = Path(output_dir).absolute()
    _safe_path(directory)
    if directory == source or directory in source.parents:
        raise ValueError('Output directory must not contain the source')
    if preview_max_edge is not None and (isinstance(preview_max_edge, bool) or not isinstance(preview_max_edge, int) or preview_max_edge < 1):
        raise ValueError('preview_max_edge must be a positive integer')
    if type(keep_master) is not bool or type(write_record) is not bool:
        raise ValueError('keep_master and write_record must be booleans')
    if type(quality) is not int or not 1 <= quality <= 100:
        raise ValueError('quality must be an integer within 1..100')
    normalized, effective = _recipe(recipe)
    versions = {'numpy': np.__version__, 'pillow': pillow_version, 'opencv': 'not-used'}
    for package in ('opencv-python', 'opencv-python-headless'):
        try:
            versions['opencv'] = importlib.metadata.version(package)
            break
        except importlib.metadata.PackageNotFoundError:
            pass
    source_stamp = stamp(source)
    identity = {'source_stamp': source_stamp, 'recipe': normalized, 'engine_version': ENGINE_VERSION,
                'versions': versions, 'preview_max_edge': preview_max_edge, 'keep_master': keep_master, 'quality': quality}
    output_names = [('full', 'full.png')] if keep_master else []
    output_names.append(('final', 'final.jpg'))
    if preview_max_edge is not None:
        output_names.append(('preview', 'preview.jpg'))
    manifest = directory / 'manifest.json'
    if directory.exists() and any(directory.iterdir()):
        try:
            _safe_path(manifest)
            record = json.loads(manifest.read_text())
            if record.get('identity') != identity:
                raise ValueError('Cache identity differs')
            if {p.name for p in directory.iterdir()} != {'manifest.json', *(filename for _, filename in output_names)}:
                raise ValueError('Unexpected output files')
            for name, filename in output_names:
                path = directory / filename
                _safe_path(path)
                entry = record['outputs'][name]
                if stamp(path) != entry.get('stamp'):
                    raise ValueError('Output metadata differs')
                with Image.open(path) as im:
                    im.load()
                    if list(im.size) != entry['dimensions'] or im.mode != 'RGB' or not im.info.get('icc_profile'):
                        raise ValueError('Output decode validation failed')
            for name, filename in output_names:
                record['outputs'][name]['path'] = str(directory / filename)
            record['status'] = 'cached'
            record['manifest_path'] = str(manifest)
            return record
        except Exception as exc:
            raise ValueError(f'Refusing nonempty or invalid output directory: {exc}') from exc
    with Image.open(source) as raw:
        if raw.format not in ('JPEG', 'PNG', 'MPO'):
            raise ValueError('Only developed JPEG/PNG sources supported')
        # Camera JPEGs may contain an MPF auxiliary preview. Pillow then
        # identifies them as MPO; frame zero is the photographed primary image.
        source_format, source_frames = raw.format, getattr(raw, 'n_frames', 1)
        raw.seek(0)
        raw.load()
        dimensions, orientation = list(raw.size), raw.getexif().get(274, 1)
        im, color_profile = to_srgb(raw)
        color = dict(color_profile, source=('embedded ICC' if color_profile['embedded'] else
                     'EXIF Adobe RGB' if color_profile['conversion'] == 'exif_adobe_rgb_to_srgb' else 'assumed sRGB'))
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    output = Image.new('RGB', im.size)
    before_sum = np.zeros(3, dtype=np.float64)
    after_sum = before_sum.copy()
    newly_saturated = 0
    before_range, after_range = [255, 0], [255, 0]
    exact = True
    for row in range(0, im.height, 128):
        box = (0, row, im.width, min(im.height, row + 128))
        a = np.asarray(im.crop(box))
        local_ev = _local_exposure(im.width, im.height, row, a.shape[0], normalized['subject_lighting'])
        b = _transform(a, effective, local_ev)
        output.paste(Image.fromarray(b), box)
        before_sum += a.sum(axis=(0, 1), dtype=np.float64)
        after_sum += b.sum(axis=(0, 1), dtype=np.float64)
        newly_saturated += int(np.count_nonzero(np.any(b == 255, axis=-1) & ~np.any(a == 255, axis=-1)))
        before_range = [min(before_range[0], int(a.min())), max(before_range[1], int(a.max()))]
        after_range = [min(after_range[0], int(b.min())), max(after_range[1], int(b.max()))]
        sampled_ev = local_ev[::17, ::17] if local_ev is not None else None
        exact = exact and np.array_equal(b[::17, ::17], _transform(a[::17, ::17], effective, sampled_ev))
    if stamp(source) != source_stamp:
        raise ValueError('Source changed during processing')
    directory.mkdir(parents=True, exist_ok=True)
    _safe_path(directory)
    outputs = {}
    for name, filename in output_names:
        image = output.copy() if name == 'preview' else output
        if name == 'preview':
            image.thumbnail((preview_max_edge, preview_max_edge), Image.Resampling.LANCZOS)
        path = directory / filename
        fmt = 'PNG' if name == 'full' else 'JPEG'
        kwargs = {'compress_level': 4} if fmt == 'PNG' else {'quality': quality, 'subsampling': 0}
        _publish(path, lambda temporary: image.save(temporary, format=fmt, icc_profile=profile, **kwargs))
        with Image.open(path) as check:
            check.load()
            size = list(check.size)
        outputs[name] = {'path': str(path), 'stamp': stamp(path), 'dimensions': size}
    count = im.width * im.height
    record = {'status': 'created', 'engine_version': ENGINE_VERSION, 'identity': identity, 'source_stamp': source_stamp,
              'source': {'path': str(source), 'stamp': source_stamp, 'raw_dimensions': dimensions, 'orientation': orientation,
                         'format': source_format, 'frames': source_frames, 'selected_frame': 0},
              'recipe': normalized, 'effective_parameters': {**effective, 'subject_lighting': normalized['subject_lighting']}, 'versions': versions,
              'color_management': color, 'outputs': outputs, 'manifest_path': str(manifest) if write_record else None,
              'parameter_units': {'exposure_ev': 'rational luminance curve k=2**EV',
                                  'temperature': 'linear RGB gains (1+t,1,1-t)',
                                  'tint': 'linear green gain 1+tint',
                                  'contrast': 'symmetric sigmoid exponent 1+contrast',
                                  'saturation': 'linear luminance chroma multiplier',
                                  'highlights': 'smooth upper-luminance exposure curve',
                                  'shadows': 'smooth lower-luminance exposure curve',
                                  'subject_lighting': 'elliptical feathered exposure; no segmentation or relighting'},
              'qa': {'newly_saturated_pixel_fraction': newly_saturated / count,
                     'mean_rgb_before': (before_sum / count).tolist(), 'mean_rgb_after': (after_sum / count).tolist(),
                     'range_before': before_range, 'range_after': after_range,
                     'geometry': {'same_oriented_dimensions': output.size == im.size,
                                  'operation': 'pointwise color transform with optional smooth elliptical exposure; no pixel displacement',
                                  'sample_pointwise_recomputation_exact': bool(exact)},
                     'master_bit_depth': 8, 'visual_review': 'not_performed'},
              'performance': {'seconds': time.perf_counter() - started}}
    if write_record:
        _publish(manifest, lambda temporary: Path(temporary).write_text(json.dumps(record, indent=2), encoding='utf-8'))
    return record
