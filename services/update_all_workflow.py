"""One explicit update sequence and a durable, deterministic issue report."""

import json
import os
import uuid
import ssl
import fcntl
from pathlib import Path
from tempfile import NamedTemporaryFile
import requests


def _diagnostic_attribute(value, name):
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _failure_diagnostic(error):
    """Keep only fixed categories, numeric HTTP status and app source locations."""
    root = Path(__file__).resolve().parent.parent
    diagnostic = {'category': 'unknown', 'location': 'недоступно'}
    reason = 'неизвестная ошибка; передайте диагностику разработчику'
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        status = None
        for owner, attribute in ((error, 'status_code'), (error, 'status'),
                                 (_diagnostic_attribute(error, 'resp'), 'status'),
                                 (_diagnostic_attribute(error, 'response'), 'status_code')):
            candidate = _diagnostic_attribute(owner, attribute)
            if type(candidate) is int and 100 <= candidate <= 599:
                status = candidate
                break
        if status is not None:
            diagnostic = dict(category='http', status=status, location=diagnostic['location'])
            if status == 401:
                diagnostic['category'] = 'auth'
                reason = 'авторизация отклонена; обновите подключение к сервису'
            elif status == 403:
                diagnostic['category'] = 'auth'
                reason = 'доступ запрещён; проверьте права подключённого аккаунта'
            elif status == 429 or status >= 500:
                reason = 'сервис временно отклонил запрос; повторите проверку позже'
            else:
                reason = 'сервис отклонил запрос; проверьте данные и настройки интеграции'
        elif any(cls.__name__ in {'RefreshError', 'DefaultCredentialsError',
                                 'AuthenticationError', 'Unauthorized'}
                 for cls in type(error).__mro__):
            diagnostic = dict(category='auth', location=diagnostic['location'])
            reason = 'ошибка авторизации; обновите подключение к сервису'
        elif isinstance(error, (ssl.SSLError, ConnectionError, TimeoutError,
                                requests.ConnectionError, requests.Timeout)):
            diagnostic = dict(category='network', location=diagnostic['location'])
            reason = 'соединение с сервисом недоступно; проверьте сеть и повторите проверку'
        elif isinstance(error, ValueError):
            diagnostic = dict(category='validation', location=diagnostic['location'])
            reason = 'данные или настройки не прошли проверку; проверьте входные данные'
        elif isinstance(error, (TypeError, AttributeError, LookupError, ArithmeticError,
                                AssertionError, NameError, NotImplementedError)):
            diagnostic = dict(category='program', location=diagnostic['location'])
            reason = 'внутренняя ошибка программы; передайте диагностику разработчику'
        traceback = error.__traceback__
        while traceback is not None:
            source = Path(traceback.tb_frame.f_code.co_filename)
            # Never expose absolute paths, source text, function names or locals.
            if source.is_absolute():
                try:
                    relative = source.resolve().relative_to(root)
                    if (relative.parts[0] in {'services', 'adapters', 'scripts'}
                            and source.is_file() and source.suffix == '.py'):
                        diagnostic['location'] = f'{relative.as_posix()}:{traceback.tb_lineno}'
                except (OSError, ValueError):
                    pass
            traceback = traceback.tb_next
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return reason, diagnostic


class UpdateAllBlocked(RuntimeError):
    """Only application-authored, credential-free explanations belong here."""


