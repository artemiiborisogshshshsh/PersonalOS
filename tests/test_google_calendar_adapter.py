from unittest.mock import Mock

from adapters.google_calendar_adapter import GoogleCalendarAdapter


def test_get_or_create_calendar_remembers_existing_calendar_id():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.calendarList().list().execute.return_value = {
        'items': [{'id': 'calendar-42', 'summary': 'Personal schedule'}]
    }

    calendar_id = adapter._get_or_create_calendar('Personal schedule')

    assert calendar_id == 'calendar-42'
    assert adapter.calendar_id == 'calendar-42'


def test_get_or_create_calendar_remembers_created_calendar_id():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.calendarList().list().execute.return_value = {'items': []}
    adapter.service.calendars().insert().execute.return_value = {'id': 'calendar-99'}

    calendar_id = adapter._get_or_create_calendar('Personal schedule')

    assert calendar_id == 'calendar-99'
    assert adapter.calendar_id == 'calendar-99'
