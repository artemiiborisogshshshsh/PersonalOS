"""End-to-end acceptance for /manage attendance through the production transport."""

from functools import partial

from dataclasses import replace

from services.attendance_preferences import AttendancePreferenceStore
from services.closed_beta_runtime import ClosedBetaApplication, PilotSource
from services.invited_beta_runtime import InvitedBot
from services.product_state import ScheduleSource
from services.published_plan_updates import PublishedPlanUpdates, decode
from tests.test_closed_beta_runtime import Calendar, NOW, URL, confirmation


def _ics():
    rows = [
        ('lecture', '20260924T030000Z', '20260924T040000Z', 'Алгебра (ЛК)', ''),
        ('practical', '20260925T030000Z', '20260925T040000Z', 'Алгебра (ПР)', ''),
        ('lab-a', '20260923T070000Z', '20260923T080000Z', 'Алгебра (ЛБ)', ''),
        ('lab-b', '20260924T070000Z', '20260924T080000Z', 'Алгебра (ЛБ)', ''),
        ('lab-c', '20260925T070000Z', '20260925T080000Z', 'Алгебра (ЛБ)', ''),
        ('other', '20260928T030000Z', '20260928T040000Z', 'Физика (ЛК)', ''),
    ]
    events = []
    for uid, start, end, title, location in rows:
        events.append(
            'BEGIN:VEVENT\r\n'
            f'UID:{uid}\r\nDTSTART:{start}\r\nDTEND:{end}\r\n'
            f'SUMMARY:{title}\r\nLOCATION:{location}\r\nEND:VEVENT\r\n'
        )
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n' + ''.join(events) +
            'END:VCALENDAR\r\n').encode()


def envelope(number, chat='101', text='/start', data=None):
    message = {'chat': {'id': chat, 'type': 'private'},
               'from': {'id': chat}, 'text': text}
    if data is not None:
        return {'update_id': number, 'callback_query': {
            'id': str(number), 'from': {'id': chat}, 'message': message, 'data': data}}
    return {'update_id': number, 'message': message}


def setup(tmp_path):
    calendars = {'101': Calendar(), '202': Calendar()}
    content = {'ics': _ics()}

    def fetch(url, path, **kwargs):
        assert url == URL
        path.write_bytes(content['ics'])

    factory = partial(
        ClosedBetaApplication, now=lambda: NOW, enable_schedule_ocr=True,
        enable_plan_updates=True,
        source_factory=lambda store: PilotSource(store, fetcher=fetch),
    )
    bot = InvitedBot('synthetic', tmp_path, calendars.__getitem__, factory)
    for chat in calendars:
        bot.invites.set_access(chat, True)
    return bot, calendars, content


def send_text(bot, number, chat, text):
    return bot.process_update(envelope(number, chat, text=text))


def send_callback(bot, number, chat, data):
    return bot.process_update(envelope(number, chat, data=data))


def onboard(bot, chat='101', start=1, *, return_reset=False):
    number = start
    response = send_text(bot, number, chat, '/start'); number += 1
    assert response['step'] == 'source'
    response = send_text(bot, number, chat, '/connect_tpu ' + URL); number += 1
    assert response['step'] == 'timezone'
    response = send_callback(bot, number, chat, 'ob:timezone:Asia/Tomsk'); number += 1
    assert response['step'] == 'calendar'
    response = send_text(bot, number, chat, '/attendance'); number += 1
    if response.get('attendance_complete'):
        response = send_callback(bot, number, chat, 'att:reset'); number += 1
    for _ in range(30):
        if response.get('attendance_complete'):
            break
        buttons = [button for row in response['buttons'] for button in row]
        save_lab = next((button for button in buttons
                         if ':lab:save' in button['callback_data']), None)
        answer_yes = next((button for button in buttons
                           if button['callback_data'].endswith(':yes')), None)
        choice = save_lab or answer_yes or buttons[0]
        response = send_callback(bot, number, chat, choice['callback_data']); number += 1
    assert response['attendance_complete']
    reset_callback = button(response, lambda item: item['callback_data'] == 'att:reset')['callback_data']
    response = send_text(bot, number, chat, '/calendar_status'); number += 1
    assert response['step'] == 'style'
    response = send_callback(bot, number, chat, 'ob:style:friendly'); number += 1
    assert response['step'] == 'profile'
    response = send_text(bot, number, chat, '/sleep 23:00 06:40'); number += 1
    assert response['step'] == 'profile'
    response = send_text(bot, number, chat, '/travel 30'); number += 1
    assert response['step'] == 'complete'
    app = bot.applications[chat][1]
    app.updates = PublishedPlanUpdates(app)
    return (number, reset_callback) if return_reset else number


