"""Attendance opt-outs require a separate guarded deletion confirmation."""

from copy import deepcopy
from datetime import timedelta

import pytest

from services.attendance_preferences import AttendancePreferenceStore
from services.invited_beta_runtime import InvitedBot
from services.published_plan_updates import decode
from tests.test_closed_beta_runtime import NOW, confirmation
from tests.test_manage_attendance_acceptance import (
    button, edit_algebra, onboard, send_callback, send_text, setup as transport_setup,
)
from tests.test_published_plan_deletions import DeleteCalendar
from tests.test_published_plan_updates import ProcessCrash


class AttendanceCalendar(DeleteCalendar):
    """A fake provider whose event IDs are never recycled after deletion."""

    def __init__(self):
        super().__init__()
        self.next_id = 0
        self.after_delete = None

    def _insert_event(self, event, calendar_id, **kwargs):
        identifier = f'event-{self.next_id}'
        self.next_id += 1
        self.writes.append(('insert', event.uid))
        self.remote[(calendar_id, identifier)] = dict(
            id=identifier, etag='v1', iCalUID=event.uid, summary=event.summary,
            description=event.description, location=event.location,
            start={'dateTime': event.dtstart.isoformat()}, end={'dateTime': event.dtend.isoformat()},
            extendedProperties={'private': {
                'personal_os_block_id': event.system_block_id,
                'personal_os_source_event_id': event.system_source_event_id,
                'personal_os_operation_id': event.system_operation_id,
            }},
        )
        return identifier

    def _delete_event(self, *args, **kwargs):
        result = super()._delete_event(*args, **kwargs)
        if self.after_delete:
            callback, self.after_delete = self.after_delete, None
            callback()
        return result


def setup(tmp_path):
    bot, calendars, content = transport_setup(tmp_path)
    for chat in calendars:
        calendars[chat] = AttendanceCalendar()
    number = onboard(bot)
    number, _ = edit_algebra(bot, '101', number, labs=2)
    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    published = send_callback(bot, number, '101', confirmation(preview)); number += 1
    assert 'опубликован' in published['text'], published
    app = bot.applications['101'][1]
    operation = next(iter(app.operations.load_all().values()))
    classes = {uid for uid, row in operation.published_plan['rows'].items()
               if row['kind'] == 'class' and row['data']['system_source_event_id'] in {'lab-a', 'lab-b'}}
    removed = {uid: row for uid, row in operation.published_plan['rows'].items()
               if uid in classes or row['kind'] == 'preparation'
               and row['data']['system_source_event_id'] in classes}
    assert len(classes) == 2 and len(removed) == 4
    return bot, calendars, content, number, app, classes, removed


