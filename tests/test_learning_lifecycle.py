"""Learning acceptance covers durable state, safe identity and delivery receipts."""
from datetime import datetime, timedelta
from dataclasses import replace
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest

from models import EventType, PersonalUniversityEvent, PersonalEventState, UniversityEvent
from services.learning_lifecycle import LearningLifecycleStore, plan_learning_preparations
from services.user_data_lifecycle import UserDataLifecycleService
from services.user_planning_profile_store import UserPlanningSettings
from services.adaptive_preparation_service import DraftPreparationBlock, UserPlanningProfile
from services.weekly_plan_service import CommitmentType, FixedCommitment

ZONE = ZoneInfo('Asia/Tomsk')


def moment(day=26, hour=14, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def event(uid='physics', title='Физика (ПР)', day=26, hour=14, end_hour=16, event_type=EventType.PRACTICAL):
    return UniversityEvent(uid, title, '', '', moment(day, hour), moment(day, end_hour), event_type)


@pytest.fixture
def store(tmp_path):
    return LearningLifecycleStore(tmp_path / 'users' / '42' / 'learning_lifecycle.json', '42')


def seed(store, events=None):
    store.reconcile_lessons(events or [event()])
    return store.lessons()[0]['id']


def test_attendance_callback_is_scoped_restart_safe_and_replay_proof(store):
    seed(store, [event(), event('math', 'Математика (ЛК)', hour=16, end_hour=18)])
    physics, math = store.lessons()
    callback = store.callback_data(physics['id'], 'miss')
    assert len(callback.encode()) <= 64
    restarted = LearningLifecycleStore(store.path, '42')
    assert restarted.apply_callback(callback)['attendance'] is False
    assert restarted.get_lesson(math['id'])['attendance'] is None
    assert restarted.get_context()['lesson_id'] == physics['id']
    assert restarted.apply_callback(callback) is None


def test_notes_photos_and_unresolved_intake_survive_restart_with_no_public_urls(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, 'Запомнить закон', kind='note', source_message_id=100)
    assert store.get_lesson(lesson_id)['homework_state'] == 'unknown'
    store.add_material(lesson_id, file_id='private-telegram-file', file_unique_id='photo-1', source_message_id=101)
    store.add_material(lesson_id, file_id='private-telegram-file', source_message_id=101)
    store.set_context(None, kind='ambiguous', pending={'file_id': 'unresolved-photo', 'text': 'К пятнице'})
    restarted = LearningLifecycleStore(store.path, '42')
    lesson = restarted.get_lesson(lesson_id)
    assert len(lesson['materials']) == 2
    assert lesson['materials'][1]['file_unique_id'] == 'photo-1'
    assert lesson['homework_state'] == 'assigned'
    assert lesson['remaining_minutes'] == 60
    assert restarted.get_context()['pending']['file_id'] == 'unresolved-photo'
    assert 'https://' not in store.path.read_text()
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_user_isolation_rejects_copied_state_wrong_paths_and_foreign_buttons(store, tmp_path):
    lesson_id = seed(store)
    token = store.callback_data(lesson_id, 'attend')
    other = LearningLifecycleStore(tmp_path / 'users' / '99' / 'learning_lifecycle.json', '99')
    seed(other)
    assert other.apply_callback(token) is None
    with pytest.raises(ValueError, match='another user'):
        LearningLifecycleStore(store.path, '99')
    with pytest.raises(ValueError):
        LearningLifecycleStore(tmp_path / 'x.json', '../42')
    copied = tmp_path / 'copied.json'
    copied.write_text(store.path.read_text())
    with pytest.raises(ValueError, match='owner'):
        LearningLifecycleStore(copied, '99')


def test_symlink_state_and_parent_are_rejected(tmp_path):
    external = tmp_path / 'external'
    external.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        LearningLifecycleStore(alias / 'learning.json', '42')
    target = external / 'target.json'
    target.write_text('{}')
    linked = tmp_path / 'linked.json'
    linked.symlink_to(target)
    with pytest.raises(ValueError, match='symlinks'):
        LearningLifecycleStore(linked, '42')


def test_export_and_deletion_include_learning_attachment_metadata(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, file_id='private-file')
    service = UserDataLifecycleService(store.path.parents[2])
    assert 'private-file' in service.export('42')['files']['learning_lifecycle.json']
    service.delete('42', confirmed=True)
    assert not store.path.exists()


def test_deadline_is_next_same_subject_and_type_with_explicit_priority(store):
    seed(store, [event(), event('lecture', 'Физика (ЛК)', day=27, event_type=EventType.LECTURE),
                 event('other', 'Математика (ПР)', day=27), event('next', day=29)])
    lesson = next(item for item in store.lessons() if item['source_uid'] == 'physics')
    assert lesson['due_at'] == moment(29).isoformat()
    store.set_deadline(lesson['id'], moment(30, 12))
    store.reconcile_lessons([event(), event('next', day=28)])
    assert store.get_lesson(lesson['id'])['due_at'] == moment(30, 12).isoformat()
    last = next(item for item in store.lessons() if item['source_uid'] == 'next')
    assert last['due_at'] is None


def test_moved_source_keeps_homework_and_invalidates_stale_buttons(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, 'Номера 15–20', file_id='photo')
    token = store.callback_data(lesson_id, 'attend')
    assert store.reconcile_lessons([event(day=27)])
    assert store.get_lesson(lesson_id)['materials'][0]['file_id'] == 'photo'
    assert store.apply_callback(token) is None
    assert store.reconcile_lessons([event(day=27)]) == []
    store.reconcile_lessons([event('new-uid', day=28)], aliases={'physics': 'new-uid'})
    assert len(store.lessons()) == 1
    assert store.get_lesson(lesson_id)['source_uid'] == 'new-uid'
    assert store.get_lesson(lesson_id)['materials'][0]['text'] == 'Номера 15–20'


def test_personal_identity_keeps_material_when_source_uid_changes(store):
    first = PersonalUniversityEvent('personal-1', 'Физика (ПР)', '', moment(), moment(hour=16),
                                    university_event_uid='source-1', state=PersonalEventState.CONFIRMED,
                                    metadata={'session_type': 'practical'})
    lesson_id = seed(store, [first])
    store.add_material(lesson_id, 'ДЗ')
    store.reconcile_lessons([replace(first, university_event_uid='source-2', start_time=moment(27), end_time=moment(27, 16))])
    assert len(store.lessons()) == 1
    assert store.get_lesson(lesson_id)['event_type'] == 'practical'
    assert store.get_lesson(lesson_id)['source_personal_id'] == 'personal-1'
    assert store.get_lesson(lesson_id)['materials'][0]['text'] == 'ДЗ'


def test_course_and_homework_duration_overrides_and_whole_homework_result(store):
    seed(store, [event(), event('next', day=29)])
    first, second = store.lessons()
    store.set_course_duration('Физика', 120)
    store.add_material(first['id'], 'Задачи')
    assert store.get_lesson(first['id'])['remaining_minutes'] == 120
    store.set_homework_duration(first['id'], 180)
    store.set_course_duration('Физика', 90)
    assert store.get_lesson(first['id'])['duration_minutes'] == 180
    assert store.get_lesson(second['id'])['duration_minutes'] == 90
    store.record_preparation_result(first['id'], False, 'Остались номера 19–20', 45)
    restarted = LearningLifecycleStore(store.path, '42')
    lesson = restarted.get_lesson(first['id'])
    assert lesson['remaining_text'] == 'Остались номера 19–20'
    assert lesson['remaining_minutes'] == 45
    completed = restarted.apply_callback(restarted.callback_data(first['id'], 'done'))
    assert completed['completed'] and completed['remaining_minutes'] == 0
    assert completed['materials'][0]['text'] == 'Задачи'
    with pytest.raises(ValueError):
        restarted.set_homework_duration(first['id'], -10)


@pytest.mark.parametrize('last_hour,travel,expected_hour,expected_minute', [
    (16, 60, 17, 0), (17, 90, 17, 0), (18, 60, 18, 45), (18, 5, 18, 0), (18, 0, 18, 0),
])
def test_evening_time_uses_local_last_lesson_and_never_before_its_end(store, last_hour, travel, expected_hour, expected_minute):
    lesson_id = seed(store, [event(end_hour=last_hour)])
    due = moment(hour=expected_hour, minute=expected_minute)
    assert not [item for item in store.due_notifications(due - timedelta(seconds=1), travel_minutes=travel)
                if item['kind'] == 'evening']
    notifications = store.due_notifications(due.astimezone(ZoneInfo('UTC')), travel_minutes=travel)
    evening = [item for item in notifications if item['kind'] == 'evening']
    assert [item['lesson_id'] for item in evening] == [lesson_id]
    assert evening[0]['due_at'] == due.isoformat()


def test_separate_reminders_only_ask_missing_answers(store):
    seed(store, [event(), event('math', 'Математика (ЛК)', hour=16, end_hour=18)])
    first, second = store.lessons()
    store.apply_callback(store.callback_data(first['id'], 'attend'))
    store.add_material(second['id'], 'Номера')
    notifications = store.due_notifications(moment(hour=19))
    evening = [item for item in notifications if item['kind'] == 'evening']
    assert len(evening) == 2
    assert next(item for item in evening if item['lesson_id'] == first['id'])['missing'] == ['homework']
    assert next(item for item in evening if item['lesson_id'] == second['id'])['missing'] == ['attendance']
    store.apply_callback(store.callback_data(first['id'], 'nothing'))
    assert all(item['lesson_id'] != first['id'] for item in store.due_notifications(moment(hour=19)))


def test_notifications_claim_retry_ack_and_restart_dedup(store):
    lesson_id = seed(store)
    now = moment(hour=15, minute=50)
    notification = store.due_notifications(now)[0]
    assert notification['kind'] == 'attendance'
    assert store.claim_notification(notification['id'], now)
    restarted = LearningLifecycleStore(store.path, '42')
    assert not restarted.due_notifications(now)
    assert not restarted.claim_notification(notification['id'], now)
    assert restarted.due_notifications(now + timedelta(minutes=6))
    restarted.release_notification(notification['id'])
    assert restarted.claim_notification(notification['id'], now + timedelta(minutes=6))
    restarted.ack_notification(notification['id'], message_id=123)
    assert not LearningLifecycleStore(store.path, '42').due_notifications(now + timedelta(minutes=7))
    # The distinct evening question still exists until answers are complete.
    assert store.due_notifications(moment(hour=17))[0]['id'] == f'evening:{lesson_id}'


def test_concurrent_instances_do_not_lose_material_and_only_one_claim_wins(store):
    lesson_id = seed(store)
    stores = [LearningLifecycleStore(store.path, '42') for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda pair: pair[1].add_material(lesson_id, str(pair[0]), source_message_id=pair[0]), enumerate(stores)))
    assert len(store.get_lesson(lesson_id)['materials']) == 8
    notification = store.due_notifications(moment(hour=17))[0]
    with ThreadPoolExecutor(max_workers=8) as executor:
        claimed = list(executor.map(lambda item: item.claim_notification(notification['id'], moment(hour=17)), stores))
    assert sum(claimed) == 1


