"""Explicit per-user source replacement decisions; no Calendar mutations."""
from datetime import timedelta
from hashlib import sha256
import json
import logging
import requests
from uuid import uuid4

from models import PersonalAttendanceRule, PersonalEventState
from services.attendance_preferences import AttendancePreferenceStore, course_name
from services.attendance_service import AttendanceRuleService
from services.onboarding_service import OnboardingStep
from services.published_plan_updates import PlanConflict

_logger = logging.getLogger(__name__)
_SAFE_EXCEPTION_CLASSES = frozenset({
    'AttributeError', 'KeyError', 'OSError', 'RuntimeError', 'TimeoutError',
    'TypeError', 'ValueError', 'UniversityScheduleFetchError',
})


def _safe_exception_class(error):
    name = type(error).__name__
    return name if name in _SAFE_EXCEPTION_CLASSES else 'unexpected'


def _safe_failure_cause(error):
    cause = error.__cause__
    if cause is None and isinstance(error, requests.RequestException):
        cause = error
    if cause is None:
        return 'none'
    if isinstance(cause, requests.Timeout):
        return 'timeout'
    if isinstance(cause, requests.ConnectionError):
        return 'connection'
    if isinstance(cause, requests.HTTPError):
        response = getattr(cause, 'response', None)
        status = getattr(response, 'status_code', None)
        if type(status) is int and 100 <= status <= 599:
            return f'http_{status}'
        return 'unexpected'
    if isinstance(cause, (ValueError, TypeError)):
        return 'parse'
    return 'unexpected'


