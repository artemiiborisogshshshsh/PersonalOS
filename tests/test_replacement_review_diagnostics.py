from tests.test_closed_beta_runtime import application
from services.telegram_replacement_review import _safe_failure_cause


def _raise(error):
    def callback():
        raise error
    return callback


def test_runtime_replacement_review_error_has_safe_code_and_class(tmp_path, caplog):
    app = application(tmp_path)
    app.replacements.review = _raise(ValueError('private upstream response body'))

    response = app.handle_text('101', '/review_replacements')

    assert 'replacement_review_runtime' in response['text']
    assert 'ValueError' in response['text']
    assert 'private upstream response body' not in response['text'] + caplog.text
    assert 'stage=review_runtime exception_class=ValueError' in caplog.text


def test_runtime_replacement_review_unknown_exception_class_is_hidden(tmp_path, caplog):
    app = application(tmp_path)
    private_type = type('PrivateFailure', (Exception,), {})
    app.replacements.review = _raise(private_type('private details'))

    response = app.handle_text('101', '/review_replacements')

    assert 'ошибка: unexpected' in response['text']
    assert 'PrivateFailure' not in response['text'] + caplog.text
    assert 'private details' not in response['text'] + caplog.text
    assert 'exception_class=unexpected' in caplog.text


def test_replacement_next_error_has_safe_code_and_class(tmp_path, caplog):
    app = application(tmp_path)
    app.replacements.review = _raise(TimeoutError('private host and URL'))

    response = app.handle_callback('101', 'replacement:next')

    assert 'replacement_next_review' in response['text']
    assert 'TimeoutError' in response['text']
    assert 'private host and URL' not in response['text'] + caplog.text
    assert 'stage=next_review exception_class=TimeoutError' in caplog.text


def test_source_failure_cause_codes_are_typed_and_bounded():
    import requests

    def wrap(cause):
        try:
            raise cause
        except Exception as inner:
            try:
                raise RuntimeError('private upstream body') from inner
            except RuntimeError as outer:
                return outer

    assert _safe_failure_cause(wrap(requests.Timeout('secret timeout'))) == 'timeout'
    assert _safe_failure_cause(wrap(requests.ConnectionError('secret host'))) == 'connection'
    http = requests.HTTPError('secret response')
    http.response = type('FakeResponse', (), {'status_code': 503})()
    assert _safe_failure_cause(wrap(http)) == 'http_503'
    invalid_http = requests.HTTPError('secret response')
    invalid_http.response = type('FakeResponse', (), {'status_code': 700})()
    assert _safe_failure_cause(wrap(invalid_http)) == 'unexpected'
    assert _safe_failure_cause(wrap(ValueError('secret parse input'))) == 'parse'
    assert _safe_failure_cause(RuntimeError('secret message')) == 'none'
