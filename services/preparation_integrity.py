"""Read-only comparison of source-derived expectations and calendar facts."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from services.calendar_integrity_service import CalendarIntegrityService


class PreparationState(str, Enum):
    DISABLED = 'disabled'
    COMPLETED = 'completed'
    PLACED = 'placed'
    MISSING = 'missing'
    CONFLICTING = 'conflicting'
    NO_AVAILABLE_SLOT = 'no_available_slot'
    UNCONFIRMED = 'unconfirmed'


def calendar_horizon(now: datetime, timezone: str):
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return start, start + timedelta(weeks=2)


@dataclass(frozen=True)
class PreparationExpectation:
    source_id: str
    scope: str
    title: str
    lesson_start: datetime
    minutes: int
    earliest: datetime
    deadline: datetime
    calendar_id: str
    disabled: bool = False
    completed: bool = False


@dataclass(frozen=True)
class PreparationCheck:
    expectation: PreparationExpectation
    state: PreparationState
    reason: str = ''


@dataclass(frozen=True)
class PreparationInfeasibility:
    """A deterministic, non-destructive capacity outcome for the runtime.

    ``capacity_deficit_minutes`` is deliberately conservative: it is the
    total duration of requirements that the planner proved it could not place
    at all, not a speculative estimate of all free time in a week.
    """
    capacity_deficit_minutes: int
    unplaced_source_ids: tuple[str, ...]
    hard_constraints: tuple[str, ...]
    alternatives: tuple[str, ...]


def assess_preparation_infeasibility(checks):
    """Return INFEASIBLE facts only for genuinely unplaced requirements.

    A visible ``manual_conflict`` proposal is intentionally *not* an
    unplaced requirement: it is reported separately as conflicting and
    remains available for a user-controlled move. Disabled, completed and
    unconfirmed inputs never make the week infeasible.
    """
    unplaced = [check for check in checks
                if check.state == PreparationState.NO_AVAILABLE_SLOT]
    if not unplaced:
        return None
    constraints = tuple(sorted({check.reason for check in unplaced if check.reason}))
    return PreparationInfeasibility(
        capacity_deficit_minutes=sum(check.expectation.minutes for check in unplaced),
        unplaced_source_ids=tuple(check.expectation.source_id for check in unplaced),
        hard_constraints=constraints,
        alternatives=(
            'освободить время до соответствующей пары или изменить обязательство вручную',
            'отключить подготовку только явным правилом, если она больше не нужна',
            'оставить конфликтное предложение и перенести его вручную, если блок существует',
        ),
    )


def validate_preparations(expectations, calendar_events, protected_intervals=(),
                          *, timezone='Asia/Tomsk', verified=True,
                          no_slot_reasons=None):
    """calendar_events contains (calendar_id, Google event) pairs.

    Callers must derive expectations from classes and rules, never from drafts.
    Missing does not mean no_available_slot unless the planner proves it.
    """
    zone = ZoneInfo(timezone)

    def local(value):
        return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)

    events = list(calendar_events)
    protected = [(local(start), local(end)) for start, end in protected_intervals]
    reasons = no_slot_reasons or {}
    checks = []
    for expected in expectations:
        if expected.disabled or expected.completed:
            checks.append(PreparationCheck(expected, PreparationState.DISABLED
                          if expected.disabled else PreparationState.COMPLETED))
            continue
        if not verified:
            checks.append(PreparationCheck(expected, PreparationState.UNCONFIRMED,
                                           'не удалось проверить Google Calendar'))
            continue
        matches = [(calendar_id, event) for calendar_id, event in events
                   if event.get('status') != 'cancelled'
                   and CalendarIntegrityService._is_preparation_projection(event)
                   and CalendarIntegrityService._source_id(event) == expected.source_id
                   and (event.get('extendedProperties', {}).get('private', {}).get(
                       'personal_os_scope', expected.scope) == expected.scope)]
        if not matches:
            reason = reasons.get((expected.scope, expected.source_id))
            checks.append(PreparationCheck(expected,
                PreparationState.NO_AVAILABLE_SLOT if reason else PreparationState.MISSING,
                reason or 'связанная подготовка отсутствует в календаре'))
            continue
        reason = ''
        if len(matches) != 1:
            reason = 'найдено несколько подготовок к одному занятию'
        else:
            calendar_id, event = matches[0]
            interval = CalendarIntegrityService._interval(event)
            if CalendarIntegrityService._is_authorized_manual_conflict(event):
                reason = 'создана с пометкой конфликта; перенеси вручную'
            elif not interval:
                reason = 'не удалось прочитать время подготовки'
            else:
                start, end = map(local, interval)
                if calendar_id != expected.calendar_id:
                    reason = 'подготовка находится в другом календаре'
                elif end - start != timedelta(minutes=expected.minutes):
                    reason = 'длительность не соответствует правилу'
                elif start < local(expected.earliest) or end > local(expected.deadline):
                    reason = 'подготовка вне допустимого окна'
                elif any(start < right and end > left for left, right in protected):
                    reason = 'пересечение с обязательным занятым временем'
                else:
                    for other_calendar, other in events:
                        if (other_calendar, other.get('id')) == (calendar_id, event.get('id')):
                            continue
                        if other.get('status') == 'cancelled' or other.get('transparency') == 'transparent':
                            continue
                        other_interval = CalendarIntegrityService._interval(other)
                        if not other_interval and other.get('start', {}).get('date'):
                            try:
                                other_interval = (
                                    datetime.fromisoformat(other['start']['date']),
                                    datetime.fromisoformat(other['end']['date']),
                                )
                            except (KeyError, ValueError):
                                reason = 'не удалось проверить время другого события'
                                break
                        if other_interval:
                            left, right = map(local, other_interval)
                            if start < right and end > left:
                                reason = 'пересечение с другим событием'
                                break
        checks.append(PreparationCheck(expected, PreparationState.CONFLICTING
                                       if reason else PreparationState.PLACED, reason))
    return checks


def render_preparation_issues(checks, *, page=None):
    """Deterministic summary (3 groups) or details (5 issues per page)."""
    issues = [check for check in checks if check.state not in {
        PreparationState.DISABLED, PreparationState.COMPLETED, PreparationState.PLACED,
    }]
    if not issues:
        return 'Проверка завершена, проблем нет.'
    if page is not None:
        page = max(0, min(page, (len(issues) - 1) // 5))
        rows = issues[page * 5:(page + 1) * 5]
        return '\n'.join([
            f'Проблемы: страница {page + 1} из {(len(issues) + 4) // 5}.',
            *[f'• {row.expectation.lesson_start:%d.%m}: {row.expectation.title} — {row.reason}.'
              for row in rows],
        ])
    groups = {}
    for issue in issues:
        key = (issue.expectation.lesson_start.date(), issue.expectation.scope, issue.reason)
        groups[key] = groups.get(key, 0) + 1
    lines = []
    for (day, scope, reason), count in sorted(groups.items())[:3]:
        if reason == 'не удалось проверить Google Calendar':
            lines.append(f'Не удалось проверить {day:%d.%m}: Google Calendar недоступен.')
            continue
        noun = ('подготовку' if count % 10 == 1 and count % 100 != 11 else
                'подготовки' if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14)
                else 'подготовок')
        kind = 'работе' if scope == 'work-preparation' else 'учёбе'
        lines.append(f'Не удалось разместить {count} {noun} к {kind} на {day:%d.%m}: {reason}.')
    if len(groups) > 3:
        lines.append('Остальные проблемы — в «Подробности».')
    return '\n'.join(lines)