def test_preparation_context_files_receipts_and_completion_are_durable(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, 'Задачи', file_id='photo')
    store.register_preparation_blocks([{'id': 'block-1', 'lesson_id': lesson_id,
                                       'start': moment(hour=18), 'end': moment(hour=19)}])
    notifications = store.due_notifications(moment(hour=18))
    start = next(item for item in notifications if item['kind'] == 'preparation_start')
    store.ack_notification(start['id'])
    restarted = LearningLifecycleStore(store.path, '42')
    assert restarted.preparation_context('block-1')['lesson']['materials'][0]['file_id'] == 'photo'
    assert not any(item['kind'] == 'preparation_start' for item in restarted.due_notifications(moment(hour=18, minute=5)))
    assert any(item['kind'] == 'preparation_end' for item in restarted.due_notifications(moment(hour=19)))
    restarted.record_preparation_result(lesson_id, True)
    assert not any(item['kind'].startswith('preparation') for item in restarted.due_notifications(moment(hour=19)))


def test_planner_splits_whole_homework_preserves_busy_intervals_and_removes_done_work(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, 'Большое ДЗ', due_at=moment(29, 18))
    store.set_homework_duration(lesson_id, 300)
    settings = UserPlanningSettings(profile=UserPlanningProfile(travel_minutes_each_way=0, recovery_minutes_after_university=0))
    busy = FixedCommitment('personal', 'Личная встреча', moment(27, 9), moment(27, 12), CommitmentType.OTHER)
    blocks = plan_learning_preparations(store, settings, moment(26, 18), [], [busy])
    assert sum(block.minutes for block in blocks) == 300
    assert len(blocks) == 3
    assert all(block.minutes <= 120 for block in blocks)
    assert all(not (block.start < busy.end and block.end > busy.start) for block in blocks)
    assert all(block.end <= moment(29, 18) for block in blocks)
    assert len({block.source_event_id for block in blocks}) == 1
    store.record_preparation_result(lesson_id, True)
    assert plan_learning_preparations(store, settings, moment(26, 18), [], [busy], blocks) == []