class UpdateAllWorkflow:
    def __init__(self, path, steps, verify):
        self.path = Path(path)
        self.steps = steps
        self.verify = verify

    @property
    def _lock_path(self):
        """A per-report advisory lock shared by bot processes.

        ``flock`` is released by the OS when a process exits, so a crashed bot
        cannot leave a stale "running" flag behind.  The empty lock file itself
        is intentionally retained: unlinking it after release would let a
        waiting process lock a different inode.
        """
        return self.path.with_name(f'.{self.path.name}.lock')

    def _acquire_run_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor

    @staticmethod
    def _release_run_lock(descriptor):
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                    delete=False, prefix='.update-all-') as output:
                temporary = Path(output.name)
                json.dump(state, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def load(self):
        if not self.path.exists():
            return None
        state = json.loads(self.path.read_text(encoding='utf-8'))
        if state.get('version') != 1:
            raise ValueError('Unknown update report version')
        return state

    def run(self, automatic=False):
        descriptor = self._acquire_run_lock()
        if descriptor is None:
            # A scheduled retry must not create noise while an interactive run
            # owns the same projection; a manual command gets an explicit fact.
            return None if automatic else {
                'text': 'Обновление уже выполняется.', 'buttons': []}
        try:
            previous = self.load()
            # Always revalidate sources on a new invocation. Per-event and shared
            # journals resume ambiguous writes; never skip reads based on a phase.
            state = dict(version=1, run_id=uuid.uuid4().hex[:12], phase='starting', issues=[])
            self._save(state)
            for name, action in [*self.steps, ('проверка Calendar', self.verify)]:
                state['phase'] = name
                self._save(state)
                try:
                    result = action()
                    if name == 'проверка Calendar':
                        state['issues'].extend(result)
                except Exception as error:
                    if isinstance(error, UpdateAllBlocked):
                        reason = str(error)
                        diagnostic = {'category': 'blocked', 'location': 'недоступно'}
                    else:
                        reason, diagnostic = _failure_diagnostic(error)
                    state['issues'].append(dict(kind='stage', day='', scope=name,
                        title=name, reason=reason, diagnostic=diagnostic))
                    break
            state['phase'] = 'complete' if not any(i['kind'] == 'stage' for i in state['issues']) else 'incomplete'
            self._save(state)
            if automatic and (not state['issues'] or (previous and previous.get('issues') == state['issues'])):
                return None
            return self.render(state)
        finally:
            self._release_run_lock(descriptor)

    def render(self, state, page=None, single=False):
        issues = state['issues']
        run_id = state['run_id']
        if not issues:
            return {'text': 'Проверка завершена, проблем нет.', 'buttons': []}
        if page is None:
            groups = {}
            for issue in issues:
                key = issue['kind'], issue['day'], issue['scope'], issue['reason']
                groups[key] = groups.get(key, 0) + 1
            lines = []
            for (kind, day, scope, reason), count in list(groups.items())[:3]:
                if kind == 'stage':
                    lines.append(f'Не удалось завершить «{scope}»: {reason}.')
                elif kind == 'infeasible':
                    lines.append(f'План недели невыполним: {reason}.')
                else:
                    label = 'подготовок' if kind == 'preparation' else 'занятий'
                    lines.append(f'{day}: проблемных {label} — {count}. Причина: {reason}.')
            if len(groups) > 3:
                lines.append('Остальные проблемы — в «Подробности».')
            buttons = [[{'text': 'Разобрать', 'callback_data': f'ua:{run_id}:one:0'},
                        {'text': 'Подробности', 'callback_data': f'ua:{run_id}:page:0'}]]
        else:
            size = 1 if single else 5
            pages = (len(issues) + size - 1) // size
            page = max(0, min(page, pages - 1))
            lines = [f'Проблемы: {page + 1}/{pages}.']
            for issue in issues[page * size:(page + 1) * size]:
                lines.append(f'• {issue["day"]} {issue["title"]}: {issue["reason"]}.')
                diagnostic = issue.get('diagnostic')
                if diagnostic:
                    lines.append(f'Диагностика: {diagnostic["category"]}; '
                                 f'место: {diagnostic["location"]}.'
                                 + (f' HTTP {diagnostic["status"]}.' if 'status' in diagnostic else ''))
                    lines.append('Результат этапа не подтверждён; часть изменений могла сохраниться.')
            mode = 'one' if single else 'page'
            buttons = [[]]
            if page:
                buttons[0].append({'text': 'Назад', 'callback_data': f'ua:{run_id}:{mode}:{page-1}'})
            if page + 1 < pages:
                buttons[0].append({'text': 'Далее', 'callback_data': f'ua:{run_id}:{mode}:{page+1}'})
        buttons.append([{'text': 'Повторить проверку', 'callback_data': f'ua:{run_id}:retry:0'}])
        return {'text': '\n'.join(lines), 'buttons': [row for row in buttons if row]}

    def callback(self, data):
        parts = data.split(':')
        state = self.load()
        if len(parts) != 4 or not state or parts[1] != state['run_id']:
            return {'text': 'Этот отчёт устарел. Открой /update_all.', 'buttons': []}
        if parts[2] == 'retry':
            return self.run()
        if parts[2] not in {'one', 'page'} or not parts[3].isdigit():
            return {'text': 'Неизвестное действие отчёта.', 'buttons': []}
        return self.render(state, int(parts[3]), single=parts[2] == 'one')
