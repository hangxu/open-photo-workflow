"""Publish a snapshot of reviewed current JPEGs without running the workflow."""
from pathlib import Path
import json
import os
import tempfile
import zipfile

from .workflow import load_batch


def _path(root, value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _selection(item, root, policy=None):
    """Review binds to the chosen revision, never an older process stage."""
    revision = item.get('final_revision')
    review = item.get('review') or {}
    if not revision:
        return None, 'no_current_final'
    if item.get('technical_pass') is not True:
        return None, 'technical_review_pending'
    if review.get('final_revision') != revision:
        return None, 'current_revision_not_reviewed'
    if review.get('passed') is not True:
        return None, 'current_revision_not_approved'
    if review.get('severity') == 'major':
        return None, 'major_issue_not_approved'
    if (policy or {}).get('tolerance') == 'strict' and review.get('severity') == 'minor':
        return None, 'minor_issue_outside_current_policy'
    requested_edge=(policy or {}).get('viewing_long_edge')
    if requested_edge and item.get('final_dimensions'):
        requested_edge=min(requested_edge,max(item['final_dimensions']))
    reviewed_edge=review.get('viewing_long_edge')
    if requested_edge and reviewed_edge and requested_edge > reviewed_edge:
        return None, 'larger_delivery_scale_not_reviewed'
    value = item.get('jpeg_path') or item.get('final_path')
    if not value:
        return None, 'no_current_jpeg'
    path = _path(root, value)
    if path.suffix.lower() not in ('.jpg', '.jpeg') or not path.is_file():
        return None, 'current_jpeg_missing'
    return path, None


def package_batch(root, output=None):
    """Write one final-only ZIP. Broken unselected photos cannot block export.

    Files are read once by ZipFile using its standard CRC; there is no application
    checksum, image decoding, task check or separate archive read-back.
    """
    batch = load_batch(root)
    root = Path(batch['root'])
    destination = Path(output).expanduser().absolute() if output else root / 'delivery' / 'final-jpg.zip'
    if output and destination.exists():
        raise FileExistsError('Package already exists; choose a new output filename')
    if not output:
        sequence = 2
        while destination.exists():
            destination = root / 'delivery' / f'final-jpg-{sequence:03d}.zip'
            sequence += 1
    if destination.suffix.lower() != '.zip':
        raise ValueError('Package output must use the .zip extension')
    selected, omitted = [], []
    policy=(batch.get('plan') or {}).get('collection_strategy',{}).get('delivery',{})
    names = set()
    for item in batch['items']:
        path, reason = _selection(item, root, policy)
        if reason:
            omitted.append({'id': item['id'], 'reason': reason})
            continue
        ident = item['id']
        if not isinstance(ident, str) or ident in ('.', '..') or any(c in ident for c in ('/', '\\', '\x00')):
            raise ValueError('Photo ID cannot be used as an archive filename')
        name = ident + '.jpg'
        if name in names:
            raise ValueError('Duplicate final filename: ' + name)
        names.add(name)
        selected.append((item, path, name))
    if not selected:
        raise ValueError('No reviewed current JPEGs to package')
    manifest = {
        'batch_id': batch['batch_id'],
        'status': 'complete' if not omitted else 'partial',
        'count': len(selected), 'total': len(batch['items']),
        'items': [{'id': i['id'], 'filename': name, 'final_revision': i['final_revision'],
                   'severity':(i.get('review') or {}).get('severity','none'),
                   'issues':(i.get('review') or {}).get('issues',[])}
                  for i, _, name in selected],
        'omitted': omitted,
    }
    if policy:manifest['delivery_policy']=policy
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + destination.name + '-', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for _, path, name in selected:
                archive.write(path, name)
            archive.writestr('DELIVERY.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        # Publish the finished ZIP atomically without overwriting any existing
        # delivery, including one another process published during this export.
        os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'archive': str(destination), **manifest}
