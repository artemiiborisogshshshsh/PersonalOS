from services.product_state import ScheduleSource, UserProductStateStore
from services.schedule_source_service import ScheduleSourceService
import pytest


ICS = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:one\r
DTSTART:20260907T090000\r
DTEND:20260907T100000\r
SUMMARY:Mathematics\r
END:VEVENT\r
END:VCALENDAR\r
'''


def test_source_is_previewed_before_safe_activation_and_previous_source_remains(tmp_path):
    store = UserProductStateStore(tmp_path / 'user.json')
    service = ScheduleSourceService(store, remote_reader=lambda _: ICS)
    first = ScheduleSource('first', 'ics_url', 'https://example.test/one.ics', 'Первый')
    second = ScheduleSource('second', 'ics_url', 'https://example.test/two.ics', 'Второй')

    preview = service.connect_and_preview('user-1', first)
    service.activate('user-1', first.id)
    service.connect_and_preview('user-1', second)
    state = service.activate('user-1', second.id)

    assert preview.event_count == 1
    assert preview.sample_titles == ['Mathematics']
    assert state.active_source_id == 'second'
    assert set(state.sources) == {'first', 'second'}


def test_invalid_ics_is_not_saved_as_a_source(tmp_path):
    store = UserProductStateStore(tmp_path / 'user.json')
    service = ScheduleSourceService(store, remote_reader=lambda _: b'not an ics')
    source = ScheduleSource('bad', 'ics_url', 'https://example.test/bad', 'Bad')

    try:
        service.connect_and_preview('user-1', source)
    except ValueError as error:
        assert 'iCalendar' in str(error)
    else:
        raise AssertionError('invalid source was accepted')
    assert not store.load('user-1').sources


def test_incomplete_or_invalid_interval_source_is_not_saved(tmp_path):
    store = UserProductStateStore(tmp_path / 'user.json')
    source = ScheduleSource('bad', 'ics_url', 'https://example.test/bad', 'Bad')
    incomplete = ICS.replace(b'DTEND:20260907T100000\r\n', b'')
    service = ScheduleSourceService(store, remote_reader=lambda _: incomplete)

    try:
        service.connect_and_preview('user-1', source)
    except ValueError as error:
        assert 'неполное' in str(error)
    else:
        raise AssertionError('incomplete event was accepted')
    assert not store.load('user-1').sources

    backwards = ICS.replace(b'DTEND:20260907T100000', b'DTEND:20260907T080000')
    service = ScheduleSourceService(store, remote_reader=lambda _: backwards)
    try:
        service.connect_and_preview('user-1', source)
    except ValueError as error:
        assert 'неверным интервалом' in str(error)
    else:
        raise AssertionError('invalid interval was accepted')
    assert not store.load('user-1').sources


def test_tpu_group_page_is_an_explicit_product_source_kind():
    source = ScheduleSource(
        'tpu-8i41', 'tpu_group_page',
        'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html', 'TPU: 8И41',
    )

    assert source.kind == 'tpu_group_page'


def test_tpu_preview_uses_injected_discovery_and_never_persists_export_key(tmp_path):
    store = UserProductStateStore(tmp_path / 'user.json')
    source = ScheduleSource(
        'tpu-8i41', 'tpu_group_page',
        'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html', 'TPU: 8И41',
    )
    export_url = 'https://ro-rasp.tpu.ru/export/ical.html?key=TEMPORARY_SECRET'
    discovered = []
    read = []
    service = ScheduleSourceService(
        store,
        remote_reader=lambda url: read.append(url) or ICS,
        tpu_discoverer=lambda url: discovered.append(url) or export_url,
    )

    service.connect_and_preview('user-1', source)

    assert discovered == [source.location]
    assert read == [export_url]
    serialized = (tmp_path / 'user.json').read_text(encoding='utf-8')
    assert 'TEMPORARY_SECRET' not in serialized
    assert store.load('user-1').sources[source.id].location == source.location


def test_remote_schedule_source_requires_https():
    with pytest.raises(ValueError, match='HTTPS'):
        ScheduleSource('insecure', 'ics_url', 'http://example.test/schedule.ics', 'Insecure')
