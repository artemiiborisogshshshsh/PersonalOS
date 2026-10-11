"""Pilot critical path with synthetic TPU and in-memory Google only."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from services.closed_beta_runtime import ClosedBetaApplication, PilotBot, PilotSource
from services.published_plan_updates import PublishedPlanUpdates, decode
from services.class_calendar_recovery import ClassCalendarRecoveryError, inspect_recovery
from services.user_state_backup import UserStateBackupService

NOW = datetime(2026, 9, 22, 9, tzinfo=ZoneInfo('Asia/Tomsk'))
URL = 'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html'
ICS = '''BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:pilot-lecture\r
DTSTART:20260924T030000Z\r
DTEND:20260924T043000Z\r
SUMMARY:ОС (ЛК)\r
END:VEVENT\r
END:VCALENDAR\r
'''.encode()


class Calendar:
    def __init__(self):
        self.is_initialized = False
        self.remote = {}
        self.writes = []
        self.calendars = {}
        self.read_failure = False
        self.fail_write_number = None
        self.fail_calendar_after_create = False
        self.fail_insert_after_write = False
        self.created_replacements = 0

    async def initialize(self):
        self.is_initialized = True
        return True

    def list_visible_calendars(self):
        if self.read_failure:
            raise RuntimeError('secret-provider-url')
        return [{'id': key, 'summary': key} for key in self.calendars]

    def list_events_in_calendar(self, calendar, *args, **kwargs):
        if self.read_failure:
            raise RuntimeError('secret-provider-url')
        return [deepcopy(e) for (cal, _), e in self.remote.items() if cal == calendar]

    def _get_or_create_calendar(self, name):
        if name not in self.calendars:
            self.writes.append(('calendar', name))
            self.calendars[name] = name
        return name

    def calendar_is_accessible(self, calendar_id):
        if self.read_failure:
            raise RuntimeError('provider denied')
        return calendar_id in self.calendars

    def replacement_calendar(self, old_id, name, *, allow_create, before_create=None):
        if old_id in self.calendars:
            raise RuntimeError('old calendar still listed')
        matches = [key for key, summary in self.calendars.items() if summary == name]
        if matches:
            return matches[0]
        if not allow_create:
            raise RuntimeError('creation uncertain')
        if before_create:
            before_create()
        self.created_replacements += 1
        new_id = name + ('-replacement' if self.created_replacements == 1
                         else f'-replacement-{self.created_replacements}')
        self.writes.append(('calendar', name))
        self.calendars[new_id] = name
        if self.fail_calendar_after_create:
            self.fail_calendar_after_create = False
            raise RuntimeError('uncertain create result')
        return new_id

    def get_event_by_uid(self, calendar, uid, **kwargs):
        if self.read_failure:
            raise RuntimeError('secret-provider-url')
        return next((deepcopy(e) for (cal, _), e in self.remote.items()
                     if cal == calendar and e['iCalUID'] == uid), None)

    def _insert_event(self, event, calendar_id, **kwargs):
        calendar = calendar_id
        if self.fail_write_number == len(self.writes):
            raise ValueError('secret-provider-error')
        identifier = 'remote-' + str(len(self.remote))
        self.writes.append(('insert', event.uid))
        self.remote[(calendar, identifier)] = dict(
            id=identifier, etag='etag-' + identifier, iCalUID=event.uid, summary=event.summary, description=event.description,
            start={'dateTime': event.dtstart.isoformat()}, end={'dateTime': event.dtend.isoformat()},
            extendedProperties={'private': {
                'personal_os_block_id': getattr(event, 'system_block_id', ''),
                'personal_os_source_event_id': getattr(event, 'system_source_event_id', ''),
                'personal_os_operation_id': getattr(event, 'system_operation_id', ''),
            }},
        )
        if self.fail_insert_after_write:
            self.fail_insert_after_write = False
            raise ValueError('uncertain insert result')
        return identifier

    def _update_event(self, *args, **kwargs):
        raise AssertionError('Frozen pilot must not update published records')

    def _delete_event(self, *args, **kwargs):
        raise AssertionError('Pilot must not delete records')


def application(tmp_path, adapter=None, content=None):
    adapter = adapter or Calendar()
    content = content if content is not None else {'ics': ICS}
    def fetch(url, path, **kwargs):
        assert url == URL
        assert kwargs == {'auto_period': True, 'export_variant_id': 2}
        if content.get('fail'):
            raise ValueError('secret-source-error')
        path.write_bytes(content['ics'])
    return ClosedBetaApplication(tmp_path, '101', adapter,
        source_factory=lambda store: PilotSource(store, fetcher=fetch), now=lambda: NOW)


def onboard(app):
    assert app.handle_text('101', '/start')['step'] == 'timezone'
    assert app.handle_callback('101', 'ob:timezone:Asia/Tomsk')['step'] == 'source'
    assert app.handle_text('101', '/connect_tpu@pilot ' + URL)['step'] == 'attendance'
    response = app.handle_text('101', '/attendance')
    for _ in range(10):
        if response.get('attendance_complete'):
            break
        response = app.handle_callback('101', response['buttons'][0][0]['callback_data'])
    assert response['attendance_complete']
    assert app.handle_text('101', '/sleep 23:00 06:40')['step'] == 'profile'
    assert app.handle_text('101', '/travel 30')['step'] == 'calendar'
    assert 'подключён' in app.handle_text('101', '/calendar_status')['text']


def confirmation(response):
    return response['buttons'][0][0]['callback_data']


def _publish_class_only(tmp_path):
    content = {'ics': ICS}
    app = application(tmp_path, content=content)
    app.updates = PublishedPlanUpdates(app)
    onboard(app)
    preview = app.handle_text('101', '/weekly_preview')
    response = app.handle_callback('101', confirmation(preview))
    assert 'опубликован' in response['text']
    operation = next(iter(app.operations.load_all().values()))
    old_id = next(iter(app.adapter.calendars))
    _, _, current_events, _, _ = app._inputs()
    selected = [event for event in current_events if event.state.value in {'confirmed', 'moved'}]
    all_rows = app.updates.rows(operation, selected)
    class_rows = {uid: row for uid, row in all_rows.items() if row['kind'] == 'class'}
    for uid, row in class_rows.items():
        remote = app.adapter.get_event_by_uid(old_id, uid, strict=True)
        row.update(event_id=remote['id'], etag='etag-' + remote['id'])
    class_uids = set(class_rows)
    operation.blocks = []
    operation.calendar_ids = {}
    operation.calendar_event_ids = {}
    operation.calendar_id = old_id
    operation.status = 'confirmed'
    operation.published_plan = {'version': operation.version, 'calendar_id': old_id, 'rows': class_rows}
    app.operations.save(operation)
    app.adapter.remote = {key: value for key, value in app.adapter.remote.items()
                          if key[0] != old_id or value['iCalUID'] in class_uids}
    return app, old_id, content


def _remove_calendar(app, old_id):
    app.adapter.calendars.pop(old_id, None)
    app.adapter.remote = {key: value for key, value in app.adapter.remote.items() if key[0] != old_id}


def test_confirmed_class_only_deleted_calendar_preview_apply_restart_repeat(tmp_path):
    app, old_id, _ = _publish_class_only(tmp_path / 'tenant')
    _remove_calendar(app, old_id)
    before = list(app.adapter.writes)
    preview = app.handle_text('101', '/weekly_preview')
    assert 'Calendar пока не изменён' in preview['text']
    assert app.adapter.writes == before
    app.handle_callback('101', 'pilot:cancel')
    assert app.adapter.writes == before
    preview = app.handle_text('101', '/weekly_preview')
    response = app.handle_callback('101', confirmation(preview))
    assert 'восстановлен' in response['text']
    count = len(app.adapter.writes)
    plan = next(iter(app.operations.load_all().values())).published_plan
    assert plan['calendar_id'] == 'Personal University Schedule-replacement'
    assert len(plan['rows']) == 1 and not plan.get('recovery_history')
    restarted = application(tmp_path / 'tenant', app.adapter)
    restarted.updates = PublishedPlanUpdates(restarted)
    assert 'уже опубликован' in restarted.handle_text('101', '/weekly_preview')['text']
    assert len(app.adapter.writes) == count


def test_class_recovery_inaccessible_calendar_stops_without_writes(tmp_path):
    app, old_id, _ = _publish_class_only(tmp_path / 'tenant')
    app.adapter.read_failure = True
    before = list(app.adapter.writes)
    response = app.handle_text('101', '/weekly_preview')
    assert not response['buttons']
    assert response['text'] and 'Calendar не изменялся' in response['text']
    assert app.adapter.writes == before


def test_class_recovery_changed_source_signature_stops(tmp_path):
    app, old_id, content = _publish_class_only(tmp_path / 'tenant')
    _remove_calendar(app, old_id)
    content['ics'] = ICS.replace('ОС (ЛК)'.encode(), 'ОС (изменено)'.encode())
    before = list(app.adapter.writes)
    response = app.handle_text('101', '/weekly_preview')
    assert not response['buttons']
    assert response['text'] and not response['buttons']
    assert app.adapter.writes == before


def test_available_calendar_with_changed_source_uses_normal_update_path(tmp_path):
    app, _, content = _publish_class_only(tmp_path / 'tenant')
    content['ics'] = ICS.replace('ОС (ЛК)'.encode(), 'ОС (изменено)'.encode())
    operation = next(iter(app.operations.load_all().values()))
    signature = operation.content_hash + '-changed'
    assert inspect_recovery(app, operation, [], [], signature, NOW) is None


def test_class_recovery_uncertain_calendar_create_resumes_without_duplicate(tmp_path):
    app, old_id, _ = _publish_class_only(tmp_path / 'tenant')
    _remove_calendar(app, old_id)
    app.adapter.fail_calendar_after_create = True
    preview = app.handle_text('101', '/weekly_preview')
    failed = app.handle_callback('101', confirmation(preview))
    assert 'не завершено' in failed['text']
    before = list(app.adapter.writes)
    assert len([c for c, name in app.adapter.writes if c == 'calendar' and name == 'Personal University Schedule']) == 2
    preview = app.handle_text('101', '/weekly_preview')
    assert 'восстановить новый календарь' not in preview['text'].lower()
    response = app.handle_callback('101', confirmation(preview))
    assert 'восстановлен' in response['text']
    assert len([c for c, name in app.adapter.writes if c == 'calendar' and name == 'Personal University Schedule']) == 2
    assert len(app.adapter.writes) > len(before)


def test_class_recovery_uncertain_event_insert_never_blindly_retries(tmp_path):
    app, old_id, _ = _publish_class_only(tmp_path / 'tenant')
    _remove_calendar(app, old_id)
    app.adapter.fail_insert_after_write = True
    preview = app.handle_text('101', '/weekly_preview')
    before_writes = list(app.adapter.writes)
    response = app.handle_callback('101', confirmation(preview))
    assert 'восстановлен' in response['text']
    uid = next(iter(app.operations.load_all().values())).published_plan['rows'].__iter__().__next__()
    inserted = len([row for row in app.adapter.writes if row == ('insert', uid)])
    baseline = len([row for row in before_writes if row == ('insert', uid)])
    assert inserted - baseline == 1
    before = list(app.adapter.writes)
    assert 'уже опубликован' in app.handle_text('101', '/weekly_preview')['text']
    assert app.adapter.writes == before


def test_class_recovery_rejects_operation_change_after_preview(tmp_path):
    app, old_id, _ = _publish_class_only(tmp_path / 'tenant')
    _remove_calendar(app, old_id)
    preview = app.handle_text('101', '/weekly_preview')
    operation = next(iter(app.operations.load_all().values()))
    operation.calendar_ids['unexpected'] = 'other-calendar'
    app.operations.save(operation)
    before = list(app.adapter.writes)
    response = app.handle_callback('101', confirmation(preview))
    assert 'операция изменилась' in response['text'].lower()
    assert app.adapter.writes == before


@pytest.mark.parametrize('field,value', [
    ('blocks', [None]), ('retained_blocks', [None]), ('previous_blocks', [None]),
    ('calendar_event_ids', {'prep': 'event'}),
    ('previous_calendar_event_ids', {'prep': 'event'}),
    ('pending_calendar_writes', {'prep': 'insert'}),
    ('calendar_ids', {'work': 'other-calendar'}),
])
def test_class_recovery_blocks_any_active_or_unknown_preparation_binding(tmp_path, field, value):
    app, old_id, _ = _publish_class_only(tmp_path / field)
    _remove_calendar(app, old_id)
    operation = next(iter(app.operations.load_all().values()))
    setattr(operation, field, value)
    _, now, events, signature, _ = app._inputs()
    selected = [event.id for event in events if event.state.value in {'confirmed', 'moved'}]
    before = list(app.adapter.writes)
    with pytest.raises(ClassCalendarRecoveryError):
        inspect_recovery(app, operation, events, selected, signature, now)
    assert app.adapter.writes == before


def test_class_recovery_preserves_manually_moved_saved_time_and_preview(tmp_path):
    app, old_id, _ = _publish_class_only(tmp_path / 'tenant')
    operation = next(iter(app.operations.load_all().values()))
    uid, row = next(iter(operation.published_plan['rows'].items()))
    source = decode(row['data'])
    moved = deepcopy(row)
    moved_data = dict(row['data'])
    moved_data['dtstart'] = (source.dtstart + timedelta(hours=1)).isoformat()
    moved_data['dtend'] = (source.dtend + timedelta(hours=1)).isoformat()
    moved['data'] = moved_data
    operation.published_plan['manual_resolutions'] = {
        uid: {'kind': 'moved', 'row': moved, 'source_identity': {
            'uid': source.system_source_event_id,
            'start': source.dtstart.isoformat(), 'end': source.dtend.isoformat(),
        }}
    }
    app.operations.save(operation)
    chosen_start = datetime.fromisoformat(moved_data['dtstart'])
    _remove_calendar(app, old_id)
    preview = app.handle_text('101', '/weekly_preview')
    assert f'{chosen_start:%d.%m %H:%M}' in preview['text']
    response = app.handle_callback('101', confirmation(preview))
    assert 'восстановлен' in response['text']
    new_id = next(iter(app.operations.load_all().values())).published_plan['calendar_id']
    remote = app.adapter.get_event_by_uid(new_id, uid, strict=True)
    assert datetime.fromisoformat(remote['start']['dateTime']) == chosen_start
    followup = app.handle_text('101', '/weekly_preview')
    assert 'уже опубликован' in followup['text'] and not followup['buttons']


def test_class_recovery_new_loss_after_final_journal_crash(tmp_path, monkeypatch):
    import services.class_calendar_recovery as recovery
    app, old_id, _ = _publish_class_only(tmp_path / 'tenant')
    _remove_calendar(app, old_id)
    preview = app.handle_text('101', '/weekly_preview')
    original_save = recovery._save

    def crash_before_complete(path, value):
        if value.get('complete'):
            raise OSError('simulated final journal fsync failure')
        original_save(path, value)

    monkeypatch.setattr(recovery, '_save', crash_before_complete)
    response = app.handle_callback('101', confirmation(preview))
    assert 'не завершено' in response['text']
    monkeypatch.setattr(recovery, '_save', original_save)
    operation = next(iter(app.operations.load_all().values()))
    replacement = operation.published_plan['calendar_id']
    _remove_calendar(app, replacement)
    preview = app.handle_text('101', '/weekly_preview')
    assert 'Calendar пока не изменён' in preview['text']
    response = app.handle_callback('101', confirmation(preview))
    assert 'восстановлен' in response['text']
    assert next(iter(app.operations.load_all().values())).published_plan['calendar_id'] != replacement


def test_pilot_onboarding_preview_confirm_repeat_and_restore(tmp_path):
    app = application(tmp_path / 'pilot')
    onboard(app)
    assert app.adapter.writes == []
    preview = app.handle_text('101', '/weekly_preview')
    assert 'ОС' in preview['text']
    assert app.pending[2].blocks
    assert app.adapter.writes == []
    assert not app.operations.path.exists()
    callback = confirmation(preview)
    assert app.handle_callback('202', callback) is None
    assert app.handle_callback('101', 'cal:apply')['buttons'] == []
    assert app.adapter.writes == []
    callback = confirmation(app.handle_text('101', '/weekly_preview'))
    assert 'опубликован' in app.handle_callback('101', callback)['text']
    saved = deepcopy(app.adapter.remote)
    writes = list(app.adapter.writes)
    assert 'устарело' in app.handle_callback('101', callback)['text']
    assert 'уже опубликован' in app.handle_text('101', '/update_all')['text']
    assert app.adapter.remote == saved and app.adapter.writes == writes
    archive = tmp_path / 'backup.zip'
    manifest = UserStateBackupService(app.root).backup(app.account.id, archive)
    assert {'onboarding.json', 'product_state.json', 'draft_operations.json',
            'university_calendar_projection.json'}.issubset(manifest['files'])
    restore_root = tmp_path / 'restored'
    UserStateBackupService(restore_root).restore(app.account.id, archive)
    restored = application(restore_root, app.adapter)
    assert restored.handle_text('101', '/start')['step'] == 'complete'
    assert 'уже опубликован' in restored.handle_text('101', '/update_all')['text']
    assert app.adapter.writes == writes


def test_restart_reject_and_expiry_never_write(tmp_path):
    app = application(tmp_path)
    onboard(app)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    app.handle_callback('101', 'pilot:cancel')
    assert 'устарело' in app.handle_callback('101', token)['text']
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    restarted = application(tmp_path, app.adapter)
    assert 'устарело' in restarted.handle_callback('101', token)['text']
    token = confirmation(restarted.handle_text('101', '/weekly_preview'))
    restarted.now = lambda: NOW + timedelta(minutes=11)
    assert 'устарело' in restarted.handle_callback('101', token)['text']
    assert app.adapter.writes == []


def test_provider_errors_and_source_changes_fail_closed(tmp_path):
    source = {'ics': ICS}
    app = application(tmp_path, content=source)
    onboard(app)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    source['fail'] = True
    reply = app.handle_callback('101', token)
    assert 'не начиналась' in reply['text'] and 'secret' not in str(reply)
    assert not app.adapter.writes
    source.pop('fail')
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    source['ics'] = ICS.replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')
    assert 'изменились' in app.handle_callback('101', token)['text']
    assert not app.adapter.writes
    app.adapter.read_failure = True
    assert 'не завершена' in app.handle_text('101', '/weekly_preview')['text']
    assert not app.adapter.writes


def test_partial_write_restart_resumes_without_duplicate(tmp_path):
    app = application(tmp_path)
    onboard(app)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    app.adapter.fail_write_number = 2  # calendar + class succeeded; first preparation fails
    result = app.handle_callback('101', token)
    assert 'часть событий' in result['text']
    assert len(app.adapter.remote) == 1
    assert next(iter(app.operations.load_all().values())).projection_pending
    app.adapter.fail_write_number = None
    restarted = application(tmp_path, app.adapter)
    token = confirmation(restarted.handle_text('101', '/weekly_preview'))
    assert 'опубликован' in restarted.handle_callback('101', token)['text']
    uids = [row['iCalUID'] for row in app.adapter.remote.values()]
    assert len(uids) == len(set(uids))
    assert len([item for item in app.adapter.writes if item == ('insert', uids[0])]) == 1


def test_busy_conflict_after_preview_and_changed_published_source_block(tmp_path):
    source = {'ics': ICS}
    app = application(tmp_path, content=source)
    onboard(app)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    block = app.pending[2].blocks[0]
    app.adapter.calendars['personal'] = 'personal'
    app.adapter.remote[('personal', 'external')] = dict(id='external', iCalUID='external',
        summary='Busy', description='', start={'dateTime': block.start.isoformat()},
        end={'dateTime': block.end.isoformat()})
    assert 'конфликт' in app.handle_callback('101', token)['text']
    assert not app.adapter.writes
    app.adapter.remote.clear()
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    assert 'опубликован' in app.handle_callback('101', token)['text']
    writes = list(app.adapter.writes)
    source['ics'] = ICS.replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')
    result = app.handle_text('101', '/update_all')
    assert 'изменились' in result['text']
    assert not result['buttons'] and app.adapter.writes == writes


def test_wrong_owner_and_disabled_legacy_actions_do_not_reach_providers(tmp_path):
    app = application(tmp_path)
    assert app.handle_text('202', '/start') is None
    for command in ('/calendar_health', '/reset_preparation_drafts', '/manage', '/tasks'):
        assert app.handle_text('101', command)['text'] == app.HELP
    for data in ('cal:apply', 'prep:stage', 'prep:replan', 'ua:retry:x'):
        assert not app.handle_callback('101', data)['buttons']
    assert not app.adapter.writes
    with pytest.raises(ValueError):
        ClosedBetaApplication(tmp_path, '202', Calendar())


def test_polling_requires_private_owner_sender_and_skips_empty_text(tmp_path, monkeypatch):
    app = application(tmp_path)
    bot = PilotBot('synthetic', '101', app)
    calls = []
    app.handle_text = lambda *args: calls.append(args)
    def update(chat, sender='101', kind='private', text='/start'):
        return {'update_id': 1, 'message': {'chat': {'id': chat, 'type': kind},
                'from': {'id': sender}, 'text': text}}
    response = Mock()
    response.json.return_value = {'result': [update('202'), update('101', kind='group'),
        update('101', sender='202'), update('101', text='  '), update('101')]}
    request = Mock(side_effect=[response, KeyboardInterrupt])
    monkeypatch.setattr('services.telegram_schedule_bot.requests.get', request)
    with pytest.raises(KeyboardInterrupt):
        bot.run_forever()
    assert calls == [('101', '/start')]
    assert bot.scheduled_tick is None


@pytest.mark.parametrize('change', ['move', 'delete'])
def test_repeat_detects_manual_calendar_override_without_repair(tmp_path, change):
    app = application(tmp_path)
    onboard(app)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    assert 'опубликован' in app.handle_callback('101', token)['text']
    key = next(key for key, event in app.adapter.remote.items()
               if event['extendedProperties']['private']['personal_os_block_id'])
    if change == 'delete':
        app.adapter.remote.pop(key)
    else:
        app.adapter.remote[key]['start']['dateTime'] = (NOW + timedelta(hours=5)).isoformat()
    before = deepcopy(app.adapter.remote)
    writes = list(app.adapter.writes)
    reply = app.handle_text('101', '/update_all')
    assert 'расходится' in reply['text'] and not reply['buttons']
    assert app.adapter.remote == before and app.adapter.writes == writes


def test_confirmation_writes_only_classes_shown_in_its_preview(tmp_path, monkeypatch):
    # Even if the rolling horizon expands, approval must not add another class.
    from dataclasses import replace
    app = application(tmp_path)
    onboard(app)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    original_inputs = app._inputs
    def expanded_inputs():
        settings, now, events, signature, busy = original_inputs()
        extra = replace(events[0], id='not-in-preview', title='Another class',
                        start_time=events[0].start_time + timedelta(days=1),
                        end_time=events[0].end_time + timedelta(days=1))
        return settings, now, [*events, extra], signature, busy
    monkeypatch.setattr(app, '_inputs', expanded_inputs)
    assert 'опубликован' in app.handle_callback('101', token)['text']
    assert all(e['iCalUID'] != 'not-in-preview' for e in app.adapter.remote.values())