def disable_labs(bot, number):
    send_text(bot, number, '101', '/manage'); number += 1
    menu = send_callback(bot, number, '101', 'manage:attendance'); number += 1
    subject = send_callback(bot, number, '101',
                            button(menu, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    subject = send_callback(bot, number, '101',
                            button(subject, lambda item: item['text'] == 'Ни одной')['callback_data'])
    number += 1
    saved = send_callback(bot, number, '101',
                          button(subject, lambda item: item['text'] == 'Сохранить')['callback_data'])
    number += 1
    assert saved['attendance_saved']
    return number


def deleted_events(calendar):
    return [uid for action, uid in calendar.writes if action == 'delete']


def test_saved_opt_out_review_and_confirmation_delete_only_two_labs_and_linked_preps(tmp_path):
    bot, calendars, _content, number, app, classes, removed = setup(tmp_path)
    calendar = calendars['101']
    before = deepcopy(calendar.remote)
    original_rows = deepcopy(next(iter(app.operations.load_all().values())).published_plan['rows'])
    expected = {(next(iter(app.operations.load_all().values())).published_plan['calendar_id'], row['event_id'])
                for row in removed.values()}
    number = disable_labs(bot, number)
    assert calendar.remote == before and not deleted_events(calendar)
    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    assert '/review_missing' in preview['text'] and not preview['buttons'], preview
    review = send_text(bot, number, '101', '/review_missing'); number += 1
    assert 'отключил посещаемость' in review['text']
    assert 'Алгебра (ЛБ)' in review['text'] and calendar.remote == before
    send_callback(bot, number, '101', 'pilot:cancel'); number += 1
    assert calendar.remote == before
    review = send_text(bot, number, '101', '/review_missing'); number += 1
    token = confirmation(review)
    result = send_callback(bot, number, '101', token); number += 1
    assert 'Удаление проверено' in result['text'], result
    assert set(deleted_events(calendar)) == {row['event_id'] for row in removed.values()}
    assert calendar.remote == {key: value for key, value in before.items() if key not in expected}
    operation = next(iter(app.operations.load_all().values()))
    assert operation.published_plan['rows'] == {uid: row for uid, row in original_rows.items() if uid not in removed}
    assert not (set(operation.published_plan.get('source_resolutions', [])) & classes)
    assert all(app.classes.projection_state.get(uid).get('override') is None for uid in classes)
    assert not send_text(bot, number, '101', '/review_missing')['buttons']; number += 1
    assert 'устар' in send_callback(bot, number, '101', token)['text']
    assert len(deleted_events(calendar)) == 4
    assert not calendars['202'].writes


def test_opted_out_classes_can_be_reenabled_only_after_fresh_preview_confirmation(tmp_path):
    bot, calendars, _content, number, app, _classes, _removed = setup(tmp_path)
    number = disable_labs(bot, number)
    review = send_text(bot, number, '101', '/review_missing'); number += 1
    result = send_callback(bot, number, '101', confirmation(review)); number += 1
    assert 'Удаление проверено' in result['text']
    # Confirm any separate preparation replan while labs remain disabled.
    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    if preview['buttons']:
        result = send_callback(bot, number, '101', confirmation(preview)); number += 1
        assert 'обновлён' in result['text'], result
    before = list(calendars['101'].writes)
    number, _ = edit_algebra(bot, '101', number, labs=2)
    assert calendars['101'].writes == before
    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    assert '+ Добавить:' in preview['text'], preview
    assert calendars['101'].writes == before
    result = send_callback(bot, number, '101', confirmation(preview))
    assert 'обновлён' in result['text'], result
    rows = next(iter(app.operations.load_all().values())).published_plan['rows']
    sources = {row['data']['system_source_event_id'] for row in rows.values() if row['kind'] == 'class'}
    assert {'lab-a', 'lab-b'} <= sources
    assert len(deleted_events(calendars['101'])) == 4


@pytest.mark.parametrize('change', ['preference', 'unknown', 'source', 'source_room', 'expired',
                                    'calendar_etag', 'ownership', 'source_cancelled'])
def test_stale_or_changed_deletion_confirmation_makes_no_deletes(tmp_path, change):
    bot, calendars, content, number, app, _classes, removed = setup(tmp_path)
    number = disable_labs(bot, number)
    token = confirmation(send_text(bot, number, '101', '/review_missing')); number += 1
    before = list(calendars['101'].writes)
    if change in {'preference', 'unknown'}:
        store = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
        preference = store.subjects['Алгебра']
        preference.labs_enabled = True if change == 'preference' else None
        preference.lab_slots = {'2:14:00', '3:14:00'}
        store.save()
    elif change == 'source':
        content['ics'] = content['ics'].replace(b'DTSTART:20260923T070000Z', b'DTSTART:20260923T080000Z')
        content['ics'] = content['ics'].replace(b'DTEND:20260923T080000Z', b'DTEND:20260923T090000Z')
    elif change == 'source_room':
        content['ics'] = content['ics'].replace(b'LOCATION:\r\n', b'LOCATION:new-room\r\n')
    elif change == 'source_cancelled':
        content['ics'] = content['ics'].replace(b'UID:lab-a', b'UID:lab-a\r\nSTATUS:CANCELLED')
    elif change == 'expired':
        app.now = lambda: NOW + timedelta(minutes=11)
    else:
        target = next(iter(removed.values()))['event_id']
        remote = next(value for (_calendar, event_id), value in calendars['101'].remote.items() if event_id == target)
        if change == 'calendar_etag':
            remote['etag'] = 'manually-changed'
        else:
            remote['extendedProperties']['private']['personal_os_block_id'] = 'another-owner'
    response = send_callback(bot, number, '101', token)
    assert 'Удаление проверено' not in response['text']
    assert calendars['101'].writes == before


def test_unknown_attendance_and_changed_published_identity_do_not_create_targets(tmp_path):
    bot, calendars, content, number, app, _classes, _removed = setup(tmp_path)
    number = disable_labs(bot, number)
    store = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
    store.subjects['Алгебра'].labs_enabled = None
    store.save()
    review = send_text(bot, number, '101', '/review_missing'); number += 1
    assert not review['buttons'] and not deleted_events(calendars['101'])
    # Restoring False is still insufficient if the source now describes a
    # different place/time than the exact class originally published.
    store.subjects['Алгебра'].labs_enabled = False
    store.save()
    content['ics'] = content['ics'].replace(b'LOCATION:', b'LOCATION:Changed')
    review = send_text(bot, number, '101', '/review_missing')
    assert not review['buttons'] and not deleted_events(calendars['101'])


def test_deletion_confirmation_is_scoped_to_invited_user(tmp_path):
    bot, calendars, _content, number, _app, _classes, _removed = setup(tmp_path)
    number = disable_labs(bot, number)
    token = confirmation(send_text(bot, number, '101', '/review_missing'))
    response = send_callback(bot, number + 1, '202', token)
    assert 'устар' in response['text']
    assert not deleted_events(calendars['101']) and not calendars['202'].writes


@pytest.mark.parametrize('change', ['preference', 'source'])
def test_changed_conditions_during_partial_delete_stop_next_write(tmp_path, change):
    bot, calendars, content, number, app, _classes, _removed = setup(tmp_path)
    number = disable_labs(bot, number)
    token = confirmation(send_text(bot, number, '101', '/review_missing')); number += 1

    def reenable():
        if change == 'source':
            content['ics'] = content['ics'].replace(b'LOCATION:\r\n', b'LOCATION:new-room\r\n')
            return
        store = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
        store.set_lab_slots('Алгебра', {'2:14:00', '3:14:00'})
        store.save()

    calendars['101'].after_delete = reenable
    response = send_callback(bot, number, '101', token)
    assert 'Удаление проверено' not in response['text']
    assert len(deleted_events(calendars['101'])) == 1
    assert next(iter(app.operations.load_all().values())).pending_plan_update['kind'] == 'delete'


def test_attendance_opt_out_is_never_an_automatic_source_cancellation(tmp_path):
    bot, calendars, _content, number, app, _classes, _removed = setup(tmp_path)
    disable_labs(bot, number)
    before = deepcopy(calendars['101'].remote)
    assert app.updates.sync_source_cancellations() == ''
    assert calendars['101'].remote == before
    assert not deleted_events(calendars['101'])


def test_started_preparation_is_retained_when_its_future_class_is_opted_out(tmp_path):
    bot, calendars, _content, number, app, _classes, removed = setup(tmp_path)
    preparations = {uid: row for uid, row in removed.items() if row['kind'] == 'preparation'}
    started_uid, started_row = min(preparations.items(), key=lambda item: decode(item[1]['data']).dtstart)
    current = decode(started_row['data']).dtstart + timedelta(minutes=1)
    assert all(current < decode(row['data']).dtstart for row in removed.values() if row['kind'] == 'class')
    app.now = lambda: current
    number = disable_labs(bot, number)
    before = deepcopy(calendars['101'].remote)
    review = send_text(bot, number, '101', '/review_missing'); number += 1
    result = send_callback(bot, number, '101', confirmation(review))
    assert 'Удаление проверено' in result['text'], result
    operation = next(iter(app.operations.load_all().values()))
    assert operation.published_plan['rows'][started_uid] == started_row
    assert started_uid in {block.id for block in operation.blocks}
    key = (operation.published_plan['calendar_id'], started_row['event_id'])
    assert calendars['101'].remote[key] == before[key]
    assert started_row['event_id'] not in deleted_events(calendars['101'])


def test_partial_attendance_delete_restart_needs_fresh_consent_and_does_not_repeat_deletes(tmp_path):
    root = tmp_path / 'beta'
    bot, calendars, _content, number, _app, _classes, _removed = setup(root)
    number = disable_labs(bot, number)
    token = confirmation(send_text(bot, number, '101', '/review_missing')); number += 1
    calendars['101'].crash_delete = True
    with pytest.raises(ProcessCrash):
        send_callback(bot, number, '101', token)
    number += 1
    restarted = InvitedBot('synthetic', root, calendars.__getitem__, bot.application_factory)
    response = send_callback(restarted, number, '101', token); number += 1
    assert 'устар' in response['text']
    review = send_text(restarted, number, '101', '/review_missing'); number += 1
    assert 'Продолжение частичного удаления' in review['text'], review
    response = send_callback(restarted, number, '101', confirmation(review))
    assert 'Удаление проверено' in response['text'], response
    assert len(deleted_events(calendars['101'])) == len(set(deleted_events(calendars['101']))) == 4