class TelegramReplacementReview:
    def __init__(self, app):
        self.app = app

    def _inputs(self):
        app = self.app
        operations = list(app.operations.load_all().values())
        if len(operations) > 1:
            raise PlanConflict('Сохранено несколько операций плана. Выбор замен остановлен; нужен оператор.')
        if any(operation.projection_pending or operation.pending_plan_update
               or operation.pending_calendar_writes for operation in operations):
            raise PlanConflict('Есть незавершённая запись плана. Выбор замен остановлен; нужен оператор.')
        if any(row.get('pending') for row in app.classes.projection_state.rows.values()):
            raise PlanConflict('Есть незавершённая запись занятий. Выбор замен остановлен; нужен оператор.')
        result = app._inputs(allow_source_review=True, allow_replacement_review=True)
        _, now, events, signature, _ = result
        protected = set()
        ever_published = set()
        for operation in operations:
            plan = operation.published_plan
            ever_published.update(plan.get('rows', {}))
            ever_published.update(plan.get('source_resolutions', []))
            ever_published.update(plan.get('manual_resolutions', {}))
            ever_published.update(plan.get('recovery_history', {}))
            protected.update(app.updates.frozen_rows(operation.published_plan, now))
            protected.update(operation.published_plan.get('recovery_history', {}))
        return now, events, signature, protected, ever_published

    def _decision_guard(self, event_id, candidate_uids, subject, now):
        """Fingerprint source and state that can change this local decision."""
        app = self.app
        state = app.snapshot._load()
        source_rows = sorted((app.snapshot._source_to_dict(event)
                              for event in state['source_events']), key=lambda row: row['uid'])
        personal_by_id = {event.id: event for event in state['personal_events']}
        target = personal_by_id.get(event_id)
        target_row = None
        if target is not None:
            target_candidates = target.metadata.get('replacement_candidates')
            if isinstance(target_candidates, list):
                target_candidates = sorted(
                    target_candidates,
                    key=lambda row: (str(row.get('uid', '')) if isinstance(row, dict) else '',
                                     json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)),
                )
            target_row = {
                'id': target.id,
                'uid': target.university_event_uid,
                'title': target.title,
                'start': target.start_time.isoformat(),
                'end': target.end_time.isoformat(),
                'location': target.location,
                'state': target.state.value if target.state else None,
                'change_reason': target.metadata.get('change_reason'),
                'replacement_candidates': target_candidates,
                'replacement_candidate': target.metadata.get('replacement_candidate'),
                'replacement_rejected_by_user': target.metadata.get('replacement_rejected_by_user'),
            }
        candidate_uids = set(candidate_uids)
        claimed = sorted({event.university_event_uid for event in state['personal_events']
                          if event.id != event_id and event.university_event_uid in candidate_uids})
        candidate_rows = {row['uid']: row for row in source_rows if row['uid'] in candidate_uids}
        subjects = {subject}
        subjects.update(course_name(row['summary']) for row in candidate_rows.values())
        preferences = AttendancePreferenceStore.load(
            app.directory / 'attendance_preferences.json',
        ).as_rule_metadata()
        target_protected = False
        target_ever_published = False
        for operation in app.operations.load_all().values():
            protected = set(app.updates.frozen_rows(operation.published_plan, now))
            protected.update(operation.published_plan.get('recovery_history', {}))
            target_protected = target_protected or event_id in protected
            plan = operation.published_plan
            ever_published = (set(plan.get('rows', {}))
                              | set(plan.get('source_resolutions', []))
                              | set(plan.get('manual_resolutions', {}))
                              | set(plan.get('recovery_history', {})))
            target_ever_published = target_ever_published or event_id in ever_published
        product = app.source.state_store.load(app.account.id)
        source = product.sources[product.active_source_id]
        pending_operations = sorted(
            operation.id for operation in app.operations.load_all().values()
            if operation.projection_pending or operation.pending_plan_update
            or operation.pending_calendar_writes
        )
        components = {
            'source': {
                'source': {'kind': source.kind, 'location': source.location},
                'events': source_rows,
                'candidates': candidate_rows,
            },
            'target': target_row,
            'attendance': {name: preferences.get(name) for name in sorted(subjects)},
            'claimed': claimed,
            'protected': target_protected,
            'ever_published': target_ever_published,
            'pending': {
                'operations': pending_operations,
                'class_writes': sorted(
                    key for key, row in app.classes.projection_state.rows.items()
                    if row.get('pending')
                ),
            },
        }
        return {
            name: sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()
            for name, value in components.items()
        }

    def review(self):
        app = self.app
        app.pending = None
        if not app.updates or app.onboarding.store.load().step not in {
            OnboardingStep.CALENDAR, OnboardingStep.COMPLETE,
        }:
            return app.reply('Сначала заверши настройку через /start.')
        try:
            now, events, _signature, protected, ever_published = self._inputs()
        except PlanConflict:
            raise
        except Exception as error:
            exception_class = _safe_exception_class(error)
            cause_code = _safe_failure_cause(error)
            _logger.warning('replacement_source_validation_failed stage=review_input exception_class=%s cause=%s',
                            exception_class, cause_code)
            return app.reply('Не удалось проверить расписание для разбора. Повтори /review_replacements; '
                             f'код: replacement_review_input, ошибка: {exception_class}, причина: {cause_code}. '
                             'Google Calendar не изменён.')
        source = {row.uid: row for row in app.snapshot._load()['source_events']}
        blocked = sorted((event for event in events if (
            event.state == PersonalEventState.NEEDS_REVIEW
            or event.state in {PersonalEventState.POSSIBLY_CANCELLED, PersonalEventState.CANCELLED}
            and event.metadata.get('change_reason') == 'missing_from_university_schedule'
            and event.university_event_uid not in source
            and event.id not in ever_published
        )),
                         key=lambda event: (event.start_time, event.id))
        if not blocked:
            return app.reply('Неоднозначных замен нет; неопубликованных исчезнувших занятий нет. '
                             'Открой /weekly_preview. Google Calendar не изменён.')
        event = blocked[0]
        if event.id in protected:
            return app.reply('Начавшееся опубликованное занятие требует оператора. История сохранена.')
        candidates = event.metadata.get('replacement_candidates')
        if not isinstance(candidates, list):
            candidate = event.metadata.get('replacement_candidate')
            candidates = [candidate] if candidate else []
        claimed = {row.university_event_uid for row in events if row.id != event.id}
        saved_candidates = candidates
        candidates = [source[item['uid']] for item in saved_candidates
                      if isinstance(item, dict) and item.get('uid') in source
                      and not source[item['uid']].is_cancelled and item['uid'] not in claimed]
        if len(candidates) > 8:
            return app.reply('Нет однозначного списка доступных вариантов. Нужен оператор; Calendar не изменён.')
        token = uuid4().hex
        app.pending = (token, now + timedelta(minutes=10), 'replacement-review', {
            'event_id': event.id,
            'candidates': [candidate.uid for candidate in candidates],
            'can_reject': event.university_event_uid not in source and (
                event.state == PersonalEventState.NEEDS_REVIEW or event.id not in ever_published
            ),
            'subject': course_name(event.title),
            'guard': self._decision_guard(event.id, [candidate.uid for candidate in candidates],
                                          course_name(event.title), now),
        })
        title = ' '.join(event.title.split())[:150]
        lines = [f'Выбор замены. Осталось занятий: {len(blocked)}.',
                 f'Было: {event.start_time:%d.%m.%Y %H:%M}–{event.end_time:%H:%M} — {title}',
                 'Выбери, какое занятие заменяет его:']
        buttons = []
        if not candidates:
            lines.append('Прежние варианты больше недоступны:')
            for item in saved_candidates[:8]:
                if not isinstance(item, dict):
                    continue
                uid = item.get('uid')
                reason = ('нет в актуальном расписании' if uid not in source else
                          'уже соответствует другому занятию' if uid in claimed else 'отменён')
                label = ' '.join(str(item.get('title', 'Занятие')).split())[:150]
                lines.append(f'• {label} — {reason}')
        for index, candidate in enumerate(candidates):
            label = ' '.join(candidate.summary.split())[:150]
            room = ' '.join(candidate.location.split())[:80] if candidate.location else 'не указана'
            lines.append(f'{index + 1}. {candidate.dtstart:%d.%m.%Y %H:%M}–{candidate.dtend:%H:%M} '
                         f'— {label}; аудитория: {room}')
            buttons.append([{'text': f'{index + 1}. {label[:45]}',
                             'callback_data': f'replacement:{token}:{index}'}])
        if event.university_event_uid not in source and (
                event.state == PersonalEventState.NEEDS_REVIEW or event.id not in ever_published):
            buttons.append([{'text': 'Без замены — не включать старое занятие',
                             'callback_data': f'replacement:{token}:none'}])
            lines.append('«Без замены» исключает исчезнувшее старое занятие из нового плана. '
                         'Для уже опубликованного события удаление потребует отдельного /review_missing.')
        buttons.append([{'text': 'Не посещаю этот предмет целиком',
                         'callback_data': f'replacement:{token}:exclude'}])
        lines.append('Выбор меняет только сопоставление расписания. Посещаемость сохраняется; '
                     'новый предмет без настройки не включается автоматически. '
                     'Google Calendar не изменён. «Отмена» оставит конфликт нерешённым.')
        buttons.append([{'text': 'Отмена', 'callback_data': 'pilot:cancel'}])
        return app.reply('\n'.join(lines), buttons)

    def handle_callback(self, data):
        app = self.app
        if data == 'replacement:next':
            try:
                return self.review()
            except PlanConflict as error:
                return app.reply(str(error))
            except Exception as error:
                exception_class = _safe_exception_class(error)
                _logger.warning('replacement_source_validation_failed stage=next_review exception_class=%s',
                                exception_class)
                return app.reply('Не удалось проверить замены (код: replacement_next_review, '
                                 f'ошибка: {exception_class}). Повтори /review_replacements; Calendar не изменён.')
        pending = app.pending
        try:
            _, token, index = data.split(':')
            if not isinstance(pending, tuple) or len(pending) != 4:
                reason = 'pending_missing' if pending is None else 'pending_shape'
                _logger.warning('replacement_form_stale reason=%s', reason)
                return app.reply(f'Форма выбора устарела (код: {reason}). Открой /review_replacements заново; '
                                 'Google Calendar не изменён.')
            if pending[2] != 'replacement-review':
                _logger.warning('replacement_form_stale reason=flow_mismatch')
                return app.reply('Форма выбора устарела (код: flow_mismatch). Открой /review_replacements заново; '
                                 'Google Calendar не изменён.')
            if pending[0] != token:
                _logger.warning('replacement_form_stale reason=token_mismatch')
                return app.reply('Форма выбора устарела (код: token_mismatch). Открой /review_replacements заново; '
                                 'Google Calendar не изменён.')
            reject = index == 'none' and pending[3].get('can_reject') is True
            exclude = index == 'exclude' and bool(pending[3].get('subject'))
            if not reject and not exclude and (not index.isdigit() or int(index) >= len(pending[3]['candidates'])):
                _logger.warning('replacement_form_stale reason=choice_invalid')
                return app.reply('Форма выбора устарела (код: choice_invalid). Открой /review_replacements заново; '
                                 'Google Calendar не изменён.')
            if app.now() >= pending[1]:
                app.pending = None
                _logger.warning('replacement_form_stale reason=expired')
                return app.reply('Форма выбора устарела (код: expired). Открой /review_replacements заново; '
                                 'Google Calendar не изменён.')
            # Consume only the matching, unexpired form. Out-of-order callbacks
            # from older Telegram messages must leave a newer form usable.
            app.pending = None
            operations = list(app.operations.load_all().values())
            if len(operations) > 1:
                return app.reply('Сохранено несколько операций плана. Выбор замен остановлен; нужен оператор.')
            if any(operation.projection_pending or operation.pending_plan_update
                   or operation.pending_calendar_writes for operation in operations):
                return app.reply('Есть незавершённая запись плана. Выбор замен остановлен; нужен оператор.')
            if any(row.get('pending') for row in app.classes.projection_state.rows.values()):
                return app.reply('Есть незавершённая запись занятий. Выбор замен остановлен; нужен оператор.')
            try:
                _, now, _, _, _ = app._source_only_inputs(
                    allow_source_review=True, allow_replacement_review=True,
                )
            except PlanConflict as error:
                return app.reply(str(error))
            except Exception as error:
                exception_class = _safe_exception_class(error)
                cause_code = _safe_failure_cause(error)
                _logger.warning('replacement_source_validation_failed stage=callback_source '
                                'exception_class=%s cause=%s', exception_class, cause_code)
                return app.reply('Не удалось проверить актуальное расписание для выбора '
                                 f'(код: replacement_callback_source, ошибка: {exception_class}, '
                                 f'причина: {cause_code}). '
                                 'Решение не применено; открой /review_replacements заново. '
                                 'Google Calendar не изменён.')
            protected = set()
            for operation in operations:
                plan = operation.published_plan
                protected.update(app.updates.frozen_rows(operation.published_plan, now))
                protected.update(operation.published_plan.get('recovery_history', {}))
            payload = pending[3]
            current_guard = self._decision_guard(payload['event_id'], payload['candidates'],
                                                 payload['subject'], now)
            changed = [name for name, digest in payload['guard'].items()
                       if current_guard.get(name) != digest]
            if changed or payload['event_id'] in protected:
                if payload['event_id'] in protected and 'protected' not in changed:
                    changed.append('protected')
                return app.reply('Расписание, посещаемость или доступность решения изменились после формы '
                                 f'(проверка: {", ".join(changed)}). Открой /review_replacements заново; '
                                 'Google Calendar не изменён.')
            if exclude:
                prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
                prefs.exclude_subject(payload['subject'])
                prefs.save()
                return app.reply('Не посещаю предмет: ' + payload['subject'] + '. '
                                 'Он исключён из новых планов. Google Calendar и история не изменены.', [[
                                     {'text': 'Следующая замена', 'callback_data': 'replacement:next'},
                                 ]])
            if reject:
                app.snapshot.reject_replacement(payload['event_id'])
                return app.reply('Выбрано «Без замены». Старое занятие сохранено в истории. '
                                 'Google Calendar не изменён. Для опубликованного события нужен /review_missing.', [[
                                     {'text': 'Следующая замена', 'callback_data': 'replacement:next'},
                                 ]])
            prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
            service = AttendanceRuleService(PersonalAttendanceRule(
                id='replacement-attendance', description='Saved attendance choices',
                metadata={'attendance_preferences': prefs.as_rule_metadata()},
            ))
            accepted = app.snapshot.accept_replacement(payload['event_id'], payload['candidates'][int(index)], service)
            return app.reply('Замена выбрана: ' + ' '.join(accepted.title.split())[:150] +
                             '. Google Calendar не изменён. Продолжи разбор, затем открой /weekly_preview.', [[
                                 {'text': 'Следующая замена', 'callback_data': 'replacement:next'},
                             ]])
        except PlanConflict as error:
            return app.reply(str(error))
        except Exception:
            return app.reply('Выбор устарел или не подтверждён. Открой /review_replacements заново. '
                             'Google Calendar не изменён.')
