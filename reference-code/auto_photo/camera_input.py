"""Prepare a full-resolution camera JPEG without unnecessary RAW development.

Only LibRaw's selected JPEG thumbnail is available through rawpy 0.27. This
module does not scan undocumented camera containers for extra preview images.
"""
from io import BytesIO
from pathlib import Path

from PIL import Image
import rawpy

from .artifacts import stamp
from .color import to_srgb
from .converter import SUPPORTED
from .sources import _metadata_agree, _read_metadata


def raw_source(source):
    source = Path(source).expanduser().resolve(strict=True)
    if source.suffix.lower() not in SUPPORTED:
        raise ValueError('A supported RAW source is required')
    return source


def read_raw_preview(source):
    """Return inexpensive RAW facts and the selected JPEG's original bytes."""
    source = raw_source(source)
    probe = {'source': str(source), 'source_stamp': stamp(source),
             'raw_metadata_dimensions': None, 'raw_flip': 0,
             'embedded_camera_preview': None, 'external_jpeg_used': False,
             'embedded_selection': 'rawpy.extract_thumb: LibRaw-selected preview only; no enumeration'}
    raw = rawpy.RawPy()
    data = None
    try:
        raw.open_file(str(source))
        probe['raw_flip'] = int(raw.sizes.flip)
        dimensions = [int(raw.sizes.width), int(raw.sizes.height)]
        if probe['raw_flip'] in (5, 6):
            dimensions.reverse()
        probe['raw_metadata_dimensions'] = dimensions
        thumbnail = raw.extract_thumb()
        if thumbnail.format == rawpy.ThumbFormat.JPEG:
            data = bytes(thumbnail.data)
            # Full decoding, not merely checking the JPEG header.
            with Image.open(BytesIO(data)) as image:
                if image.format not in ('JPEG', 'MPO'):
                    raise ValueError('Embedded thumbnail is not a JPEG')
                image.seek(0)  # Camera MPO files use frame zero as the photograph.
                image.load()
                probe['embedded_camera_preview'] = {
                    'format': image.format, 'primary_frame': 0, 'dimensions': list(image.size),
                    'bytes': len(data), 'fully_decoded': True}
        else:
            probe['embedded_unavailable_reason'] = 'LibRaw selected a non-JPEG preview'
    except (rawpy.LibRawError, OSError, ValueError) as exc:
        data = None
        probe['embedded_unavailable_reason'] = str(exc)
    finally:
        raw.close()
    return probe, data


def paired_candidate(source, paired_jpeg, raw_metadata):
    """Use only an explicit or unique same-stem pair with no EXIF conflict."""
    if paired_jpeg is False:
        return None, {'stage': 'paired_jpeg', 'accepted': False, 'reason': 'disabled'}
    if paired_jpeg is None:
        siblings = [p for p in source.parent.iterdir()
                    if p.is_file() and p.stem == source.stem]
        raws = [p for p in siblings if p.suffix.lower() in SUPPORTED]
        candidates = [p for p in siblings if p.suffix.lower() in ('.jpg', '.jpeg')]
        if len(raws) != 1 or len(candidates) != 1:
            return None, {'stage': 'paired_jpeg', 'accepted': False,
                          'reason': 'no unique same-stem RAW/JPEG pair'}
        candidate = candidates[0].resolve()
        basis = 'unique_same_stem_and_no_metadata_conflict'
    else:
        candidate = Path(paired_jpeg).expanduser().resolve()
        basis = 'explicit_pair_and_no_metadata_conflict'
    if not candidate.is_file() or candidate.suffix.lower() not in ('.jpg', '.jpeg'):
        return None, {'stage': 'paired_jpeg', 'accepted': False,
                      'reason': 'paired JPEG is missing or has an unsupported extension'}
    metadata = _read_metadata(candidate)
    if not _metadata_agree(raw_metadata, metadata):
        return None, {'stage': 'paired_jpeg', 'accepted': False,
                      'reason': 'capture time or camera metadata conflicts', 'path': str(candidate)}
    return candidate, {'stage': 'paired_jpeg', 'path': str(candidate), 'pairing_basis': basis}


def normalize_jpeg(data, min_edge, *, inherited_color=None, raw_flip=0, raw_dimensions=None):
    """Decode, orient and interpret color once; keep bytes when no edit is needed.

    Return normalized pixels for a possible encode plus the original JPEG bytes
    when they can be published unchanged. No sharpening, grading or upscaling.
    """
    with Image.open(BytesIO(data)) as image:
        if image.format not in ('JPEG', 'MPO'):
            raise ValueError('Camera input is not a JPEG')
        image.seek(0)
        image.load()
        dimensions = list(image.size)
        if max(dimensions) < min_edge:
            raise ValueError(f'JPEG long edge {max(dimensions)} is below {min_edge}')
        orientation = image.getexif().get(274, 1)
        upright, profile = to_srgb(image, inherited_color=inherited_color)
        applied_flip = 0
        # Embedded JPEGs often omit orientation. Respect their own EXIF first;
        # otherwise use RAW orientation, avoiding a second 90-degree rotation
        # when LibRaw already returned upright pixels.
        if orientation in (None, 1):
            if raw_flip == 3:
                upright = upright.transpose(Image.Transpose.ROTATE_180)
                applied_flip = 3
            elif raw_flip in (5, 6) and (not raw_dimensions or
                    (upright.width > upright.height) != (raw_dimensions[0] > raw_dimensions[1])):
                upright = upright.transpose(Image.Transpose.ROTATE_90 if raw_flip == 5
                                             else Image.Transpose.ROTATE_270)
                applied_flip = raw_flip
        original_bytes = (data if orientation in (None, 1) and not applied_flip
                          and profile['conversion'] == 'assumed_srgb' else None)
        return upright, original_bytes, {
            'container_format': image.format, 'primary_frame': 0,
            'jpeg_encoded_dimensions': dimensions, 'dimensions': list(upright.size),
            'dimensions_basis': 'decoded_jpeg', 'source_profile': profile,
            'exif_orientation': orientation, 'raw_flip_applied': applied_flip,
            'original_jpeg_bytes_preserved': original_bytes is not None,
            'new_jpeg_encodings': 0 if original_bytes is not None else 1,
            'upscaled': False}
