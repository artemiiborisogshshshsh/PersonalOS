import json

from services.source_update_notices import (
    IDENTITY_NOTICE_TEXT,
    MAX_INCIDENTS,
    NOTICE_TEXT,
    acknowledge_notice,
    clear_incidents,
    pending_notice,
    queue_success_notice,
    record_issue,
)


def deliver(state, sender):
    notice = pending_notice(state)
    if notice is None:
        return False
    sender(notice['text'])
    acknowledge_notice(state, notice['fingerprint'])
    return True


def test_alternating_incidents_queue_once_without_overwriting():
    state = {}
    for category in ('identity', 'provider', 'identity', 'provider'):
        record_issue(state, category=category, diagnostic=category,
                     notice=IDENTITY_NOTICE_TEXT if category == 'identity' else NOTICE_TEXT)
    assert [item['category'] for item in state['source_update_incidents']] == ['identity', 'provider']
    assert [item['text'] for item in state['source_update_notice_queue']] == [
        IDENTITY_NOTICE_TEXT, NOTICE_TEXT,
    ]


def test_pending_and_seen_incidents_survive_restart_and_retry_after_delivery_failure():
    state = {}
    record_issue(state, category='provider', diagnostic='TimeoutError')
    try:
        deliver(state, lambda _text: (_ for _ in ()).throw(RuntimeError('send failed')))
    except RuntimeError:
        pass
    restored = json.loads(json.dumps(state))
    sent = []
    assert deliver(restored, sent.append)
    assert sent == [NOTICE_TEXT]
    assert not deliver(restored, sent.append)
    assert not record_issue(restored, category='provider', diagnostic='TimeoutError')


def test_alternating_failure_queue_persists_and_delivers_in_order_after_restart():
    state = {}
    record_issue(state, category='identity_conflict', diagnostic='identity',
                 notice=IDENTITY_NOTICE_TEXT)
    record_issue(state, category='provider_unavailable', diagnostic='TimeoutError')
    record_issue(state, category='identity_conflict', diagnostic='identity',
                 notice=IDENTITY_NOTICE_TEXT)
    restored = json.loads(json.dumps(state))
    sent = []
    assert deliver(restored, sent.append)
    assert deliver(restored, sent.append)
    assert not deliver(restored, sent.append)
    assert sent == [IDENTITY_NOTICE_TEXT, NOTICE_TEXT]


def test_healthy_reset_allows_incident_recurrence():
    state = {}
    record_issue(state, category='identity', diagnostic='identity mismatch')
    clear_incidents(state)
    # Recovery makes the undelivered failure obsolete.
    assert pending_notice(state) is None
    assert record_issue(state, category='identity', diagnostic='identity mismatch')


def test_distinct_incidents_are_bounded_and_diagnostic_is_explicit():
    state = {}
    for index in range(MAX_INCIDENTS + 2):
        record_issue(state, category='provider', diagnostic=f'category-{index}')
    assert len(state['source_update_incidents']) == MAX_INCIDENTS
    assert len(state['source_update_notice_queue']) == MAX_INCIDENTS
    assert all('credential' not in item['diagnostic'].lower()
               for item in state['source_update_incidents'])


def test_provider_diagnostic_can_be_a_safe_category_only():
    state = {}
    record_issue(state, category='provider_unavailable', diagnostic='TimeoutError')
    serialized = repr(state)
    assert 'TimeoutError' in serialized
    assert 'secret' not in serialized.lower()
    assert 'token' not in serialized.lower()


def test_undelivered_success_notice_survives_healthy_reset_and_restart():
    state = {}
    success = 'Расписание изменилось. Google Calendar обновлён.'
    queue_success_notice(state, success)
    try:
        deliver(state, lambda _text: (_ for _ in ()).throw(RuntimeError('send failed')))
    except RuntimeError:
        pass

    # A subsequent known healthy no-change result clears incidents, but must
    # leave the already queued success message available for delivery.
    record_issue(state, category='provider_unavailable', diagnostic='TimeoutError')
    clear_incidents(state)
    restored = json.loads(json.dumps(state))
    sent = []
    assert deliver(restored, sent.append)
    assert sent == [success]
    assert not deliver(restored, sent.append)
