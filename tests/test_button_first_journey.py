"""Exercise the normal setup through attached controls, not command examples."""
from tests.test_closed_beta_runtime import URL, application


def choose(app, response, *, label=None, prefix=None):
    candidates = [button for row in response.get('buttons', []) for button in row
                  if (label is None or button['text'] == label)
                  and (prefix is None or button.get('callback_data', '').startswith(prefix))]
    assert len(candidates) == 1, (label, prefix, response)
    return app.handle_callback('101', candidates[0]['callback_data'])


def profile_step(app):
    source = app.handle_text('101', '/start')
    assert source['step'] == 'source'
    response = app.handle_text('101', URL)
    assert response['step'] == 'timezone'
    response = choose(app, response, prefix='ob:timezone:')
    assert response['step'] == 'calendar'
    assert any(button.get('callback_data') == 'main:connect_google'
               for row in response['buttons'] for button in row)
    # The fixture has an authenticated Calendar adapter; connection itself is
    # covered through InvitedBot's secure OAuth callback tests.
    response = choose(app, response, prefix='ob:calendar:check')
    assert response['step'] == 'style'
    response = choose(app, response, prefix='ob:style:friendly')
    assert response['step'] == 'profile'
    return response


def test_normal_setup_needs_no_typed_settings_and_publication_stays_separate(tmp_path):
    app = application(tmp_path)
    response = profile_step(app)
    before = list(app.adapter.writes)
    response = choose(app, response, prefix='main:sleep')
    response = choose(app, response, label='23:00')
    response = choose(app, response, label='07:00')
    assert app.profile.load().sleep_end == '06:40'
    response = choose(app, response, label='Сохранить')
    assert app.profile.load().sleep_end == '07:00'
    response = choose(app, response, prefix='main:travel')
    response = choose(app, response, label='30 мин')
    assert app.profile.load().profile.travel_minutes_each_way == 60
    response = choose(app, response, label='Сохранить')
    assert app.profile.load().profile.travel_minutes_each_way == 30
    assert response['step'] == 'complete'
    assert app.adapter.writes == before
    response = choose(app, response, prefix='main:plan')
    assert app.adapter.writes == before
    response = choose(app, response, prefix='pilot:apply:')
    assert 'опубликован' in response['text'].lower()
    assert app.adapter.writes != before


def test_leaving_profile_card_cancels_typed_value_capture(tmp_path):
    app = application(tmp_path)
    profile_step(app)
    app.handle_callback('101', 'main:travel')
    app.handle_callback('101', 'main:open')
    assert app.handler.try_handle_profile_preferences('45') is None
    assert app.profile.load().profile.travel_minutes_each_way == 60
    assert not app.adapter.writes