def button(reply, predicate):
    return next(button for row in reply['buttons'] for button in row if predicate(button))


def edit_algebra(bot, chat, number, *, labs=2):
    home = send_text(bot, number, chat, '/manage'); number += 1
    assert any(button['callback_data'] == 'manage:attendance'
               for row in home['buttons'] for button in row)
    reply = send_callback(bot, number, chat, 'manage:attendance'); number += 1
    assert 'выбери предмет' in reply['text'].casefold()
    reply = send_callback(bot, number, chat,
                          button(reply, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    assert 'Алгебра' in reply['text']
    # Toggle lecture and practical on. Each action produces a fresh revisioned callback.
    for label in ('Лекции', 'Практики'):
        choice = button(reply, lambda item: label in item['text'])
        if not choice['text'].startswith('☑'):
            reply = send_callback(bot, number, chat, choice['callback_data'])
            number += 1
    for _ in range(labs):
        lab = button(reply, lambda item: ':lab:' in item['callback_data']
                     and not item['text'].startswith('☑'))
        reply = send_callback(bot, number, chat, lab['callback_data'])
        number += 1
    reply = send_callback(bot, number, chat,
                          button(reply, lambda item: item['text'] == 'Сохранить')['callback_data'])
    number += 1
    assert reply.get('attendance_saved') is True
    return number, reply


def disable_physics_lecture(bot, chat, number):
    menu = send_callback(bot, number, chat, 'manage:attendance'); number += 1
    subject = send_callback(
        bot, number, chat,
        button(menu, lambda item: item['text'].startswith('Физика'))['callback_data'])
    number += 1
    lecture = button(subject, lambda item: 'Лекции' in item['text'])
    if lecture['text'].startswith('☑'):
        subject = send_callback(bot, number, chat, lecture['callback_data']); number += 1
    saved = send_callback(bot, number, chat,
                          button(subject, lambda item: item['text'] == 'Сохранить')['callback_data'])
    number += 1
    assert saved.get('attendance_saved') is True
    return number


def test_manage_attendance_transport_preview_apply_restart_and_user_isolation(tmp_path):
    bot, calendars, _ = setup(tmp_path / 'beta')
    number = onboard(bot)
    assert not calendars['101'].writes and not calendars['202'].writes

    number = disable_physics_lecture(bot, '101', number)
    number, saved = edit_algebra(bot, '101', number)
    assert not calendars['101'].writes
    assert 'ЛК — да' in saved['text'] and 'ПР — да' in saved['text']
    assert 'ЛБ — 2' in saved['text']
    token_menu = send_callback(bot, number, '101', 'manage:attendance'); number += 1
    token_subject = send_callback(
        bot, number, '101',
        button(token_menu, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    foreign_callback = button(token_subject, lambda item: 'Практики' in item['text'])['callback_data']

    # The existing weekly review path is still preview-first; draft save alone
    # must never reach the mocked provider.
    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    assert not calendars['101'].writes
    assert 'Алгебра (ЛК)' in preview['text']
    assert 'Алгебра (ПР)' in preview['text']
    assert 'Физика (ЛК)' not in preview['text']
    assert 'Алгебра (ЛБ)' in preview['text']

    # A preference change invalidates the old preview token. Apply is blocked
    # until a fresh preview is made and confirmed.
    fresh_menu = send_callback(bot, number, '101', 'manage:attendance'); number += 1
    fresh_subject = send_callback(
        bot, number, '101', button(fresh_menu, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    toggle = button(fresh_subject, lambda item: 'Лекции' in item['text'])
    changed = send_callback(bot, number, '101', toggle['callback_data']); number += 1
    changed = send_callback(bot, number, '101',
                            button(changed, lambda item: item['text'] == 'Сохранить')['callback_data'])
    number += 1
    assert changed.get('attendance_saved') is True
    before = list(calendars['101'].writes)
    stale = send_callback(bot, number, '101', confirmation(preview)); number += 1
    assert not calendars['101'].writes
    assert 'измен' in stale['text'].casefold() or 'устар' in stale['text'].casefold()

    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    assert not calendars['101'].writes
    applied = send_callback(bot, number, '101', confirmation(preview)); number += 1
    assert 'опубликован' in applied['text'].casefold()
    assert len(calendars['101'].writes) > len(before)
    operation = next(iter(bot.applications['101'][1].operations.load_all().values()))
    rows = operation.published_plan['rows']
    assert sum(row['kind'] == 'preparation' for row in rows.values()) > 0
    assert sum(row['kind'] == 'class' for row in rows.values()) == 3
    class_sources = {
        decode(row['data']).system_source_event_id
        for row in rows.values() if row['kind'] == 'class'
    }
    assert class_sources == {'practical', 'lab-a', 'lab-b'}
    class_personal_ids = {uid for uid, row in rows.items() if row['kind'] == 'class'}
    preparation_sources = {
        decode(row['data']).system_source_event_id
        for row in rows.values() if row['kind'] == 'preparation'
    }
    assert preparation_sources <= class_personal_ids

    # The second user has a different persisted preference store and cannot
    # use the first user's editor nonce or published preview.
    number = onboard(bot, '202', 1000)
    other_menu = send_callback(bot, number, '202', 'manage:attendance')
    second_algebra = button(other_menu, lambda item: item['text'].startswith('Алгебра'))['text']
    assert 'ЛБ — нет' in second_algebra and 'ЛБ — 2' not in second_algebra
    foreign = send_callback(bot, number + 1, '202', foreign_callback)
    assert 'устар' in foreign['text'].casefold() or 'недоступ' in foreign['text'].casefold()
    assert not calendars['202'].writes

    restarted = InvitedBot('synthetic', tmp_path / 'beta', calendars.__getitem__, bot.application_factory)
    restarted.invites.set_access('101', True)
    restarted_app = restarted.process_update(envelope(number + 1, '101', text='/manage'))
    restarted_menu = restarted.process_update(envelope(number + 2, '101', data='manage:attendance'))
    assert restarted_app and restarted_menu
    assert 'ЛК — нет' in button(restarted_menu, lambda item: item['text'].startswith('Алгебра'))['text']
    foreign_preview = restarted.process_update(
        envelope(number + 3, '202', data=confirmation(preview)))
    assert 'устар' in foreign_preview['text'].casefold()
    assert not calendars['202'].writes


def test_manage_attendance_all_and_none_lab_actions_are_explicit_saves(tmp_path):
    bot, calendars, _ = setup(tmp_path / 'beta')
    number = onboard(bot)
    menu = send_callback(bot, number, '101', 'manage:attendance'); number += 1
    subject = send_callback(
        bot, number, '101', button(menu, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    subject = send_callback(bot, number, '101', button(subject, lambda item: item['text'] == 'Все')['callback_data'])
    number += 1
    saved = send_callback(bot, number, '101', button(subject, lambda item: item['text'] == 'Сохранить')['callback_data'])
    number += 1
    assert saved.get('attendance_saved') is True and 'ЛБ — 3' in saved['text']

    menu = send_callback(bot, number, '101', 'manage:attendance'); number += 1
    subject = send_callback(
        bot, number, '101', button(menu, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    subject = send_callback(bot, number, '101', button(subject, lambda item: item['text'] == 'Ни одной')['callback_data'])
    number += 1
    saved = send_callback(bot, number, '101', button(subject, lambda item: item['text'] == 'Сохранить')['callback_data'])
    assert saved.get('attendance_saved') is True and 'ЛБ — нет' in saved['text']
    assert not calendars['101'].writes


def test_manage_attendance_discards_draft_when_source_or_timezone_changes(tmp_path):
    bot, calendars, content = setup(tmp_path / 'beta')
    number = onboard(bot)
    app = bot.applications['101'][1]
    prefs_path = app.directory / 'attendance_preferences.json'
    before = AttendancePreferenceStore.load(prefs_path).subjects

    menu = send_callback(bot, number, '101', 'manage:attendance'); number += 1
    subject = send_callback(
        bot, number, '101', button(menu, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    subject = send_callback(bot, number, '101', button(subject, lambda item: 'Практики' in item['text'])['callback_data'])
    number += 1
    save_callback = button(subject, lambda item: item['text'] == 'Сохранить')['callback_data']

    # Source validation runs at commit time. A changed feed invalidates the open
    # draft without saving its local toggles.
    content['ics'] = content['ics'].replace(b'UID:lab-c', b'UID:lab-c-replaced')
    response = send_callback(bot, number, '101', save_callback); number += 1
    assert 'расписание изменилось' in response['text'].casefold()
    assert AttendancePreferenceStore.load(prefs_path).subjects == before
    assert not calendars['101'].writes

    # A timezone change also invalidates the draft before it can be committed.
    menu = send_callback(bot, number, '101', 'manage:attendance'); number += 1
    subject = send_callback(
        bot, number, '101', button(menu, lambda item: item['text'].startswith('Алгебра'))['callback_data'])
    number += 1
    subject = send_callback(bot, number, '101', button(subject, lambda item: 'Практики' in item['text'])['callback_data'])
    number += 1
    save_callback = button(subject, lambda item: item['text'] == 'Сохранить')['callback_data']
    settings = app.profile.load()
    settings.profile = replace(settings.profile, timezone='UTC')
    app.profile.save(settings)
    response = send_callback(bot, number, '101', save_callback)
    assert 'часовой пояс изменился' in response['text'].casefold()
    assert AttendancePreferenceStore.load(prefs_path).subjects == before
    assert not calendars['101'].writes


def test_completed_wizard_reset_button_cannot_clear_manage_saved_choices(tmp_path):
    bot, _calendars, _content = setup(tmp_path / 'beta')
    number, old_reset = onboard(bot, return_reset=True)
    number = disable_physics_lecture(bot, '101', number)
    prefs_path = bot.applications['101'][1].directory / 'attendance_preferences.json'
    saved = AttendancePreferenceStore.load(prefs_path).subjects
    assert saved['Физика'].lectures is False

    send_callback(bot, number, '101', 'manage:home'); number += 1
    response = send_callback(bot, number, '101', old_reset)
    assert 'устар' in response['text'].casefold()
    assert AttendancePreferenceStore.load(prefs_path).subjects == saved


def _start_wizard(bot):
    response = send_text(bot, 1, '101', '/start')
    assert response['step'] == 'source'
    response = send_text(bot, 2, '101', '/connect_tpu ' + URL)
    assert response['step'] == 'timezone'
    response = send_callback(bot, 3, '101', 'ob:timezone:Asia/Tomsk')
    assert response['step'] == 'calendar'
    response = send_text(bot, 4, '101', '/attendance')
    callback = button(response, lambda item: item['callback_data'].startswith('att:'))['callback_data']
    app = bot.applications['101'][1]
    prefs_path = app.directory / 'attendance_preferences.json'
    return app, prefs_path, callback


def test_ongoing_wizard_callback_is_rejected_after_source_or_timezone_change(tmp_path):
    for change in ('source', 'timezone'):
        bot, _calendars, _content = setup(tmp_path / change)
        app, prefs_path, old_callback = _start_wizard(bot)
        before = AttendancePreferenceStore.load(prefs_path).subjects
        if change == 'source':
            state = app.source.state_store.load(app.account.id)
            source = ScheduleSource(
                'alternate', 'tpu_group_page', 'https://ro-rasp.tpu.ru/gruppa_99999/2026/1/view.html',
                'Alternate TPU',
            )
            state.sources[source.id] = source
            state.activate_source(source.id)
            app.source.state_store.save(state)
        else:
            settings = app.profile.load()
            settings.profile = replace(settings.profile, timezone='UTC')
            app.profile.save(settings)
        response = send_callback(bot, 5, '101', old_callback)
        assert 'изменился' in response['text'].casefold()
        assert AttendancePreferenceStore.load(prefs_path).subjects == before

