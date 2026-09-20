from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from services.calendar_integrity_service import CalendarIntegrityService


def prep(event_id, source_id, start, end, block='block', manual_conflict=False,
         owned=False):
    event = {
        'id': event_id, 'updated': event_id,
        'description': (f'AI Calendar Operation: operation\nAI Calendar Block: {block}\nAI Calendar Source: {source_id}\n'
                        + ('Manual conflict: true\n' if manual_conflict else '') + 'Status: draft'),
        'start': {'dateTime': start.isoformat()}, 'end': {'dateTime': end.isoformat()},
    }
    if owned:
        event['extendedProperties'] = {'private': {
            'personal_os_block_id': block,
            'personal_os_source_event_id': source_id,
            'personal_os_operation_id': 'operation',
        }}
    return event


def test_read_only_inspection_never_deletes_and_outside_horizon_is_protected():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'work'}]
    day = datetime(2026, 9, 10, 18)
    adapter.list_events_in_calendar.return_value = [
        prep('inside', '7:missing', day, day + timedelta(minutes=20), owned=True),
        prep('future', '7:future', day + timedelta(days=30), day + timedelta(days=30, minutes=20)),
    ]
    service = CalendarIntegrityService(adapter)
    report = service.read_violations([], [], now=datetime(2026, 9, 7))
    assert report.violations == []  # No explicit source coverage: absence is not cancellation.
    report = service.read_violations([], [], now=datetime(2026, 9, 7),
        verified_work_horizon=(datetime(2026, 9, 7), datetime(2026, 9, 21)))
    assert report.violations == [('work', 'inside', 'stale')]
    adapter._delete_event.assert_not_called()
    adapter.get_event_by_id.return_value = adapter.list_events_in_calendar.return_value[0]
    service.apply_allowed_fixes(report)
    adapter._delete_event.assert_called_once_with('work', 'inside', strict=True)


def test_health_removes_only_duplicate_stale_or_overlapping_system_preparations():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'study', 'summary': 'Study'}]
    source_start = datetime(2026, 9, 10, 10)
    source = SimpleNamespace(id='source', start_time=source_start,
                             end_time=source_start + timedelta(hours=1))
    duplicate_old = prep('duplicate-old', 'source', source_start - timedelta(days=2), source_start - timedelta(days=2, minutes=-20), 'same', owned=True)
    duplicate_old['updated'] = '2026-09-08T10:00:00Z'
    duplicate_new = prep('duplicate-new', 'source', source_start - timedelta(days=1), source_start - timedelta(days=1, minutes=-20), 'same', owned=True)
    duplicate_new['updated'] = '2026-09-09T10:00:00Z'
    adapter.list_events_in_calendar.return_value = [
        prep('overlap', 'source', source_start, source_start + timedelta(minutes=20), 'overlap'),
        prep('old', '7:missing', source_start - timedelta(hours=2), source_start - timedelta(hours=1), 'old', owned=True),
        duplicate_old,
        duplicate_new,
        {'id': 'user', 'description': 'личное событие',
         'start': {'dateTime': source_start.isoformat()}, 'end': {'dateTime': (source_start + timedelta(minutes=20)).isoformat()}},
    ]
    adapter._delete_event.return_value = True
    adapter.get_event_by_id.side_effect = lambda calendar, event_id, **kwargs: next(
        (event for event in adapter.list_events_in_calendar.return_value
         if event.get('id') == event_id), None)

    report = CalendarIntegrityService(adapter).inspect_and_cleanup(
        [source], [], now=datetime(2026, 9, 7),
        verified_work_horizon=(datetime(2026, 9, 7), datetime(2026, 9, 21)),
    )

    assert report.repaired == 2
    assert report.overlapping_preparations == 0
    assert report.stale_preparations == 1
    assert report.duplicate_preparations == 1
    assert {call.args[1] for call in adapter._delete_event.call_args_list} == {'old', 'duplicate-old'}
    assert ('study', 'overlap', 'overlap') in report.violations
    assert 'осталось: 1' in report.render()


