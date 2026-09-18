from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from services.system_edit_service import SystemEditStore


def university(identifier='personal-university:one'):
    return SimpleNamespace(
        id=identifier, title='Архитектура ИС (ЛБ)',
        start_time=datetime(2026, 9, 10, 12, 40),
    )


def work(identifier='1:2'):
    return SimpleNamespace(
        id=identifier, display_name='Информатика — Анна',
        start=datetime(2026, 9, 10, 18),
    )


def block(identifier='personal-university:one'):
    return SimpleNamespace(
        source_event_id=identifier, title='Подготовка: Архитектура ИС',
        start=datetime(2026, 9, 9, 18),
    )


def test_system_edit_inventory_uses_short_codes_and_persists_hide_overrides(tmp_path):
    store = SystemEditStore(tmp_path / 'edits.json')

    records = store.refresh_inventory([university()], [work()], [('university', block())])

    assert [record.code for record in records] == ['u1', 'w1', 'p1']
    assert store.record('U1').source_id == 'personal-university:one'
    store.set_hidden('university', 'personal-university:one', True)
    store.set_hidden('work-preparation', '1:2', True)

    restored = SystemEditStore(tmp_path / 'edits.json')
    assert restored.university_hidden('personal-university:one')
    assert restored.preparation_suppressed('work', '1:2')


def test_suppressed_preparation_stays_listed_so_it_can_be_restored(tmp_path):
    store = SystemEditStore(tmp_path / 'edits.json')
    store.refresh_inventory([], [], [('university', block())])
    store.set_hidden('university-preparation', 'personal-university:one', True)

    records = store.refresh_inventory([], [], [])

    assert len(records) == 1
    assert records[0].kind == 'university-preparation'
    assert records[0].hidden is True


def test_preparation_rule_can_disable_an_entire_course_or_one_session_type(tmp_path):
    store = SystemEditStore(tmp_path / 'edits.json')

    store.set_preparation_rule('Архитектура  ИС', 'ЛБ')

    assert store.preparation_disabled('архитектура ис', 'lab')
    assert not store.preparation_disabled('Архитектура ИС', 'lecture')
    store.set_preparation_rule('Архитектура ИС', 'все')
    assert store.preparation_disabled('Архитектура ИС', 'lecture')
    store.set_preparation_rule('Архитектура ИС', 'ЛБ', disabled=False)
    assert store.preparation_disabled('Архитектура ИС', 'lecture')


def test_delete_owned_preparations_never_touches_user_events(tmp_path):
    store = SystemEditStore(tmp_path / 'edits.json')
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'study'}, {'id': 'personal'}]
    adapter.list_events_in_calendar.side_effect = [
        [{
            'id': 'owned',
            'description': 'AI Calendar Block: prep:1\nAI Calendar Source: source-1',
            'extendedProperties': {'private': {'personal_os_source_event_id': 'source-1'}},
        }],
        [{
            'id': 'user', 'description': 'моя подготовка',
            'extendedProperties': {'private': {'personal_os_source_event_id': 'source-1'}},
        }],
    ]
    adapter._delete_event.return_value = True

    assert store.delete_owned_preparations(adapter, 'source-1') == 1
    adapter._delete_event.assert_called_once_with('study', 'owned')
