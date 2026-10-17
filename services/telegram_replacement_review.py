"""Explicit per-user source replacement decisions; no Calendar mutations."""
from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid4

from models import PersonalAttendanceRule, PersonalEventState
from services.attendance_preferences import AttendancePreferenceStore, course_name
from services.attendance_service import AttendanceRuleService
from services.onboarding_service import OnboardingStep
from services.published_plan_updates import PlanConflict


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
        for operation in operations:
            protected.update(app.updates.frozen_rows(operation.published_plan, now))
            protected.update(operation.published_plan.get('recovery_history', {}))
        return now, events, signature, protected

    def _decision_guard(self, event_id, candidate_uids, subject, now):
        """Fingerprint only source and state that can change this local decision."""
        app = self.app
        state = app.snapshot._load()
        source_rows = sorted((app.snapshot._source_to_dict(event)
                              for event in state['source_events']), key=lambda row: row['uid'])
        personal_by_id = {event.id: event for event in state['personal_events']}
        target = personal_by_id.get(event_id)
        target_row = None
        if target is not None:
            target_row = {
                'id': target.id,
                'uid': target.university_event_uid,
                'title': target.title,
                'start': target.start_time.isoformat(),
                'end': target.end_time.isoformat(),
                'location': target.location,
                'change_reason': target.metadata.get('change_reason'),
                'replacement_candidates': target.metadata.get('replacement_candidates'),
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
        for operation in app.operations.load_all().values():
            protected = set(app.updates.frozen_rows(operation.published_plan, now))
            protected.update(operation.published_plan.get('recovery_history', {}))
            target_protected = target_protected or event_id in protected
        product = app.source.state_store.load(app.account.id)
        source = product.sources[product.active_source_id]
        guard = {
            'source': {'kind': source.kind, 'location': source.location},
            'source_events': source_rows,
            'target': target_row,
            'candidates': candidate_rows,
            'claimed_candidates': claimed,
            'attendance': {name: preferences.get(name) for name in sorted(subjects)},
            'target_protected': target_protected,
            'pending_class_writes': sorted(
                key for key, row in app.classes.projection_state.rows.items() if row.get('pending')
            ),
        }
        return sha256(json.dumps(guard, sort_keys=True, ensure_ascii=False,
                                default=str).encode()).hexdigest()

    def review(self):
        app = self.app
        app.pending = None
        if not app.updates or app.onboarding.store.load().step not in {
            OnboardingStep.CALENDAR, OnboardingStep.COMPLETE,
        }:
            return app.reply('Сначала заверши настройку через /start.')
        now, events, _signature, protected = self._inputs()
        blocked = sorted((event for event in events if event.state == PersonalEventState.NEEDS_REVIEW),
                         key=lambda event: (event.start_time, event.id))
        if not blocked:
            return app.reply('Неоднозначных замен нет. Открой /weekly_preview. Google Calendar не изменён.')
        event = blocked[0]
        if event.id in protected:
            return app.reply('Начавшееся опубликованное занятие требует оператора. История сохранена.')
        candidates = event.metadata.get('replacement_candidates')
        if not isinstance(candidates, list):
            candidate = event.metadata.get('replacement_candidate')
            candidates = [candidate] if candidate else []
        source = {row.uid: row for row in app.snapshot._load()['source_events']}
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
            'can_reject': event.university_event_uid not in source,
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
        if event.university_event_uid not in source:
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
            except Exception:
                return app.reply('Не удалось проверить замены. Повтори /review_replacements; Calendar не изменён.')
        pending = app.pending
        app.pending = None
        try:
            _, token, index = data.split(':')
            if (not pending or len(pending) != 4 or pending[2] != 'replacement-review'
                    or pending[0] != token or app.now() >= pending[1]):
                return app.reply('Форма выбора устарела. Открой /review_replacements заново; '
                                 'Google Calendar не изменён.')
            reject = index == 'none' and pending[3].get('can_reject') is True
            exclude = index == 'exclude' and bool(pending[3].get('subject'))
            if not reject and not exclude and (not index.isdigit() or int(index) >= len(pending[3]['candidates'])):
                raise ValueError('Invalid candidate')
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
            except Exception:
                return app.reply('Не удалось проверить актуальное расписание для выбора. '
                                 'Решение не применено; открой /review_replacements заново. '
                                 'Google Calendar не изменён.')
            protected = set()
            for operation in operations:
                protected.update(app.updates.frozen_rows(operation.published_plan, now))
                protected.update(operation.published_plan.get('recovery_history', {}))
            payload = pending[3]
            current_guard = self._decision_guard(payload['event_id'], payload['candidates'],
                                                 payload['subject'], now)
            if current_guard != payload['guard'] or payload['event_id'] in protected:
                return app.reply('Расписание, посещаемость или доступность решения изменились после формы. '
                                 'Открой /review_replacements заново; Google Calendar не изменён.')
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
