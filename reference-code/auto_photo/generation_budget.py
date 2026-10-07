"""Reserve built-in generation handoffs before an agent calls the image tool.

This ledger counts registered handoffs, including uncertain outcomes. It cannot
observe or prevent calls made outside this entry point. Imports without an
attempt ID are recorded as reused or untracked, never invented paid calls.
"""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

TILES = ('TL', 'TR', 'BL', 'BR')


def planned_calls(plan):
    """Baseline calls for this plan, not cumulative across plan revisions."""
    return sum(0 if photo['route'] == 'C' else
               4 if (photo.get('generation') or {}).get('method', 'tiled' if photo['route'] == 'T' else 'whole') == 'tiled' else 1
               for photo in plan.get('photos', []))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _used(item):
    return sum(row.get('counted') is True for row in item.get('generation_attempts', []))


def _budget(state):
    plan = state.get('plan') or {}
    limit = plan.get('collection_strategy', {}).get('generation_budget', planned_calls(plan))
    used = sum(_used(item) for item in state['items'])
    return {'limit': limit, 'used': used, 'remaining': max(0, limit-used)}


def _slot(item, tile):
    photo = item.get('plan') or {}
    if photo.get('route') not in ('A','B','T'):
        raise ValueError('Generation requires an A/B/T plan')
    tiled = (photo.get('generation') or {}).get('method', 'tiled' if photo['route'] == 'T' else 'whole') == 'tiled'
    if tiled and tile not in TILES:
        raise ValueError('Tiled generation requires TL, TR, BL or BR')
    if not tiled and tile is not None:
        raise ValueError('Whole-image generation cannot select a tile')
    return photo


def begin_generation(root, photo_id, tile=None, retry_reason=None):
    """Return a reserved attempt or an existing pending one without another call.

The caller must pass the returned attempt_id to import_generated metadata. An
explicit retry may supersede a pending outcome, but both reservations count.
    """
    from .workflow import _lock, _find, _save, load_batch
    if retry_reason is not None and (not isinstance(retry_reason, str) or not retry_reason.strip()):
        raise ValueError('retry_reason must explain why another generation is needed')
    with _lock(root) as root:
        state = load_batch(root)
        if state.get('stop_requested'):
            raise ValueError('Batch is stopped; resume before reserving a generation')
        item = _find(state, photo_id)
        photo = _slot(item, tile)
        generation = photo['generation']
        if generation.get('method', 'tiled' if photo['route'] == 'T' else 'whole') == 'tiled' and not generation.get('upgrade_reason'):
            raise ValueError('A new tiled generation needs an explicit upgrade_reason; existing native tiles may still be reused')
        request = item.get('generation_request')
        if not request or request.get('plan_revision') != item['plan_revision'] or request.get('source_revision') != item['source_revision']:
            raise ValueError('Run the current plan first to prepare its generation handoff')
        handoff = deepcopy(request)
        if tile:
            handoff['input'] = request['tiles'][tile]['path']
            handoff['tile'] = tile
            handoff['input_dimensions'] = request['tiles'][tile]['dimensions']
        attempts = item.setdefault('generation_attempts', [])
        same_slot = [row for row in attempts if row.get('tile') == tile and row.get('counted') is True]
        pending = next((row for row in reversed(same_slot) if row['status'] in ('reserved','outcome_unknown')), None)
        if pending and retry_reason is None:
            return {'attempt_id': pending['attempt_id'], 'photo_id': photo_id, 'tile': tile,
                    'status': 'pending', 'should_call': False, 'budget': _budget(state),
                    'message': 'The previous handoff outcome is pending; import its result or explicitly request a bounded retry.'}
        existing = item.get('generated_tiles', {}).get(tile) if tile else item.get('generated')
        if (same_slot or existing) and retry_reason is None:
            raise ValueError('A generation already exists; another call requires retry_reason')
        maximum = generation.get('max_attempts', 1)
        if len(same_slot) >= maximum:
            raise ValueError(f'Generation attempt limit reached for this photo/tile ({maximum})')
        budget = _budget(state)
        if budget['used'] >= budget['limit']:
            raise ValueError('Batch generation budget exhausted; uncertain calls remain counted')
        if pending:
            pending['status'] = 'outcome_unknown'
        attempt = {'attempt_id': str(uuid4()), 'photo_id': photo_id, 'tile': tile,
                   'source_revision': item['source_revision'], 'plan_revision': item['plan_revision'],
                   'request_id': request['request_id'], 'number': len(same_slot)+1,
                   'status': 'reserved', 'counted': True, 'reserved_at': _now()}
        if retry_reason is not None:
            attempt['retry_reason'] = retry_reason.strip()
        attempts.append(attempt)
        item['generation_calls'] = _used(item)
        state['generation_calls'] = sum(_used(row) for row in state['items'])
        _save(root, state)
        return {'attempt_id': attempt['attempt_id'], 'photo_id': photo_id, 'tile': tile,
                'status': 'reserved', 'should_call': True, 'attempt_number': attempt['number'],
                'budget': _budget(state), 'request': handoff}


def record_generation_import(state, item, metadata, tile=None):
    """Return an item patch for the same atomic commit as the imported artifact."""
    _slot(item, tile)
    metadata = metadata or {}
    attempts = deepcopy(item.get('generation_attempts', []))
    attempt_id = metadata.get('attempt_id')
    if attempt_id is not None and metadata.get('reused') is True:
        raise ValueError('A registered generation attempt cannot also be marked reused')
    if attempt_id is not None:
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError('metadata.attempt_id must be a nonempty registered attempt ID')
        entry = next((row for row in attempts if row['attempt_id'] == attempt_id), None)
        if not entry or entry.get('counted') is not True:
            raise ValueError('Generation attempt is not registered for this photo')
        if entry.get('tile') != tile or entry.get('source_revision') != item['source_revision'] or entry.get('plan_revision') != item['plan_revision']:
            raise ValueError('Generation attempt belongs to another tile or plan/source revision')
        entry.update(status='received', received_at=entry.get('received_at', _now()))
    else:
        attempts.append({'attempt_id': str(uuid4()), 'photo_id': item['id'], 'tile': tile,
                         'source_revision': item['source_revision'], 'plan_revision': item['plan_revision'],
                         'status': 'reused' if metadata.get('reused') is True else 'untracked',
                         'counted': False, 'received_at': _now()})
    patch = {'generation_attempts': attempts,
             'generation_calls': sum(row.get('counted') is True for row in attempts)}
    return patch