def test_planner_reserves_unrelated_preps_and_fails_all_or_nothing(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, 'ДЗ', due_at=moment(26, 20))
    settings = UserPlanningSettings()
    unrelated = DraftPreparationBlock('other', 'other-lesson', 'Другой блок', moment(26, 18), moment(26, 20), 120, '')
    with pytest.raises(ValueError, match='не хватает'):
        plan_learning_preparations(store, settings, moment(26, 18), [], [], [unrelated])
    assert store.get_lesson(lesson_id)['remaining_minutes'] == 60


def test_planner_does_not_invent_deadline(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, 'ДЗ без срока')
    assert plan_learning_preparations(store, UserPlanningSettings(), moment(26, 18), [], []) == []
    assert store.get_lesson(lesson_id)['due_at'] is None


def test_missing_and_later_remain_unfinished_until_actual_homework_arrives(store):
    lesson_id = seed(store)
    store.apply_callback(store.callback_data(lesson_id, 'miss'))
    store.apply_callback(store.callback_data(lesson_id, 'later'))
    evening = store.due_notifications(moment(hour=17))[0]
    assert evening['missing'] == ['homework']
    store.add_material(lesson_id, 'Узнал у одногруппников: номера 15–20')
    assert store.due_notifications(moment(hour=17)) == []


