import ssl
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from services.update_all_workflow import UpdateAllBlocked, UpdateAllWorkflow


def issue(index=0):
    return dict(kind='preparation', day=f'{index+1:02d}.09', scope='work',
                title='Информатика', reason='нет свободных 20 минут')


def test_runs_direct_stages_then_verifies_without_success_log(tmp_path):
    calls = []
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [
        ('TPU', lambda: calls.append('tpu')), ('AlfaCRM', lambda: calls.append('work')),
    ], lambda: calls.append('verify') or [])
    assert workflow.run()['text'] == 'Проверка завершена, проблем нет.'
    assert calls == ['tpu', 'work', 'verify']
    assert workflow.load()['phase'] == 'complete'


def test_concurrent_run_is_reported_without_invoking_any_stage(tmp_path):
    path = tmp_path / 'run.json'
    first = UpdateAllWorkflow(path, [], lambda: [])
    descriptor = first._acquire_run_lock()
    assert descriptor is not None
    try:
        action = Mock()
        blocked = UpdateAllWorkflow(path, [('sync', action)], Mock())
        assert blocked.run() == {'text': 'Обновление уже выполняется.', 'buttons': []}
        assert blocked.run(automatic=True) is None
        action.assert_not_called()
        blocked.verify.assert_not_called()
    finally:
        first._release_run_lock(descriptor)


def test_run_lock_is_released_after_an_incomplete_run(tmp_path):
    path = tmp_path / 'run.json'
    first = UpdateAllWorkflow(path, [('sync', Mock(side_effect=RuntimeError('failed')))], Mock())
    assert 'Не удалось завершить' in first.run()['text']
    second_action = Mock()
    second = UpdateAllWorkflow(path, [('sync', second_action)], lambda: [])
    assert second.run()['text'] == 'Проверка завершена, проблем нет.'
    second_action.assert_called_once()


def test_failure_stops_writes_and_never_leaks_exception_url(tmp_path):
    write = Mock()
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [
        ('TPU', Mock(side_effect=ssl.SSLError('secret=https://private.test?token=secret'))),
        ('write', write),
    ], Mock())
    reply = workflow.run()
    assert 'TPU' in reply['text']
    assert 'secret' not in reply['text']
    assert 'secret' not in workflow.path.read_text()
    write.assert_not_called()
    workflow.verify.assert_not_called()
    assert workflow.load()['phase'] == 'incomplete'


def test_summary_three_groups_and_all_details_reachable(tmp_path):
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [], lambda: [issue(i) for i in range(12)])
    summary = workflow.run()
    assert summary['text'].count('Причина') == 3
    identifier = workflow.load()['run_id']
    assert workflow.callback(f'ua:{identifier}:page:0')['text'].count('•') == 5
    assert workflow.callback(f'ua:{identifier}:page:2')['text'].count('•') == 2
    assert workflow.callback(f'ua:{identifier}:one:4')['text'].count('•') == 1
    assert len(summary['buttons']) == 2


def test_manual_conflict_report_stays_concise_and_explains_manual_move(tmp_path):
    reason = 'создана с пометкой конфликта; перенеси вручную'
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [], lambda: [
        dict(issue(index), reason=reason) for index in range(4)
    ])
    summary = workflow.run()
    assert summary['text'].count('Причина') == 3
    assert 'создана с пометкой конфликта; перенеси вручную' in summary['text']


def test_infeasible_plan_is_rendered_as_a_explicit_weekly_outcome(tmp_path):
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [], lambda: [
        dict(kind='infeasible', day='', scope='weekly-plan', title='План недели',
             reason='не хватает как минимум 60 минут для 1 подготовок'),
    ])

    reply = workflow.run()

    assert 'План недели невыполним' in reply['text']


def test_stale_callback_never_starts_new_sync(tmp_path):
    action = Mock()
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [('sync', action)], lambda: [issue()])
    workflow.run()
    identifier = workflow.load()['run_id']
    workflow.run()
    action.reset_mock()
    assert 'устарел' in workflow.callback(f'ua:{identifier}:retry:0')['text']
    action.assert_not_called()


def test_automatic_reports_only_changed_problems_and_reloads_after_restart(tmp_path):
    path = tmp_path / 'run.json'
    assert UpdateAllWorkflow(path, [], lambda: []).run(automatic=True) is None
    assert UpdateAllWorkflow(path, [], lambda: [issue()]).run(automatic=True)
    assert UpdateAllWorkflow(path, [], lambda: [issue()]).run(automatic=True) is None
    assert UpdateAllWorkflow(path, [], lambda: [issue(1)]).run(automatic=True)


