"""Explicit EXIF/ICC color interpretation shared by previews and grading."""
from io import BytesIO
import os
from pathlib import Path

from PIL import ImageCms, ImageOps


class ColorProfileError(ValueError):
    """Color evidence cannot be converted safely; do not silently assume sRGB."""


def color_evidence(tags, detail=None, interoperability=None):
    detail, interoperability = detail or {}, interoperability or {}
    index = interoperability.get(1)
    if isinstance(index, bytes):
        index = index.decode('ascii', errors='replace')
    return {key: value for key, value in {
        'colorspace': detail.get(40961, tags.get(40961)),
        'interop_index': index.rstrip('\x00') if isinstance(index, str) else index,
    }.items() if value is not None}


def image_color_evidence(image):
    exif = image.getexif()
    detail, interoperability = {}, {}
    try:
        detail = exif.get_ifd(34665)
        interoperability = exif.get_ifd(40965)
    except (KeyError, TypeError, ValueError):
        pass
    return color_evidence(exif, detail, interoperability)


def to_srgb(image, inherited_color=None):
    """Orient and convert; parent RAW evidence applies only to untagged previews.

    EXIF ColorSpace=65535 plus InteroperabilityIndex=R03 identifies Adobe RGB.
    Other uncalibrated spaces are deliberately not guessed. On non-macOS hosts,
    set AUTO_PHOTO_ADOBE_RGB_ICC to an installed Adobe RGB (1998) ICC profile.
    """
    icc = image.info.get('icc_profile')
    evidence = image_color_evidence(image)
    inherited = not evidence and bool(inherited_color) and not icc
    if inherited:
        evidence = dict(inherited_color)
    upright = ImageOps.exif_transpose(image)
    profile = {'embedded': bool(icc), 'description': None, 'conversion': 'assumed_srgb',
               'exif_color': evidence, 'inherited_from_raw': inherited}
    input_profile = None
    if icc:
        try:
            input_profile = ImageCms.ImageCmsProfile(BytesIO(icc))
        except (OSError, ValueError, ImageCms.PyCMSError) as exc:
            raise ColorProfileError(f'cannot safely convert embedded color profile: {exc}') from exc
        profile['conversion'] = 'icc_to_srgb'
    elif upright.mode != 'RGB':
        raise ColorProfileError('Untagged non-RGB or non-sRGB source is ambiguous')
    elif evidence.get('colorspace') == 65535 and evidence.get('interop_index') == 'R03':
        path = Path(os.environ.get('AUTO_PHOTO_ADOBE_RGB_ICC',
                                  '/System/Library/ColorSync/Profiles/AdobeRGB1998.icc')).expanduser()
        try:
            input_profile = ImageCms.ImageCmsProfile(str(path))
        except (OSError, ValueError, ImageCms.PyCMSError) as exc:
            raise ColorProfileError('Adobe RGB EXIF source requires an installed Adobe RGB (1998) '
                                    f'ICC profile; set AUTO_PHOTO_ADOBE_RGB_ICC (tried {path})') from exc
        profile['conversion'] = 'exif_adobe_rgb_to_srgb'
    elif evidence.get('colorspace') not in (None, 1) or evidence.get('interop_index') not in (None, 'R98'):
        raise ColorProfileError('Untagged non-RGB or non-sRGB source is ambiguous')
    if input_profile is None:
        return upright.copy(), profile
    try:
        profile['description'] = ImageCms.getProfileDescription(input_profile).strip()
        upright = ImageCms.profileToProfile(upright, input_profile,
                                           ImageCms.createProfile('sRGB'), outputMode='RGB')
    except (OSError, ValueError, ImageCms.PyCMSError) as exc:
        raise ColorProfileError(f'cannot safely convert source color profile: {exc}') from exc
    return upright, profile
