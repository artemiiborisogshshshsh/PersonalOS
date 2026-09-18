import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from services.ai.intent_interpreter import IntentInterpreter
from services.application.command import CreateTaskCommand, CreateUniversityEventCommand


def interpret(text, now=None):
    service = IntentInterpreter()
    assert asyncio.run(service.initialize())
    return asyncio.run(service.interpret_intent(text, {'now': now} if now else None))


def test_one_recognized_phrase_produces_a_confident_task_proposal():
    result = interpret('Создай задачу купить учебник')

    assert result.success is True
    assert result.confidence >= 0.7
    assert isinstance(result.data, CreateTaskCommand)
    assert 'купить учебник' in result.data.title


def test_event_proposal_uses_the_supplied_user_timezone_and_relative_date():
    now = datetime(2026, 9, 14, 21, tzinfo=ZoneInfo('Asia/Tomsk'))
    result = interpret('Запланируй встречу завтра в 23:30', now)

    assert result.success is True
    assert isinstance(result.data, CreateUniversityEventCommand)
    assert result.data.is_group_event is False
    assert result.data.dtstart == datetime(2026, 9, 15, 23, 30, tzinfo=ZoneInfo('Asia/Tomsk'))
    assert result.data.dtend.hour == 0
    assert result.data.dtend.date().isoformat() == '2026-09-16'


def test_unknown_or_destructive_text_never_creates_a_placeholder_command():
    for text in ('удали встречу завтра', 'перенеси подготовку', 'блаблабла'):
        result = interpret(text)
        assert result.success is False
        assert result.data is None
        assert 'уточнить' in result.error
