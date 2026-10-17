"""Explicit per-user source replacement decisions; no Calendar mutations."""
from datetime import timedelta
from uuid import uuid4

from models import PersonalAttendanceRule, PersonalEventState
from services.attendance_preferences import AttendancePreferenceStore
from services.attendance_service import AttendanceRuleService
from services.onboarding_service import OnboardingStep
from services.published_plan_updates import PlanConflict


class TelegramReplacementReview:
    def __init__(self, app):
        self.app = app

    def _inputs(self):
        app = self.app
        operations = list(app.operations.load_all().values())
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

    def review(self):
        app = self.app
        app.pending = None
        if not app.updates or app.onboarding.store.load().step not in {
            OnboardingStep.CALENDAR, OnboardingStep.COMPLETE,
        }:
            return app.reply('Сначала заверши настройку через /start.')
        now, events, signature, protected = self._inputs()
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
        candidates = [source[item['uid']] for item in candidates
                      if isinstance(item, dict) and item.get('uid') in source
                      and not source[item['uid']].is_cancelled and item['uid'] not in claimed]
        if not candidates or len(candidates) > 8:
            return app.reply('Нет однозначного списка доступных вариантов. Нужен оператор; Calendar не изменён.')
        token = uuid4().hex
        app.pending = (token, now + timedelta(minutes=10), 'replacement-review', {
            'event_id': event.id, 'signature': signature,
            'candidates': [candidate.uid for candidate in candidates],
        })
        title = ' '.join(event.title.split())[:150]
        lines = [f'Выбор замены. Осталось занятий: {len(blocked)}.',
                 f'Было: {event.start_time:%d.%m.%Y %H:%M}–{event.end_time:%H:%M} — {title}',
                 'Выбери, какое занятие заменяет его:']
        buttons = []
        for index, candidate in enumerate(candidates):
            label = ' '.join(candidate.summary.split())[:150]
            room = ' '.join(candidate.location.split())[:80] if candidate.location else 'не указана'
            lines.append(f'{index + 1}. {candidate.dtstart:%d.%m.%Y %H:%M}–{candidate.dtend:%H:%M} '
                         f'— {label}; аудитория: {room}')
            buttons.append([{'text': f'{index + 1}. {label[:45]}',
                             'callback_data': f'replacement:{token}:{index}'}])
        lines.append('Выбор меняет только сопоставление расписания. Посещаемость сохраняется; '
                     'новый предмет без настройки не включается автоматически. '
                     'Google Calendar не изменён. Если подходящего варианта нет, нажми «Отмена».')
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
                raise ValueError('Stale decision')
            if not index.isdigit() or int(index) >= len(pending[3]['candidates']):
                raise ValueError('Invalid candidate')
            now, events, signature, protected = self._inputs()
            payload = pending[3]
            if signature != payload['signature'] or payload['event_id'] in protected:
                raise ValueError('Source changed')
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
