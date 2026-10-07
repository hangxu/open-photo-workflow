"""Small delivery-oriented summaries from the authoritative state only."""
from datetime import datetime
from .workflow import load_batch, _review_valid


def _seconds(start, end):
    if not start or not end:
        return None
    try:
        delta = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        return round(delta, 3) if delta >= 0 else None
    except (ValueError, TypeError):
        return None


def summarize(batch):
    items = batch['items']
    approved, minor, rejected, final_count = 0, 0, 0, 0
    attempts, untracked = [], 0
    for item in items:
        revision = item.get('final_revision')
        review = item.get('review') or {}
        final_count += bool(revision and item.get('jpeg_path'))
        if revision and review.get('final_revision') == revision:
            if _review_valid(batch,item) and item.get('technical_pass') is True:
                approved += 1
                minor += review.get('severity') == 'minor'
            elif review.get('passed') is False:
                rejected += 1
        history = item.get('generation_attempts', [])
        attempts.extend(history)
        untracked += sum(row.get('status') == 'untracked' for row in history)
        # Legacy imports have no reliable request accounting. They are unknown,
        # not free generations and not evidence for a zero-call throughput claim.
        if not history:
            untracked += bool(item.get('generated')) + len(item.get('generated_tiles', {}))
    reserved = sum(row.get('counted') is True for row in attempts)
    return {
        'batch_id': batch['batch_id'],
        'photos': len(items),
        'input_files': sum(1 + bool(item.get('paired_jpeg')) for item in items),
        'finals': final_count, 'approved': approved, 'approved_with_minor_issues': minor,
        'rejected': rejected, 'not_yet_approved': len(items) - approved,
        'approval_rate': round(approved / len(items), 4) if items else None,
        'generation_reservations': reserved,
        'generation_results_received': sum(row.get('status') == 'received' for row in attempts),
        'generation_outcome_pending': sum(row.get('status') in ('reserved', 'outcome_unknown') for row in attempts),
        'reused_imports': sum(row.get('status') == 'reused' for row in attempts),
        'untracked_imports': untracked,
        'reservations_per_approved': round(reserved / approved, 3) if approved and not untracked else None,
        'first_final_seconds': _seconds(batch.get('created_at'), batch.get('first_final_at')),
        'first_approval_seconds': _seconds(batch.get('created_at'), batch.get('first_approved_at')),
        'wall_seconds_to_last_state': _seconds(batch.get('created_at'), batch.get('updated_at')),
        'token_usage': None,
        'measurement_notes': [
            'Reservations include unknown outcomes; they are not confirmed provider calls or charges.',
            'Elapsed time includes pauses and review; no image or other task was inspected.',
            'Approval measures recorded review decisions, not independent user preference.',
        ],
    }


def batch_metrics(root):
    return summarize(load_batch(root))
