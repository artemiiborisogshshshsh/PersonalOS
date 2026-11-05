"""Elapsed lessons remain usable for homework without retroactive planning."""
from tests.test_closed_beta_runtime import ICS, NOW, application, onboard, confirmation
from tests.test_published_plan_deletions import DeleteCalendar
from services.published_plan_updates import PublishedPlanUpdates


class UniqueCalendar(DeleteCalendar):
    # Google allocates new IDs after deletion; the old fake reuses len(remote).
    def _insert_event(self, event, calendar_id, **kwargs):
        identifier = super()._insert_event(event, calendar_id, **kwargs)
        row = self.remote.pop((calendar_id, identifier))
        self.sequence = getattr(self, 'sequence', 0) + 1
        unique = f'elapsed-test-{self.sequence}'
        row['id'] = unique
        self.remote[(calendar_id, unique)] = row
        return unique


def test_elapsed_unpublished_lesson_does_not_block_future_plan_or_homework(tmp_path):
    past = ICS.replace(b'pilot-lecture', b'past-lecture').replace(b'20260924', b'20260921')
    content = {'ics': ICS.replace(b'END:VCALENDAR', b'BEGIN:VEVENT' + past.split(b'BEGIN:VEVENT')[1])}
    calendar = UniqueCalendar()
    app = application(tmp_path, calendar, content)
    app.updates = PublishedPlanUpdates(app)
    onboard(app)
    before = list(calendar.writes)
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert calendar.writes == before
    old = next(event for event in app.snapshot.personal_events() if event.end_time <= NOW)
    lesson = next(row for row in app.learning.store.lessons() if row['source_personal_id'] == old.id)
    applied = app.handle_callback('101', confirmation(preview))
    assert 'опубликован' in applied['text'], applied
    operation = next(iter(app.operations.load_all().values()))
    assert old.id not in operation.published_plan['rows']
    assert all(block.source_event_id != old.id for block in operation.blocks)
    assert any(event.id == old.id for event in app.snapshot.personal_events())

    app.learning.store.add_material(lesson['id'], text='Решить задания', kind='homework')
    result = app.sync_learning()
    assert 'обновлён' in result, result
    operation = next(iter(app.operations.load_all().values()))
    assert any(block.source_event_id == old.id and block.end > NOW for block in operation.blocks)
