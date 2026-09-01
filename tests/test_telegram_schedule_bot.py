from datetime import datetime, timezone
from pathlib import Path

from services.telegram_schedule_bot import TelegramScheduleBot
from services.university_schedule_source import UniversityScheduleFetchResult


def fetch_result(_: str, output_path: Path) -> UniversityScheduleFetchResult:
    return UniversityScheduleFetchResult(
        source_url='https://example.test/schedule.ics',
        output_path=output_path,
        event_count=40,
        content_hash='a' * 64,
        fetched_at=datetime.now(timezone.utc),
    )


def bot() -> TelegramScheduleBot:
    return TelegramScheduleBot(
        token='test-token',
        allowed_chat_id='123',
        source_url='https://example.test/schedule.ics',
        output_path=Path('data/schedule.ics'),
        fetcher=fetch_result,
    )


def test_update_schedule_command_fetches_and_reports_result():
    reply = bot().handle_text('123', '/update_schedule')

    assert '40 событий' in reply
    assert 'aaaaaaaaaaaa' in reply


def test_bot_rejects_messages_from_other_chats():
    assert bot().handle_text('999', '/update_schedule') is None


def test_bot_supports_help_and_unknown_command():
    assert '/update_schedule' in bot().handle_text('123', '/help')
    assert 'Неизвестная' in bot().handle_text('123', '/anything')
