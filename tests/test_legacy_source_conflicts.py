from copy import deepcopy
from datetime import timedelta

import pytest

from tests.test_legacy_preparation_commands import legacy_runtime


def test_work_command_reports_source_overlap_with_both_times(legacy_runtime):
    lesson = legacy_runtime.lessons[0]
    legacy_runtime.university[0].end_time = lesson.start + timedelta(minutes=5)

    def check(bot, adapter, remote):
        snapshot = deepcopy((legacy_runtime.lessons, legacy_runtime.university))
        response = bot.handle_text('123', '/work_schedule')
        text = response['text']
        assert 'Конфликт источников' in text
        assert '18:05' in text and '18:00' in text
        assert 'с учётом дороги' in text
        assert (legacy_runtime.lessons, legacy_runtime.university) == snapshot

    legacy_runtime.run(check)


@pytest.mark.parametrize('later_university', [False, True])
def test_known_route_reports_short_gap_without_moving_sources(legacy_runtime, later_university):
    lesson = legacy_runtime.lessons[0]
    legacy_runtime.university[0].end_time = lesson.start - timedelta(minutes=20)
    if later_university:
        evening = deepcopy(legacy_runtime.university[0])
        evening.id = 'evening-study'
        evening.start_time = lesson.end + timedelta(hours=1)
        evening.end_time = evening.start_time + timedelta(hours=1)
        legacy_runtime.university.append(evening)

    def check(bot, adapter, remote):
        snapshot = deepcopy((legacy_runtime.lessons, legacy_runtime.university))
        bot.handle_text('123', '/work_schedule')
        bot.work_route_apply(lesson.id, 'direct')
        response = bot.handle_text('123', '/work_schedule')
        assert 'доступно 20 мин, нужно 60 мин' in response['text']
        assert (legacy_runtime.lessons, legacy_runtime.university) == snapshot

    legacy_runtime.run(check)
