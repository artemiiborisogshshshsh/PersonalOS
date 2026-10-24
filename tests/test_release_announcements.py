import json
import os
import stat
import threading

import pytest

from services.release_announcements import CHANNEL, announce_release


REV = 'a' * 40


def args(text='Release!'):
    release = {'id': 'v1', 'revision': REV, 'text': text}
    return release, {'status': 'success', 'release_id': 'v1', 'revision': REV}


def success(mid=7):
    return {'ok': True, 'result': {'message_id': mid, 'chat': {'id': -1}}}


@pytest.mark.parametrize('receipt', [
    {'status': 'draft', 'release_id': 'v1', 'revision': REV},
    {'status': 'failed', 'release_id': 'v1', 'revision': REV},
    {'status': 'success', 'release_id': 'other', 'revision': REV},
    {'status': 'success', 'release_id': 'v1', 'revision': 'b' * 40},
])
def test_invalid_receipt_never_sends(tmp_path, receipt):
    release, _ = args()
    calls = []
    with pytest.raises(ValueError):
        announce_release(tmp_path, release, receipt, lambda *a: calls.append(a))
    assert calls == []


def test_deduplicates_after_restart_and_rejects_content_change(tmp_path):
    release, receipt = args()
    calls = []
    transport = lambda channel, text: (calls.append((channel, text)) or success())
    assert announce_release(tmp_path, release, receipt, transport)['status'] == 'sent'
    assert announce_release(tmp_path, release, receipt, transport)['status'] == 'already_sent'
    assert len(calls) == 1 and calls[0][0] == CHANNEL
    changed, receipt = args('Different')
    with pytest.raises(ValueError):
        announce_release(tmp_path, changed, receipt, transport)
    assert len(calls) == 1


def test_explicit_rejection_can_retry(tmp_path):
    release, receipt = args()
    calls = []
    def transport(channel, text):
        calls.append(1)
        return {'ok': False, 'error_code': 429, 'description': 'private token=secret'} if len(calls) == 1 else success(8)
    assert announce_release(tmp_path, release, receipt, transport) == {'status': 'rejected', 'error_code': 429}
    assert announce_release(tmp_path, release, receipt, transport)['status'] == 'sent'
    assert len(calls) == 2
    assert 'secret' not in (tmp_path / 'release_announcements.json').read_text()


def test_reservation_write_failure_never_sends(tmp_path, monkeypatch):
    import services.release_announcements as module
    release, receipt = args()
    calls = []
    def fail(*a, **k):
        raise OSError('secret')
    monkeypatch.setattr(module, '_write', fail)
    with pytest.raises(RuntimeError, match='state'):
        announce_release(tmp_path, release, receipt, lambda *a: calls.append(a))
    assert calls == []


def test_concurrent_calls_send_once(tmp_path):
    release, receipt = args()
    calls = []
    barrier = threading.Barrier(2)
    outcomes = []
    def transport(channel, text):
        calls.append(1)
        return success()
    def run():
        barrier.wait()
        outcomes.append(announce_release(tmp_path, release, receipt, transport))
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(calls) == 1
    assert {x['status'] for x in outcomes} == {'sent', 'already_sent'}


def test_crash_leaves_sending_then_becomes_uncertain(tmp_path):
    release, receipt = args()
    def crash(*a):
        raise KeyboardInterrupt('secret description')
    with pytest.raises(KeyboardInterrupt):
        announce_release(tmp_path, release, receipt, crash)
    calls = []
    assert announce_release(tmp_path, release, receipt, lambda *a: calls.append(a)) == {'status': 'uncertain'}
    assert calls == []
    data = json.loads((tmp_path / 'release_announcements.json').read_text())
    assert next(iter(data['entries'].values()))['status'] == 'uncertain'


def test_ambiguous_response_and_bad_message_id_are_uncertain(tmp_path):
    for response in ({'ok': True, 'result': {'message_id': 0, 'chat': {}}}, {'ok': True}):
        location = tmp_path / str(len(list(tmp_path.iterdir())))
        release, receipt = args()
        assert announce_release(location, release, receipt, lambda *a: response) == {'status': 'uncertain'}
        assert announce_release(location, release, receipt, lambda *a: pytest.fail('must not retry')) == {'status': 'uncertain'}


