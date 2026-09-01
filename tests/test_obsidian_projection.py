from datetime import datetime

from services.obsidian_projection_service import ObsidianProjectionService


def test_projection_preserves_user_content_and_updates_only_marker_section(tmp_path):
    note = tmp_path / 'Математика.md'
    user_before = '# Математика\n\nМои личные заметки.\n'
    note.write_text(user_before, encoding='utf-8')
    service = ObsidianProjectionService()
    event = {
        'uid': 'event-1',
        'summary': 'Математика (ЛК)',
        'dtstart': datetime(2026, 9, 1, 10, 0),
    }

    service.project_course(note, 'Математика', [event], [], [])
    first = note.read_text(encoding='utf-8')
    service.project_course(
        note,
        'Математика',
        [{**event, 'summary': 'Математика (ЛК) — перенос'}],
        [{'id': 'task-1', 'title': 'Повторить тему'}],
        [{'uid': 'prep-1', 'source_event_uid': 'event-1', 'summary': 'Подготовка'}],
    )
    second = note.read_text(encoding='utf-8')

    assert first.startswith(user_before)
    assert second.startswith(user_before)
    assert second.count('PERSONAL_OS:COURSE:математика:START') == 1
    assert 'Мои личные заметки.' in second
    assert 'task-1' in second and 'prep-1' in second
    assert 'перенос' in second


def test_projection_is_idempotent(tmp_path):
    note = tmp_path / 'Course.md'
    service = ObsidianProjectionService()
    service.project_course(note, 'Course', [], [], [])
    first = note.read_text(encoding='utf-8')
    service.project_course(note, 'Course', [], [], [])
    assert note.read_text(encoding='utf-8') == first
