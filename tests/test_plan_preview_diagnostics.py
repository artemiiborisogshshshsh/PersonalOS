import pytest

from tests.test_closed_beta_runtime import application


@pytest.mark.parametrize('command', [
    '/weekly_preview', '/preparations', '/update_all', '/update_schedule',
])
def test_plan_preview_failure_returns_safe_runtime_code_and_class(tmp_path, caplog, command):
    app = application(tmp_path)

    def fail():
        raise TimeoutError('private URL, token, and provider response')

    app.preview = fail
    response = app.handle_text('101', command)

    assert 'Проверка не завершена. Calendar не изменялся.' in response['text']
    assert 'plan_preview_runtime' in response['text']
    assert 'TimeoutError' in response['text']
    assert 'stage=command_preview exception_class=TimeoutError' in caplog.text
    assert 'private URL' not in response['text'] + caplog.text
    assert 'token' not in response['text'] + caplog.text
    assert 'provider response' not in response['text'] + caplog.text


def test_plan_preview_unknown_exception_class_is_hidden(tmp_path, caplog):
    app = application(tmp_path)
    private_type = type('PrivateFailure', (Exception,), {})

    def fail():
        raise private_type('secret failure details')

    app.preview = fail
    response = app.handle_text('101', '/weekly_preview')

    assert 'ошибка: unexpected' in response['text']
    assert 'PrivateFailure' not in response['text'] + caplog.text
    assert 'secret failure details' not in response['text'] + caplog.text


def test_non_preview_command_keeps_existing_generic_failure_reply(tmp_path, caplog):
    app = application(tmp_path)

    def fail(*_args):
        raise ValueError('secret details')

    app.handler.handle_text = fail
    response = app.handle_text('101', '/start')

    assert response['text'] == (
        'Проверка не завершена. Calendar не изменялся. Повтори команду; '
        'при повторной ошибке обратись к оператору.'
    )
    assert 'plan_preview_runtime' not in response['text'] + caplog.text
    assert 'secret details' not in response['text'] + caplog.text
