from datetime import datetime

import pytest

from services.alfacrm_schedule_source import (
    AlfaCRMConnection, AlfaCRMError, AlfaCRMScheduleSource,
)
from services.weekly_plan_service import CommitmentType


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def source_with(payloads):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response(payloads.pop(0))

    source = AlfaCRMScheduleSource(
        AlfaCRMConnection(
            'https://school.alfacrm.pro', 7, 'teacher@example.test', 'secret',
            teacher_id=42,
        ),
        post=post,
    )
    return source, calls


def test_fetches_only_planned_teacher_lessons_as_fixed_work_commitments():
    source, calls = source_with([
        {'token': 'temporary-token'},
        {'items': [
            {
                'id': 11, 'status': 1, 'date': '2026-09-04',
                'time_from': '12:00:00', 'time_to': '13:30:00',
                'subject_name': 'Математика', 'room_name': 'кабинет 5',
                'teacher_ids': [42],
            },
            {
                'id': 12, 'status': 2, 'date': '2026-09-04',
                'time_from': '14:00', 'time_to': '15:00', 'name': 'Отмена',
            },
        ]},
    ])

    lessons = source.fetch(datetime(2026, 9, 4), datetime(2026, 9, 5))

    assert len(lessons) == 1
    assert lessons[0].id == '7:11'
    assert lessons[0].title == 'Математика'
    assert lessons[0].start.hour == 12
    assert lessons[0].location == 'кабинет 5'
    assert lessons[0].to_fixed_commitment().commitment_type == CommitmentType.TUTORING
    assert calls[1][1]['json'] == {
        'page': 0, 'status': 1, 'date_from': '2026-09-04', 'date_to': '2026-09-05',
        'teacher_id': 42,
    }
    assert calls[1][1]['headers']['X-ALFACRM-TOKEN'] == 'temporary-token'


def test_rejects_a_lesson_without_a_schedule_interval():
    source, _ = source_with([
        {'token': 'temporary-token'},
        {'items': [{'id': 11, 'status': 1, 'date': '2026-09-04'}]},
    ])

    with pytest.raises(AlfaCRMError, match='no start time'):
        source.fetch(datetime(2026, 9, 4), datetime(2026, 9, 5))


def test_rejects_unknown_lesson_snapshot_instead_of_treating_it_as_empty():
    source, _ = source_with([
        {'token': 'temporary-token'},
        {'payload': []},
    ])

    with pytest.raises(AlfaCRMError, match='recognised records container'):
        source.fetch(datetime(2026, 9, 4), datetime(2026, 9, 5))


def test_accepts_alfacrm_full_database_timestamps_for_lesson_times():
    source, _ = source_with([
        {'token': 'temporary-token'},
        {'items': [{
            'id': 11, 'status': 1, 'date': '2026-09-16',
            'time_from': '2026-09-16 19:30:01',
            'time_to': '2026-09-16 20:15:01', 'name': 'Занятие',
        }]},
    ])

    lessons = source.fetch(datetime(2026, 9, 16), datetime(2026, 9, 17))

    assert (lessons[0].start.hour, lessons[0].start.minute) == (19, 30)
    assert (lessons[0].end.hour, lessons[0].end.minute) == (20, 15)


def test_connection_requires_https_and_does_not_print_api_key():
    with pytest.raises(ValueError, match='HTTPS'):
        AlfaCRMConnection('http://school.example', 1, 'a@example.test', 'secret', 1)
    assert 'secret' not in repr(
        AlfaCRMConnection('https://school.example', 1, 'a@example.test', 'secret', 1)
    )


def test_enriches_generic_lesson_with_read_only_subject_group_and_customer_names():
    def post(url, **kwargs):
        if url.endswith('/auth/login'):
            return Response({'token': 'token'})
        if url.endswith('/lesson/index'):
            return Response({'items': [{
                'id': 11, 'status': 1, 'date': '2026-09-10',
                'time_from': '18:00', 'time_to': '19:30', 'name': 'Занятие AlfaCRM',
                'subject_id': 3, 'group_ids': [4], 'customer_ids': [5],
            }]})
        if url.endswith('/subject/index'):
            return Response({'items': [{'id': 3, 'name': 'Математика'}]})
        if url.endswith('/group/index'):
            return Response({'items': [{'id': 4, 'name': '10 класс'}]})
        if url.endswith('/customer/index'):
            return Response({'items': [{'id': 5, 'name': 'Анна'}]})
        raise AssertionError(url)

    source = AlfaCRMScheduleSource(
        AlfaCRMConnection('https://school.alfacrm.pro', 7, 'teacher@example.test', 'secret', 42),
        post=post,
    )
    lesson = source.fetch(datetime(2026, 9, 10), datetime(2026, 9, 11))[0]

    assert lesson.display_name == 'Математика — 10 класс'
    assert lesson.students == ('Анна',)