SECRET = 'https://private.test/path?token=TOP_SECRET password=TOP_SECRET'


@pytest.mark.parametrize('status, category, action', [
    (401, 'auth', 'обновите подключение'),
    (403, 'auth', 'проверьте права'),
    (400, 'http', 'проверьте данные'),
    (429, 'http', 'повторите проверку позже'),
    (503, 'http', 'повторите проверку позже'),
])
@pytest.mark.parametrize('shape', ['resp', 'response', 'status_code', 'status'])
def test_safe_http_diagnostics(tmp_path, status, category, action, shape):
    error = RuntimeError(SECRET)
    setattr(error, shape, {'resp': SimpleNamespace(status=status),
                           'response': SimpleNamespace(status_code=status)}.get(shape, status))
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [('sync', Mock(side_effect=error))], Mock())
    reply = workflow.run()
    state = workflow.load()
    diagnostic = state['issues'][0]['diagnostic']
    assert diagnostic['category'] == category
    assert diagnostic['status'] == status
    assert action in reply['text']
    details = workflow.callback(f'ua:{state["run_id"]}:page:0')
    assert f'HTTP {status}' in details['text']
    assert 'services/update_all_workflow.py:' in details['text']
    assert 'часть изменений могла сохраниться' in details['text']
    for text in (str(reply), str(details), workflow.path.read_text()):
        assert 'TOP_SECRET' not in text
        assert 'private.test' not in text
        assert str(tmp_path) not in text
    workflow.verify.assert_not_called()


@pytest.mark.parametrize('error, category, action', [
    (ssl.SSLError(SECRET), 'network', 'проверьте сеть'),
    (TimeoutError(SECRET), 'network', 'проверьте сеть'),
    (requests.ConnectionError(SECRET), 'network', 'проверьте сеть'),
    (requests.Timeout(SECRET), 'network', 'проверьте сеть'),
    (ValueError(SECRET), 'validation', 'проверьте входные данные'),
    (TypeError(SECRET), 'program', 'передайте диагностику'),
    (KeyError(SECRET), 'program', 'передайте диагностику'),
    (RuntimeError(SECRET), 'unknown', 'передайте диагностику'),
    (type('RefreshError', (Exception,), {})(SECRET), 'auth', 'обновите подключение'),
    (type('TOP_SECRET', (Exception,), {})(SECRET), 'unknown', 'передайте диагностику'),
])
def test_safe_error_categories_and_automatic_silence(tmp_path, error, category, action):
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [('sync', Mock(side_effect=error))], lambda: [])
    assert action in workflow.run(automatic=True)['text']
    state = workflow.load()
    assert state['issues'][0]['diagnostic']['category'] == category
    details = workflow.callback(f'ua:{state["run_id"]}:one:0')
    assert f'Диагностика: {category}' in details['text']
    assert 'TOP_SECRET' not in str(details) + workflow.path.read_text()
    assert workflow.run(automatic=True) is None


def test_wrapped_failure_uses_root_category_without_messages(tmp_path):
    def fail():
        try:
            raise requests.Timeout(SECRET)
        except requests.Timeout as error:
            raise RuntimeError(SECRET) from error

    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [('sync', fail)], lambda: [])
    assert 'проверьте сеть' in workflow.run()['text']
    assert workflow.load()['issues'][0]['diagnostic']['category'] == 'network'
    assert 'TOP_SECRET' not in workflow.path.read_text()
    assert workflow.run(automatic=True) is None


def test_hostile_exception_metadata_and_cyclic_cause(tmp_path):
    class HostileError(Exception):
        @property
        def response(self):
            raise ValueError(SECRET)

        def __str__(self):
            raise AssertionError('must not stringify untrusted errors')

    error = HostileError()
    error.status = SECRET
    error.__cause__ = error
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [('sync', Mock(side_effect=error))], lambda: [])
    assert 'неизвестная ошибка' in workflow.run()['text']
    assert 'TOP_SECRET' not in workflow.path.read_text()


def test_trusted_blocked_and_legacy_stage_details(tmp_path):
    workflow = UpdateAllWorkflow(tmp_path / 'run.json', [
        ('sync', Mock(side_effect=UpdateAllBlocked('Сначала выберите календарь'))),
    ], lambda: [])
    assert 'Сначала выберите календарь' in workflow.run()['text']
    state = workflow.load()
    del state['issues'][0]['diagnostic']
    assert 'Сначала выберите календарь' in workflow.render(state, page=0)['text']
