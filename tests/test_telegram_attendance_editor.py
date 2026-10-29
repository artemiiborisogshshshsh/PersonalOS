from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from models import EventType, UniversityEvent
from services.attendance_preferences import (
    AttendancePreferenceStore, SubjectAttendancePreference, lab_slot_key,
)
from services.telegram_attendance_editor import TelegramAttendanceEditor


def event(kind, hour=10, subject='ОС', uid=None):
    start = datetime(2026, 9, 2, hour)
    return UniversityEvent(uid or f'{subject}-{kind.value}-{hour}', f'{subject} (ЛБ)', '',
                           'ауд. 201', start, start + timedelta(hours=1), kind, True)


def schedule():
    return [event(EventType.LECTURE), event(EventType.PRACTICAL),
            *[event(EventType.LAB, hour) for hour in (12, 14, 16)]]


def button(reply, text):
    return next(item['callback_data'] for row in reply['buttons'] for item in row
                if text in item['text'])


def click(editor, reply, text, events=None):
    return editor.handle_callback(button(reply, text), events=events)


def open_subject(editor, events=None, subject='ОС'):
    menu = editor.start(events or schedule())
    return click(editor, menu, subject)


@pytest.mark.parametrize('hours', [(12,), (12, 16), (12, 14, 16), ()])
def test_explicit_lab_choices_persist_one_many_all_or_none(tmp_path, hours):
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    reply = open_subject(editor)
    if len(hours) == 3:
        reply = click(editor, reply, 'Все')
    elif not hours:
        reply = click(editor, reply, 'Все')
        reply = click(editor, reply, 'Ни одной')
    else:
        for hour in hours:
            reply = click(editor, reply, f'{hour}:00')
    assert not editor.store.path.exists()
    saved = click(editor, reply, 'Сохранить')
    assert saved['attendance_saved'] is True
    assert '/weekly_preview' in saved['text']
    assert not any(item['callback_data'].startswith('cal:apply')
                   for row in saved['buttons'] for item in row)
    loaded = AttendancePreferenceStore.load(editor.store.path)
    preference = loaded.subjects['ОС']
    assert preference.lab_slots == {lab_slot_key(event(EventType.LAB, hour)) for hour in hours}
    assert preference.labs_enabled is bool(hours)
    for lab in schedule()[2:]:
        assert loaded.preference_for(lab) is (lab.dtstart.hour in hours)