def test_block_end_shift_reissues_result_prompt_without_repeating_start(store):
    lesson_id = seed(store)
    store.add_material(lesson_id, 'ДЗ')
    block = {'id': 'block', 'lesson_id': lesson_id, 'start': moment(hour=18), 'end': moment(hour=19)}
    store.register_preparation_blocks([block])
    for notification in store.due_notifications(moment(hour=18)):
        store.ack_notification(notification['id'])
    first_end = next(item for item in store.due_notifications(moment(hour=19)) if item['kind'] == 'preparation_end')
    store.ack_notification(first_end['id'])
    store.register_preparation_blocks([{**block, 'end': moment(hour=20)}])
    later = store.due_notifications(moment(hour=20))
    end = next(item for item in later if item['kind'] == 'preparation_end')
    assert end['id'] != first_end['id']
    assert all(item['kind'] != 'preparation_start' for item in later)


def test_planner_retains_the_actual_personal_source_identity(store):
    personal = PersonalUniversityEvent('personal-1', 'Физика (ПР)', '', moment(), moment(hour=16),
                                        university_event_uid='source', state=PersonalEventState.CONFIRMED,
                                        metadata={'session_type': 'practical'})
    lesson_id = seed(store, [personal])
    store.add_material(lesson_id, 'Задачи', due_at=moment(29, 18))
    blocks = plan_learning_preparations(store, UserPlanningSettings(), moment(26, 18), [personal], [])
    assert blocks[0].source_event_id == 'personal-1'
    store.register_preparation_blocks(blocks)
    assert store.preparation_context(blocks[0].id)['lesson']['id'] == lesson_id


