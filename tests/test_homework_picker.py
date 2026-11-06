from datetime import timedelta

from tests.test_telegram_learning_flow import flow_app


def test_picker_keeps_all_lessons_and_page_two_preserves_attachment(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    real = lessons[0]
    now = app.now()
    synthetic = []
    for index in range(21):
        row = dict(real)
        row.update(id=f'picker-{index}', course=f'Курс {index}',
                   start=(now + timedelta(days=40 + index)).isoformat(), active=True)
        synthetic.append(row)
    today = dict(real)
    today.update(id='today', course='Сегодня', start=(now + timedelta(minutes=30)).isoformat())
    synthetic[19]['id'] = real['id']
    monkeypatch.setattr(app.learning.store, 'lessons', lambda: synthetic + [today])
    get_lesson = app.learning.store.get_lesson
    lookup = {row['id']: row for row in synthetic + [today] if row['id'] != real['id']}
    monkeypatch.setattr(app.learning.store, 'get_lesson', lambda lesson_id: lookup.get(lesson_id) or get_lesson(lesson_id))

    result = app.learning._choose({'attachment': {'file_id': 'photo'}})
    state = app.learning._state()['selection']
    assert state['lessons'][0] == 'today'
    assert len(state['lessons']) == 22
    next_button = next(button for row in result['buttons'] for button in row if button['text'] == 'Дальше')
    page = app.learning.handle_callback(next_button['callback_data'])
    assert '2/2' in page['text']
    # Global index 20 points at the original stored lesson, exercising normal persistence.
    selected_callback = next(button for row in page['buttons'] for button in row if button['callback_data'].endswith(':20'))
    saved = app.learning.handle_callback(selected_callback['callback_data'])
    assert 'Сохранено к занятию' in saved['text']
    assert app.learning.store.get_lesson(real['id'])['materials'][-1]['file_id'] == 'photo'
    assert 'устарела' in app.learning.handle_callback(selected_callback['callback_data'])['text']


def test_picker_rejects_bad_pages_without_consuming_selection(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    app.learning._choose({'text': 'задали номера 1'})
    token = app.learning._state()['selection']['token']
    for suffix in ('-1', '999', 'bad'):
        assert 'устарела' in app.learning.handle_callback(f'learn:page:{token}:{suffix}')['text']
    assert app.learning._state()['selection']['pending']['text'] == 'задали номера 1'


def test_picker_missing_or_inactive_lesson_invalidates_page_and_selection(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    app.learning._choose({'attachment': {'file_id': 'not-saved'}})
    token = app.learning._state()['selection']['token']
    monkeypatch.setattr(app.learning.store, 'get_lesson', lambda _lesson_id: (_ for _ in ()).throw(KeyError()))
    page = app.learning.handle_callback(f'learn:page:{token}:0')
    assert 'изменились' in page['text']
    assert app.learning._state().get('selection') is None

    app, lessons = flow_app(tmp_path / 'inactive', monkeypatch)
    lesson = lessons[0]
    app.learning._choose({'attachment': {'file_id': 'not-saved'}})
    callback = f"learn:select:{app.learning._state()['selection']['token']}:0"
    monkeypatch.setattr(app.learning.store, 'get_lesson', lambda _lesson_id: {**lesson, 'active': False})
    result = app.learning.handle_callback(callback)
    assert 'изменилось' in result['text']
    assert not lessons[0]['materials']


def test_empty_picker_includes_main_menu_button(tmp_path, monkeypatch):
    app, _ = flow_app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.learning.store, 'lessons', lambda: [])
    reply = app.learning.material_entry()
    assert 'нет' in reply['text']
    assert any(button['callback_data'] == 'main:open' for row in reply['buttons'] for button in row)