def test_health_reports_violations_when_a_safe_delete_fails():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'study'}]
    start = datetime(2026, 9, 10, 10)
    source = SimpleNamespace(id='source', start_time=start, end_time=start + timedelta(hours=1))
    adapter.list_events_in_calendar.return_value = [
        prep('overlap', 'source', start, start + timedelta(minutes=20)),
    ]
    adapter._delete_event.return_value = False

    report = CalendarIntegrityService(adapter).inspect_and_cleanup(
        [source], [], now=datetime(2026, 9, 7),
    )

    assert report.violations == [('study', 'overlap', 'overlap')]
    assert report.repaired == 0
    assert 'осталось: 1' in report.render()
    assert 'Нарушений не найдено' not in report.render()


def test_health_reports_but_does_not_delete_a_preparation_overlapping_another_lesson():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'study', 'summary': 'Study'}]
    work_start = datetime(2026, 9, 10, 18)
    adapter.list_events_in_calendar.return_value = [
        prep('wrong-place', 'personal-university:legacy', work_start,
             work_start + timedelta(minutes=20), 'legacy-block'),
    ]
    adapter._delete_event.return_value = True
    work_lesson = SimpleNamespace(id='7:11', start=work_start,
                                  end=work_start + timedelta(minutes=90))

    report = CalendarIntegrityService(adapter).inspect_and_cleanup(
        [], [work_lesson], now=datetime(2026, 9, 7),
    )

    assert report.violations == [('study', 'wrong-place', 'overlap')]
    adapter._delete_event.assert_not_called()


def test_health_reports_but_does_not_delete_a_preparation_overlapping_recovery():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'study'}]
    recovery_start = datetime(2026, 9, 7, 19, 30)
    adapter.list_events_in_calendar.return_value = [
        prep('recovery-overlap', 'personal-university:legacy',
             recovery_start + timedelta(minutes=15), recovery_start + timedelta(minutes=35)),
    ]
    adapter._delete_event.return_value = True

    report = CalendarIntegrityService(adapter).inspect_and_cleanup(
        [], [], now=datetime(2026, 9, 7), protected_intervals=[
            (recovery_start, recovery_start + timedelta(minutes=30)),
        ],
    )

    assert report.violations == [('study', 'recovery-overlap', 'overlap')]
    adapter._delete_event.assert_not_called()


def test_health_retains_only_owned_manual_conflict_preparation_on_overlap():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'study'}]
    start = datetime(2026, 9, 7, 18)
    adapter.list_events_in_calendar.return_value = [
        prep('manual', '7:lesson', start, start + timedelta(minutes=20), 'manual',
             manual_conflict=True, owned=True),
        prep('ordinary', '7:lesson', start, start + timedelta(minutes=20), 'ordinary'),
        prep('forged', '7:lesson', start, start + timedelta(minutes=20), 'forged',
             manual_conflict=True),
    ]
    adapter._delete_event.return_value = True

    report = CalendarIntegrityService(adapter).inspect_and_cleanup(
        [], [SimpleNamespace(id='7:lesson', start=start,
                             end=start + timedelta(minutes=90))],
        now=datetime(2026, 9, 7),
    )

    assert set(report.violations) == {
        ('study', 'manual', 'manual_conflict'),
        ('study', 'ordinary', 'overlap'),
        ('study', 'forged', 'overlap'),
    }
    assert report.repaired == 0
    assert 'осталось: 3' in report.render()
    adapter._delete_event.assert_not_called()


def test_health_uses_snapshot_event_ids_for_legacy_owned_preparation():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'study'}]
    start = datetime(2026, 9, 7, 18)
    adapter.list_events_in_calendar.return_value = [{
        'id': 'legacy-owned', 'description': 'old projection without marker',
        'start': {'dateTime': start.isoformat()},
        'end': {'dateTime': (start + timedelta(minutes=20)).isoformat()},
    }]
    adapter._delete_event.return_value = True

    report = CalendarIntegrityService(adapter).inspect_and_cleanup(
        [], [SimpleNamespace(id='7:lesson', start=start,
                             end=start + timedelta(minutes=90))],
        owned_event_ids={'legacy-owned'},
        now=datetime(2026, 9, 7),
    )

    assert report.violations == [('study', 'legacy-owned', 'overlap')]
    adapter._delete_event.assert_not_called()
