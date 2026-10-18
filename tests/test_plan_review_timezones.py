from datetime import datetime

from services.published_plan_updates import format_interval


def test_interval_uses_profile_timezone_for_same_utc_instant():
    start = datetime.fromisoformat('2026-10-18T06:30:00+00:00')
    end = datetime.fromisoformat('2026-10-18T06:50:00+00:00')

    assert format_interval(start, end, 'UTC') == '18.10 06:30–06:50'
    assert format_interval(start, end, 'Asia/Tomsk') == '18.10 13:30–13:50'


def test_interval_shows_end_date_when_profile_timezone_crosses_midnight():
    start = datetime.fromisoformat('2026-10-17T16:30:00+00:00')
    end = datetime.fromisoformat('2026-10-17T17:30:00+00:00')

    assert format_interval(start, end, 'Asia/Tomsk') == '17.10 23:30–18.10 00:30'


def test_interval_preserves_existing_tomsk_local_display():
    start = datetime.fromisoformat('2026-10-18T13:30:00+07:00')
    end = datetime.fromisoformat('2026-10-18T13:50:00+07:00')

    assert format_interval(start, end, 'Asia/Tomsk') == '18.10 13:30–13:50'
