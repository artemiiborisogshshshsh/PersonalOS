from datetime import datetime, timedelta

from models import EventType, UniversityEvent
from services.attendance_preferences import AttendancePreferenceStore, SubjectAttendancePreference
from services.telegram_attendance_onboarding import TelegramAttendanceOnboarding
from services.telegram_attendance_editor import TelegramAttendanceEditor


def event(subject, kind, hour=10):
    start = datetime(2026, 9, 2, hour)
    return UniversityEvent(f'{subject}-{kind.value}-{hour}', f'{subject} (группа)', '',
                          '', start, start + timedelta(hours=1), kind, True)


def button(reply, label):
    return next(item for row in reply['buttons'] for item in row if label in item['text'])


def test_bulk_confirm_only_fills_unanswered_and_preserves_manual_choices(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    store.subjects['Alpha'] = SubjectAttendancePreference(lectures=False, practicals=None,
                                                           labs_enabled=True, lab_slots={'2:12:00'})
    store.subjects['Beta'] = SubjectAttendancePreference(excluded=True)
    store.save()
    events = [event('Alpha', EventType.LECTURE), event('Alpha', EventType.PRACTICAL),
              event('Alpha', EventType.LAB, 12), event('Beta', EventType.LECTURE),
              event('Gamma', EventType.LECTURE), event('Gamma', EventType.PRACTICAL)]
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore.load(store.path))
    prompt = flow.start(events)
    mass = button(prompt, 'Принять для всех')
    confirm = flow.handle_callback(mass['callback_data'])
    assert '2 пока неотвеченных' in confirm['text']
    assert flow.handle_callback(button(confirm, 'Отмена')['callback_data'])['text']
    assert flow.store.subjects['Alpha'].lectures is False
    assert flow.store.subjects['Alpha'].practicals is None
    assert flow.store.subjects['Alpha'].lab_slots == {'2:12:00'}

    prompt = flow.start(events)
    confirm = flow.handle_callback(button(prompt, 'Принять для всех')['callback_data'])
    saved = flow.handle_callback(button(confirm, 'Да, настроить')['callback_data'])
    assert saved.get('attendance_complete') is True
    assert flow.store.subjects['Alpha'].lectures is False
    assert flow.store.subjects['Alpha'].practicals is None
    assert flow.store.subjects['Beta'].excluded is True
    assert flow.store.subjects['Gamma'].lectures is True
    assert flow.store.subjects['Gamma'].practicals is True
    assert flow.store.subjects['Alpha'].lab_slots == {'2:12:00'}
    assert button(confirm, 'Да, настроить')['callback_data'].endswith(':save')


def test_lab_all_none_are_explicit_draft_actions_and_stale_question_buttons_rejected(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    events = [event('Alpha', EventType.LECTURE), event('Alpha', EventType.PRACTICAL),
              event('Alpha', EventType.LAB, 12), event('Alpha', EventType.LAB, 14)]
    first = flow.start(events)
    old = button(first, 'Посещаю')['callback_data']
    second = flow.handle_callback(old)
    assert 'устарели' in flow.handle_callback(old)['text']
    second = flow.handle_callback(button(second, 'Посещаю')['callback_data'])
    assert 'лабораторные' in second['text']
    all_reply = flow.handle_callback(button(second, 'Все ЛБ')['callback_data'])
    assert all(item['text'].startswith('✅') for row in all_reply['buttons'][:2] for item in row)
    assert 'устарели' in flow.handle_callback(button(second, 'Все ЛБ')['callback_data'])['text']
    none_reply = flow.handle_callback(button(all_reply, 'Ни одной ЛБ')['callback_data'])
    assert not any(item['text'].startswith('✅') for row in none_reply['buttons'][:2] for item in row)
    done = flow.handle_callback(button(none_reply, 'Сохранить: не посещаю')['callback_data'])
    assert done['attendance_complete']
    assert flow.store.subjects['Alpha'].labs_enabled is False


def test_editor_menu_keeps_full_long_titles_in_text_and_uses_short_numbered_buttons(tmp_path):
    prefix = 'Very long course title ' + ('same prefix ' * 12)
    events = [event(prefix + 'A', EventType.LECTURE), event(prefix + 'B', EventType.LECTURE)]
    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    menu = editor.start(events)
    labels = [item['text'] for row in menu['buttons'] for item in row if item['callback_data'].endswith(('open:0', 'open:1'))]
    assert labels[0].startswith('1. Very long course title')
    assert labels[1].startswith('2. Very long course title')
    assert labels[0][3:] == labels[1][3:]
    assert all(len(label) <= 48 for label in labels)
    assert prefix + 'A' in menu['text'] and prefix + 'B' in menu['text']
    assert '1. ' + prefix + 'A' in menu['text'] and '2. ' + prefix + 'B' in menu['text']


def test_lab_cards_put_full_room_in_numbered_body_and_keep_buttons_short(tmp_path):
    room = 'Корпус с длинным названием, аудитория 417, лабораторный этаж'
    labs = [event('Alpha', EventType.LAB, 12), event('Alpha', EventType.LAB, 14)]
    labs[0].location = room
    wizard = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'wizard.json'))
    card = wizard.start(labs)
    assert '1. Ср 12:00 · ' + room in card['text']
    assert all(len(item['text']) < 30 for row in card['buttons'] for item in row
               if item['callback_data'].endswith(('toggle:2~12~00', 'toggle:2~14~00')))
    assert any(item['text'].startswith('ЛБ1 · Ср 12:00') for row in card['buttons'] for item in row)

    editor = TelegramAttendanceEditor(AttendancePreferenceStore(tmp_path / 'editor.json'))
    menu = editor.start(labs)
    subject = editor.handle_callback(button(menu, 'Alpha')['callback_data'])
    assert room in subject['text']
    assert any(item['text'].startswith('☐ ЛБ1 · Ср 12:00') for row in subject['buttons'] for item in row)