def test_utf16_limit_and_channel_pin(tmp_path):
    release, receipt = args('😀' * 2048)
    assert announce_release(tmp_path, release, receipt, lambda *a: success())['status'] == 'sent'
    release, receipt = args('😀' * 2049)
    with pytest.raises(ValueError):
        announce_release(tmp_path / 'too-long', release, receipt, lambda *a: pytest.fail('must not send'))
    release, receipt = args()
    with pytest.raises(ValueError):
        announce_release(tmp_path / 'wrong-channel', release, receipt, lambda *a: pytest.fail('must not send'), '@other')


@pytest.mark.parametrize('text', ['', ' \n\t', '\ud800'])
def test_empty_whitespace_or_unencodable_text_rejected(tmp_path, text):
    release, receipt = args(text)
    with pytest.raises(ValueError, match='Invalid announcement text'):
        announce_release(tmp_path, release, receipt, lambda *a: pytest.fail('must not send'))


def test_malformed_existing_entry_fails_closed(tmp_path):
    release, receipt = args()
    (tmp_path / 'release_announcements.json').write_text(json.dumps({
        'version': 1, 'entries': {CHANNEL + ':v1': {}}
    }))
    with pytest.raises(RuntimeError, match='Invalid announcement state'):
        announce_release(tmp_path, release, receipt, lambda *a: pytest.fail('must not send'))


@pytest.mark.parametrize('exception', [ValueError('secretValueError'), RuntimeError('secretRuntimeError')])
def test_transport_errors_are_sanitized_and_quarantined(tmp_path, exception):
    release, receipt = args()
    result = announce_release(tmp_path, release, receipt, lambda *a: (_ for _ in ()).throw(exception))
    assert result == {'status': 'uncertain'}
    assert 'secret' not in (tmp_path / 'release_announcements.json').read_text()
    assert announce_release(tmp_path, release, receipt,
                            lambda *a: pytest.fail('must not retry')) == {'status': 'uncertain'}


def test_provider_5xx_is_uncertain_and_not_retried(tmp_path):
    release, receipt = args()
    calls = []
    def transport(*_):
        calls.append(1)
        return {'ok': False, 'error_code': 500, 'description': 'private failure'}
    assert announce_release(tmp_path, release, receipt, transport) == {'status': 'uncertain'}
    assert announce_release(tmp_path, release, receipt, transport) == {'status': 'uncertain'}
    assert len(calls) == 1


def test_created_parent_entries_are_synced_before_transport(tmp_path, monkeypatch):
    import services.release_announcements as module
    release, receipt = args()
    syncs = []
    real_fsync = os.fsync
    def track(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            syncs.append(fd)
        return real_fsync(fd)
    monkeypatch.setattr(module.os, 'fsync', track)
    def transport(*_):
        # The nested state directory's two new parent entries are synced first.
        assert len(syncs) >= 2
        return success()
    assert announce_release(tmp_path / 'state' / 'nested', release, receipt, transport)['status'] == 'sent'


def test_parent_fsync_failure_prevents_transport(tmp_path, monkeypatch):
    import services.release_announcements as module
    release, receipt = args()
    calls = []
    real_fsync = os.fsync
    seen_directory_fsync = []
    def fail_parent(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) and not seen_directory_fsync:
            seen_directory_fsync.append(fd)
            raise OSError('secret')
        return real_fsync(fd)
    monkeypatch.setattr(module.os, 'fsync', fail_parent)
    with pytest.raises(RuntimeError, match='state operation'):
        announce_release(tmp_path / 'new-state', release, receipt, lambda *a: calls.append(a))
    assert calls == []


def test_retry_resyncs_directory_after_creation_sync_failure(tmp_path, monkeypatch):
    import services.release_announcements as module
    release, receipt = args()
    real_fsync = os.fsync
    failed = []
    synced = []
    def fail_once(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            if not failed:
                failed.append(True)
                raise OSError('secret')
            synced.append(True)
        return real_fsync(fd)
    monkeypatch.setattr(module.os, 'fsync', fail_once)
    target = tmp_path / 'state'
    with pytest.raises(RuntimeError, match='state operation'):
        announce_release(target, release, receipt, lambda *a: pytest.fail('must not send'))
    sent_after_sync = []
    def transport(*_):
        sent_after_sync.append(bool(synced))
        return success()
    assert announce_release(target, release, receipt, transport)['status'] == 'sent'
    assert sent_after_sync == [True]
