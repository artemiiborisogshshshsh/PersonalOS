"""Bounded, read-only plan views and explicit local preference drafts."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from uuid import uuid4
from models import PersonalEventState


def button(text, data):
    return {'text': text, 'callback_data': data}


def recovery_buttons():
    return [
        [button('Длительность подготовки', 'planpref:open:duration')],
        [button('Окно и лимиты', 'planpref:open:limits')],
        [button('Посещаемость', 'main:attendance')],
        [button('Пересчитать план', 'main:plan')],
        [button('← Главное меню', 'main:open')],
    ]


def conflict_lines(operation, events):
    by_id = {event.id: event for event in events}
    result = []
    for uid, reason in operation.no_slot_reasons.items():
        event = by_id.get(uid)
        name = f'{event.title} · {event.start_time:%d.%m %H:%M}' if event else 'Занятие из расписания'
        result.append(f'• {name}: {reason}.')
    result.extend(f'• {block.title}: конфликт времени; нужен свободный слот.'
                  for block in operation.blocks if block.manual_conflict)
    return result


EXPIRED_WINDOW_REASON = 'допустимое окно подготовки уже прошло'


def validated_omission_ids(operation, events, settings, now):
    """Match durable consent to exactly one source interval and expired policy window."""
    by_id = {event.id: event for event in events}
    valid = set()
    for uid, row in operation.omitted_preparations.items():
        event = by_id.get(uid)
        if not isinstance(row, dict) or event is None:
            continue
        try:
            acknowledged = datetime.fromisoformat(row['acknowledged_at'])
            if (row.get('policy') == 'expired_preparation_window_v1'
                    and row.get('reason') == EXPIRED_WINDOW_REASON
                    and row.get('start') == event.start_time.isoformat()
                    and row.get('end') == event.end_time.isoformat()
                    and row.get('title') == event.title
                    and event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
                    and settings.profile.preparation_window(event.start_time)[1] <= acknowledged
                    and acknowledged <= now and acknowledged < event.start_time):
                valid.add(uid)
        except (KeyError, TypeError, ValueError):
            continue
    return valid


def unresolved_preparations(operation, events, settings, now):
    valid = validated_omission_ids(operation, events, settings, now)
    return {uid: reason for uid, reason in operation.no_slot_reasons.items()
            if uid not in valid or reason != EXPIRED_WINDOW_REASON}


def pages(lines, byte_limit=2900):
    result, current = [], []
    for line in lines:
        # Bound individual source titles as well as the complete Telegram card.
        line = line[:400]
        if current and len(('\n'.join([*current, line])).encode('utf-8')) > byte_limit:
            result.append(current)
            current = []
        current.append(line)
    return [*result, current] if current else result


class TelegramPlanPreview:
    def __init__(self, app):
        self.app = app
        self.view = None
        self.draft = None
        self.omission_proposal = None

    def clear(self):
        self.view = self.draft = self.omission_proposal = None

    def render(self, operation, selected, *, status='', approval=None, diagnostic_events=None, omission_signature=None):
        events = diagnostic_events or selected
        unresolved = unresolved_preparations(operation, events, self.app.profile.load(), self.app.now())
        manual = any(block.manual_conflict for block in operation.blocks)
        issues = conflict_lines(operation, events)
        acknowledged = len(operation.no_slot_reasons) - len(unresolved)
        self.omission_proposal = None
        by_id = {event.id: event for event in selected}
        eligible = (omission_signature is not None and unresolved and not manual
                    and not operation.omitted_preparations
                    and all(reason == EXPIRED_WINDOW_REASON and uid in by_id
                            and by_id[uid].start_time > self.app.now()
                            and self.app.profile.load().profile.preparation_window(by_id[uid].start_time)[1] <= self.app.now()
                            for uid, reason in unresolved.items())
                    and not self.app.operations.load_all())
        dated = {}
        for event in selected:
            dated.setdefault(event.start_time.date(), []).append(
                f'• {event.start_time:%H:%M}–{event.end_time:%H:%M} — {event.title}')
        for block in operation.blocks:
            dated.setdefault(block.start.date(), []).append(
                f'• {block.start:%H:%M}–{block.end:%H:%M} — {block.title}')
        detail = []
        for day, rows in sorted(dated.items()):
            detail.extend([f'{day:%d.%m.%Y}', *sorted(rows)])
        issue_pages = pages(issues)
        detail_pages = pages(detail)
        self.view = dict(token=uuid4().hex[:16], expires=self.app.now() + timedelta(minutes=10),
                         details=detail_pages, issues=issue_pages, approval=approval,
                         classes=len(selected), preparations=len(operation.blocks), status=status,
                         publishable=bool(approval) and not unresolved and not manual)
        if eligible:
            self.omission_proposal = dict(token=self.view['token'], expires=self.view['expires'],
                operation=operation, selected_ids=frozenset(by_id), signature=omission_signature)
        summary = (f'Предпросмотр плана\nПары: {len(selected)} · Подготовки: {len(operation.blocks)}'
                   f'\nНе размещено или конфликтует: {len(issues)}.\nCalendar пока не изменён.')
        if selected:
            summary += '\nПредметы: ' + ', '.join(dict.fromkeys(event.title[:70] for event in selected[:3]))
        if acknowledged:
            summary += f'\nС твоего согласия пропущено подготовок: {acknowledged}. Пары включены в план.'
        if unresolved or manual:
            summary += '\n\n' + '\n'.join(line[:250] for line in issues[:3])
            summary += ('\n\nПубликация заблокирована. Можно сократить длительность, расширить окно подготовки '
                        'или проверить посещаемость. Прошедшее окно нельзя вернуть изменением лимита.')
        elif status:
            summary += '\n' + status
        else:
            summary += '\nЗаписать эти пары и подготовки в отдельный календарь Google?'
        self.view['summary'] = summary
        return self._card('summary', 0)

    def _card(self, kind, index):
        view = self.view
        token = view['token']
        if kind == 'summary':
            text = view['summary']
        else:
            collection = view[kind]
            if not 0 <= index < len(collection):
                return self.app.reply('Страница недоступна. Открой новый предпросмотр.', recovery_buttons())
            text = ('Причины' if kind == 'issues' else 'План по датам') + f' · {index + 1}/{len(collection)}\n'
            text += '\n'.join(collection[index]) + '\nCalendar пока не изменён.'
        buttons = []
        if kind != 'summary':
            nav = []
            if index:
                nav.append(button('← Назад', f'planui:{token}:{kind}:{index - 1}'))
            if index + 1 < len(view[kind]):
                nav.append(button('Далее →', f'planui:{token}:{kind}:{index + 1}'))
            if nav:
                buttons.append(nav)
            buttons.append([button('К сводке', f'planui:{token}:summary:0')])
        else:
            if view['details']:
                buttons.append([button('План по датам', f'planui:{token}:details:0')])
            if view['issues']:
                buttons.append([button('Все причины', f'planui:{token}:issues:0')])
        if self.omission_proposal:
            buttons.append([button('Пропустить подготовки с прошедшим сроком',
                                   f"planomit:{self.omission_proposal['token']}:review")])
        if view['publishable']:
            buttons.insert(0, [button('Подтвердить запись', 'pilot:apply:' + view['approval']),
                            button('Отмена', 'pilot:cancel')])
        buttons += recovery_buttons()
        return dict(self.app.reply(text, buttons), edit_message=kind != 'summary')

    def callback(self, data):
        if data.startswith('planomit:'):
            return self._omissions(data)
        if data.startswith('planpref:'):
            return self._preferences(data)
        parts = data.split(':')
        if (len(parts) != 4 or not self.view or parts[1] != self.view['token']
                or self.app.now() >= self.view['expires'] or parts[2] not in {'summary', 'details', 'issues'}
                or not parts[3].isdigit()):
            return self.app.reply('Предпросмотр устарел. Пересчитай план.', recovery_buttons())
        return self._card(parts[2], int(parts[3]))

    def _omissions(self, data):
        proposal = self.omission_proposal
        parts = data.split(':')
        app = self.app
        if (len(parts) != 3 or not proposal or parts[1] != proposal['token']
                or app.now() >= proposal['expires'] or parts[2] not in {'review', 'ack'}):
            return app.reply('Согласие устарело. Пересчитай план.', recovery_buttons())
        if parts[2] == 'review':
            proposal['reviewed'] = True
            return app.reply(
                f"Не будут созданы подготовки с прошедшим сроком: {len(proposal['operation'].no_slot_reasons)}.\n"
                + '\n'.join(line[:200] for line in conflict_lines(proposal['operation'],
                    self.app.snapshot.personal_events())[:3]) + '\n'
                'Полный список — в «Все причины». '
                'Все выбранные пары остаются в плане. Подготовки не считаются выполненными.\n'
                'Согласен пропустить эти подготовки? После согласия появится отдельное подтверждение записи.', [
                    [button('Да, пропустить эти подготовки', f'planomit:{proposal["token"]}:ack')],
                    [button('Все причины', f'planui:{proposal["token"]}:issues:0')],
                    [button('Отмена', 'main:plan')]])
        if not proposal.get('reviewed'):
            return app.reply('Сначала проверь список пропускаемых подготовок.', [[
                button('Проверить пропуск', f'planomit:{proposal["token"]}:review')]])
        self.omission_proposal = None  # At most one acknowledgement attempt.
        settings, now, events, signature, _ = app._inputs()
        if signature != proposal['signature'] or app.operations.load_all():
            return app.reply('Данные изменились. Пересчитай план; согласие не сохранено.', recovery_buttons())
        selected = [event for event in events if event.id in proposal['selected_ids']]
        operation = proposal['operation']
        by_id = {event.id: event for event in selected}
        if (len(selected) != len(proposal['selected_ids']) or any(event.start_time <= now for event in selected)
                or any(block.start <= now or block.manual_conflict for block in operation.blocks)):
            return app.reply('Время плана изменилось. Пересчитай план.', recovery_buttons())
        for uid, reason in operation.no_slot_reasons.items():
            event = by_id.get(uid)
            if (reason != EXPIRED_WINDOW_REASON or event is None
                    or event.state not in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
                    or settings.profile.preparation_window(event.start_time)[1] > now):
                return app.reply('Пропуск этих подготовок недоступен. Пересчитай план.', recovery_buttons())
        operation.omitted_preparations = {uid: dict(reason=reason, policy='expired_preparation_window_v1',
            start=by_id[uid].start_time.isoformat(), end=by_id[uid].end_time.isoformat(),
            title=by_id[uid].title, acknowledged_at=now.isoformat())
            for uid, reason in operation.no_slot_reasons.items()}
        token = uuid4().hex
        app.pending = (token, now + timedelta(minutes=10), operation, signature, proposal['selected_ids'])
        return self.render(operation, selected, approval=token, diagnostic_events=events)

    def _preferences(self, data):
        app = self.app
        app.pending = None
        if any(operation.projection_pending or operation.pending_plan_update
               for operation in app.operations.load_all().values()):
            return app.reply('Сначала проверь незавершённую публикацию. Настройки сохранены без изменений.', recovery_buttons())
        parts = data.split(':')
        if len(parts) == 3 and parts[1] == 'open' and parts[2] in {'duration', 'limits'}:
            current = app.profile.load().profile
            fields = (('lecture_minutes', 'ЛК'), ('practical_minutes', 'ПР'), ('lab_minutes', 'ЛБ')) if parts[2] == 'duration' else (
                ('preparation_window_days', 'Окно, дней'), ('max_single_block_minutes', 'Блок, мин'),
                ('max_continuous_deep_work_minutes', 'Непрерывная работа, мин'))
            self.draft = dict(token=uuid4().hex[:12], expires=app.now() + timedelta(minutes=10),
                              original=asdict(current), values={}, fields=fields)
        draft = self.draft
        if not draft or app.now() >= draft['expires']:
            return app.reply('Настройка устарела. Открой её заново.', recovery_buttons())
        token = draft['token']
        if len(parts) >= 3 and parts[1] != 'open':
            if parts[1] != token:
                return app.reply('Эта настройка устарела.', recovery_buttons())
            if parts[2] == 'save' and len(parts) == 3:
                settings = app.profile.load()
                if asdict(settings.profile) != draft['original']:
                    self.draft = None
                    return app.reply('Настройки уже изменились. Открой их заново.', recovery_buttons())
                settings.profile = replace(settings.profile, **draft['values'])
                app.profile.save(settings)
                self.clear()
                return app.reply('Настройки сохранены. Calendar не изменён. Пересчитай план и проверь его перед публикацией.', recovery_buttons())
            if parts[2] == 'field' and len(parts) == 4 and parts[3].isdigit():
                index = int(parts[3])
                if index >= len(draft['fields']):
                    return app.reply('Настройка недоступна.', recovery_buttons())
                field, label = draft['fields'][index]
                options = [3, 7, 10, 14] if field == 'preparation_window_days' else [30, 45, 60, 90, 120, 180]
                return app.reply(label + ': выбери значение. Оно сохранится после нажатия «Сохранить».', [
                    [button(str(value), f'planpref:{token}:set:{index}:{value}') for value in options[:3]],
                    [button(str(value), f'planpref:{token}:set:{index}:{value}') for value in options[3:]],
                    [button('Отмена', 'main:settings')]])
            if parts[2] == 'set' and len(parts) == 5 and parts[3].isdigit() and parts[4].isdigit():
                index, value = int(parts[3]), int(parts[4])
                if index >= len(draft['fields']):
                    return app.reply('Настройка недоступна.', recovery_buttons())
                field = draft['fields'][index][0]
                allowed = [3, 7, 10, 14] if field == 'preparation_window_days' else [30, 45, 60, 90, 120, 180]
                if value not in allowed:
                    return app.reply('Недопустимое значение.', recovery_buttons())
                draft['values'][field] = value
        rows = []
        labels = []
        for index, (field, label) in enumerate(draft['fields']):
            value = draft['values'].get(field, draft['original'][field])
            labels.append(f'{label}: {value}')
            rows.append([button(f'{label}: {value}', f'planpref:{token}:field:{index}')])
        rows += [[button('Сохранить', f'planpref:{token}:save')], [button('Отмена', 'main:settings')]]
        return app.reply('Подготовка к занятиям\n' + '\n'.join(labels) + '\nВыбери параметр. Calendar не изменён.', rows)