def test_lecture_toggle_does_not_disable_practicals_or_labs(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    store.subjects['ОС'] = SubjectAttendancePreference(True, True, True, {'2:12:00'})
    store.save()
    editor = TelegramAttendanceEditor(store)
    reply = click(editor, open_subject(editor), 'Лекции')
    click(editor, reply, 'Сохранить')
    loaded = AttendancePreferenceStore.load(store.path)
    assert loaded.preference_for(event(EventType.LECTURE)) is False
    assert loaded.preference_for(event(EventType.PRACTICAL)) is True
    assert loaded.preference_for(event(EventType.LAB, 12)) is True


def test_back_discards_draft_and_reopening_shows_saved_values(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    editor = TelegramAttendanceEditor(store)
    reply = click(editor, open_subject(editor), 'Лекции')
    menu = click(editor, reply, 'Назад')
    assert not store.path.exists()
    reopened = click(editor, menu, 'ОС')
    assert any(item['text'] == '☐ Лекции' for row in reopened['buttons'] for item in row)
    click(editor, click(editor, reopened, 'Практики'), 'Сохранить')
    reopened = open_subject(editor)
    assert any(item['text'] == '☑ Практики' for row in reopened['buttons'] for item in row)


def test_restart_preserves_saved_choices_and_rejects_old_buttons(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    editor = TelegramAttendanceEditor(store)
    reply = click(editor, open_subject(editor), 'Практики')
    old = button(reply, 'Сохранить')
    click(editor, reply, 'Сохранить')
    before = store.path.read_bytes()
    restarted = TelegramAttendanceEditor(AttendancePreferenceStore.load(store.path))
    assert 'устарели' in restarted.handle_callback(old)['text']
    reopened = open_subject(restarted)
    assert 'устарели' in restarted.handle_callback(old)['text']
    assert any(item['text'] == '☑ Практики' for row in reopened['buttons'] for item in row)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('change', [
    lambda value: replace(value, uid='replacement'),
    lambda value: replace(value, location='ауд. 300'),
    lambda value: replace(value, dtstart=value.dtstart + timedelta(minutes=15)),
    lambda value: replace(value, summary='Другой (ЛБ)'),
    lambda value: replace(value, attendance_slot_key='new-slot'),
    lambda value: replace(value, event_type=EventType.PRACTICAL),
])
def test_changed_fresh_source_rejects_save_without_writes(tmp_path, change):
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    events = schedule()
    reply = click(editor, open_subject(editor, events), 'Все')
    changed = [*events[:-1], change(events[-1])]
    rejected = click(editor, reply, 'Сохранить', events=changed)
    assert 'Расписание изменилось' in rejected['text']
    assert not editor.store.path.exists()
    assert 'устарели' in click(editor, reply, 'Сохранить')['text']


def test_source_order_alone_does_not_reject_save(tmp_path):
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    events = schedule()
    reply = open_subject(editor, events)
    reply = click(editor, reply, 'Все', events=list(reversed(events)))
    assert click(editor, reply, 'Сохранить', events=list(reversed(events)))['attendance_saved']


def test_invalid_cross_instance_and_old_screen_buttons_cannot_mutate(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    editor = TelegramAttendanceEditor(store)
    reply = open_subject(editor)
    old = button(reply, 'Все')
    other = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'other.json'))
    other_reply = open_subject(other)
    token_prefix = old.rsplit(':', 1)[0]
    malformed = ['', 'ate:wrong', token_prefix + ':lab:99', token_prefix + ':lab:-1',
                 token_prefix + ':save:0', token_prefix + ':unknown',
                 token_prefix + ':lab:' + '9' * 100, button(other_reply, 'Все')]
    before = deepcopy(store.subjects)
    for value in malformed:
        assert 'устарели' in editor.handle_callback(value)['text']
    assert store.subjects == before
    assert not store.path.exists()
    reply = editor.handle_callback(old)
    assert 'устарели' in editor.handle_callback(old)['text']
    click(editor, reply, 'Сохранить')
    assert len(AttendancePreferenceStore.load(store.path).subjects['ОС'].lab_slots) == 3


def test_save_preserves_another_subject_changed_after_draft_opened(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    editor = TelegramAttendanceEditor(store)
    reply = open_subject(editor)
    other = AttendancePreferenceStore.load(store.path)
    other.set_choice('Математика', 'practical', True)
    other.save()
    saved = click(editor, click(editor, reply, 'Лекции'), 'Сохранить')
    assert saved['attendance_saved']
    assert AttendancePreferenceStore.load(store.path).subjects['Математика'].practicals is True


def test_same_subject_concurrent_update_requires_reopening(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    first, second = TelegramAttendanceEditor(store), TelegramAttendanceEditor(AttendancePreferenceStore(store.path))
    first_reply, second_reply = open_subject(first), open_subject(second)
    click(second, click(second, second_reply, 'Практики'), 'Сохранить')
    before = store.path.read_bytes()
    rejected = click(first, click(first, first_reply, 'Все'), 'Сохранить')
    assert 'attendance_saved' not in rejected
    assert 'другом окне' in rejected['text']
    assert store.path.read_bytes() == before
    reopened = click(first, rejected, 'ОС')
    assert any(item['text'] == '☑ Практики' for row in reopened['buttons'] for item in row)


def test_excluded_course_only_reenabled_after_explicit_selection(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    store.subjects['ОС'] = SubjectAttendancePreference(True, True, True, {'2:12:00'}, excluded=True)
    store.save()
    editor = TelegramAttendanceEditor(store)
    reply = open_subject(editor)
    assert 'Предмет исключён' in reply['text']
    click(editor, reply, 'Сохранить')
    assert AttendancePreferenceStore.load(store.path).subject_is_excluded('ОС')
    reply = click(editor, open_subject(editor), 'Практики')
    click(editor, reply, 'Сохранить')
    loaded = AttendancePreferenceStore.load(store.path)
    assert not loaded.subject_is_excluded('ОС')
    assert loaded.preference_for(event(EventType.PRACTICAL)) is True
    assert loaded.preference_for(event(EventType.LECTURE)) is False
    assert loaded.preference_for(event(EventType.LAB, 12)) is False


def test_timezone_labels_callback_size_and_pagination(tmp_path):
    events = [event(EventType.LAB, subject=f'Длинное название предмета {index:02}' + 'й' * 90)
              for index in range(12)]
    events[0] = replace(events[0], dtstart=datetime(2026, 9, 2, 22, tzinfo=timezone.utc))
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    menu = editor.start(events)
    for row in menu['buttons']:
        for item in row:
            assert len(item['callback_data'].encode()) <= 64
    reply = click(editor, menu, '00')
    assert 'Чт 05:00 · ауд. 201' in str(reply['buttons'])
    menu = click(editor, reply, 'Назад')
    page = click(editor, menu, '→')
    assert '11' in str(page['buttons'])
    click(editor, page, '←')


def test_empty_source_explains_next_step_without_writes(tmp_path):
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    reply = editor.start([])
    assert 'обнови расписание' in reply['text']
    assert not editor.store.path.exists()


def test_utc_source_slots_match_planner_local_slots_and_roll_weekday(tmp_path):
    from zoneinfo import ZoneInfo

    zone = ZoneInfo('Asia/Tomsk')
    events = [replace(event(EventType.LAB, hour=hour),
                      dtstart=datetime(2026, 9, 2, hour, tzinfo=timezone.utc),
                      dtend=datetime(2026, 9, 2, hour + 1, tzinfo=timezone.utc))
              for hour in (7, 22)]
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    reply = open_subject(editor, events)
    assert 'Ср 14:00' in str(reply['buttons'])
    assert 'Чт 05:00' in str(reply['buttons'])
    reply = click(editor, reply, 'Все', events=events)
    saved = click(editor, reply, 'Сохранить', events=events)
    assert saved['attendance_saved']
    loaded = AttendancePreferenceStore.load(editor.store.path)
    assert loaded.subjects['ОС'].lab_slots == {'2:14:00', '3:05:00'}
    planner_events = [replace(value, dtstart=value.dtstart.astimezone(zone),
                              dtend=value.dtend.astimezone(zone)) for value in events]
    assert all(loaded.preference_for(value) is True for value in planner_events)
    assert all(value.dtstart.tzinfo is timezone.utc for value in events)


def test_equivalent_localized_source_snapshot_does_not_invalidate_utc_session(tmp_path):
    from zoneinfo import ZoneInfo

    original = replace(event(EventType.LAB),
                       dtstart=datetime(2026, 9, 2, 22, tzinfo=timezone.utc),
                       dtend=datetime(2026, 9, 2, 23, tzinfo=timezone.utc))
    local = replace(original, dtstart=original.dtstart.astimezone(ZoneInfo('Asia/Tomsk')),
                    dtend=original.dtend.astimezone(ZoneInfo('Asia/Tomsk')))
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    reply = open_subject(editor, [original])
    reply = click(editor, reply, 'Все', events=[local])
    assert click(editor, reply, 'Сохранить', events=[local])['attendance_saved']


def test_explicit_attendance_slot_identity_survives_timezone_normalization(tmp_path):
    source = replace(event(EventType.LAB), attendance_slot_key='stable-slot',
                     dtstart=datetime(2026, 9, 2, 22, tzinfo=timezone.utc))
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    reply = click(editor, open_subject(editor, [source]), 'Все')
    click(editor, reply, 'Сохранить')
    assert AttendancePreferenceStore.load(editor.store.path).subjects['ОС'].lab_slots == {'stable-slot'}
