from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from models import EventType, PersonalAttendanceRule, UniversityEvent
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType
from services.attendance_service import AttendanceRuleService
from services.calendar.weekly_plan_projection_service import (
    WeeklyPlanCalendarProjectionService,
)
from services.mvp_pipeline import PersonalOSMvpPipeline
from services.obsidian_projection_service import ObsidianProjectionService
from services.preparation.preparation_block_service import PreparationBlockService
from services.preparation.preparation_integration_service import (
    PreparationIntegrationService,
)
from services.tutoring_service import TutoringMode, TutoringSession
from services.weekly_plan_service import WeeklyPlanPipeline, WeeklyPlanStatus


START = datetime(2026, 9, 7, 9, 0)


def university_source():
    return UniversityEvent(
        uid='university-source-1',
        summary='Математика (ЛК)',
        description='Группа 8И41. Преподаватель: Иванов И.И.',
        location='Аудитория 101',
        dtstart=START + timedelta(days=1, hours=1),
        dtend=START + timedelta(days=1, hours=2),
        event_type=EventType.LECTURE,
        is_group_event=True,
    )


def test_full_mvp_pipeline_from_source_to_feedback(tmp_path):
    calendar_adapter = Mock()
    calendar_adapter._get_or_create_calendar.return_value = 'calendar-id'
    calendar_adapter._get_events_in_range.return_value = []
    calendar_adapter._insert_event.return_value = 'google-id'
    calendar_adapter._update_event.return_value = 'google-id'
    projector = WeeklyPlanCalendarProjectionService(calendar_adapter)

    prep_blocks = Mock(spec=PreparationBlockService)
    preparation = PreparationIntegrationService(prep_blocks)
    attendance = AttendanceRuleService(PersonalAttendanceRule(
        id='student-attendance',
        description='Student confirmed mathematics lectures',
        metadata={'attendance_preferences': {
            'Математика': {'lectures': True},
        }},
    ))
    weekly = WeeklyPlanPipeline(
        PlanningEngine(num_candidates=6), projector=projector
    )
    notification = Mock()
    pipeline = PersonalOSMvpPipeline(
        attendance_service=attendance,
        weekly_plan_pipeline=weekly,
        preparation_service=preparation,
        obsidian_service=ObsidianProjectionService(),
        notification_adapter=notification,
    )
    project = PlanningItem(
        id='project-task',
        title='Проект',
        description='Работа над проектом',
        item_type=PlanningItemType.PROJECT_TASK,
        duration_minutes=60,
        earliest_start=START,
        latest_end=START + timedelta(hours=9),
        flexible=True,
        priority=2,
        metadata={'domain': 'projects', 'deep_work': True},
    )
    tutoring = TutoringSession(
        id='tutoring-1',
        student='Максим',
        start=START + timedelta(hours=5),
        duration_minutes=45,
        mode=TutoringMode.ONLINE,
        material_preparation_minutes=30,
        homework_review_minutes=15,
    )
    note = tmp_path / 'Математика.md'
    note.write_text('# Математика\n\nПользовательский текст.\n', encoding='utf-8')

    result = pipeline.run(
        old_university_events=[],
        new_university_events=[university_source()],
        personal_events=[],
        week_start=START - timedelta(days=1),
        week_end=START + timedelta(days=2),
        planning_items=[project],
        tutoring_sessions=[tutoring],
        approved_by='artemij',
        obsidian_notes={'Математика': note},
    )

    assert result.sync.changed
    assert len(result.personal_events) == 1
    assert len(result.preparation_requirements) == 1
    assert result.weekly_plan.status == WeeklyPlanStatus.COMMITTED
    assert result.weekly_plan.selected_candidate.valid
    assert result.execution_records
    assert calendar_adapter._insert_event.call_count >= 4
    notification.send_notification.assert_called_once()
    note_text = note.read_text(encoding='utf-8')
    assert 'Пользовательский текст.' in note_text
    assert 'university-source-1' in note_text

    original = result.execution_records[0]
    feedback = pipeline.record_feedback(
        original,
        original.planned_start,
        original.planned_end - timedelta(minutes=5),
        'completed',
    )
    assert feedback.supersedes_id == original.id
    assert original.actual_start is None


def test_mvp_pipeline_refuses_unapproved_commit():
    pipeline = PersonalOSMvpPipeline(
        attendance_service=AttendanceRuleService(),
        weekly_plan_pipeline=WeeklyPlanPipeline(PlanningEngine()),
    )
    with pytest.raises(PermissionError):
        pipeline.run([], [], [], START, START + timedelta(days=1))
