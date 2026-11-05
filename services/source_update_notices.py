"""Durable, bounded notices for automatic schedule refresh incidents."""
from __future__ import annotations

from hashlib import sha256
import json


MAX_INCIDENTS = 32
NOTICE_TEXT = (
    'Не удалось проверить автообновление расписания. Повторю проверку позже. '
    'Для ручной проверки открой «Мой план» в меню.'
)
IDENTITY_NOTICE_TEXT = (
    'Не удалось безопасно подтвердить исходное расписание. Открой «Мой план» в меню; '
    'если ошибка остаётся, напиши в комментариях канала. История сохранена.'
)


def issue_fingerprint(category: str, diagnostic: str = '') -> str:
    """Hash only bounded, already-sanitized diagnostic data."""
    value = json.dumps([category, diagnostic], ensure_ascii=False, separators=(',', ':'))
    return sha256(value.encode()).hexdigest()


def record_issue(state: dict, *, category: str, diagnostic: str = '',
                 notice: str = NOTICE_TEXT) -> bool:
    """Queue a distinct issue once; pending notices are never overwritten.

    Returns true when a new pending notice was added. Diagnostics must be
    safe summaries supplied by the caller, never arbitrary provider messages.
    """
    fingerprint = issue_fingerprint(category, diagnostic)
    incidents = state.setdefault('source_update_incidents', [])
    pending = state.setdefault('source_update_notice_queue', [])
    if any(item.get('fingerprint') == fingerprint for item in incidents):
        return False
    if any(item.get('fingerprint') == fingerprint for item in pending):
        return False
    if len(pending) >= MAX_INCIDENTS:
        return False
    incidents.append({'fingerprint': fingerprint, 'category': category,
                      'diagnostic': diagnostic})
    del incidents[:-MAX_INCIDENTS]
    pending.append({'fingerprint': fingerprint, 'text': notice, 'kind': 'incident'})
    state.setdefault('source_notice', pending[0]['text'])
    return True


def queue_success_notice(state: dict, notice: str) -> None:
    """Queue a successful update notice separately from failure incidents."""
    fingerprint = 'success:' + sha256(notice.encode()).hexdigest()
    queue = state.setdefault('source_update_notice_queue', [])
    if any(item.get('fingerprint') == fingerprint for item in queue):
        return
    if len(queue) >= MAX_INCIDENTS:
        return
    queue.append({'fingerprint': fingerprint, 'text': notice, 'kind': 'success'})
    state.setdefault('source_notice', queue[0]['text'])


def clear_incidents(state: dict) -> None:
    """Clear stale failure alerts while retaining undelivered success notices."""
    state.pop('source_update_incidents', None)
    prior_queue = state.get('source_update_notice_queue', [])
    queue = [item for item in prior_queue
             if item.get('kind') == 'success']
    legacy_notice = state.get('source_notice')
    if not queue and not prior_queue and legacy_notice and not state.get('last_source_problem'):
        # Older versions stored successful updates in source_notice and cleared
        # last_source_problem when creating them; preserve that pending notice.
        queue = [{'fingerprint': 'legacy-success:' + sha256(legacy_notice.encode()).hexdigest(),
                  'text': legacy_notice, 'kind': 'success'}]
    if queue:
        state['source_update_notice_queue'] = queue
        state['source_notice'] = queue[0]['text']
    else:
        state.pop('source_update_notice_queue', None)
        state.pop('source_notice', None)


def pending_notice(state: dict) -> dict | None:
    queue = state.get('source_update_notice_queue') or []
    if queue:
        return queue[0]
    # Drain notices written by versions predating the durable queue.
    if state.get('source_notice'):
        return {'fingerprint': 'legacy', 'text': state['source_notice']}
    return None


def acknowledge_notice(state: dict, fingerprint: str) -> None:
    """Remove a successfully delivered notice and remember it for this streak."""
    queue = state.get('source_update_notice_queue') or []
    acknowledged = queue[0] if queue and queue[0].get('fingerprint') == fingerprint else None
    if queue and queue[0].get('fingerprint') == fingerprint:
        queue.pop(0)
    state['source_update_notice_queue'] = queue
    if fingerprint == 'legacy':
        state.pop('source_notice', None)
    elif queue:
        state['source_notice'] = queue[0]['text']
    else:
        state.pop('source_notice', None)
    if fingerprint == 'legacy':
        return
    if acknowledged and acknowledged.get('kind') == 'success':
        return
    incidents = state.setdefault('source_update_incidents', [])
    if not any(item.get('fingerprint') == fingerprint for item in incidents):
        incidents.append({'fingerprint': fingerprint, 'category': 'delivered', 'diagnostic': ''})
        del incidents[:-MAX_INCIDENTS]