def test_calendar_dirty_revision_is_atomic_and_ack_does_not_hide_later_change(store):
    lesson_id = seed(store)
    first = store.calendar_revision()
    assert store.has_pending_calendar_sync()
    store.mark_calendar_synced(first)
    store.callback_data(lesson_id, 'attend')
    store.set_context(lesson_id)
    assert not store.has_pending_calendar_sync()
    store.add_material(lesson_id, 'ДЗ')
    second = store.calendar_revision()
    assert second > first
    store.mark_calendar_synced(first)
    assert LearningLifecycleStore(store.path, '42').has_pending_calendar_sync()
    store.mark_calendar_synced(second)
    assert not store.has_pending_calendar_sync()


def test_cancelled_or_missing_lesson_retains_material_but_stops_prompts(store):
    personal = PersonalUniversityEvent('personal', 'Физика (ПР)', '', moment(), moment(hour=16),
                                      university_event_uid='source', state=PersonalEventState.CONFIRMED)
    lesson_id = seed(store, [personal])
    store.add_material(lesson_id, 'ДЗ', file_id='photo')
    store.reconcile_lessons([replace(personal, state=PersonalEventState.POSSIBLY_CANCELLED)])
    assert not store.get_lesson(lesson_id)['active']
    assert store.get_lesson(lesson_id)['materials'][0]['file_id'] == 'photo'
    assert not store.due_notifications(moment(hour=15, minute=55))


@pytest.mark.parametrize('pause,expected_start', [(15, (14, 0)), (30, (14, 15)), (0, (13, 50))])
def test_continuation_respects_configured_pause_after_recently_finished_prep(store, pause, expected_start):
    lesson_id = seed(store, [event(hour=10, end_hour=12)])
    store.add_material(lesson_id, 'Номера 15–21', due_at=moment(hour=18))
    store.record_preparation_result(lesson_id, False, 'Осталось решить номер 21', 10)
    finished = DraftPreparationBlock('finished', lesson_id, 'Подготовка',
                                     moment(hour=13, minute=35), moment(hour=13, minute=45), 10, '')
    settings = UserPlanningSettings(profile=UserPlanningProfile(preparation_break_minutes=pause))
    # Runtime gives the planner a five-minute publication lead after the end
    # answer. That lead must not replace the configured preparation pause.
    blocks = plan_learning_preparations(store, settings, moment(hour=13, minute=50), [], [], [finished])
    assert len(blocks) == 1 and blocks[0].minutes == 10
    assert blocks[0].start == moment(hour=expected_start[0], minute=expected_start[1])
    assert blocks[0].start - finished.end >= timedelta(minutes=pause)
    assert store.get_lesson(lesson_id)['remaining_text'] == 'Осталось решить номер 21'
    assert store.get_lesson(lesson_id)['materials'][0]['text'] == 'Номера 15–21'


def test_new_homework_block_leaves_pause_before_and_after_retained_future_prep(store):
    lesson_id = seed(store, [event(hour=10, end_hour=12)])
    store.add_material(lesson_id, 'ДЗ', due_at=moment(hour=18))
    store.set_homework_duration(lesson_id, 10)
    future = DraftPreparationBlock('retained', 'other-source', 'Другая подготовка',
                                   moment(hour=14, minute=10), moment(hour=14, minute=20), 10, '')
    blocks = plan_learning_preparations(store, UserPlanningSettings(), moment(hour=13, minute=50), [], [], [future])
    assert len(blocks) == 1
    # 13:50–14:00 would leave only ten minutes before the retained block.
    # The next legal interval begins fifteen minutes after its end.
    assert blocks[0].start == moment(hour=14, minute=35)
    assert blocks[0].start - future.end == timedelta(minutes=15)


def test_replaced_own_future_prep_does_not_pin_its_old_pause(store):
    lesson_id = seed(store, [event(hour=10, end_hour=12)])
    store.add_material(lesson_id, 'ДЗ', due_at=moment(hour=18))
    store.set_homework_duration(lesson_id, 10)
    replaced = DraftPreparationBlock('replace', lesson_id, 'Старая подготовка',
                                     moment(hour=14, minute=10), moment(hour=14, minute=20), 10, '')
    blocks = plan_learning_preparations(store, UserPlanningSettings(), moment(hour=13, minute=50), [], [], [replaced])
    assert len(blocks) == 1 and blocks[0].start == moment(hour=13, minute=50)
