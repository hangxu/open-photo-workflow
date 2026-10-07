"""Optional, offline macOS Vision candidate detection; never authorizes restoration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time

from PIL import Image, ImageOps
from .artifacts import stamp, atomic_json

_LIMITATIONS = [
    'Candidate boxes only; text boxes are not exact paper masks or approved restore regions.',
    'Generic watch/object detection is unavailable.',
    'Faces may be missed; face counts do not determine people roles or identities.',
    'Detection uses an EXIF-oriented preview with maximum edge 1600; small details may be missed.',
]


def _top_left_bbox(box):
    """Convert Vision normalized lower-left x,y,width,height to top-left corners."""
    x, y, width, height = map(float, box)
    return [max(0., min(1., v)) for v in (x, 1 - y - height, x + width, 1 - y)]


def _prepare_preview(source, destination):
    with Image.open(source) as original:
        preview = ImageOps.exif_transpose(original).convert('RGB')
        preview.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        preview.save(destination, format='PNG')
        return preview.size


def _backend(deadline):
    source = Path(__file__).with_name('vision_detect.swift')
    identity = {'source_stamp': stamp(source), 'platform': platform.platform()}
    cache = Path(tempfile.gettempdir()) / f'auto-photo-vision-{os.getuid()}'
    cache.mkdir(mode=0o700, exist_ok=True)
    if cache.is_symlink() or cache.stat().st_uid != os.getuid():
        raise RuntimeError('unsafe detector cache directory')
    executable = cache / 'detect'
    record_path = cache / 'build.json'
    if executable.is_symlink() or record_path.is_symlink():
        raise RuntimeError('unsafe detector cache files')
    try:
        build_record = json.loads(record_path.read_text())
    except (OSError, ValueError):
        build_record = {}
    if (not executable.is_file() or build_record.get('identity') != identity
            or build_record.get('output_stamp') != stamp(executable)):
        with tempfile.TemporaryDirectory(dir=cache) as build:
            target = Path(build) / 'detect'
            subprocess.run(['/usr/bin/swiftc', '-module-cache-path', str(Path(build) / 'modules'),
                            str(source), '-o', str(target)], capture_output=True, check=True,
                           timeout=max(.01, deadline - time.monotonic()))
            os.replace(target, executable)
            atomic_json(record_path, {'identity': identity, 'output_stamp': stamp(executable)})
    return executable


def detect_candidates(path, *, enabled=True, timeout=90, recognition_level='fast') -> dict:
    """Return local candidate regions; unavailable backends fail closed without OCR text.

    Coordinates refer to the EXIF-oriented source, normalized to its dimensions.
    ``recognition_level`` may be ``fast`` or ``accurate``. Timeout covers build/run.
    """
    start = time.monotonic()
    result = dict(available=False, backend='macos-vision', regions=[],
                  limitations=list(_LIMITATIONS), elapsed_seconds=0.)
    try:
        if not enabled:
            result['reason'] = 'disabled'
        elif platform.system() != 'Darwin':
            result['reason'] = 'macOS Vision is unavailable on this platform'
        elif recognition_level not in ('fast', 'accurate'):
            result['reason'] = 'recognition_level must be fast or accurate'
        else:
            deadline = start + max(0., float(timeout))
            executable = _backend(deadline)
            with tempfile.TemporaryDirectory(prefix='auto-photo-detection-') as scratch:
                preview = Path(scratch) / 'preview.png'
                size = _prepare_preview(path, preview)
                run = subprocess.run([str(executable), str(preview), recognition_level],
                                     capture_output=True, text=True, check=True,
                                     timeout=max(.01, deadline - time.monotonic()))
                payload = json.loads(run.stdout)
            regions = []
            for index, row in enumerate(payload['regions'], 1):
                if row['kind'] not in ('text', 'face'):
                    continue
                regions.append(dict(id=f"{row['kind']}-{index}", kind=row['kind'],
                                    bbox=_top_left_bbox(row['bbox']), confidence=float(row['confidence'])))
            result.update(available=True, regions=regions, preview_size=list(size),
                          recognition_level=recognition_level)
    except subprocess.TimeoutExpired:
        result['reason'] = 'local detector timeout'
    except subprocess.CalledProcessError:
        result['reason'] = 'Vision build or execution failed (SDK/backend unavailable)'
    except Exception as error:
        # Do not echo file paths, OCR content, or compiler diagnostics into reports.
        result['reason'] = f'local detector unavailable ({type(error).__name__})'
    result['elapsed_seconds'] = round(time.monotonic() - start, 4)
    return result
