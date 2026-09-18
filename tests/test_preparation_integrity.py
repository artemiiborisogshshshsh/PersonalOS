from dataclasses import replace
from datetime import datetime, timedelta

from services.preparation_integrity import (
    PreparationExpectation, PreparationState, calendar_horizon,
    assess_preparation_infeasibility, render_preparation_issues, validate_preparations,
)


def expectation(source='7:1'):
    return PreparationExpectation(source, 'work-preparation', 'Информатика',
        datetime(2026, 9, 15, 18), 20, datetime(2026, 9, 8, 18),
        datetime(2026, 9, 15, 18), 'work')


def event(source='7:1', identifier='prep-1', manual_conflict=False, owned=False):
    payload = dict(id=identifier,
        description=(f'AI Calendar Operation: operation\nAI Calendar Block: {identifier}\nAI Calendar Source: {source}'
                     + ('\nManual conflict: true' if manual_conflict else '')),
        start={'dateTime': '2026-09-12T10:00:00+07:00'},
        end={'dateTime': '2026-09-12T10:20:00+07:00'})
    if owned:
        payload['extendedProperties'] = {'private': {
            'personal_os_block_id': identifier,
            'personal_os_source_event_id': source,
            'personal_os_operation_id': 'operation',
        }}
    return ('work', payload)


def test_twin_lessons_require_independent_projections():
    checks = validate_preparations([expectation(), expectation('7:2')], [event()])
    assert [check.state for check in checks] == [PreparationState.PLACED, PreparationState.MISSING]


def test_absence_does_not_invent_no_available_slot():
    assert validate_preparations([expectation()], [])[0].state == PreparationState.MISSING
    checks = validate_preparations([expectation()], [],
        no_slot_reasons={('work-preparation', '7:1'): 'нет свободных 20 минут'})
    assert checks[0].state == PreparationState.NO_AVAILABLE_SLOT


def test_only_genuinely_unplaced_requirements_make_the_week_infeasible():
    checks = validate_preparations([expectation()], [],
        no_slot_reasons={('work-preparation', '7:1'): 'нет свободных 20 минут'})

    result = assess_preparation_infeasibility(checks)

    assert result.capacity_deficit_minutes == 20
    assert result.unplaced_source_ids == ('7:1',)
    assert result.hard_constraints == ('нет свободных 20 минут',)
    assert 'освободить время' in result.alternatives[0]


def test_unverified_calendar_does_not_claim_a_missing_event():
    assert validate_preparations([expectation()], [], verified=False)[0].state == PreparationState.UNCONFIRMED


def test_explicit_exceptions_and_completion_do_not_require_restoration():
    checks = validate_preparations([
        replace(expectation(), disabled=True), replace(expectation('7:2'), completed=True),
    ], [])
    assert [check.state for check in checks] == [PreparationState.DISABLED, PreparationState.COMPLETED]
    assert render_preparation_issues(checks) == 'Проверка завершена, проблем нет.'


def test_protected_commute_and_duplicate_are_conflicts():
    checks = validate_preparations([expectation()], [event()],
        [(datetime(2026, 9, 12, 9), datetime(2026, 9, 12, 11))])
    assert checks[0].state == PreparationState.CONFLICTING
    checks = validate_preparations([expectation()], [event(), event(identifier='duplicate')])
    assert 'несколько' in checks[0].reason


def test_owned_manual_conflict_is_reported_as_conflicting_not_placed_or_missing():
    checks = validate_preparations([expectation()], [
        event(manual_conflict=True, owned=True),
    ])
    assert checks[0].state == PreparationState.CONFLICTING
    assert checks[0].reason == 'создана с пометкой конфликта; перенеси вручную'
    assert 'перенеси вручную' in render_preparation_issues(checks)


def test_unowned_manual_conflict_marker_does_not_bypass_overlap_validation():
    checks = validate_preparations([expectation()], [event(manual_conflict=True)], [
        (datetime(2026, 9, 12, 9), datetime(2026, 9, 12, 11)),
    ])
    assert checks[0].state == PreparationState.CONFLICTING
    assert checks[0].reason == 'пересечение с обязательным занятым временем'


def test_wrong_calendar_duration_and_window():
    assert validate_preparations([replace(expectation(), calendar_id='study')], [event()])[0].state == PreparationState.CONFLICTING
    assert validate_preparations([replace(expectation(), minutes=60)], [event()])[0].state == PreparationState.CONFLICTING
    assert validate_preparations([replace(expectation(), earliest=datetime(2026, 9, 13))], [event()])[0].state == PreparationState.CONFLICTING


def test_horizon_uses_local_monday_across_month_boundary():
    now = datetime.fromisoformat('2026-08-30T18:00:00+00:00')
    start, end = calendar_horizon(now, 'Asia/Tomsk')
    assert start.isoformat() == '2026-08-31T00:00:00+07:00'
    assert end.isoformat() == '2026-09-14T00:00:00+07:00'


def test_summary_is_bounded_and_details_keep_all_issues():
    expected = [replace(expectation(str(i)), lesson_start=datetime(2026, 9, 14) + timedelta(days=i))
                for i in range(12)]
    checks = validate_preparations(expected, [])
    assert render_preparation_issues(checks).count('Не удалось') == 3
    assert render_preparation_issues(checks, page=0).count('•') == 5
    assert render_preparation_issues(checks, page=2).count('•') == 2