def _complete_preferences(store):
    store.set_choice('Alpha', 'lecture', True)
    store.set_choice('Alpha', 'practical', True)
    store.set_lab_slots('Alpha', {'2:12:00'})
    store.save()


def test_reset_requires_confirmation_cancel_replay_and_concurrent_change_are_safe(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    _complete_preferences(store)
    events = [event('Alpha', EventType.LECTURE), event('Alpha', EventType.PRACTICAL),
              event('Alpha', EventType.LAB, 12)]
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore.load(store.path))
    complete = flow.start(events)
    saved = AttendancePreferenceStore.load(store.path).subjects.copy()
    assert button(complete, 'Изменить ответы')['callback_data'] == 'manage:attendance'
    old_reset = button(complete, 'Сбросить все ответы')['callback_data']
    confirm = flow.handle_callback(old_reset)
    assert 'удалит все сохранённые ответы' in confirm['text']
    assert AttendancePreferenceStore.load(store.path).subjects == saved
    cancelled = flow.handle_callback(button(confirm, 'Отмена')['callback_data'])
    assert cancelled['attendance_complete']
    assert AttendancePreferenceStore.load(store.path).subjects == saved
    # Replaying the old completion button is stale and cannot clear anything.
    again = flow.handle_callback(old_reset)
    assert 'устарели' in again['text']
    assert AttendancePreferenceStore.load(store.path).subjects == saved

    current_preview = flow.handle_callback(button(cancelled, 'Сбросить все ответы')['callback_data'])
    reset_save = button(current_preview, 'Да, сбросить')['callback_data']
    concurrent = AttendancePreferenceStore.load(store.path)
    concurrent.set_choice('Alpha', 'practical', False)
    concurrent.save()
    latest = AttendancePreferenceStore.load(store.path).subjects
    rejected = flow.handle_callback(reset_save)
    assert 'устарели' in rejected['text']
    assert AttendancePreferenceStore.load(store.path).subjects == latest
    assert 'устарели' in flow.handle_callback(reset_save)['text']


def test_confirmed_reset_clears_only_after_current_revision_confirmation(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    _complete_preferences(store)
    events = [event('Alpha', EventType.LECTURE), event('Alpha', EventType.PRACTICAL),
              event('Alpha', EventType.LAB, 12)]
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore.load(store.path))
    complete = flow.start(events)
    preview = flow.handle_callback(button(complete, 'Сбросить все ответы')['callback_data'])
    save = button(preview, 'Да, сбросить')['callback_data']
    assert save.endswith(':save')
    question = flow.handle_callback(save)
    assert not AttendancePreferenceStore.load(store.path).subjects
    assert 'посещаешь' in question['text']
    assert 'устарели' in flow.handle_callback(save)['text']
