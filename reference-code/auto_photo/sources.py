"""Logical photo sources and inexpensive classification previews.

RAW/JPEG pairing is local and conservative. File stamps detect ordinary edits;
they are not content identities. Preparing a RAW preview never creates the
full-resolution editing input, which belongs to the execution stage.
"""
from collections import defaultdict
from io import BytesIO
from pathlib import Path
import struct
import tempfile

from PIL import Image, ImageCms, TiffImagePlugin
import rawpy

from .artifacts import stamp
from .converter import SUPPORTED as RAW_FORMATS
from .preflight import analyze_image
from .color import ColorProfileError, color_evidence, image_color_evidence, to_srgb

RASTER_FORMATS = {'.jpg', '.jpeg', '.png'}
JPEG_FORMATS = {'.jpg', '.jpeg'}


def _text(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    return str(value).strip(' \t\r\n\x00') if value is not None else None


def _read_metadata(path):
    """Read EXIF headers, including TIFF-based RAW, without decoding pixels."""
    path = Path(path)
    tags, detail, interoperability, color = {}, {}, {}, {}
    try:
        if path.suffix.lower() in RAW_FORMATS:
            with path.open('rb') as handle:
                header = handle.read(8)
                if header[:4] not in (b'II*\x00', b'MM\x00*'):
                    return {}
                endian = '<' if header[:2] == b'II' else '>'
                handle.seek(struct.unpack(endian + 'I', header[4:8])[0])
                directory = TiffImagePlugin.ImageFileDirectory_v2(ifh=header)
                directory.load(handle)
                tags = dict(directory)
                offset = tags.get(34665)
                if isinstance(offset, tuple):
                    offset = offset[0] if offset else None
                if isinstance(offset, int) and offset > 0:
                    handle.seek(offset)
                    exif_directory = TiffImagePlugin.ImageFileDirectory_v2(ifh=header)
                    exif_directory.load(handle)
                    detail = dict(exif_directory)
                    offset = detail.get(40965)
                    if isinstance(offset, tuple):
                        offset = offset[0] if offset else None
                    if isinstance(offset, int) and offset > 0:
                        try:
                            handle.seek(offset)
                            interop_directory = TiffImagePlugin.ImageFileDirectory_v2(ifh=header)
                            interop_directory.load(handle)
                            interoperability = dict(interop_directory)
                        except (OSError, ValueError, TypeError, SyntaxError, struct.error):
                            # Keep ColorSpace=uncalibrated so a broken R03 IFD
                            # is rejected, rather than losing all color evidence.
                            pass
                color = color_evidence(tags, detail, interoperability)
        else:
            with Image.open(path) as image:
                exif = image.getexif()
                tags = dict(exif)
                try:
                    detail = exif.get_ifd(34665)
                except (KeyError, TypeError, ValueError):
                    pass
                color = image_color_evidence(image)
    except (OSError, ValueError, TypeError, SyntaxError, struct.error):
        return {}
    return {key: value for key, value in {
        'captured_at': _text(detail.get(36867) or tags.get(36867) or tags.get(306)),
        'camera_make': _text(tags.get(271)),
        'camera_model': _text(tags.get(272)),
        'orientation': tags.get(274),
        'color': color or None,
    }.items() if value is not None}


def _metadata_agree(first, second):
    for key in ('captured_at', 'camera_make', 'camera_model'):
        left, right = first.get(key), second.get(key)
        if left and right and ' '.join(left.split()).casefold() != ' '.join(right.split()).casefold():
            return False
    return True


def discover_sources(input_path, recursive=False, pair_raw_jpeg=True):
    """Return one row per source, merging only an unambiguous RAW/JPEG pair."""
    root = Path(input_path).expanduser().resolve(strict=True)
    supported = RAW_FORMATS | RASTER_FORMATS
    if root.is_file():
        if root.suffix.lower() not in supported:
            raise ValueError(f'Unsupported image input: {root}')
        # An explicitly selected RAW/JPEG can use its own unambiguous sibling.
        files = sorted(p.resolve() for p in root.parent.iterdir()
                       if p.is_file() and p.stem == root.stem
                       and p.suffix.lower() in supported) if pair_raw_jpeg and root.suffix.lower() != '.png' else [root]
    else:
        paths = root.rglob('*') if recursive else root.iterdir()
        files = sorted({p.resolve() for p in paths if p.is_file() and p.suffix.lower() in supported})
    groups = defaultdict(list)
    for path in files:
        groups[(path.parent, path.stem)].append(path)
    metadata, pairs, paired = {}, {}, set()
    if pair_raw_jpeg:
        for group in groups.values():
            raws = [p for p in group if p.suffix.lower() in RAW_FORMATS]
            jpegs = [p for p in group if p.suffix.lower() in JPEG_FORMATS]
            if len(raws) != 1 or len(jpegs) != 1:
                continue
            raw, jpeg = raws[0], jpegs[0]
            metadata[raw], metadata[jpeg] = _read_metadata(raw), _read_metadata(jpeg)
            if _metadata_agree(metadata[raw], metadata[jpeg]):
                pairs[raw] = jpeg
                paired.add(jpeg)
    rows = []
    for path in files:
        if path in paired:
            continue
        jpeg = pairs.get(path)
        if root.is_file() and path != root and jpeg != root:
            continue
        row = {'source': str(path), 'source_stamp': stamp(path), 'source_revision': 1,
               'is_raw': path.suffix.lower() in RAW_FORMATS,
               'paired_jpeg': str(jpeg) if jpeg else None,
               'paired_jpeg_stamp': stamp(jpeg) if jpeg else None}
        if path in metadata:
            row['source_metadata'] = metadata[path]
        if jpeg:
            row['paired_jpeg_metadata'] = metadata[jpeg]
        rows.append(row)
    return rows


def _raw_dimensions(raw):
    sizes = raw.sizes
    width, height = int(sizes.width), int(sizes.height)
    if sizes.flip in (5, 6):
        width, height = height, width
    return (width, height) if width > 0 and height > 0 else None


def _save_small(image, destination, max_edge, dimensions=None, flip=0, inherited_color=None):
    upright, profile = to_srgb(image, inherited_color=inherited_color)
    if dimensions and (upright.width > upright.height) != (dimensions[0] > dimensions[1]) and flip in (5, 6):
        upright = upright.transpose(Image.Transpose.ROTATE_90 if flip == 5 else Image.Transpose.ROTATE_270)
    upright.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    options = {'quality': 92}
    options['icc_profile'] = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    upright.save(destination, format='JPEG', **options)
    return profile


def _extract_small(raw, destination, max_edge, dimensions, inherited_color=None):
    if raw is None:
        return None
    try:
        thumb = raw.extract_thumb()
        if thumb.format == rawpy.ThumbFormat.JPEG:
            with Image.open(BytesIO(thumb.data)) as image:
                profile = _save_small(image, destination, max_edge, dimensions, raw.sizes.flip, inherited_color)
            return 'embedded_jpeg', profile
        if thumb.format == rawpy.ThumbFormat.BITMAP:
            image = Image.fromarray(thumb.data)
            profile = _save_small(image, destination, max_edge, dimensions, raw.sizes.flip, inherited_color)
            return 'embedded_bitmap', profile
    except ColorProfileError:
        raise
    except (rawpy.LibRawError, OSError, ValueError):
        pass
    return None


def prepare_preview(row, output_dir, max_edge=1280, raw_profile=None, raw_executable=None):
    """Create only preview.jpg; RAW dimensions describe metadata until rendered."""
    if type(max_edge) is not int or max_edge < 1:
        raise ValueError('max_edge must be a positive integer')
    source = Path(row['source']).expanduser().resolve(strict=True)
    before = stamp(source)
    if row.get('source_stamp') and row['source_stamp'] != before:
        raise ValueError('Source metadata changed before preview')
    output = Path(output_dir).expanduser().absolute()
    preview = output / 'preview.jpg'
    if preview.resolve() == source or (row.get('paired_jpeg') and preview.resolve() == Path(row['paired_jpeg']).resolve()):
        raise ValueError('Preview must not overwrite source')
    if preview.exists():
        raise FileExistsError(f'preview already exists: {preview}')
    output.mkdir(parents=True, exist_ok=True)
    is_raw = row.get('is_raw', source.suffix.lower() in RAW_FORMATS)
    dimensions, raw = None, None
    metadata = dict(row.get('source_metadata') or _read_metadata(source))
    # Older saved rows may predate color metadata. Never let those rows erase
    # authoritative RAW tags when preparing an untagged embedded preview.
    if is_raw and 'color' not in metadata:
        color = _read_metadata(source).get('color')
        if color:
            metadata['color'] = color
    kind, preview_source = 'source', str(source)
    if not is_raw:
        info = analyze_image(source, preview, max_edge=max_edge)
        basis = 'source'
    else:
        try:
            raw = rawpy.RawPy()
            raw.open_file(str(source))  # Unlike imread, this does not unpack full RAW pixels.
            dimensions = _raw_dimensions(raw)
        except (rawpy.LibRawError, OSError, ValueError, TypeError, AttributeError):
            if raw is not None:
                raw.close()
            raw = None
        try:
            jpeg = Path(row['paired_jpeg']) if row.get('paired_jpeg') else None
            if jpeg and row.get('paired_jpeg_stamp') and stamp(jpeg) != row['paired_jpeg_stamp']:
                raise ValueError('Paired JPEG metadata changed before preview')
            if jpeg:
                try:
                    info = analyze_image(jpeg, preview, max_edge=max_edge)
                    kind, preview_source = 'paired_jpeg', str(jpeg)
                    metadata = row.get('paired_jpeg_metadata') or _read_metadata(jpeg)
                except (OSError, ValueError):
                    jpeg = None
            if not jpeg:
                with tempfile.TemporaryDirectory(prefix='.preview-', dir=output) as temporary:
                    temporary = Path(temporary)
                    small = temporary / 'preview-source.jpg'
                    embedded = _extract_small(raw, small, max_edge, dimensions, metadata.get('color'))
                    if embedded is None:
                        from . import rawtherapee
                        renderer = getattr(rawtherapee, 'render_photo', None)
                        if renderer is not None:
                            record = renderer(source, temporary / 'render', profile=raw_profile,
                                              executable=raw_executable, preview_edge=max_edge,
                                              write_record=False)
                            if record.get('status') != 'completed':
                                raise ValueError(record.get('reason') or record.get('error') or 'RAW preview rendering failed')
                            small = Path(record.get('jpeg_path') or record['final_path'])
                            kind = 'raw_small_render'
                        elif raw is not None:
                            raw.unpack()
                            pixels = raw.postprocess(half_size=True, use_camera_wb=True,
                                                     output_color=rawpy.ColorSpace.sRGB, output_bps=8)
                            _save_small(Image.fromarray(pixels), small, max_edge)
                            kind = 'raw_half_size'
                        else:
                            raise ValueError('RAW has no usable embedded preview or small-preview renderer')
                    else:
                        kind, embedded_profile = embedded
                    info = analyze_image(small, preview, max_edge=max_edge)
                    if embedded is not None:
                        info['source_profile'] = embedded_profile
            basis = 'raw_metadata' if dimensions else 'preview'
            if dimensions:
                info['width'], info['height'] = dimensions
            info['time'].update({key: metadata[key] for key in ('captured_at', 'camera_model', 'camera_make') if metadata.get(key)})
            info['time']['source_mtime_ns'] = before['mtime_ns']
        finally:
            if raw is not None:
                raw.close()
    if stamp(source) != before:
        preview.unlink(missing_ok=True)
        raise ValueError('Source metadata changed during preview')
    info.update(source_stamp=before, preview_kind=kind, preview_source=preview_source,
                dimensions_basis=basis)
    return info
